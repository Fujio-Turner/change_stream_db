#!/usr/bin/env python3
"""
Production-ready _changes feed processor for:
  - Couchbase Sync Gateway
  - Capella App Services
  - Couchbase Edge Server

Supports longpoll with configurable intervals, checkpoint management,
bulk_get fallback, async parallel or sequential processing, and
forwarding results via stdout or HTTP.
"""

__version__ = "1.3.0"

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import ssl
import sys
import time
import threading
from pathlib import Path

import aiohttp
import aiohttp.web
from icecream import ic

from rest import (
    OutputForwarder,
    OutputEndpointDown,
    DeadLetterQueue,
    determine_method,
    serialize_doc,
    CONTENT_TYPES,
    VALID_OUTPUT_FORMATS,
)
from rest.output_http import check_serialization_library
from cbl_store import USE_CBL, CBLStore, CBLMaintenanceScheduler, close_db, migrate_files_to_cbl
from pipeline_logging import (
    configure_logging,
    log_event,
    infer_operation,
    get_redactor,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------

class MetricsCollector:
    """
    Thread-safe metrics collector that renders Prometheus text exposition format.

    All counters/gauges are stored as simple numeric values and rendered
    on demand when the /_metrics endpoint is hit.
    """

    def __init__(self, src: str, database: str):
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._labels = f'src="{src}",database="{database}"'

        # Counters (monotonically increasing)
        self.poll_cycles_total: int = 0
        self.poll_errors_total: int = 0
        self.changes_received_total: int = 0
        self.changes_processed_total: int = 0
        self.changes_filtered_total: int = 0
        self.changes_deleted_total: int = 0
        self.changes_removed_total: int = 0
        self.output_requests_total: int = 0
        self.output_errors_total: int = 0
        self.docs_fetched_total: int = 0
        self.checkpoint_saves_total: int = 0
        self.checkpoint_save_errors_total: int = 0
        self.retries_total: int = 0

        # Output by HTTP method (PUT / DELETE)
        self.output_put_total: int = 0
        self.output_delete_total: int = 0
        self.output_put_errors_total: int = 0
        self.output_delete_errors_total: int = 0
        self.output_success_total: int = 0
        self.dead_letter_total: int = 0

        # Bytes tracking
        self.bytes_received_total: int = 0     # bytes from _changes + bulk_get/GETs
        self.bytes_output_total: int = 0       # bytes sent to output endpoint

        # _changes feed content tracking (always counted, regardless of filter settings)
        self.feed_deletes_seen_total: int = 0  # changes with deleted=true in the feed
        self.feed_removes_seen_total: int = 0  # changes with removed=true in the feed

        # Gauges (can go up and down)
        self.last_batch_size: int = 0
        self.last_poll_timestamp: float = 0.0
        self.checkpoint_seq: str = "0"
        self.output_endpoint_up: int = 1

        # Output response time tracking (for summary)
        self._output_resp_times: list[float] = []

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + value)

    def set(self, name: str, value) -> None:
        with self._lock:
            setattr(self, name, value)

    def record_output_response_time(self, seconds: float) -> None:
        with self._lock:
            self._output_resp_times.append(seconds)

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            labels = self._labels

            # Output response time summary stats
            ort = self._output_resp_times
            ort_count = len(ort)
            ort_sum = sum(ort) if ort else 0.0
            ort_sorted = sorted(ort) if ort else []

            def _quantile(q: float) -> float:
                if not ort_sorted:
                    return 0.0
                idx = int(q * (len(ort_sorted) - 1))
                return ort_sorted[idx]

        lines: list[str] = []

        def _counter(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{{{labels}}} {value}")

        def _gauge(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{labels}}} {value}")

        # -- Process info --
        _gauge("changes_worker_uptime_seconds",
               "Time in seconds since the worker started.", f"{uptime:.3f}")

        # -- Poll loop --
        _counter("changes_worker_poll_cycles_total",
                 "Total number of _changes poll cycles completed.", self.poll_cycles_total)
        _counter("changes_worker_poll_errors_total",
                 "Total number of _changes poll errors.", self.poll_errors_total)
        _gauge("changes_worker_last_poll_timestamp_seconds",
               "Unix timestamp of the last successful _changes poll.", f"{self.last_poll_timestamp:.3f}")
        _gauge("changes_worker_last_batch_size",
               "Number of changes in the last batch received.", self.last_batch_size)

        # -- Changes --
        _counter("changes_worker_changes_received_total",
                 "Total number of changes received from the _changes feed.", self.changes_received_total)
        _counter("changes_worker_changes_processed_total",
                 "Total number of changes processed and forwarded.", self.changes_processed_total)
        _counter("changes_worker_changes_filtered_total",
                 "Total number of changes filtered out (deletes + removes).", self.changes_filtered_total)
        _counter("changes_worker_changes_deleted_total",
                 "Total number of deleted changes filtered out.", self.changes_deleted_total)
        _counter("changes_worker_changes_removed_total",
                 "Total number of removed changes filtered out.", self.changes_removed_total)

        # -- Feed content (always counted, regardless of filter settings) --
        _counter("changes_worker_feed_deletes_seen_total",
                 "Total changes with deleted=true seen in the feed.", self.feed_deletes_seen_total)
        _counter("changes_worker_feed_removes_seen_total",
                 "Total changes with removed=true seen in the feed.", self.feed_removes_seen_total)

        # -- Bytes --
        _counter("changes_worker_bytes_received_total",
                 "Total bytes received from _changes feed, bulk_get, and individual doc GETs.", self.bytes_received_total)
        _counter("changes_worker_bytes_output_total",
                 "Total bytes sent to the output endpoint.", self.bytes_output_total)

        # -- Doc fetching --
        _counter("changes_worker_docs_fetched_total",
                 "Total documents fetched via bulk_get or individual GET.", self.docs_fetched_total)

        # -- Output --
        _counter("changes_worker_output_requests_total",
                 "Total output requests sent to the downstream endpoint.", self.output_requests_total)
        _counter("changes_worker_output_errors_total",
                 "Total output request errors.", self.output_errors_total)

        # Output by HTTP method
        lines.append("# HELP changes_worker_output_requests_by_method_total Output requests broken down by HTTP method.")
        lines.append("# TYPE changes_worker_output_requests_by_method_total counter")
        lines.append(f'changes_worker_output_requests_by_method_total{{{labels},method="PUT"}} {self.output_put_total}')
        lines.append(f'changes_worker_output_requests_by_method_total{{{labels},method="DELETE"}} {self.output_delete_total}')

        lines.append("# HELP changes_worker_output_errors_by_method_total Output errors broken down by HTTP method.")
        lines.append("# TYPE changes_worker_output_errors_by_method_total counter")
        lines.append(f'changes_worker_output_errors_by_method_total{{{labels},method="PUT"}} {self.output_put_errors_total}')
        lines.append(f'changes_worker_output_errors_by_method_total{{{labels},method="DELETE"}} {self.output_delete_errors_total}')

        _counter("changes_worker_output_success_total",
                 "Total output requests that succeeded.", self.output_success_total)
        _counter("changes_worker_dead_letter_total",
                 "Total documents written to the dead letter queue.", self.dead_letter_total)

        _gauge("changes_worker_output_endpoint_up",
               "Whether the output endpoint is reachable (1=up, 0=down).", self.output_endpoint_up)

        # Output response time summary
        lines.append("# HELP changes_worker_output_response_time_seconds Output HTTP response time in seconds.")
        lines.append("# TYPE changes_worker_output_response_time_seconds summary")
        for q in (0.5, 0.9, 0.99):
            lines.append(f'changes_worker_output_response_time_seconds{{{labels},quantile="{q}"}} {_quantile(q):.6f}')
        lines.append(f"changes_worker_output_response_time_seconds_sum{{{labels}}} {ort_sum:.6f}")
        lines.append(f"changes_worker_output_response_time_seconds_count{{{labels}}} {ort_count}")

        # -- Checkpoint --
        _counter("changes_worker_checkpoint_saves_total",
                 "Total checkpoint save operations.", self.checkpoint_saves_total)
        _counter("changes_worker_checkpoint_save_errors_total",
                 "Total checkpoint save errors (fell back to local file).", self.checkpoint_save_errors_total)
        lines.append("# HELP changes_worker_checkpoint_seq Current checkpoint sequence value.")
        lines.append("# TYPE changes_worker_checkpoint_seq gauge")
        # Sequence can be a non-numeric string (e.g. "12:34"), expose as info label
        lines.append(f'changes_worker_checkpoint_seq{{{labels},seq="{self.checkpoint_seq}"}} 1')

        # -- Retries --
        _counter("changes_worker_retries_total",
                 "Total HTTP retry attempts across all requests.", self.retries_total)

        lines.append("")
        return "\n".join(lines)


async def _metrics_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """aiohttp handler for GET /_metrics"""
    metrics: MetricsCollector = request.app["metrics"]
    body = metrics.render()
    return aiohttp.web.Response(
        text=body,
        content_type="text/plain",
        charset="utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )


async def start_metrics_server(metrics: MetricsCollector, host: str, port: int) -> aiohttp.web.AppRunner:
    """Start a lightweight HTTP server that serves /_metrics in Prometheus format."""
    from aiohttp import web

    app = web.Application()
    app["metrics"] = metrics
    app.router.add_get("/_metrics", _metrics_handler)
    app.router.add_get("/metrics", _metrics_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log_event(logger, "info", "METRICS", "metrics server listening",
              host=host, port=port)
    return runner


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    if USE_CBL:
        store = CBLStore()
        cfg = store.load_config()
        if cfg:
            ic(cfg)
            return cfg
        # First run: import from file
        if path:
            with open(path) as f:
                cfg = json.load(f)
            store.save_config(cfg)
            ic(cfg)
            return cfg
    # Fallback: read from file directly
    with open(path or "config.json") as f:
        cfg = json.load(f)
    ic(cfg)
    return cfg


VALID_SOURCES = ("sync_gateway", "app_services", "edge_server")


def validate_config(cfg: dict) -> tuple[str, list[str], list[str]]:
    """
    Validate the entire config against the selected gateway.src.

    Returns (src, warnings, errors).
    Errors are fatal – the process should not start.
    Warnings are logged but execution continues.
    """
    warnings: list[str] = []
    errors: list[str] = []

    gw = cfg.get("gateway", {})
    auth_cfg = cfg.get("auth", {})
    feed_cfg = cfg.get("changes_feed", {})

    # -- gateway.src -----------------------------------------------------------
    src = gw.get("src", "sync_gateway")
    if src not in VALID_SOURCES:
        errors.append(
            f"gateway.src must be one of {VALID_SOURCES}, got '{src}'"
        )
        return src, warnings, errors  # can't validate further

    # -- gateway basics --------------------------------------------------------
    if not gw.get("url"):
        errors.append("gateway.url is required")
    if not gw.get("database"):
        errors.append("gateway.database is required")

    # App Services is always HTTPS
    url = gw.get("url", "")
    if src == "app_services" and url.startswith("http://"):
        warnings.append(
            "App Services endpoints are typically HTTPS – "
            "gateway.url starts with http://, verify this is correct"
        )

    # -- auth ------------------------------------------------------------------
    auth_method = auth_cfg.get("method", "basic")

    if auth_method == "bearer" and src == "edge_server":
        errors.append(
            "auth.method=bearer is not supported by Edge Server – "
            "use 'basic' or 'session' instead"
        )

    if auth_method == "basic":
        if not auth_cfg.get("username"):
            errors.append("auth.username is required when auth.method=basic")
        if not auth_cfg.get("password"):
            errors.append("auth.password is required when auth.method=basic")
    elif auth_method == "session":
        if not auth_cfg.get("session_cookie"):
            errors.append("auth.session_cookie is required when auth.method=session")
    elif auth_method == "bearer":
        if not auth_cfg.get("bearer_token"):
            errors.append("auth.bearer_token is required when auth.method=bearer")
    elif auth_method != "none":
        errors.append(
            f"auth.method must be 'basic', 'session', 'bearer', or 'none' – got '{auth_method}'"
        )

    # -- changes_feed ----------------------------------------------------------
    feed_type = feed_cfg.get("feed_type", "longpoll")

    if feed_type == "websocket" and src == "edge_server":
        errors.append(
            "changes_feed.feed_type=websocket is not supported by Edge Server – "
            "use 'longpoll', 'continuous', or 'sse'"
        )

    if feed_type == "sse" and src != "edge_server":
        errors.append(
            f"changes_feed.feed_type=sse is only supported by Edge Server – "
            f"not available on {src.replace('_', ' ').title()}"
        )

    valid_feeds_by_src = {
        "sync_gateway": ("longpoll", "continuous", "websocket", "normal"),
        "app_services": ("longpoll", "continuous", "websocket", "normal"),
        "edge_server": ("longpoll", "continuous", "sse", "normal"),
    }
    if feed_type not in valid_feeds_by_src.get(src, ()):
        errors.append(
            f"changes_feed.feed_type='{feed_type}' is not valid for {src} – "
            f"allowed: {valid_feeds_by_src[src]}"
        )

    version_type = feed_cfg.get("version_type", "rev")
    if version_type != "rev" and src == "edge_server":
        errors.append(
            f"changes_feed.version_type='{version_type}' is not supported by Edge Server – "
            "Edge Server does not support the version_type parameter"
        )
    if version_type not in ("rev", "cv"):
        errors.append(
            f"changes_feed.version_type must be 'rev' or 'cv', got '{version_type}'"
        )

    timeout_ms = feed_cfg.get("timeout_ms", 60000)
    if src == "edge_server" and timeout_ms > 900000:
        warnings.append(
            f"changes_feed.timeout_ms={timeout_ms} exceeds Edge Server's "
            f"max of 900000ms (15 min) – it will be clamped"
        )

    include_docs = feed_cfg.get("include_docs", True)
    if not include_docs and src == "edge_server":
        warnings.append(
            "changes_feed.include_docs=false with Edge Server – "
            "Edge Server has no _bulk_get endpoint, docs will be fetched "
            "individually via GET /{keyspace}/{docid} (slower for large batches)"
        )

    heartbeat_ms = feed_cfg.get("heartbeat_ms", 0)
    if src == "edge_server" and heartbeat_ms > 0 and heartbeat_ms < 25000:
        warnings.append(
            f"changes_feed.heartbeat_ms={heartbeat_ms} is below Edge Server's "
            f"minimum of 25000ms – server may reject it"
        )

    poll_interval = feed_cfg.get("poll_interval_seconds", 10)
    if poll_interval < 1:
        warnings.append(
            f"changes_feed.poll_interval_seconds={poll_interval} is very aggressive – "
            "consider at least 1 second to avoid hammering the server"
        )

    http_timeout = feed_cfg.get("http_timeout_seconds", 300)
    if http_timeout < 10:
        warnings.append(
            f"changes_feed.http_timeout_seconds={http_timeout} is very low – "
            "large feeds (since=0) may time out before completing"
        )

    # -- output ----------------------------------------------------------------
    out_cfg = cfg.get("output", {})
    out_mode = out_cfg.get("mode", "stdout")
    if out_mode not in ("stdout", "http"):
        errors.append(f"output.mode must be 'stdout' or 'http', got '{out_mode}'")
    if out_mode == "http" and not out_cfg.get("target_url"):
        errors.append("output.target_url is required when output.mode=http")

    # -- output_format ---------------------------------------------------------
    out_fmt = out_cfg.get("output_format", "json")
    if out_fmt not in VALID_OUTPUT_FORMATS:
        errors.append(
            f"output.output_format must be one of {VALID_OUTPUT_FORMATS}, got '{out_fmt}'"
        )
    # Check if the required library is installed for binary/yaml formats
    missing = check_serialization_library(out_fmt)
    if missing:
        fmt_name, pip_name = missing
        errors.append(
            f"output.output_format='{fmt_name}' requires the '{pip_name}' library – "
            f"pip install {pip_name}"
        )

    if out_mode == "http":
        out_auth_method = out_cfg.get("target_auth", {}).get("method", "none")
        if out_auth_method == "basic":
            if not out_cfg.get("target_auth", {}).get("username"):
                errors.append("output.target_auth.username is required when target_auth.method=basic")
            if not out_cfg.get("target_auth", {}).get("password"):
                errors.append("output.target_auth.password is required when target_auth.method=basic")
        elif out_auth_method == "session":
            if not out_cfg.get("target_auth", {}).get("session_cookie"):
                errors.append("output.target_auth.session_cookie is required when target_auth.method=session")
        elif out_auth_method == "bearer":
            if not out_cfg.get("target_auth", {}).get("bearer_token"):
                errors.append("output.target_auth.bearer_token is required when target_auth.method=bearer")

        out_retry = out_cfg.get("retry", {})
        out_max_retries = out_retry.get("max_retries", 3)
        if out_max_retries < 0:
            errors.append(f"output.retry.max_retries must be >= 0, got {out_max_retries}")

        if not out_cfg.get("halt_on_failure", True):
            warnings.append(
                "output.halt_on_failure=false – if the output endpoint fails, "
                "docs will be skipped and the checkpoint will still advance"
            )

    # -- retry -----------------------------------------------------------------
    retry_cfg = cfg.get("retry", {})
    max_retries = retry_cfg.get("max_retries", 5)
    if max_retries < 0:
        errors.append(f"retry.max_retries must be >= 0, got {max_retries}")

    # -- metrics ---------------------------------------------------------------
    metrics_cfg = cfg.get("metrics", {})
    if metrics_cfg.get("enabled", False):
        metrics_port = metrics_cfg.get("port", 9090)
        if not isinstance(metrics_port, int) or metrics_port < 1 or metrics_port > 65535:
            errors.append(f"metrics.port must be an integer between 1 and 65535, got {metrics_port}")

    return src, warnings, errors


def build_base_url(gw: dict) -> str:
    """Build the keyspace URL: {url}/{db}.{scope}.{collection}"""
    base = gw["url"].rstrip("/")
    db = gw["database"]
    scope = gw.get("scope", "")
    collection = gw.get("collection", "")
    if scope and collection:
        keyspace = f"{db}.{scope}.{collection}"
    else:
        keyspace = db
    return f"{base}/{keyspace}"


def build_ssl_context(gw: dict) -> ssl.SSLContext | None:
    url = gw["url"]
    if not url.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    if gw.get("accept_self_signed_certs"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def build_auth_headers(auth_cfg: dict, src: str = "sync_gateway") -> dict:
    method = auth_cfg.get("method", "basic")
    headers: dict[str, str] = {}
    if method == "bearer":
        if src == "edge_server":
            logger.warning("Bearer token auth is not supported by Edge Server – falling back to basic")
        else:
            headers["Authorization"] = f"Bearer {auth_cfg['bearer_token']}"
    elif method == "session":
        headers["Cookie"] = f"SyncGatewaySession={auth_cfg['session_cookie']}"
    return headers


def build_basic_auth(auth_cfg: dict) -> aiohttp.BasicAuth | None:
    if auth_cfg.get("method", "basic") == "basic" and auth_cfg.get("username"):
        return aiohttp.BasicAuth(auth_cfg["username"], auth_cfg.get("password", ""))
    return None


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

class Checkpoint:
    """
    CBL-style checkpoint stored on Sync Gateway as a _local document.

    Key derivation (mirrors CBL):
        UUID = SHA1(local_client_id + SG_URL + channels)
        doc id = _sync:local:{UUID}
        SG REST path = {keyspace}/_local/checkpoint-{UUID}

    The checkpoint document contains (CBL-compatible):
        {
            "client_id": "<local_client_id>",
            "SGs_Seq": "<last_seq>",
            "time": <epoch timestamp>,
            "remote": <monotonic counter>
        }
    """

    def __init__(self, cfg: dict, gw_cfg: dict, channels: list[str]):
        self._enabled = cfg.get("enabled", True)
        self._lock = asyncio.Lock()
        self._seq: str = "0"
        self._rev: str | None = None  # SG doc _rev for updates
        self._internal: int = 0

        # Build the deterministic UUID the same way CBL does:
        #   HASH(local_client_id + SG URL + channel_names)
        client_id = cfg.get("client_id", "changes_worker")
        sg_url = build_base_url(gw_cfg)
        channel_str = ",".join(sorted(channels)) if channels else ""
        raw = f"{client_id}{sg_url}{channel_str}"
        self._uuid = hashlib.sha1(raw.encode()).hexdigest()
        self._client_id = client_id
        self._local_doc_id = f"checkpoint-{self._uuid}"

        # Fallback to local file when SG is unreachable for checkpoint ops
        self._fallback_path = Path(cfg.get("file", "checkpoint.json"))

        ic(self._uuid, self._local_doc_id, raw)

    @property
    def local_doc_path(self) -> str:
        """Returns the REST path segment: _local/checkpoint-{uuid}"""
        return f"_local/{self._local_doc_id}"

    @property
    def seq(self) -> str:
        return self._seq

    # -- SG-backed load/save ---------------------------------------------------

    async def load(self, http: "RetryableHTTP", base_url: str,
                   auth: aiohttp.BasicAuth | None, headers: dict) -> str:
        """GET {keyspace}/_local/checkpoint-{uuid} from Sync Gateway."""
        if not self._enabled:
            return self._seq

        url = f"{base_url}/{self.local_doc_path}"
        ic("checkpoint load", url)
        try:
            resp = await http.request("GET", url, auth=auth, headers=headers)
            data = await resp.json()
            resp.release()
            self._seq = str(data.get("SGs_Seq", "0"))
            self._rev = data.get("_rev")
            self._internal = data.get("remote", data.get("local_internal", 0))
            log_event(logger, "info", "CHECKPOINT",
                      "loaded checkpoint from Sync Gateway",
                      operation="SELECT", seq=self._seq,
                      doc_id=self._local_doc_id, storage="sg")
        except ClientHTTPError as exc:
            if exc.status == 404:
                log_event(logger, "info", "CHECKPOINT",
                          "no existing checkpoint on SG – starting from 0",
                          operation="SELECT", storage="sg")
                self._seq = "0"
            else:
                log_event(logger, "warn", "CHECKPOINT",
                          "checkpoint load fell back to local storage",
                          operation="SELECT", status=exc.status, storage="fallback")
                self._seq = self._load_fallback()
        except Exception as exc:
            log_event(logger, "warn", "CHECKPOINT",
                      "checkpoint load fell back to local storage: %s" % exc,
                      operation="SELECT", storage="fallback")
            self._seq = self._load_fallback()

        return self._seq

    async def save(self, seq: str, http: "RetryableHTTP", base_url: str,
                   auth: aiohttp.BasicAuth | None, headers: dict) -> None:
        """PUT {keyspace}/_local/checkpoint-{uuid} on Sync Gateway."""
        if not self._enabled:
            return

        async with self._lock:
            self._internal += 1
            self._seq = seq
            body: dict = {
                "client_id": self._client_id,
                "SGs_Seq": seq,
                "time": int(time.time()),
                "remote": self._internal,
            }
            if self._rev:
                body["_rev"] = self._rev

            url = f"{base_url}/{self.local_doc_path}"
            ic("checkpoint save", url, seq, self._internal)
            try:
                req_headers = {**headers, "Content-Type": "application/json"}
                resp = await http.request("PUT", url, json=body, auth=auth, headers=req_headers)
                resp_data = await resp.json()
                resp.release()
                self._rev = resp_data.get("rev", self._rev)
                log_event(logger, "info", "CHECKPOINT",
                          "saved checkpoint to Sync Gateway",
                          operation="UPDATE", seq=seq,
                          doc_id=self._local_doc_id, storage="sg")
            except Exception as exc:
                log_event(logger, "warn", "CHECKPOINT",
                          "checkpoint save fell back to local storage: %s" % exc,
                          operation="UPDATE", seq=seq, storage="fallback")
                self._save_fallback(seq)

    # -- Local file fallback ---------------------------------------------------

    def _load_fallback(self) -> str:
        if USE_CBL:
            data = CBLStore().load_checkpoint(self._uuid)
            if data:
                seq = data.get("SGs_Seq", "0")
                ic("checkpoint loaded from CBL", seq)
                return seq
            return "0"
        # Original file fallback
        if self._fallback_path.exists():
            data = json.loads(self._fallback_path.read_text())
            seq = str(data.get("SGs_Seq", data.get("last_seq", "0")))
            ic("checkpoint loaded from file", seq)
            return seq
        return "0"

    def _save_fallback(self, seq: str) -> None:
        if USE_CBL:
            CBLStore().save_checkpoint(self._uuid, seq, self._client_id, self._internal)
            ic("checkpoint saved to CBL", seq)
            return
        # Original file fallback
        self._fallback_path.write_text(json.dumps({
            "SGs_Seq": seq,
            "time": int(time.time()),
            "remote": self._internal,
        }))
        ic("checkpoint saved to file", seq)


# ---------------------------------------------------------------------------
# HTTP helpers with retry
# ---------------------------------------------------------------------------

class RetryableHTTP:
    def __init__(self, session: aiohttp.ClientSession, retry_cfg: dict):
        self._session = session
        self._max_retries = retry_cfg.get("max_retries", 5)
        self._backoff_base = retry_cfg.get("backoff_base_seconds", 1)
        self._backoff_max = retry_cfg.get("backoff_max_seconds", 60)
        self._retry_statuses = set(retry_cfg.get("retry_on_status", [500, 502, 503, 504]))

    async def request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._session.request(method, url, **kwargs)
                if resp.status < 300:
                    return resp
                body = await resp.text()
                if resp.status in self._retry_statuses:
                    log_event(logger, "warn", "RETRY",
                              "retryable response",
                              http_method=method, url=url,
                              status=resp.status, attempt=attempt)
                    resp.release()
                elif 400 <= resp.status < 500:
                    log_event(logger, "error", "HTTP",
                              "client error",
                              http_method=method, url=url,
                              status=resp.status)
                    raise ClientHTTPError(resp.status, body)
                elif 300 <= resp.status < 400:
                    log_event(logger, "warn", "HTTP",
                              "redirect – not following",
                              http_method=method, url=url,
                              status=resp.status)
                    raise RedirectHTTPError(resp.status, body)
                else:
                    raise ServerHTTPError(resp.status, body)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log_event(logger, "warn", "RETRY",
                          "connection error: %s" % exc,
                          http_method=method, url=url, attempt=attempt)
                last_exc = exc

            delay = min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_max)
            log_event(logger, "info", "RETRY",
                      "backing off before retry",
                      delay_seconds=delay, attempt=attempt)
            await asyncio.sleep(delay)

        raise ConnectionError(f"All {self._max_retries} retries exhausted for {method} {url}") from last_exc


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
    return [lst[i:i + size] for i in range(0, len(lst), size)]


async def fetch_docs(http: RetryableHTTP, base_url: str, rows: list[dict],
                     auth: aiohttp.BasicAuth | None, headers: dict,
                     src: str, max_concurrent: int = 20,
                     batch_size: int = 100,
                     metrics: MetricsCollector | None = None) -> list[dict]:
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
    ic(f"fetch_docs: {len(eligible)} docs in {len(batches)} batch(es) of {batch_size}")

    all_results: list[dict] = []
    for i, batch in enumerate(batches):
        ic(f"fetch_docs batch {i + 1}/{len(batches)}: {len(batch)} docs")
        if src == "edge_server":
            results = await _fetch_docs_individually(http, base_url, batch, auth, headers, max_concurrent, metrics=metrics)
        else:
            results = await _fetch_docs_bulk_get(http, base_url, batch, auth, headers, metrics=metrics)
        all_results.extend(results)

    return all_results


async def _fetch_docs_bulk_get(http: RetryableHTTP, base_url: str, rows: list[dict],
                               auth: aiohttp.BasicAuth | None, headers: dict,
                               metrics: MetricsCollector | None = None) -> list[dict]:
    """Fetch full docs via _bulk_get (Sync Gateway / App Services)."""
    docs_req = [{"id": r["id"], "rev": r["changes"][0]["rev"]} for r in rows]
    if not docs_req:
        return []
    url = f"{base_url}/_bulk_get?revs=false"
    payload = {"docs": docs_req}
    ic(url, len(docs_req))
    resp = await http.request("POST", url, json=payload, auth=auth, headers={**headers, "Content-Type": "application/json"})
    # _bulk_get returns multipart/mixed or JSON depending on SG version
    ct = resp.content_type or ""
    results: list[dict] = []
    if "application/json" in ct:
        raw_bytes = await resp.read()
        if metrics:
            metrics.inc("bytes_received_total", len(raw_bytes))
        body = json.loads(raw_bytes)
        for item in body.get("results", []):
            for doc_entry in item.get("docs", []):
                ok = doc_entry.get("ok")
                if ok:
                    results.append(ok)
    else:
        # Fallback: read raw text and attempt JSON extraction
        raw = await resp.text()
        if metrics:
            metrics.inc("bytes_received_total", len(raw.encode("utf-8")))
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


async def _fetch_docs_individually(http: RetryableHTTP, base_url: str, rows: list[dict],
                                   auth: aiohttp.BasicAuth | None, headers: dict,
                                   max_concurrent: int,
                                   metrics: MetricsCollector | None = None) -> list[dict]:
    """
    Fetch docs one-by-one via GET /{keyspace}/{docid}?rev={rev}.

    Used for Edge Server which does not have a _bulk_get endpoint.
    Requests are fanned out with a semaphore to cap concurrency.
    """
    sem = asyncio.Semaphore(max_concurrent)
    results: list[dict] = []
    lock = asyncio.Lock()

    async def _get_one(row: dict) -> None:
        doc_id = row.get("id", "")
        rev = row["changes"][0]["rev"] if row.get("changes") else None
        url = f"{base_url}/{doc_id}"
        params: dict[str, str] = {}
        if rev:
            params["rev"] = rev
        ic("GET doc (edge_server)", url, rev)
        async with sem:
            try:
                resp = await http.request("GET", url, params=params, auth=auth, headers=headers)
                raw_bytes = await resp.read()
                if metrics:
                    metrics.inc("bytes_received_total", len(raw_bytes))
                doc = json.loads(raw_bytes)
                resp.release()
                async with lock:
                    results.append(doc)
            except Exception as exc:
                logger.warning("Failed to fetch doc %s: %s", doc_id, exc)

    tasks = [asyncio.create_task(_get_one(r)) for r in rows]
    ic(f"Fetching {len(tasks)} docs individually (Edge Server, concurrency={max_concurrent})")
    await asyncio.gather(*tasks)
    return results



# ---------------------------------------------------------------------------
# Helpers: shared batch processing & continuous feed
# ---------------------------------------------------------------------------

def _build_changes_params(feed_cfg: dict, src: str, since: str,
                          feed_type: str, timeout_ms: int,
                          limit: int = 0) -> dict[str, str]:
    """Build query params for a _changes request."""
    params: dict[str, str] = {
        "feed": feed_type,
        "since": since,
        "heartbeat": str(feed_cfg.get("heartbeat_ms", 30000)),
        "timeout": str(timeout_ms),
    }
    if feed_cfg.get("active_only"):
        params["active_only"] = "true"
    if feed_cfg.get("include_docs"):
        params["include_docs"] = "true"
    if limit > 0:
        params["limit"] = str(limit)
    channels = feed_cfg.get("channels", [])
    if channels:
        params["filter"] = "sync_gateway/bychannel"
        params["channels"] = ",".join(channels)
    if src in ("sync_gateway", "app_services"):
        params["version_type"] = feed_cfg.get("version_type", "rev")
    return params


async def _sleep_with_backoff(retry_cfg: dict, failure_count: int,
                              shutdown_event: asyncio.Event) -> None:
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
    output: "OutputForwarder",
    dlq: "DeadLetterQueue",
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
) -> tuple[str, bool]:
    """
    Process a batch of _changes results: filter, fetch docs, forward to output,
    checkpoint.  Returns (new_since, output_failed).
    """
    sequential = proc_cfg.get("sequential", False)

    if metrics:
        metrics.inc("poll_cycles_total")
        metrics.set("last_poll_timestamp", time.time())
        metrics.set("last_batch_size", len(results))
        metrics.inc("changes_received_total", len(results))

    if not results:
        new_since = str(last_seq)
        await checkpoint.save(new_since, http, base_url, basic_auth, auth_headers)
        if metrics:
            metrics.inc("checkpoint_saves_total")
            metrics.set("checkpoint_seq", new_since)
        return new_since, False

    log_event(logger, "debug", "CHANGES", "received _changes batch",
              seq=since, batch_size=len(results))

    # Count deletes/removes in the feed (always), then optionally filter
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
        if proc_cfg.get("ignore_delete") and change.get("deleted"):
            ic("ignoring deleted", change.get("id"))
            deleted_count += 1
            continue
        if proc_cfg.get("ignore_remove") and change.get("removed"):
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
        log_event(logger, "debug", "PROCESSING", "filtered changes batch",
                  input_count=len(results), filtered_count=len(filtered))

    # If include_docs was false, fetch full docs
    docs_by_id: dict[str, dict] = {}
    if not feed_cfg.get("include_docs") and filtered:
        batch_size = proc_cfg.get("get_batch_number", 100)
        fetched = await fetch_docs(http, base_url, filtered, basic_auth, auth_headers,
                                   src, max_concurrent, batch_size, metrics=metrics)
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
            doc_id = change.get("id", "")
            if feed_cfg.get("include_docs"):
                doc = change.get("doc", change)
            else:
                doc = docs_by_id.get(doc_id, change)
            method = determine_method(change)
            op = infer_operation(change=change, doc=doc, method=method)
            log_event(logger, "trace", "OUTPUT", "sending document",
                      operation=op, doc_id=doc_id, mode=output._mode,
                      http_method=method)
            result = await output.send(doc, method)
            result["_change"] = change
            result["_doc"] = doc
            if result.get("ok"):
                log_event(logger, "debug", "OUTPUT", "document forwarded",
                          operation=op, doc_id=doc_id,
                          status=result.get("status"))
            else:
                log_event(logger, "warn", "OUTPUT", "document delivery failed",
                          operation=op, doc_id=doc_id,
                          status=result.get("status"))
            return result

    if every_n_docs > 0 and sequential:
        for i in range(0, len(filtered), every_n_docs):
            sub_batch = filtered[i:i + every_n_docs]
            for change in sub_batch:
                try:
                    result = await process_one(change)
                    if result.get("ok"):
                        batch_success += 1
                    else:
                        batch_fail += 1
                        if dlq.enabled and metrics:
                            metrics.inc("dead_letter_total")
                        await dlq.write(result["_doc"], result, change.get("seq", ""))
                except OutputEndpointDown as exc:
                    output_failed = True
                    logger.error("OUTPUT DOWN – not advancing checkpoint past since=%s: %s", since, exc)
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
                        if dlq.enabled and metrics:
                            metrics.inc("dead_letter_total")
                        await dlq.write(result["_doc"], result, change.get("seq", ""))
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
                        if dlq.enabled and metrics:
                            metrics.inc("dead_letter_total")
                        await dlq.write(result["_doc"], result, result["_change"].get("seq", ""))
        except OutputEndpointDown as exc:
            output_failed = True
            logger.error(
                "OUTPUT DOWN – not advancing checkpoint past since=%s: %s",
                since, exc,
            )

    if metrics:
        metrics.inc("changes_processed_total", len(filtered))

    total = batch_success + batch_fail
    if total > 0:
        log_event(logger, "info", "PROCESSING",
                  "batch complete: %d/%d succeeded, %d failed%s" % (
                      batch_success, total, batch_fail,
                      " (%d written to dead letter queue)" % batch_fail if batch_fail and dlq.enabled else "",
                  ))

    output.log_stats()

    if output_failed:
        return since, True

    if not (every_n_docs > 0 and sequential):
        since = str(last_seq)
        await checkpoint.save(since, http, base_url, basic_auth, auth_headers)
        if metrics:
            metrics.inc("checkpoint_saves_total")
            metrics.set("checkpoint_seq", since)

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
    output: "OutputForwarder",
    dlq: "DeadLetterQueue",
    checkpoint: Checkpoint,
    semaphore: asyncio.Semaphore,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    timeout_ms: int,
    changes_http_timeout: aiohttp.ClientTimeout,
) -> str:
    """
    Phase 1 of continuous mode: catch up using one-shot normal requests
    with a limit.  Repeats until the server returns 0 results, meaning
    we are caught up.  Returns the latest since value.
    """
    catchup_limit = feed_cfg.get("continuous_catchup_limit", 500)
    failure_count = 0

    logger.info("CONTINUOUS catch-up: starting from since=%s (limit=%d)", since, catchup_limit)

    while not shutdown_event.is_set():
        params = _build_changes_params(feed_cfg, src, since, "normal", timeout_ms,
                                       limit=catchup_limit)
        ic(changes_url, params, since, "catch-up")

        try:
            resp = await http.request("GET", changes_url, params=params,
                                      auth=basic_auth, headers=auth_headers,
                                      timeout=changes_http_timeout)
            raw_body = await resp.read()
            body = json.loads(raw_body)
            if metrics:
                metrics.inc("bytes_received_total", len(raw_body))
            resp.release()
            failure_count = 0
        except (ClientHTTPError, RedirectHTTPError) as exc:
            logger.error("Non-retryable error during catch-up: %s", exc)
            if metrics:
                metrics.inc("poll_errors_total")
            raise
        except (ConnectionError, ServerHTTPError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            failure_count += 1
            logger.error("Catch-up request failed (attempt #%d): %s", failure_count, exc)
            if metrics:
                metrics.inc("poll_errors_total")
            await _sleep_with_backoff(retry_cfg, failure_count, shutdown_event)
            continue

        results = body.get("results", [])
        last_seq = body.get("last_seq", since)
        ic(len(results), last_seq, "catch-up batch")

        since, output_failed = await _process_changes_batch(
            results, str(last_seq), since,
            feed_cfg=feed_cfg, proc_cfg=proc_cfg, output=output, dlq=dlq,
            checkpoint=checkpoint, http=http, base_url=base_url,
            basic_auth=basic_auth, auth_headers=auth_headers,
            semaphore=semaphore, src=src, metrics=metrics,
            every_n_docs=every_n_docs, max_concurrent=max_concurrent,
        )

        if output_failed:
            await _sleep_or_shutdown(feed_cfg.get("poll_interval_seconds", 10), shutdown_event)
            continue

        if not results:
            logger.info("CONTINUOUS catch-up complete at since=%s", since)
            return since

        logger.info("CONTINUOUS catch-up: got %d rows, fetching next batch from since=%s",
                     len(results), since)

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
    output: "OutputForwarder",
    dlq: "DeadLetterQueue",
    checkpoint: Checkpoint,
    semaphore: asyncio.Semaphore,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None,
    every_n_docs: int,
    max_concurrent: int,
    timeout_ms: int,
) -> str:
    """
    Phase 2 of continuous mode: open a streaming connection with
    feed=continuous and read changes line-by-line.  Returns the latest
    since value when the stream ends (disconnect / error).
    """
    params = _build_changes_params(feed_cfg, src, since, "continuous", timeout_ms)
    # No limit for continuous mode – we want all changes as they arrive
    params.pop("limit", None)
    # No server-side timeout – the stream stays open indefinitely
    params.pop("timeout", None)

    # Use an open-ended HTTP timeout for the streaming connection
    continuous_timeout = aiohttp.ClientTimeout(total=None, sock_read=None)

    logger.info("CONTINUOUS stream: connecting from since=%s", since)
    ic(changes_url, params, since, "continuous stream")

    failure_count = 0

    while not shutdown_event.is_set():
        try:
            resp = await http.request("GET", changes_url, params=params,
                                      auth=basic_auth, headers=auth_headers,
                                      timeout=continuous_timeout)
        except (ConnectionError, ServerHTTPError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            failure_count += 1
            logger.error("Continuous stream connect failed (attempt #%d): %s", failure_count, exc)
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
                except json.JSONDecodeError:
                    logger.warning("Continuous stream: unparseable line: %s", line[:200])
                    continue

                row_seq = str(row.get("seq", since))
                ic(row.get("id"), row_seq, "continuous row")

                since, output_failed = await _process_changes_batch(
                    [row], row_seq, since,
                    feed_cfg=feed_cfg, proc_cfg=proc_cfg, output=output, dlq=dlq,
                    checkpoint=checkpoint, http=http, base_url=base_url,
                    basic_auth=basic_auth, auth_headers=auth_headers,
                    semaphore=semaphore, src=src, metrics=metrics,
                    every_n_docs=every_n_docs, max_concurrent=max_concurrent,
                )

                if output_failed:
                    logger.warning("Output failed during continuous stream – dropping to catch-up")
                    break

            # Update params with latest since for reconnect
            params["since"] = since
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


# ---------------------------------------------------------------------------
# Core: changes feed loop
# ---------------------------------------------------------------------------

async def poll_changes(cfg: dict, src: str, shutdown_event: asyncio.Event,
                       metrics: MetricsCollector | None = None) -> None:
    gw = cfg["gateway"]
    auth_cfg = cfg["auth"]
    feed_cfg = cfg["changes_feed"]
    proc_cfg = cfg["processing"]
    out_cfg = cfg["output"]
    retry_cfg = cfg.get("retry", {})

    log_event(logger, "info", "PROCESSING", "source type: %s" % src)

    base_url = build_base_url(gw)
    ssl_ctx = build_ssl_context(gw)
    basic_auth = build_basic_auth(auth_cfg)
    auth_headers = build_auth_headers(auth_cfg, src)

    channels = feed_cfg.get("channels", [])
    checkpoint = Checkpoint(cfg.get("checkpoint", {}), gw, channels)

    # Session-level timeout is kept loose; the _changes request uses its own.
    timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
    connector = aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else aiohttp.TCPConnector()

    # Per-request timeout for _changes calls.
    # since=0 can return 100K+ rows and take minutes, so this must be much
    # higher than a typical 30-75s HTTP timeout.  Default 300s (5 min).
    changes_http_timeout = aiohttp.ClientTimeout(
        total=feed_cfg.get("http_timeout_seconds", 300),
    )

    sequential = proc_cfg.get("sequential", False)
    max_concurrent = proc_cfg.get("max_concurrent", 20)
    dry_run = proc_cfg.get("dry_run", False)
    semaphore = asyncio.Semaphore(max_concurrent)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        http = RetryableHTTP(session, retry_cfg)
        output = OutputForwarder(session, out_cfg, dry_run, metrics=metrics,
                                        build_basic_auth_fn=build_basic_auth,
                                        build_auth_headers_fn=build_auth_headers,
                                        retryable_http_cls=RetryableHTTP)
        dlq = DeadLetterQueue(out_cfg.get("dead_letter_path", ""))
        every_n_docs = cfg.get("checkpoint", {}).get("every_n_docs", 0)

        # If output is HTTP, verify the endpoint is reachable before starting
        if out_cfg.get("mode") == "http":
            if not await output.test_reachable():
                if out_cfg.get("halt_on_failure", True):
                    log_event(logger, "error", "OUTPUT",
                              "output endpoint unreachable at startup – aborting")
                    return
                else:
                    log_event(logger, "warn", "OUTPUT",
                              "output endpoint unreachable at startup – continuing (halt_on_failure=false)")

        # Load checkpoint from SG _local doc (CBL-style)
        since = feed_cfg.get("since", "0")
        if since == "0" and cfg.get("checkpoint", {}).get("enabled", True):
            since = await checkpoint.load(http, base_url, basic_auth, auth_headers)

        throttle = feed_cfg.get("throttle_feed", 0)

        # Source-specific feed type validation
        feed_type = feed_cfg.get("feed_type", "longpoll")
        if src == "edge_server" and feed_type == "websocket":
            logger.warning("Edge Server does not support feed=websocket, falling back to longpoll")
            feed_type = "longpoll"
        if src != "edge_server" and feed_type == "sse":
            logger.warning("SSE feed is only supported by Edge Server, falling back to longpoll")
            feed_type = "longpoll"

        # Edge Server caps timeout at 900000ms (15 min)
        timeout_ms = feed_cfg.get("timeout_ms", 60000)
        if src == "edge_server" and timeout_ms > 900000:
            logger.warning("Edge Server max timeout is 900000ms – clamping from %d", timeout_ms)
            timeout_ms = 900000

        changes_url = f"{base_url}/_changes"

        # Shared kwargs for _process_changes_batch / catch-up / continuous
        batch_kwargs = dict(
            feed_cfg=feed_cfg, proc_cfg=proc_cfg, output=output, dlq=dlq,
            checkpoint=checkpoint, http=http, base_url=base_url,
            basic_auth=basic_auth, auth_headers=auth_headers,
            semaphore=semaphore, src=src, metrics=metrics,
            every_n_docs=every_n_docs, max_concurrent=max_concurrent,
        )

        # ── Continuous mode: 2-phase catch-up then stream ────────────────
        if feed_type == "continuous":
            log_event(logger, "info", "CHANGES", "feed mode: continuous (catch-up → stream)")
            while not shutdown_event.is_set():
                since = await _catch_up_normal(
                    since=since, changes_url=changes_url,
                    retry_cfg=retry_cfg, shutdown_event=shutdown_event,
                    timeout_ms=timeout_ms,
                    changes_http_timeout=changes_http_timeout,
                    **batch_kwargs,
                )
                if shutdown_event.is_set():
                    break
                since = await _consume_continuous_stream(
                    since=since, changes_url=changes_url,
                    retry_cfg=retry_cfg, session=session,
                    shutdown_event=shutdown_event,
                    timeout_ms=timeout_ms,
                    **batch_kwargs,
                )
            return

        # ── Polled mode (longpoll / normal / sse / websocket) ────────────
        while not shutdown_event.is_set():
            params = _build_changes_params(feed_cfg, src, since, feed_type, timeout_ms)
            # throttle_feed overrides limit – eat the feed one bite at a time
            if throttle > 0:
                params["limit"] = str(throttle)
            elif feed_cfg.get("limit", 0) > 0:
                params["limit"] = str(feed_cfg["limit"])

            ic(changes_url, params, since)

            try:
                resp = await http.request("GET", changes_url, params=params,
                                          auth=basic_auth, headers=auth_headers,
                                          timeout=changes_http_timeout)
                raw_body = await resp.read()
                body = json.loads(raw_body)
                if metrics:
                    metrics.inc("bytes_received_total", len(raw_body))
                resp.release()
            except (ClientHTTPError, RedirectHTTPError) as exc:
                logger.error("Non-retryable error polling _changes: %s", exc)
                if metrics:
                    metrics.inc("poll_errors_total")
                break
            except (ConnectionError, ServerHTTPError) as exc:
                logger.error("Retries exhausted polling _changes: %s", exc)
                if metrics:
                    metrics.inc("poll_errors_total")
                await _sleep_or_shutdown(feed_cfg.get("poll_interval_seconds", 10), shutdown_event)
                continue

            results = body.get("results", [])
            last_seq = body.get("last_seq", since)
            ic(len(results), last_seq)

            since, output_failed = await _process_changes_batch(
                results, str(last_seq), since,
                **batch_kwargs,
            )

            if output_failed:
                logger.warning(
                    "Waiting %ds before retrying (checkpoint held at since=%s)",
                    feed_cfg.get("poll_interval_seconds", 10), since,
                )
                await _sleep_or_shutdown(feed_cfg.get("poll_interval_seconds", 10), shutdown_event)
                continue

            if not results:
                await _sleep_or_shutdown(feed_cfg.get("poll_interval_seconds", 10), shutdown_event)
                continue

            # When throttling: if we got a full batch there are more rows
            # waiting — loop immediately for the next bite. Only sleep once
            # we get a partial batch (caught up).
            if throttle > 0 and len(results) >= throttle:
                logger.info("Throttle: got full batch (%d), fetching next bite immediately", len(results))
                continue

            await _sleep_or_shutdown(feed_cfg.get("poll_interval_seconds", 10), shutdown_event)


async def _sleep_or_shutdown(seconds: float, event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------

async def test_connection(cfg: dict, src: str) -> bool:
    """
    Verify the SG / App Service / Edge Server endpoint is reachable.

    Checks performed:
      1. GET {base_url}/  – server root (returns db info / welcome)
      2. GET {base_url}/_changes?since=0&limit=1 – confirm _changes endpoint responds
      3. Checkpoint read
    """
    gw = cfg["gateway"]
    auth_cfg = cfg["auth"]
    retry_cfg = cfg.get("retry", {})
    base_url = build_base_url(gw)
    root_url = gw["url"].rstrip("/")
    ssl_ctx = build_ssl_context(gw)
    basic_auth = build_basic_auth(auth_cfg)
    auth_headers = build_auth_headers(auth_cfg, src)

    connector = aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else aiohttp.TCPConnector()
    timeout = aiohttp.ClientTimeout(total=15)
    ok = True

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        http = RetryableHTTP(session, {**retry_cfg, "max_retries": 1})

        # 1) Server root
        src_label = src.replace("_", " ").title()
        print(f"\n{'='*60}")
        print(f"  Source type:           {src_label}")
        print(f"  Testing connection to: {root_url}")
        print(f"  Keyspace:              {base_url}")
        print(f"  Auth method:           {auth_cfg.get('method', 'basic')}")
        print(f"{'='*60}\n")

        try:
            resp = await http.request("GET", f"{root_url}/", auth=basic_auth, headers=auth_headers)
            body = await resp.json()
            resp.release()
            print(f"  [✓] Server root reachable")
            ic(body)
            for key in ("version", "vendor", "couchdb", "ADMIN"):
                if key in body:
                    print(f"      {key}: {body[key]}")
        except Exception as exc:
            print(f"  [✗] Server root UNREACHABLE: {exc}")
            ok = False

        # 2) Database / keyspace info
        try:
            resp = await http.request("GET", f"{base_url}/", auth=basic_auth, headers=auth_headers)
            body = await resp.json()
            resp.release()
            db_name = body.get("db_name", body.get("name", "?"))
            state = body.get("state", "?")
            print(f"  [✓] Keyspace reachable  (db_name={db_name}, state={state})")
        except Exception as exc:
            print(f"  [✗] Keyspace UNREACHABLE: {exc}")
            ok = False

        # 3) _changes endpoint
        try:
            resp = await http.request(
                "GET", f"{base_url}/_changes",
                params={"since": "0", "limit": "1"},
                auth=basic_auth, headers=auth_headers,
            )
            body = await resp.json()
            resp.release()
            last_seq = body.get("last_seq", "?")
            n_results = len(body.get("results", []))
            print(f"  [✓] _changes endpoint OK  (last_seq={last_seq}, sample_results={n_results})")
        except Exception as exc:
            print(f"  [✗] _changes endpoint FAILED: {exc}")
            ok = False

        # 4) Checkpoint (read-only)
        channels = cfg.get("changes_feed", {}).get("channels", [])
        checkpoint = Checkpoint(cfg.get("checkpoint", {}), gw, channels)
        try:
            seq = await checkpoint.load(http, base_url, basic_auth, auth_headers)
            print(f"  [✓] Checkpoint readable   (saved since={seq})")
        except Exception as exc:
            print(f"  [✗] Checkpoint read FAILED: {exc}")
            ok = False

        # 5) Output / consumer endpoint (only when mode=http)
        out_cfg = cfg.get("output", {})
        if out_cfg.get("mode") == "http":
            output = OutputForwarder(session, out_cfg, dry_run=False,
                                                build_basic_auth_fn=build_basic_auth,
                                                build_auth_headers_fn=build_auth_headers,
                                                retryable_http_cls=RetryableHTTP)
            if await output.test_reachable():
                print(f"  [✓] Output endpoint reachable ({out_cfg.get('target_url', '')})")
            else:
                print(f"  [✗] Output endpoint UNREACHABLE ({out_cfg.get('target_url', '')})")
                ok = False
        else:
            print(f"  [–] Output mode=stdout (no endpoint to check)")

    print(f"\n{'='*60}")
    if ok:
        print("  Result: ALL CHECKS PASSED ✓")
    else:
        print("  Result: SOME CHECKS FAILED ✗")
    print(f"{'='*60}\n")
    return ok


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Couchbase _changes feed worker")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--test", action="store_true", help="Test connectivity and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # Run one-time migration if CBL is available
    if USE_CBL:
        migrate_files_to_cbl(args.config)

    cfg = load_config(args.config)
    configure_logging(cfg.get("logging", {}))

    log_event(logger, "info", "PROCESSING", "changes_worker v%s starting (CBL=%s)" % (__version__, USE_CBL))

    # ── Startup config validation ────────────────────────────────────────
    src, warnings, errors = validate_config(cfg)

    src_label = src.replace("_", " ").title()
    logger.info("Source type: %s", src_label)

    for w in warnings:
        logger.warning("CONFIG WARNING: %s", w)

    if errors:
        logger.error("=" * 60)
        logger.error("  STARTUP ABORTED – config errors detected")
        logger.error("=" * 60)
        for e in errors:
            logger.error("  ✗ %s", e)
        logger.error("=" * 60)
        logger.error(
            "Fix the errors above in %s and try again.", args.config
        )
        sys.exit(1)

    if warnings:
        logger.info("Config validation passed with %d warning(s)", len(warnings))
    else:
        logger.info("Config validation passed – all settings OK")
    # ─────────────────────────────────────────────────────────────────────

    if args.test:
        ok = asyncio.run(test_connection(cfg, src))
        sys.exit(0 if ok else 1)

    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    # ── CBL maintenance scheduler ───────────────────────────────────────
    cbl_scheduler: CBLMaintenanceScheduler | None = None
    if USE_CBL:
        cbl_cfg = cfg.get("cbl_maintenance", {})
        if cbl_cfg.get("enabled", True):
            interval = cbl_cfg.get("interval_hours", 24)
            cbl_scheduler = CBLMaintenanceScheduler(interval_hours=interval)
            cbl_scheduler.start()

    # ── Metrics server ───────────────────────────────────────────────────
    metrics_cfg = cfg.get("metrics", {})
    metrics: MetricsCollector | None = None
    metrics_runner: aiohttp.web.AppRunner | None = None

    if metrics_cfg.get("enabled", False):
        database = cfg.get("gateway", {}).get("database", "")
        metrics = MetricsCollector(src, database)

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        if metrics is not None:
            metrics_host = metrics_cfg.get("host", "0.0.0.0")
            metrics_port = metrics_cfg.get("port", 9090)
            metrics_runner = loop.run_until_complete(
                start_metrics_server(metrics, metrics_host, metrics_port)
            )

        loop.run_until_complete(poll_changes(cfg, src, shutdown_event, metrics=metrics))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        if cbl_scheduler is not None:
            cbl_scheduler.stop()
        if USE_CBL:
            close_db()
        if metrics_runner is not None:
            loop.run_until_complete(metrics_runner.cleanup())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
