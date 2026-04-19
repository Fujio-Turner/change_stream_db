"""
_changes feed processing: HTTP helpers, doc fetching, batch processing,
continuous/websocket stream consumers, and DLQ replay.

Extracted from main.py to keep the REST/_changes client logic in the
rest/ package.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import Checkpoint, MetricsCollector

import aiohttp

try:
    from icecream import ic
except ImportError:  # pragma: no cover
    ic = lambda *a, **kw: None  # noqa: E731

from pipeline_logging import log_event, infer_operation
from rest import OutputForwarder, OutputEndpointDown, DeadLetterQueue, determine_method

logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# HTTP helpers with retry
# ---------------------------------------------------------------------------


class ShutdownRequested(Exception):
    """Raised when a shutdown signal interrupts a retryable operation."""


class RetryableHTTP:
    def __init__(self, session: aiohttp.ClientSession, retry_cfg: dict):
        self._session = session
        self._max_retries = retry_cfg.get("max_retries", 5)
        self._backoff_base = retry_cfg.get("backoff_base_seconds", 1)
        self._backoff_max = retry_cfg.get("backoff_max_seconds", 60)
        self._retry_statuses = set(
            retry_cfg.get("retry_on_status", [500, 502, 503, 504])
        )
        self._metrics = None
        self._shutdown_event: asyncio.Event | None = None

    def set_metrics(self, metrics: MetricsCollector | None) -> None:
        self._metrics = metrics

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        self._shutdown_event = event

    async def request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        shutdown = kwargs.pop("shutdown_event", None) or self._shutdown_event
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            if shutdown and shutdown.is_set():
                raise ShutdownRequested(
                    f"Shutdown requested before attempt #{attempt} for {method} {url}"
                )

            try:
                t_auth = time.monotonic()
                resp = await self._session.request(method, url, **kwargs)
                auth_elapsed = time.monotonic() - t_auth
                # Track auth metrics for inbound (gateway) requests
                if self._metrics:
                    self._metrics.inc("inbound_auth_total")
                    self._metrics.record_inbound_auth_time(auth_elapsed)
                    if resp.status in (401, 403):
                        self._metrics.inc("inbound_auth_failure_total")
                    else:
                        self._metrics.inc("inbound_auth_success_total")
                if resp.status < 300:
                    return resp
                body = await resp.text()
                if resp.status in self._retry_statuses:
                    log_event(
                        logger,
                        "warn",
                        "RETRY",
                        "retryable response",
                        http_method=method,
                        url=url,
                        status=resp.status,
                        attempt=attempt,
                    )
                    resp.release()
                    if self._metrics:
                        self._metrics.inc("retries_total")
                elif 400 <= resp.status < 500:
                    log_event(
                        logger,
                        "error",
                        "HTTP",
                        "client error",
                        http_method=method,
                        url=url,
                        status=resp.status,
                    )
                    raise ClientHTTPError(resp.status, body)
                elif 300 <= resp.status < 400:
                    log_event(
                        logger,
                        "warn",
                        "HTTP",
                        "redirect – not following",
                        http_method=method,
                        url=url,
                        status=resp.status,
                    )
                    raise RedirectHTTPError(resp.status, body)
                else:
                    raise ServerHTTPError(resp.status, body)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log_event(
                    logger,
                    "warn",
                    "RETRY",
                    "connection error: %s" % exc,
                    http_method=method,
                    url=url,
                    attempt=attempt,
                )
                last_exc = exc
                if self._metrics:
                    self._metrics.inc("retries_total")

            if attempt < self._max_retries:
                delay = min(
                    self._backoff_base * (2 ** (attempt - 1)), self._backoff_max
                )
                log_event(
                    logger,
                    "info",
                    "RETRY",
                    "backing off before retry",
                    delay_seconds=delay,
                    attempt=attempt,
                )
                if shutdown:
                    try:
                        await asyncio.wait_for(shutdown.wait(), timeout=delay)
                        raise ShutdownRequested(
                            f"Shutdown during backoff for {method} {url}"
                        )
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(delay)

        if self._metrics:
            self._metrics.inc("retry_exhausted_total")
        raise ConnectionError(
            f"All {self._max_retries} retries exhausted for {method} {url}"
        ) from last_exc


class ClientHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class RedirectHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class ServerHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


# ---------------------------------------------------------------------------
# Fetch-docs helpers (bulk_get for SG/App Services, individual GET for Edge)
# ---------------------------------------------------------------------------


def _chunked(lst: list, size: int) -> list[list]:
    """Split a list into chunks of at most `size` items."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


async def fetch_docs(
    http: RetryableHTTP,
    base_url: str,
    rows: list[dict],
    auth: aiohttp.BasicAuth | None,
    headers: dict,
    src: str,
    max_concurrent: int = 20,
    batch_size: int = 100,
    metrics: MetricsCollector | None = None,
) -> list[dict]:
    """
    Fetch full document bodies for _changes rows that only have id/rev.

    Rows are processed in batches of `batch_size` (default 100) to avoid
    overwhelming the server with a single massive request.

    - Sync Gateway / App Services → POST _bulk_get  (one request per batch)
    - Edge Server → individual GET /{keyspace}/{docid}?rev=  (no _bulk_get)
    """
    eligible = [r for r in rows if r.get("changes")]
    if not eligible:
        return []

    batches = _chunked(eligible, batch_size)
    log_event(
        logger,
        "info",
        "HTTP",
        "fetching %d docs in %d batch(es)" % (len(eligible), len(batches)),
        batch_size=batch_size,
        doc_count=len(eligible),
    )

    all_results: list[dict] = []
    for i, batch in enumerate(batches):
        log_event(
            logger,
            "debug",
            "HTTP",
            "fetch batch %d/%d: %d docs" % (i + 1, len(batches), len(batch)),
            batch_size=len(batch),
        )
        if src == "edge_server":
            results = await _fetch_docs_individually(
                http, base_url, batch, auth, headers, max_concurrent, metrics=metrics
            )
        else:
            results = await _fetch_docs_bulk_get(
                http, base_url, batch, auth, headers, metrics=metrics
            )
        all_results.extend(results)

    return all_results


async def _fetch_single_doc_with_retry(
    http: RetryableHTTP,
    base_url: str,
    doc_id: str,
    rev: str,
    auth: aiohttp.BasicAuth | None,
    headers: dict,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    metrics: MetricsCollector | None = None,
) -> dict | None:
    """Fetch a single doc via GET with exponential backoff.

    Used as a fallback when _bulk_get is missing documents.
    """
    url = f"{base_url}/{doc_id}"
    params: dict[str, str] = {}
    if rev:
        params["rev"] = rev
    for attempt in range(1, max_retries + 1):
        try:
            resp = await http.request(
                "GET", url, params=params, auth=auth, headers=headers
            )
            raw_bytes = await resp.read()
            resp.release()
            if metrics:
                metrics.inc("bytes_received_total", len(raw_bytes))
            doc = json.loads(raw_bytes)
            return doc
        except ClientHTTPError as exc:
            if exc.status in (401, 403):
                raise
            ic("bulk_get fallback: client error", doc_id, exc.status, attempt)
            log_event(
                logger,
                "warn",
                "HTTP",
                "bulk_get fallback GET failed (client error)",
                doc_id=doc_id,
                status=exc.status,
                attempt=attempt,
            )
        except Exception as exc:
            ic("bulk_get fallback: error", doc_id, type(exc).__name__, attempt)
            log_event(
                logger,
                "warn",
                "RETRY",
                "bulk_get fallback GET failed",
                doc_id=doc_id,
                attempt=attempt,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
        if attempt < max_retries:
            delay = min(backoff_base * (2 ** (attempt - 1)), 60)
            await asyncio.sleep(delay)

    ic("bulk_get fallback: exhausted retries", doc_id)
    log_event(
        logger,
        "error",
        "HTTP",
        "failed to get doc from failed _bulk_get after retries",
        doc_id=doc_id,
        attempt=max_retries,
    )
    if metrics:
        metrics.inc("doc_fetch_errors_total")
    return None


async def _fetch_docs_bulk_get(
    http: RetryableHTTP,
    base_url: str,
    rows: list[dict],
    auth: aiohttp.BasicAuth | None,
    headers: dict,
    metrics: MetricsCollector | None = None,
) -> list[dict]:
    """Fetch full docs via _bulk_get (Sync Gateway / App Services)."""
    docs_req = [{"id": r["id"], "rev": r["changes"][0]["rev"]} for r in rows]
    if not docs_req:
        return []
    url = f"{base_url}/_bulk_get?revs=false"
    payload = {"docs": docs_req}
    requested_count = len(docs_req)
    log_event(
        logger,
        "info",
        "HTTP",
        "_bulk_get: requesting %d docs" % requested_count,
        doc_count=requested_count,
    )
    # DEBUG: log the individual _id,_rev pairs being requested
    for dr in docs_req:
        log_event(
            logger,
            "debug",
            "HTTP",
            "_bulk_get request item",
            doc_id=dr["id"],
        )
    ic(url, requested_count)
    t0 = time.monotonic()
    resp = await http.request(
        "POST",
        url,
        json=payload,
        auth=auth,
        headers={**headers, "Content-Type": "application/json"},
    )
    # _bulk_get returns multipart/mixed or JSON depending on SG version
    ct = resp.content_type or ""
    results: list[dict] = []
    response_bytes = 0
    if "application/json" in ct:
        raw_bytes = await resp.read()
        response_bytes = len(raw_bytes)
        resp.release()
        if metrics:
            metrics.inc("bytes_received_total", response_bytes)
        try:
            body = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            logger.warning(
                "bulk_get: malformed JSON response (%d bytes): %s",
                len(raw_bytes),
                exc,
            )
            if metrics:
                metrics.inc("doc_fetch_errors_total")
            return []
        for item in body.get("results", []):
            for doc_entry in item.get("docs", []):
                ok = doc_entry.get("ok")
                if ok:
                    results.append(ok)
    else:
        # Fallback: read raw text and attempt JSON extraction
        raw = await resp.text()
        response_bytes = len(raw.encode("utf-8"))
        resp.release()
        if metrics:
            metrics.inc("bytes_received_total", response_bytes)
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if metrics:
        metrics.inc("doc_fetch_requests_total")
        metrics.record_doc_fetch_time(time.monotonic() - t0)

    log_event(
        logger,
        "info",
        "HTTP",
        "_bulk_get: received %d docs" % len(results),
        doc_count=len(results),
    )
    log_event(
        logger,
        "debug",
        "HTTP",
        "_bulk_get response detail",
        doc_count=len(results),
        input_count=requested_count,
        bytes=response_bytes,
    )
    for doc in results:
        log_event(
            logger,
            "debug",
            "HTTP",
            "_bulk_get result doc",
            doc_id=doc.get("_id", ""),
        )

    # -- Verify we got all requested docs back --
    returned_count = len(results)
    if returned_count < requested_count:
        missing_count = requested_count - returned_count
        ic("🍦 bulk_get missing docs", requested_count, returned_count, missing_count)
        log_event(
            logger,
            "warn",
            "HTTP",
            "🍦 _bulk_get returned fewer docs than requested",
            batch_size=requested_count,
            doc_count=returned_count,
            input_count=missing_count,
        )

        # Determine which doc IDs are missing
        returned_ids = {doc.get("_id", "") for doc in results}
        missing_rows = [r for r in rows if r["id"] not in returned_ids]

        ic("bulk_get: fetching missing docs individually", len(missing_rows))

        recovered: list[dict] = []
        failed_ids: list[str] = []
        for row in missing_rows:
            doc_id = row["id"]
            rev = row["changes"][0]["rev"] if row.get("changes") else ""
            doc = await _fetch_single_doc_with_retry(
                http, base_url, doc_id, rev, auth, headers, metrics=metrics
            )
            if doc is not None:
                recovered.append(doc)
            else:
                failed_ids.append(doc_id)

        if recovered:
            ic("bulk_get fallback: recovered docs", len(recovered))
            log_event(
                logger,
                "info",
                "HTTP",
                "got %d document(s) from failed _bulk_get via individual GET"
                % len(recovered),
                doc_count=len(recovered),
                batch_size=missing_count,
            )
            results.extend(recovered)

        if failed_ids:
            ic("bulk_get fallback: permanently failed", failed_ids)
            log_event(
                logger,
                "error",
                "HTTP",
                "failed to get %d doc(s) from failed _bulk_get after retries"
                % len(failed_ids),
                doc_count=len(failed_ids),
            )

    return results


async def _fetch_docs_individually(
    http: RetryableHTTP,
    base_url: str,
    rows: list[dict],
    auth: aiohttp.BasicAuth | None,
    headers: dict,
    max_concurrent: int,
    metrics: MetricsCollector | None = None,
) -> list[dict]:
    """
    Fetch docs one-by-one via GET /{keyspace}/{docid}?rev={rev}.

    Used for Edge Server which does not have a _bulk_get endpoint.
    Requests are fanned out with a semaphore to cap concurrency.
    """
    sem = asyncio.Semaphore(max_concurrent)
    results: list[dict] = []
    lock = asyncio.Lock()
    t0 = time.monotonic()

    async def _get_one(row: dict) -> None:
        doc_id = row.get("id", "")
        rev = row["changes"][0]["rev"] if row.get("changes") else None
        url = f"{base_url}/{doc_id}"
        params: dict[str, str] = {}
        if rev:
            params["rev"] = rev
        log_event(
            logger,
            "debug",
            "HTTP",
            "GET single doc",
            doc_id=doc_id,
        )
        async with sem:
            try:
                resp = await http.request(
                    "GET", url, params=params, auth=auth, headers=headers
                )
                raw_bytes = await resp.read()
                if metrics:
                    metrics.inc("bytes_received_total", len(raw_bytes))
                doc = json.loads(raw_bytes)
                resp.release()
                log_event(
                    logger,
                    "debug",
                    "HTTP",
                    "GET single doc received",
                    doc_id=doc_id,
                    bytes=len(raw_bytes),
                )
                async with lock:
                    results.append(doc)
            except ClientHTTPError as exc:
                if exc.status in (401, 403):
                    raise  # auth errors are non-retryable
                logger.warning("Failed to fetch doc %s: HTTP %d", doc_id, exc.status)
                if metrics:
                    metrics.inc("doc_fetch_errors_total")
            except Exception as exc:
                logger.warning("Failed to fetch doc %s: %s", doc_id, exc)
                if metrics:
                    metrics.inc("doc_fetch_errors_total")

    tasks = [asyncio.create_task(_get_one(r)) for r in rows]
    log_event(
        logger,
        "info",
        "HTTP",
        "fetching %d docs individually" % len(tasks),
        doc_count=len(tasks),
    )
    await asyncio.gather(*tasks)
    if metrics:
        metrics.inc("doc_fetch_requests_total")
        metrics.record_doc_fetch_time(time.monotonic() - t0)
    return results


# ---------------------------------------------------------------------------
# Helpers: shared batch processing & continuous feed
# ---------------------------------------------------------------------------


def _parse_seq_number(seq) -> int:
    """Extract the numeric portion of a sequence value for comparison.

    Sync Gateway sequences can be plain integers (``150``), strings
    (``"150"``), or compound strings (``"42:150"``).  CouchDB uses
    opaque strings like ``"292786-g1AAAAFe..."`` where the leading
    integer is the sequence number.  This helper extracts the largest
    integer component so that ``last_seq`` from ``_changes`` can be
    compared to ``update_seq`` from the database root endpoint.
    """
    s = str(seq)
    # Split on both ":" (SG compound) and "-" (CouchDB opaque) delimiters
    parts = re.split(r"[:\-]", s)
    best = 0
    for part in parts:
        try:
            best = max(best, int(part))
        except ValueError:
            continue
    return best


async def fetch_db_update_seq(
    http: RetryableHTTP,
    base_url: str,
    basic_auth: aiohttp.BasicAuth | None,
    auth_headers: dict,
) -> int | None:
    """GET ``{base_url}/`` and return the ``update_seq`` value.

    The database root endpoint returns metadata including ``update_seq``
    which represents the latest sequence number in the database.  This is
    used during optimized initial sync to know when catch-up pagination
    has reached the end of the feed that existed at the start of the sync.

    Returns ``None`` if the request fails or the field is missing so the
    caller can fall back to the existing zero-results strategy.
    """
    try:
        url = base_url.rstrip("/") + "/"
        resp = await http.request(
            "GET",
            url,
            auth=basic_auth,
            headers=auth_headers,
        )
        body = json.loads(await resp.read())
        resp.release()
        raw_seq = body.get("update_seq")
        # Edge Server database-level response: update_seq is nested
        # under each collection in "collections", not at the top level.
        # Use the max update_seq across all collections.
        if raw_seq is None and "collections" in body:
            collections = body["collections"]
            if collections:
                raw_seq = max(c.get("update_seq", 0) for c in collections.values())
                log_event(
                    logger,
                    "debug",
                    "CHANGES",
                    "extracted update_seq from Edge Server collections",
                    url=url,
                    collection_count=len(collections),
                )
        if raw_seq is None:
            log_event(
                logger,
                "warn",
                "CHANGES",
                "database root response missing update_seq",
                url=url,
            )
            return None
        seq_int = _parse_seq_number(raw_seq)
        log_event(
            logger,
            "info",
            "CHANGES",
            "fetched database update_seq=%d as initial sync target" % seq_int,
            url=url,
            update_seq=seq_int,
        )
        return seq_int
    except Exception as exc:
        log_event(
            logger,
            "warn",
            "CHANGES",
            "failed to fetch database update_seq: %s – "
            "falling back to zero-results completion" % exc,
        )
        return None


def _build_changes_body(
    feed_cfg: dict,
    src: str,
    since: str,
    feed_type: str,
    timeout_ms: int,
    limit: int = 0,
    active_only_override: bool | None = None,
    include_docs_override: bool | None = None,
) -> dict:
    """Build JSON body for a POST _changes request.

    Both Sync Gateway and CouchDB accept the same parameters in the
    request body that they accept as query parameters, so we send
    everything via POST body to avoid URL-length limits.

    ``active_only_override`` lets the caller force ``active_only`` on or
    off regardless of the config value (used during initial sync).

    ``include_docs_override`` lets the caller force ``include_docs`` on or
    off regardless of the config value (used during initial sync).
    """
    body: dict = {
        "feed": feed_type,
        "since": since,
        "heartbeat": feed_cfg.get("heartbeat_ms", 30000),
        "timeout": timeout_ms,
    }
    # active_only is a Couchbase-specific parameter (not supported by CouchDB)
    use_active_only = (
        active_only_override
        if active_only_override is not None
        else feed_cfg.get("active_only", False)
    )
    if use_active_only and src != "couchdb":
        body["active_only"] = True
    use_include_docs = (
        include_docs_override
        if include_docs_override is not None
        else feed_cfg.get("include_docs", False)
    )
    if use_include_docs:
        body["include_docs"] = True
    if limit > 0:
        body["limit"] = limit
    # Channels filter is SG/App Services specific (not CouchDB)
    channels = feed_cfg.get("channels", [])
    if channels and src != "couchdb":
        body["filter"] = "sync_gateway/bychannel"
        body["channels"] = ",".join(channels)
    if src in ("sync_gateway", "app_services"):
        body["version_type"] = feed_cfg.get("version_type", "rev")
    return body


async def _sleep_with_backoff(
    retry_cfg: dict, failure_count: int, shutdown_event: asyncio.Event
) -> None:
    """Exponential backoff sleep using retry config."""
    base = retry_cfg.get("backoff_base_seconds", 1)
    max_s = retry_cfg.get("backoff_max_seconds", 60)
    delay = min(base * (2 ** (failure_count - 1)), max_s)
    logger.info("Backing off %.1fs before retry (failure #%d)", delay, failure_count)
    await _sleep_or_shutdown(delay, shutdown_event)


async def _process_changes_batch(
    results: list[dict],
    last_seq: str,
    since: str,
    *,
    feed_cfg: dict,
    proc_cfg: dict,
    output: OutputForwarder,
    dlq: DeadLetterQueue,
    checkpoint: Checkpoint,
    http: RetryableHTTP,
    base_url: str,
    basic_auth: aiohttp.BasicAuth | None,
    auth_headers: dict,
    semaphore: asyncio.Semaphore,
    src: str,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    shutdown_cfg: dict | None = None,
    initial_sync: bool = False,
    job_id: str = "",
    attachment_processor=None,
) -> tuple[str, bool]:
    """
    Process a batch of _changes results: filter, fetch docs, forward to output,
    checkpoint.  Returns (new_since, output_failed).

    When ``initial_sync`` is True and the source is CouchDB (which lacks
    ``active_only``), deleted and removed changes are silently filtered
    out regardless of the ``ignore_delete``/``ignore_remove`` config.
    """
    batch_t0 = time.monotonic()
    sequential = proc_cfg.get("sequential", False)

    if metrics:
        metrics.inc("poll_cycles_total")
        metrics.set("last_poll_timestamp", time.time())
        metrics.set("last_batch_size", len(results))
        metrics.inc("changes_received_total", len(results))
        metrics.record_batch_received(len(results))

    if not results:
        new_since = str(last_seq)
        await checkpoint.save(new_since, http, base_url, basic_auth, auth_headers)
        if metrics:
            metrics.inc("checkpoint_saves_total")
            metrics.set("checkpoint_seq", new_since)
        return new_since, False

    if metrics and len(results) >= metrics.flood_threshold:
        log_event(
            logger,
            "warn",
            "FLOOD",
            "flood detected: %d changes in single batch (threshold=%d)"
            % (len(results), metrics.flood_threshold),
            batch_size=len(results),
            flood_threshold=metrics.flood_threshold,
        )

    log_event(
        logger,
        "info",
        "CHANGES",
        "_changes batch: %d changes" % len(results),
        batch_size=len(results),
    )
    # DEBUG: log each individual change row (gated to avoid overhead)
    if logger.isEnabledFor(logging.DEBUG):
        for change in results:
            c_id = change.get("id", "")
            c_rev = ""
            c_changes = change.get("changes", [])
            if c_changes:
                c_rev = c_changes[0].get("rev", "")
            log_event(
                logger,
                "debug",
                "CHANGES",
                "change row",
                doc_id=c_id,
                seq=change.get("seq", ""),
            )
            ic(c_id, c_rev, change.get("seq", ""))

    # Count deletes/removes in the feed (always), then optionally filter.
    # During initial sync for CouchDB (no active_only), force-skip
    # deleted/removed changes to replicate active_only behaviour.
    force_skip_deletes = initial_sync and src == "couchdb"
    filtered: list[dict] = []
    deleted_count = 0
    removed_count = 0
    feed_deletes = 0
    feed_removes = 0
    for change in results:
        if change.get("deleted"):
            feed_deletes += 1
        if change.get("removed"):
            feed_removes += 1
        if (proc_cfg.get("ignore_delete") or force_skip_deletes) and change.get(
            "deleted"
        ):
            ic("ignoring deleted", change.get("id"))
            deleted_count += 1
            continue
        if (proc_cfg.get("ignore_remove") or force_skip_deletes) and change.get(
            "removed"
        ):
            ic("ignoring removed", change.get("id"))
            removed_count += 1
            continue
        filtered.append(change)

    if metrics:
        if feed_deletes:
            metrics.inc("feed_deletes_seen_total", feed_deletes)
        if feed_removes:
            metrics.inc("feed_removes_seen_total", feed_removes)
        if deleted_count or removed_count:
            metrics.inc("changes_deleted_total", deleted_count)
            metrics.inc("changes_removed_total", removed_count)
            metrics.inc("changes_filtered_total", deleted_count + removed_count)

    if deleted_count or removed_count:
        log_event(
            logger,
            "debug",
            "PROCESSING",
            "filtered changes batch",
            input_count=len(results),
            filtered_count=len(filtered),
        )

    # If include_docs was false, fetch full docs
    docs_by_id: dict[str, dict] = {}
    if not feed_cfg.get("include_docs") and filtered:
        batch_size = proc_cfg.get("get_batch_number", 100)
        fetched = await fetch_docs(
            http,
            base_url,
            filtered,
            basic_auth,
            auth_headers,
            src,
            max_concurrent,
            batch_size,
            metrics=metrics,
        )
        for doc in fetched:
            docs_by_id[doc.get("_id", "")] = doc
        if metrics:
            metrics.inc("docs_fetched_total", len(fetched))

    # Process changes – send each doc to the output
    output_failed = False
    batch_success = 0
    batch_fail = 0

    async def process_one(change: dict) -> dict:
        async with semaphore:
            if metrics:
                metrics.inc("active_tasks")
            try:
                doc_id = change.get("id", "")
                if feed_cfg.get("include_docs"):
                    doc = change.get("doc", change)
                else:
                    doc = docs_by_id.get(doc_id, change)
                # ── ATTACHMENT stage (between MIDDLE and RIGHT) ──
                if attachment_processor is not None:
                    try:
                        doc, _skip = await attachment_processor.process(
                            doc, base_url, http, basic_auth, auth_headers, src
                        )
                    except Exception as att_exc:
                        log_event(
                            logger,
                            "error",
                            "PROCESSING",
                            "attachment processing failed: %s" % att_exc,
                            doc_id=doc_id,
                        )
                        raise

                method = determine_method(
                    change,
                    write_method=getattr(output, "_write_method", "PUT"),
                    delete_method=getattr(output, "_delete_method", "DELETE"),
                )
                op = infer_operation(change=change, doc=doc, method=method)
                log_event(
                    logger,
                    "trace",
                    "OUTPUT",
                    "sending document",
                    operation=op,
                    doc_id=doc_id,
                    mode=output._mode,
                    http_method=method,
                )
                result = await output.send(doc, method)
                result["_change"] = change
                result["_doc"] = doc
                if result.get("ok"):
                    log_event(
                        logger,
                        "debug",
                        "OUTPUT",
                        "document forwarded",
                        operation=op,
                        doc_id=doc_id,
                        status=result.get("status"),
                    )
                else:
                    log_event(
                        logger,
                        "warn",
                        "OUTPUT",
                        "document delivery failed",
                        operation=op,
                        doc_id=doc_id,
                        status=result.get("status"),
                    )
                return result
            finally:
                if metrics:
                    metrics.inc("active_tasks", -1)

    if every_n_docs > 0 and sequential:
        for i in range(0, len(filtered), every_n_docs):
            sub_batch = filtered[i : i + every_n_docs]
            for change in sub_batch:
                try:
                    result = await process_one(change)
                    if result.get("ok"):
                        batch_success += 1
                    else:
                        batch_fail += 1
                        if result.get("data_error_action") == "skip":
                            log_event(
                                logger,
                                "warn",
                                "OUTPUT",
                                "data error – skipping doc (data_error_action=skip)",
                                doc_id=change.get("id", ""),
                            )
                        else:
                            if dlq.enabled and metrics:
                                metrics.inc("dead_letter_total")
                                metrics.set("dlq_last_write_epoch", time.time())
                            await dlq.write(
                                result["_doc"],
                                result,
                                change.get("seq", ""),
                                target_url=getattr(output, "target_url", ""),
                                metrics=metrics,
                            )
                except (OutputEndpointDown, ShutdownRequested) as exc:
                    output_failed = True
                    is_shutdown = isinstance(exc, ShutdownRequested)
                    log_event(
                        logger,
                        "error",
                        "OUTPUT",
                        "%s – not advancing checkpoint past since=%s: %s"
                        % ("SHUTDOWN" if is_shutdown else "OUTPUT DOWN", since, exc),
                        doc_id=change.get("id", ""),
                        seq=str(change.get("seq", "")),
                    )
                    # DLQ remaining docs in this sub-batch if shutdown + dlq_inflight_on_shutdown
                    if (
                        is_shutdown
                        and (shutdown_cfg or {}).get("dlq_inflight_on_shutdown", False)
                        and dlq.enabled
                    ):
                        remaining = sub_batch[sub_batch.index(change) :]
                        for rem in remaining:
                            rem_doc = (
                                rem.get("doc", rem)
                                if feed_cfg.get("include_docs")
                                else docs_by_id.get(rem.get("id", ""), rem)
                            )
                            await dlq.write(
                                rem_doc,
                                {
                                    "doc_id": rem.get("id", ""),
                                    "method": "PUT",
                                    "status": 0,
                                    "error": "shutdown_inflight",
                                },
                                rem.get("seq", ""),
                                target_url=getattr(output, "target_url", ""),
                                metrics=metrics,
                            )
                        if metrics:
                            metrics.inc("dead_letter_total", len(remaining))
                            metrics.set("dlq_last_write_epoch", time.time())
                        log_event(
                            logger,
                            "warn",
                            "SHUTDOWN",
                            "DLQ'd %d remaining docs from sub-batch" % len(remaining),
                        )
                    break
            if output_failed:
                break
            sub_seq = str(sub_batch[-1].get("seq", last_seq))
            since = sub_seq
            await checkpoint.save(since, http, base_url, basic_auth, auth_headers)
            if metrics:
                metrics.inc("checkpoint_saves_total")
                metrics.set("checkpoint_seq", since)
    else:
        try:
            if sequential:
                for change in filtered:
                    result = await process_one(change)
                    if result.get("ok"):
                        batch_success += 1
                    else:
                        batch_fail += 1
                        if result.get("data_error_action") == "skip":
                            log_event(
                                logger,
                                "warn",
                                "OUTPUT",
                                "data error – skipping doc (data_error_action=skip)",
                                doc_id=change.get("id", ""),
                            )
                        else:
                            if dlq.enabled and metrics:
                                metrics.inc("dead_letter_total")
                                metrics.set("dlq_last_write_epoch", time.time())
                            await dlq.write(
                                result["_doc"],
                                result,
                                change.get("seq", ""),
                                target_url=getattr(output, "target_url", ""),
                                metrics=metrics,
                            )
            else:
                tasks = [asyncio.create_task(process_one(c)) for c in filtered]
                done, _ = await asyncio.wait(tasks)
                for t in done:
                    if t.exception():
                        raise t.exception()
                    result = t.result()
                    if result.get("ok"):
                        batch_success += 1
                    else:
                        batch_fail += 1
                        if result.get("data_error_action") == "skip":
                            log_event(
                                logger,
                                "warn",
                                "OUTPUT",
                                "data error – skipping doc (data_error_action=skip)",
                                doc_id=result.get("doc_id", ""),
                            )
                        else:
                            if dlq.enabled and metrics:
                                metrics.inc("dead_letter_total")
                                metrics.set("dlq_last_write_epoch", time.time())
                            await dlq.write(
                                result["_doc"],
                                result,
                                result["_change"].get("seq", ""),
                                target_url=getattr(output, "target_url", ""),
                                metrics=metrics,
                            )
        except (OutputEndpointDown, ShutdownRequested) as exc:
            output_failed = True
            is_shutdown = isinstance(exc, ShutdownRequested)
            log_event(
                logger,
                "error",
                "OUTPUT",
                "%s – not advancing checkpoint past since=%s: %s"
                % ("SHUTDOWN" if is_shutdown else "OUTPUT DOWN", since, exc),
                error_detail=str(exc),
            )
            # DLQ all unprocessed docs if shutdown + dlq_inflight_on_shutdown
            if (
                is_shutdown
                and (shutdown_cfg or {}).get("dlq_inflight_on_shutdown", False)
                and dlq.enabled
            ):
                # In sequential mode, we know which docs haven't been tried yet
                processed_ids = set()  # noqa: F841
                if sequential:
                    # Find which docs were already processed (succeeded or failed above)
                    # The current change that raised is the boundary
                    pass
                # For parallel mode, all docs were dispatched as tasks;
                # unfinished ones got cancelled — DLQ all filtered docs that didn't succeed
                dlq_count = 0
                for ch in filtered:
                    ch_doc = (
                        ch.get("doc", ch)
                        if feed_cfg.get("include_docs")
                        else docs_by_id.get(ch.get("id", ""), ch)
                    )
                    await dlq.write(
                        ch_doc,
                        {
                            "doc_id": ch.get("id", ""),
                            "method": "PUT",
                            "status": 0,
                            "error": "shutdown_inflight",
                        },
                        ch.get("seq", ""),
                        target_url=getattr(output, "target_url", ""),
                        metrics=metrics,
                    )
                    dlq_count += 1
                if metrics:
                    metrics.inc("dead_letter_total", dlq_count)
                    metrics.set("dlq_last_write_epoch", time.time())
                log_event(
                    logger,
                    "warn",
                    "SHUTDOWN",
                    "DLQ'd %d docs from batch (checkpoint not advanced)" % dlq_count,
                )

    if metrics:
        metrics.inc("changes_processed_total", len(filtered))
        metrics.set(
            "changes_pending",
            metrics.changes_received_total - metrics.changes_processed_total,
        )

    total = batch_success + batch_fail
    if total > 0:
        log_event(
            logger,
            "info",
            "PROCESSING",
            "batch complete: %d/%d succeeded, %d failed%s"
            % (
                batch_success,
                total,
                batch_fail,
                " (%d written to dead letter queue)" % batch_fail
                if batch_fail and dlq.enabled
                else "",
            ),
        )

    # Flush DLQ meta once per batch (not per doc) to minimise CBL writes
    if batch_fail > 0 and dlq.enabled:
        _job = job_id or getattr(checkpoint, "_client_id", "")
        dlq.flush_insert_meta(_job)
        if metrics:
            metrics.set("dlq_pending_count", len(dlq.list_pending()))

    output.log_stats()

    if output_failed:
        if metrics:
            metrics.record_batch_processing_time(time.monotonic() - batch_t0)
            metrics.inc("batches_total")
            metrics.inc("batches_failed_total")
        return since, True

    if not (every_n_docs > 0 and sequential):
        since = str(last_seq)
        await checkpoint.save(since, http, base_url, basic_auth, auth_headers)
        if metrics:
            metrics.inc("checkpoint_saves_total")
            metrics.set("checkpoint_seq", since)

    if metrics:
        metrics.record_batch_processing_time(time.monotonic() - batch_t0)
        metrics.inc("batches_total")

    return since, False


async def _catch_up_normal(
    *,
    since: str,
    changes_url: str,
    feed_cfg: dict,
    proc_cfg: dict,
    retry_cfg: dict,
    src: str,
    http: RetryableHTTP,
    basic_auth: aiohttp.BasicAuth | None,
    auth_headers: dict,
    base_url: str,
    output: OutputForwarder,
    dlq: DeadLetterQueue,
    checkpoint: Checkpoint,
    semaphore: asyncio.Semaphore,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    timeout_ms: int,
    changes_http_timeout: aiohttp.ClientTimeout,
    shutdown_cfg: dict | None = None,
    initial_sync: bool = False,
    attachment_processor=None,
) -> str:
    """
    Phase 1 of continuous mode: catch up using one-shot normal requests.
    Repeats until the server returns 0 results, meaning we are caught up.
    Returns the latest since value.

    When ``initial_sync`` is True, ``active_only=true`` is forced for
    Couchbase products so historical deletes are skipped.

    When ``optimize_initial_sync`` is True (from feed_cfg), requests use
    a ``limit`` to page through the feed in chunks.  The worker first
    fetches the database ``update_seq`` via ``GET {base_url}/`` to
    establish a target endpoint.  Once ``last_seq`` from ``_changes``
    reaches or exceeds that target, the initial sync is complete and the
    worker switches to steady-state mode where deletes are processed.
    This avoids the consistency gap where deletes between chunks could
    be missed.  If the ``update_seq`` fetch fails, the worker falls
    back to the original zero-results completion strategy.

    When ``optimize_initial_sync`` is False (the default), no limit is
    set and the full feed is returned in one request — simpler and
    avoids the consistency gap entirely.
    """
    optimize_initial = feed_cfg.get("optimize_initial_sync", False)
    catchup_limit = feed_cfg.get("continuous_catchup_limit", 500)
    # Only apply limit when optimized chunking is enabled during initial sync
    use_limit = (
        catchup_limit if (initial_sync and optimize_initial) or not initial_sync else 0
    )
    failure_count = 0

    # When using optimized/chunked initial sync, fetch the database
    # update_seq first so we know the exact endpoint to reach.
    target_seq: int | None = None
    if initial_sync and optimize_initial:
        target_seq = await fetch_db_update_seq(http, base_url, basic_auth, auth_headers)

    log_event(
        logger,
        "info",
        "CHANGES",
        "catch-up starting (limit=%s, active_only=%s, include_docs=%s%s)"
        % (
            use_limit if use_limit > 0 else "none",
            True if initial_sync else feed_cfg.get("active_only", False),
            False if initial_sync else feed_cfg.get("include_docs", False),
            ", target_seq=%d" % target_seq if target_seq is not None else "",
        ),
        seq=since,
    )

    while not shutdown_event.is_set():
        body_payload = _build_changes_body(
            feed_cfg,
            src,
            since,
            "normal",
            timeout_ms,
            limit=use_limit,
            active_only_override=True if initial_sync else None,
            include_docs_override=False if initial_sync else None,
        )
        ic(changes_url, body_payload, since, "catch-up")

        try:
            t0_changes = time.monotonic()
            resp = await http.request(
                "POST",
                changes_url,
                json=body_payload,
                auth=basic_auth,
                headers={**auth_headers, "Content-Type": "application/json"},
                timeout=changes_http_timeout,
            )
            raw_body = await resp.read()
            body = json.loads(raw_body)
            if metrics:
                metrics.inc("bytes_received_total", len(raw_body))
                metrics.record_changes_request_time(time.monotonic() - t0_changes)
            resp.release()
            failure_count = 0
        except (ClientHTTPError, RedirectHTTPError) as exc:
            logger.error("Non-retryable error during catch-up: %s", exc)
            if metrics:
                metrics.inc("poll_errors_total")
            raise
        except (
            ConnectionError,
            ServerHTTPError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as exc:
            failure_count += 1
            logger.error(
                "Catch-up request failed (attempt #%d): %s", failure_count, exc
            )
            if metrics:
                metrics.inc("poll_errors_total")
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
            continue

        results = body.get("results", [])
        last_seq = body.get("last_seq", since)
        ic(len(results), last_seq, "catch-up batch")

        since, output_failed = await _process_changes_batch(
            results,
            str(last_seq),
            since,
            feed_cfg=feed_cfg,
            proc_cfg=proc_cfg,
            output=output,
            dlq=dlq,
            checkpoint=checkpoint,
            http=http,
            base_url=base_url,
            basic_auth=basic_auth,
            auth_headers=auth_headers,
            semaphore=semaphore,
            src=src,
            metrics=metrics,
            every_n_docs=every_n_docs,
            max_concurrent=max_concurrent,
            shutdown_cfg=shutdown_cfg,
            initial_sync=initial_sync,
            attachment_processor=attachment_processor,
        )

        if output_failed:
            await _sleep_or_shutdown(
                feed_cfg.get("poll_interval_seconds", 10), shutdown_event
            )
            continue

        # ── Check if initial sync is complete ─────────────────────────
        # When using optimized/chunked initial sync with a target_seq,
        # completion is determined by last_seq reaching the target —
        # NOT by getting zero results (which would require an extra
        # round-trip and leaves a consistency gap between chunks).
        reached_target = (
            initial_sync
            and target_seq is not None
            and results
            and _parse_seq_number(last_seq) >= target_seq
        )

        if not results or reached_target:
            if initial_sync and not checkpoint.initial_sync_done:
                checkpoint._initial_sync_done = True
                await checkpoint.save(since, http, base_url, basic_auth, auth_headers)
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "initial sync complete – reverting to config settings"
                    + (
                        " (reached target_seq=%d)" % target_seq
                        if reached_target
                        else ""
                    ),
                )
            log_event(
                logger,
                "info",
                "CHANGES",
                "catch-up complete",
                seq=since,
            )
            return since

        log_event(
            logger,
            "info",
            "CHANGES",
            "catch-up batch: %d changes received" % len(results),
            seq=since,
            batch_size=len(results),
        )

    return since


async def _consume_continuous_stream(
    *,
    since: str,
    changes_url: str,
    feed_cfg: dict,
    proc_cfg: dict,
    retry_cfg: dict,
    src: str,
    http: RetryableHTTP,
    session: aiohttp.ClientSession,
    basic_auth: aiohttp.BasicAuth | None,
    auth_headers: dict,
    base_url: str,
    output: OutputForwarder,
    dlq: DeadLetterQueue,
    checkpoint: Checkpoint,
    semaphore: asyncio.Semaphore,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    timeout_ms: int,
    shutdown_cfg: dict | None = None,
    attachment_processor=None,
) -> str:
    """
    Phase 2 of continuous mode: open a streaming connection with
    feed=continuous and read changes line-by-line.  Returns the latest
    since value when the stream ends (disconnect / error).
    """
    body_payload = _build_changes_body(feed_cfg, src, since, "continuous", timeout_ms)
    # No limit for continuous mode – we want all changes as they arrive
    body_payload.pop("limit", None)
    # No server-side timeout – the stream stays open indefinitely
    body_payload.pop("timeout", None)

    # Use an open-ended HTTP timeout for the streaming connection
    continuous_timeout = aiohttp.ClientTimeout(total=None, sock_read=None)

    logger.info("CONTINUOUS stream: connecting from since=%s", since)
    ic(changes_url, body_payload, since, "continuous stream")

    failure_count = 0

    while not shutdown_event.is_set():
        try:
            resp = await http.request(
                "POST",
                changes_url,
                json=body_payload,
                auth=basic_auth,
                headers={**auth_headers, "Content-Type": "application/json"},
                timeout=continuous_timeout,
            )
        except (
            ConnectionError,
            ServerHTTPError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as exc:
            failure_count += 1
            logger.error(
                "Continuous stream connect failed (attempt #%d): %s", failure_count, exc
            )
            if metrics:
                metrics.inc("poll_errors_total")
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
            continue
        except (ClientHTTPError, RedirectHTTPError) as exc:
            logger.error("Non-retryable error opening continuous stream: %s", exc)
            if metrics:
                metrics.inc("poll_errors_total")
            raise

        logger.info("CONTINUOUS stream: connected, listening for changes")
        if metrics and failure_count > 0:
            metrics.inc("stream_reconnects_total")
        failure_count = 0

        try:
            while not shutdown_event.is_set():
                raw_line = await resp.content.readline()
                if raw_line == b"":
                    logger.warning("Continuous stream closed by server (EOF)")
                    break

                if metrics:
                    metrics.inc("bytes_received_total", len(raw_line))

                line = raw_line.strip()
                if not line:
                    continue  # heartbeat / blank line

                try:
                    row = json.loads(line)
                    if metrics:
                        metrics.inc("stream_messages_total")
                except json.JSONDecodeError:
                    logger.warning(
                        "Continuous stream: unparseable line: %s", line[:200]
                    )
                    if metrics:
                        metrics.inc("stream_parse_errors_total")
                    continue

                row_seq = str(row.get("seq", since))
                ic(row.get("id"), row_seq, "continuous row")

                since, output_failed = await _process_changes_batch(
                    [row],
                    row_seq,
                    since,
                    feed_cfg=feed_cfg,
                    proc_cfg=proc_cfg,
                    output=output,
                    dlq=dlq,
                    checkpoint=checkpoint,
                    http=http,
                    base_url=base_url,
                    basic_auth=basic_auth,
                    auth_headers=auth_headers,
                    semaphore=semaphore,
                    src=src,
                    metrics=metrics,
                    every_n_docs=every_n_docs,
                    max_concurrent=max_concurrent,
                    shutdown_cfg=shutdown_cfg,
                    attachment_processor=attachment_processor,
                )

                if output_failed:
                    logger.warning(
                        "Output failed during continuous stream – dropping to catch-up"
                    )
                    break

            # Update body with latest since for reconnect
            body_payload["since"] = since
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
            failure_count += 1
            logger.warning("Continuous stream read error: %s", exc)
            if metrics:
                metrics.inc("poll_errors_total")
        finally:
            resp.release()

        if failure_count > 0:
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
        else:
            # Clean EOF / output failure – return to catch-up
            return since

    return since


async def _consume_websocket_stream(
    *,
    since: str,
    changes_url: str,
    feed_cfg: dict,
    proc_cfg: dict,
    retry_cfg: dict,
    src: str,
    http: RetryableHTTP,
    session: aiohttp.ClientSession,
    basic_auth: aiohttp.BasicAuth | None,
    auth_headers: dict,
    base_url: str,
    output: OutputForwarder,
    dlq: DeadLetterQueue,
    checkpoint: Checkpoint,
    semaphore: asyncio.Semaphore,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    timeout_ms: int,
    shutdown_cfg: dict | None = None,
    attachment_processor=None,
) -> str:
    """
    WebSocket mode: open a real WebSocket connection to the _changes
    endpoint and read change rows as messages.

    Sync Gateway expects:
      1. ws:// (or wss://) connection to {keyspace}/_changes?feed=websocket
      2. After connection, send a JSON payload with parameters (since, etc.)
      3. Server streams back one JSON message per change row, ending with
         a final message containing only "last_seq".
    """
    # Build ws:// URL from http:// URL
    ws_url = changes_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url += "?feed=websocket"

    # Build the JSON payload to send after connection (mirrors sg_websocket_feed.py)
    payload: dict = {"since": since}
    if feed_cfg.get("include_docs"):
        payload["include_docs"] = True
    if feed_cfg.get("active_only") and src != "couchdb":
        payload["active_only"] = True
    channels = feed_cfg.get("channels", [])
    if channels and src != "couchdb":
        payload["filter"] = "sync_gateway/bychannel"
        payload["channels"] = ",".join(channels)
    # Request periodic heartbeat frames from SG so idle connections
    # stay alive and are not mistaken for dead sockets.
    # Default to 30s; the idle timeout below is set well above this.
    heartbeat_ms = feed_cfg.get("heartbeat_ms", 30000)
    if heartbeat_ms and src != "couchdb":
        payload["heartbeat"] = heartbeat_ms

    # Build WebSocket headers for auth
    ws_headers = dict(auth_headers) if auth_headers else {}
    if basic_auth:
        import base64

        credentials = f"{basic_auth.login}:{basic_auth.password}"
        ws_headers["Authorization"] = "Basic " + base64.b64encode(
            credentials.encode("utf-8")
        ).decode("utf-8")

    logger.info("WEBSOCKET stream: connecting from since=%s", since)
    ic(ws_url, payload, since, "websocket stream")

    failure_count = 0

    while not shutdown_event.is_set():
        try:
            ws = await session.ws_connect(
                ws_url,
                headers=ws_headers,
                heartbeat=None,  # SG does not respond to WS ping/pong
                timeout=aiohttp.ClientWSTimeout(ws_close=timeout_ms / 1000.0),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            failure_count += 1
            logger.error(
                "WebSocket connect failed (attempt #%d): %s", failure_count, exc
            )
            if metrics:
                metrics.inc("poll_errors_total")
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
            continue

        logger.info("WEBSOCKET stream: connected, sending payload")
        if metrics and failure_count > 0:
            metrics.inc("stream_reconnects_total")
        failure_count = 0

        try:
            # Send the request payload
            await ws.send_json(payload)

            # Idle timeout: if no heartbeat or data arrives within 3× the
            # heartbeat interval (or 5 minutes if no heartbeat), treat as
            # dead connection and reconnect.
            if heartbeat_ms and src != "couchdb":
                ws_idle_timeout = max(heartbeat_ms * 3 / 1000.0, 120.0)
            else:
                ws_idle_timeout = max(timeout_ms * 2 / 1000.0, 300.0)

            while not shutdown_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=ws_idle_timeout)
                except asyncio.TimeoutError:
                    failure_count += 1
                    logger.warning(
                        "WebSocket idle timeout (%.0fs) – reconnecting (failure #%d)",
                        ws_idle_timeout,
                        failure_count,
                    )
                    if metrics:
                        metrics.inc("poll_errors_total")
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    # SG sends empty frames as heartbeats – skip them
                    if not msg.data or not msg.data.strip():
                        continue

                    if metrics:
                        metrics.inc("bytes_received_total", len(msg.data))

                    try:
                        parsed = json.loads(msg.data)
                        if metrics:
                            metrics.inc("stream_messages_total")
                    except json.JSONDecodeError:
                        logger.warning(
                            "WebSocket: unparseable message (length=%d)", len(msg.data)
                        )
                        if metrics:
                            metrics.inc("stream_parse_errors_total")
                        continue

                    # SG may send a single dict or an array of change rows
                    rows = parsed if isinstance(parsed, list) else [parsed]

                    # Check for final message: dict with "last_seq" and no "id"
                    if (
                        isinstance(parsed, dict)
                        and "last_seq" in parsed
                        and "id" not in parsed
                    ):
                        since = str(parsed["last_seq"])
                        ic(since, "websocket last_seq received")
                        payload["since"] = since
                        break

                    # Filter out any last_seq-only sentinel dicts in an array
                    change_rows = [r for r in rows if isinstance(r, dict) and "id" in r]
                    if not change_rows:
                        continue

                    last_seq = str(change_rows[-1].get("seq", since))
                    ic(
                        len(change_rows),
                        last_seq,
                        "websocket batch",
                        [
                            {
                                k: r.get(k)
                                for k in ("_id", "_rev", "_deleted", "_removed", "seq")
                                if k in r
                            }
                            for r in change_rows
                        ],
                    )

                    since, output_failed = await _process_changes_batch(
                        change_rows,
                        last_seq,
                        since,
                        feed_cfg=feed_cfg,
                        proc_cfg=proc_cfg,
                        output=output,
                        dlq=dlq,
                        checkpoint=checkpoint,
                        http=http,
                        base_url=base_url,
                        basic_auth=basic_auth,
                        auth_headers=auth_headers,
                        semaphore=semaphore,
                        src=src,
                        metrics=metrics,
                        every_n_docs=every_n_docs,
                        max_concurrent=max_concurrent,
                        shutdown_cfg=shutdown_cfg,
                        attachment_processor=attachment_processor,
                    )
                    payload["since"] = since

                    if output_failed:
                        logger.warning(
                            "Output failed during WebSocket stream – reconnecting"
                        )
                        break

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                    logger.warning("WebSocket stream closed by server")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.warning("WebSocket stream error: %s", ws.exception())
                    break

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            failure_count += 1
            logger.warning("WebSocket stream read error: %s", exc)
            if metrics:
                metrics.inc("poll_errors_total")
        finally:
            if not ws.closed:
                await ws.close()

        if failure_count > 0:
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
        else:
            # Clean close – reconnect immediately for more changes
            continue

    return since


# ---------------------------------------------------------------------------
# DLQ replay
# ---------------------------------------------------------------------------


async def _replay_dead_letter_queue(
    dlq: DeadLetterQueue,
    output: OutputForwarder,
    metrics: MetricsCollector | None,
    shutdown_event: asyncio.Event,
    current_target_url: str = "",
) -> dict:
    """Replay pending DLQ entries before processing new _changes.

    Sends each DLQ doc to the output endpoint. On success, purges the entry
    from CBL so it doesn't accumulate. On failure, leaves it for next startup.
    Entries that exceed max_replay_attempts are skipped (archived).
    Entries whose target_url differs from the current config are flagged.

    Returns a summary dict with counts.
    """
    # Purge expired entries before replaying
    expired = dlq.purge_expired()
    if expired > 0:
        log_event(
            logger,
            "info",
            "DLQ",
            "purged %d expired DLQ entries (retention=%ds)"
            % (expired, dlq.retention_seconds),
        )

    pending = dlq.list_pending()
    if not pending:
        log_event(logger, "info", "DLQ", "no pending dead-letter entries to replay")
        return {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "expired": expired,
        }

    log_event(
        logger,
        "info",
        "DLQ",
        "replaying %d dead-letter entries before processing new changes" % len(pending),
    )

    succeeded = 0
    failed = 0
    skipped = 0
    max_attempts = dlq.max_replay_attempts
    for entry in pending:
        if shutdown_event.is_set():
            log_event(logger, "warn", "DLQ", "shutdown during DLQ replay – stopping")
            break

        dlq_id = entry.get("id", "")
        doc_id = entry.get("doc_id_original", entry.get("doc_id", ""))
        method = entry.get("method", "PUT")
        entry_target = entry.get("target_url", "")
        replay_attempts = entry.get("replay_attempts", 0)

        # Skip entries that have exceeded max replay attempts
        if max_attempts > 0 and replay_attempts >= max_attempts:
            skipped += 1
            log_event(
                logger,
                "warn",
                "DLQ",
                "skipping DLQ entry – max replay attempts (%d) reached" % max_attempts,
                doc_id=doc_id,
                dlq_id=dlq_id,
                replay_attempts=replay_attempts,
            )
            continue

        # Warn if the entry was created for a different output target
        if entry_target and current_target_url and entry_target != current_target_url:
            log_event(
                logger,
                "warn",
                "DLQ",
                "DLQ entry target_url differs from current config",
                doc_id=doc_id,
                dlq_id=dlq_id,
                entry_target=entry_target,
                current_target=current_target_url,
            )

        # Get the full doc data
        full_entry = dlq.get_entry_doc(dlq_id)
        if full_entry is None:
            log_event(
                logger,
                "warn",
                "DLQ",
                "could not load DLQ entry for replay",
                doc_id=dlq_id,
            )
            failed += 1
            continue

        doc = full_entry.get("doc_data", {})
        log_event(
            logger,
            "info",
            "DLQ",
            "replaying DLQ entry",
            doc_id=doc_id,
            dlq_id=dlq_id,
            method=method,
            replay_attempt=replay_attempts + 1,
        )

        try:
            result = await output.send(doc, method)
            if result.get("ok"):
                await dlq.purge(dlq_id)
                succeeded += 1
                log_event(
                    logger,
                    "info",
                    "DLQ",
                    "DLQ entry replayed successfully – purged",
                    doc_id=doc_id,
                    dlq_id=dlq_id,
                )
            else:
                dlq.increment_replay_attempts(dlq_id)
                failed += 1
                log_event(
                    logger,
                    "warn",
                    "DLQ",
                    "DLQ entry replay failed – keeping for next startup",
                    doc_id=doc_id,
                    dlq_id=dlq_id,
                    status=result.get("status"),
                    replay_attempts=replay_attempts + 1,
                )
        except Exception as exc:
            dlq.increment_replay_attempts(dlq_id)
            failed += 1
            log_event(
                logger,
                "warn",
                "DLQ",
                "DLQ entry replay error: %s" % exc,
                doc_id=doc_id,
                dlq_id=dlq_id,
                replay_attempts=replay_attempts + 1,
            )

    # Flush drain timestamp once after the entire replay batch
    if succeeded > 0:
        dlq.flush_drain_meta()

    summary = {
        "total": len(pending),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "expired": expired,
    }
    log_event(
        logger,
        "info",
        "DLQ",
        "DLQ replay complete: %d/%d succeeded, %d failed, %d skipped, %d expired"
        % (succeeded, len(pending), failed, skipped, expired),
    )
    return summary


# ---------------------------------------------------------------------------
# Sleep helper
# ---------------------------------------------------------------------------


async def _sleep_or_shutdown(seconds: float, event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
