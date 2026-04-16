"""
HTTP output module for changes_worker.

Handles forwarding processed documents to a downstream REST endpoint
via PUT / POST / DELETE, with retry, response-time tracking, dead-letter
queue, and multiple serialization formats.
"""

import asyncio
import json
import logging
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp

# Optional serialization libraries – imported lazily so the worker starts
# even if they are not installed (only errors if actually selected).
try:
    import msgpack  # type: ignore
except ImportError:
    msgpack = None

try:
    import cbor2  # type: ignore
except ImportError:
    cbor2 = None

try:
    import bson  # type: ignore
except ImportError:
    bson = None

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

from pipeline_logging import log_event, infer_operation

logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

CONTENT_TYPES: dict[str, str] = {
    "json": "application/json",
    "xml": "application/xml",
    "form": "application/x-www-form-urlencoded",
    "msgpack": "application/msgpack",
    "cbor": "application/cbor",
    "bson": "application/bson",
    "yaml": "application/yaml",
}

VALID_OUTPUT_FORMATS = tuple(CONTENT_TYPES.keys())


def _dict_to_xml(doc: dict, root_tag: str = "doc") -> bytes:
    """Convert a flat-ish dict to XML bytes."""
    root = ET.Element(root_tag)
    _dict_to_xml_elements(root, doc)
    return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8")


def _dict_to_xml_elements(parent: ET.Element, data) -> None:
    if isinstance(data, dict):
        for key, val in data.items():
            key_str = str(key).lstrip("_")
            child = ET.SubElement(parent, key_str if key_str else "item")
            _dict_to_xml_elements(child, val)
    elif isinstance(data, (list, tuple)):
        for item in data:
            child = ET.SubElement(parent, "item")
            _dict_to_xml_elements(child, item)
    else:
        parent.text = str(data) if data is not None else ""


def _flatten_dict(d: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict for form-encoding: {"a": {"b": 1}} → {"a.b": "1"}"""
    items: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, key))
        elif isinstance(v, (list, tuple)):
            items[key] = json.dumps(v, default=str)
        else:
            items[key] = str(v) if v is not None else ""
    return items


def serialize_doc(doc: dict, fmt: str) -> tuple[bytes | str, str]:
    """
    Serialize a document dict to the requested format.

    Returns (body_bytes_or_str, content_type).
    """
    if fmt == "json":
        return json.dumps(doc, default=str), CONTENT_TYPES["json"]

    if fmt == "xml":
        return _dict_to_xml(doc), CONTENT_TYPES["xml"]

    if fmt == "form":
        flat = _flatten_dict(doc)
        return urllib.parse.urlencode(flat), CONTENT_TYPES["form"]

    if fmt == "msgpack":
        if msgpack is None:
            raise RuntimeError("msgpack library not installed – pip install msgpack")
        return msgpack.packb(doc, default=str), CONTENT_TYPES["msgpack"]

    if fmt == "cbor":
        if cbor2 is None:
            raise RuntimeError("cbor2 library not installed – pip install cbor2")
        return cbor2.dumps(doc), CONTENT_TYPES["cbor"]

    if fmt == "bson":
        if bson is None:
            raise RuntimeError("bson library not installed – pip install pymongo (provides bson)")
        return bson.BSON.encode(doc), CONTENT_TYPES["bson"]

    if fmt == "yaml":
        if yaml is None:
            raise RuntimeError("pyyaml library not installed – pip install pyyaml")
        return yaml.dump(doc, default_flow_style=False), CONTENT_TYPES["yaml"]

    raise ValueError(f"Unknown output_format: {fmt}")


def check_serialization_library(out_fmt: str) -> tuple[str, str] | None:
    """
    Check if the required serialization library is installed for the given format.

    Returns (format_name, pip_name) tuple if the library is MISSING, or None if OK.
    """
    _lib_check: dict[str, tuple] = {
        "msgpack": (msgpack, "msgpack"),
        "cbor": (cbor2, "cbor2"),
        "bson": (bson, "pymongo"),
        "yaml": (yaml, "pyyaml"),
    }
    if out_fmt in _lib_check:
        mod, pip_name = _lib_check[out_fmt]
        if mod is None:
            return (out_fmt, pip_name)
    return None


# ---------------------------------------------------------------------------
# Output / forwarding
# ---------------------------------------------------------------------------

class OutputEndpointDown(Exception):
    """Raised when the output target is unreachable and halt_on_failure is set."""


class OutputForwarder:
    """
    Manages sending processed docs to the consumer endpoint (or stdout).

    When mode=http:
      - Has its own RetryableHTTP with output-specific retry settings
      - Tracks per-request response times (min / max / avg / count)
      - On non-retryable failure (4xx) or exhausted retries (5xx):
          * If halt_on_failure=true → raises OutputEndpointDown so the
            main loop stops processing and does NOT advance the checkpoint
          * If halt_on_failure=false → logs the error and continues
      - Handles 3xx as non-retryable errors

    When mode=stdout:
      - Writes JSON to stdout, no failure handling needed
    """

    def __init__(self, session: aiohttp.ClientSession, out_cfg: dict, dry_run: bool,
                 metrics=None, build_basic_auth_fn=None, build_auth_headers_fn=None,
                 retryable_http_cls=None):
        self._mode = out_cfg.get("mode", "stdout")
        self._target_url = out_cfg.get("target_url", "").rstrip("/")
        self._dry_run = dry_run
        self._halt_on_failure = out_cfg.get("halt_on_failure", True)
        self._log_response_times = out_cfg.get("log_response_times", True)
        self._output_format = out_cfg.get("output_format", "json")
        self._metrics = metrics

        # Auth for the output endpoint
        _build_basic = build_basic_auth_fn or _default_build_basic_auth
        _build_headers = build_auth_headers_fn or _default_build_auth_headers
        self._auth = _build_basic(out_cfg.get("target_auth", {}))
        self._headers = _build_headers(out_cfg.get("target_auth", {}))

        # Extra request options from config (query params, custom headers)
        req_opts = out_cfg.get("request_options", {})
        self._extra_params = req_opts.get("params", {})
        self._extra_headers = req_opts.get("headers", {})

        # Output-specific retry (separate from the gateway retry)
        out_retry = out_cfg.get("retry", {
            "max_retries": 3,
            "backoff_base_seconds": 1,
            "backoff_max_seconds": 30,
            "retry_on_status": [500, 502, 503, 504],
        })
        _http_cls = retryable_http_cls or _RetryableHTTPLazy
        self._http = _http_cls(session, out_retry) if self._mode == "http" else None

        # Response time tracking
        self._resp_times: list[float] = []
        self._lock = asyncio.Lock()

    # -- Public API ------------------------------------------------------------

    def _method_key(self, method: str) -> str:
        """Map HTTP method to metrics key prefix: 'put' or 'delete'."""
        return "delete" if method == "DELETE" else "put"

    async def send(self, doc: dict, method: str = "PUT") -> dict:
        """Send a single doc. Returns result dict with 'ok' bool. Raises OutputEndpointDown if halt_on_failure."""
        if self._mode == "stdout":
            self._send_stdout(doc)
            if self._metrics:
                self._metrics.inc("output_requests_total")
                mk = self._method_key(method)
                self._metrics.inc(f"output_{mk}_total")
            return {"ok": True, "doc_id": doc.get("_id", doc.get("id", "unknown")), "method": method}

        doc_id = doc.get("_id", doc.get("id", "unknown"))
        url = f"{self._target_url}/{doc_id}"
        body, content_type = serialize_doc(doc, self._output_format)
        body_len = len(body) if isinstance(body, (bytes, str)) else 0

        if self._dry_run:
            log_event(logger, "info", "OUTPUT", "dry run",
                      operation=infer_operation(doc=doc, method=method),
                      doc_id=doc_id, http_method=method, url=url, bytes=body_len)
            return {"ok": True, "doc_id": doc_id, "method": method, "dry_run": True}

        assert self._http is not None

        from icecream import ic
        ic(method, url, self._output_format)
        mk = self._method_key(method)

        t_start = time.monotonic()
        try:
            merged_headers = {**self._headers, **self._extra_headers, "Content-Type": content_type}
            resp = await self._http.request(
                method, url, data=body, auth=self._auth,
                headers=merged_headers,
                params=self._extra_params or None,
            )
            elapsed_ms = (time.monotonic() - t_start) * 1000
            status = resp.status
            resp.release()

            await self._record_time(elapsed_ms)
            if self._metrics:
                self._metrics.inc("output_requests_total")
                self._metrics.inc(f"output_{mk}_total")
                self._metrics.inc("bytes_output_total", body_len)
                self._metrics.record_output_response_time(elapsed_ms / 1000)
            log_event(logger, "debug", "OUTPUT", "forwarded document",
                      operation=infer_operation(doc=doc, method=method),
                      doc_id=doc_id, http_method=method, url=url,
                      status=status, elapsed_ms=round(elapsed_ms, 1),
                      bytes=body_len)
            if self._metrics:
                self._metrics.inc("output_success_total")
            return {"ok": True, "doc_id": doc_id, "method": method, "status": status}

        except _ClientHTTPError as exc:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            await self._record_time(elapsed_ms)
            if self._metrics:
                self._metrics.inc("output_errors_total")
                self._metrics.inc(f"output_{mk}_errors_total")
                self._metrics.inc("bytes_output_total", body_len)
                self._metrics.record_output_response_time(elapsed_ms / 1000)
            log_event(logger, "error", "OUTPUT", "client error",
                      operation=infer_operation(doc=doc, method=method),
                      doc_id=doc_id, http_method=method, url=url,
                      status=exc.status, elapsed_ms=round(elapsed_ms, 1))
            if self._halt_on_failure:
                if self._metrics:
                    self._metrics.set("output_endpoint_up", 0)
                raise OutputEndpointDown(
                    f"Output endpoint returned {exc.status} for {method} {url} – "
                    f"halting to preserve checkpoint"
                ) from exc
            else:
                log_event(logger, "warn", "OUTPUT",
                          "halt_on_failure=false – skipping doc",
                          doc_id=doc_id, status=exc.status)
                return {"ok": False, "doc_id": doc_id, "method": method, "status": exc.status, "error": exc.body[:500]}

        except _RedirectHTTPError as exc:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            await self._record_time(elapsed_ms)
            if self._metrics:
                self._metrics.inc("output_errors_total")
                self._metrics.inc(f"output_{mk}_errors_total")
                self._metrics.inc("bytes_output_total", body_len)
                self._metrics.record_output_response_time(elapsed_ms / 1000)
            log_event(logger, "error", "OUTPUT", "redirect error",
                      operation=infer_operation(doc=doc, method=method),
                      doc_id=doc_id, http_method=method, url=url,
                      status=exc.status, elapsed_ms=round(elapsed_ms, 1))
            if self._halt_on_failure:
                if self._metrics:
                    self._metrics.set("output_endpoint_up", 0)
                raise OutputEndpointDown(
                    f"Output endpoint returned redirect {exc.status} for {method} {url}"
                ) from exc
            else:
                log_event(logger, "warn", "OUTPUT",
                          "halt_on_failure=false – skipping doc",
                          doc_id=doc_id, status=exc.status)
                return {"ok": False, "doc_id": doc_id, "method": method, "status": exc.status, "error": exc.body[:500]}

        except (ConnectionError, _ServerHTTPError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            await self._record_time(elapsed_ms)
            if self._metrics:
                self._metrics.inc("output_errors_total")
                self._metrics.inc(f"output_{mk}_errors_total")
                self._metrics.inc("bytes_output_total", body_len)
                self._metrics.record_output_response_time(elapsed_ms / 1000)
            log_event(logger, "error", "OUTPUT", "output failed after retries",
                      operation=infer_operation(doc=doc, method=method),
                      doc_id=doc_id, http_method=method, url=url,
                      elapsed_ms=round(elapsed_ms, 1))
            if self._halt_on_failure:
                if self._metrics:
                    self._metrics.set("output_endpoint_up", 0)
                raise OutputEndpointDown(
                    f"Output endpoint unreachable for {method} {url} – "
                    f"halting to preserve checkpoint: {exc}"
                ) from exc
            else:
                log_event(logger, "warn", "OUTPUT",
                          "halt_on_failure=false – skipping doc",
                          doc_id=doc_id)
                return {"ok": False, "doc_id": doc_id, "method": method, "status": 0, "error": str(exc)[:500]}

    async def test_reachable(self) -> bool:
        """Quick health check – HEAD or GET the target URL root."""
        if self._mode != "http" or not self._target_url:
            return True
        assert self._http is not None
        try:
            t_start = time.monotonic()
            resp = await self._http.request("GET", self._target_url, auth=self._auth, headers=self._headers)
            elapsed_ms = (time.monotonic() - t_start) * 1000
            resp.release()
            log_event(logger, "info", "HTTP", "output endpoint reachable",
                      operation="SELECT", url=self._target_url,
                      status=resp.status, elapsed_ms=round(elapsed_ms, 1))
            return True
        except Exception as exc:
            log_event(logger, "error", "HTTP", "output endpoint unreachable: %s" % exc,
                      url=self._target_url)
            return False

    def log_stats(self) -> None:
        """Log accumulated response time statistics."""
        if not self._log_response_times or not self._resp_times:
            return
        n = len(self._resp_times)
        avg = sum(self._resp_times) / n
        lo = min(self._resp_times)
        hi = max(self._resp_times)
        log_event(logger, "info", "OUTPUT",
                  "output stats: %d requests | avg=%.1fms | min=%.1fms | max=%.1fms" % (n, avg, lo, hi))

    # -- Internal --------------------------------------------------------------

    def _send_stdout(self, doc: dict) -> None:
        body, _ = serialize_doc(doc, self._output_format)
        if isinstance(body, bytes):
            sys.stdout.buffer.write(body + b"\n")
            sys.stdout.buffer.flush()
        else:
            sys.stdout.write(body + "\n")
            sys.stdout.flush()

    async def _record_time(self, ms: float) -> None:
        if self._log_response_times:
            async with self._lock:
                self._resp_times.append(ms)


def determine_method(change: dict) -> str:
    if change.get("deleted"):
        return "DELETE"
    return "PUT"


class DeadLetterQueue:
    """
    Dead letter queue for documents that failed output delivery.

    When CBL is available, entries are stored as CBL documents.
    Otherwise falls back to append-only JSONL file.
    """

    def __init__(self, path: str):
        from cbl_store import USE_CBL as _use_cbl
        self._use_cbl = _use_cbl
        self._store = None
        if self._use_cbl:
            from cbl_store import CBLStore
            self._store = CBLStore()
        self._path = Path(path) if path and not self._use_cbl else None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._use_cbl or self._path is not None

    async def write(self, doc: dict, result: dict, seq: str | int) -> None:
        if self._use_cbl and self._store:
            self._store.add_dlq_entry(
                doc_id=result.get("doc_id", "unknown"),
                seq=str(seq),
                method=result.get("method", "PUT"),
                status=result.get("status", 0),
                error=result.get("error", ""),
                doc=doc,
            )
            log_event(logger, "warn", "DLQ", "entry written to CBL",
                      operation="INSERT", doc_id=result.get("doc_id"),
                      seq=str(seq), storage="cbl")
            return
        # Original file fallback
        if not self._path:
            return
        entry = {
            "doc_id": result.get("doc_id", "unknown"),
            "seq": str(seq),
            "method": result.get("method", "PUT"),
            "status": result.get("status", 0),
            "error": result.get("error", ""),
            "time": int(time.time()),
            "doc": doc,
        }
        async with self._lock:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        log_event(logger, "warn", "DLQ", "entry written to file",
                  operation="INSERT", doc_id=result.get("doc_id"),
                  seq=str(seq), storage="file")


# ---------------------------------------------------------------------------
# Minimal auth helpers (so this module can work standalone)
# The main changes_worker passes its own build_basic_auth / build_auth_headers
# via constructor args; these are fallback defaults.
# ---------------------------------------------------------------------------

def _default_build_basic_auth(auth_cfg: dict) -> aiohttp.BasicAuth | None:
    if auth_cfg.get("method", "none") == "basic":
        username = auth_cfg.get("username", "")
        password = auth_cfg.get("password", "")
        if username:
            return aiohttp.BasicAuth(username, password)
    return None


def _default_build_auth_headers(auth_cfg: dict) -> dict:
    method = auth_cfg.get("method", "none")
    headers: dict[str, str] = {}
    if method == "bearer":
        headers["Authorization"] = f"Bearer {auth_cfg.get('bearer_token', '')}"
    elif method == "session":
        headers["Cookie"] = f"SyncGatewaySession={auth_cfg.get('session_cookie', '')}"
    return headers


# ---------------------------------------------------------------------------
# Lazy import shim for RetryableHTTP / exception classes from changes_worker.
# At import time we don't know if they exist yet, so we resolve on first use
# or accept them via constructor injection.
# ---------------------------------------------------------------------------

class _ClientHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class _RedirectHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class _ServerHTTPError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class _RetryableHTTPLazy:
    """Thin wrapper that delegates to the real RetryableHTTP from changes_worker."""

    def __init__(self, session: aiohttp.ClientSession, retry_cfg: dict):
        from main import RetryableHTTP
        self._inner = RetryableHTTP(session, retry_cfg)

    async def request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        return await self._inner.request(method, url, **kwargs)
