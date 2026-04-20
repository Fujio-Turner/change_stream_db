"""
Base output forwarder for RDBMS outputs.

Provides shared logic for metrics (with per-engine + per-job_id labels),
mapping loading, send() pre-flight, retry loop, and stats logging.
Each engine subclass only needs to implement the driver-specific pieces:
pool creation, SQL execution, error classification, and introspection.
"""

import abc
import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from pathlib import Path

from pipeline_logging import log_event

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

logger = logging.getLogger("changes_worker")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL identifier and return it unchanged.

    Raises ValueError if the name contains characters that could enable
    SQL injection.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid SQL {context}: {name!r} — "
            "only letters, digits, underscores, and $ are allowed"
        )
    return name


# ── Per-engine / per-job metrics proxy ──────────────────────────────────────
# Wraps the global MetricsCollector so every inc() also records a labeled
# counter that can be broken out by engine and job_id on the /_metrics page.


class DbMetrics:
    """
    Lightweight metrics wrapper that tracks counters with (engine, job_id)
    labels while also delegating to the global MetricsCollector for totals.

    Usage in Prometheus:
        # Global totals (backward compat – existing dashboards keep working)
        changes_worker_output_requests_total{src="...",database="..."} 500

        # Per-engine / per-job breakdowns
        changes_worker_db_output_requests_total{engine="postgres",job_id="orders_sync"} 300
        changes_worker_db_output_requests_total{engine="oracle",job_id="analytics"} 200
    """

    # Class-level registry so the metrics endpoint can iterate all instances.
    _registry_lock = threading.Lock()
    _registry: list["DbMetrics"] = []

    def __init__(self, engine: str, job_id: str, global_metrics=None):
        self.engine = engine
        self.job_id = job_id or engine  # fallback: use engine name
        self._global = global_metrics  # MetricsCollector from main.py
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._resp_times: deque[float] = deque(maxlen=10_000)

        with DbMetrics._registry_lock:
            DbMetrics._registry.append(self)

    # ── counter / timing ────────────────────────────────────────────────

    def inc(self, name: str, value: int = 1) -> None:
        """Increment both the local labeled counter AND the global total."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value
        if self._global:
            self._global.inc(name, value)

    def record_output_response_time(self, seconds: float) -> None:
        with self._lock:
            self._resp_times.append(seconds)
        if self._global:
            self._global.record_output_response_time(seconds)

    # ── snapshot (called by render) ─────────────────────────────────────

    def snapshot(self) -> tuple[dict[str, int], list[float]]:
        """Return a copy of counters + resp_times under the lock."""
        with self._lock:
            return dict(self._counters), list(self._resp_times)

    # ── cleanup ─────────────────────────────────────────────────────────

    def unregister(self) -> None:
        with DbMetrics._registry_lock:
            try:
                DbMetrics._registry.remove(self)
            except ValueError:
                pass

    # ── class-level render (called from MetricsCollector.render) ────────

    @classmethod
    def render_all(cls) -> str:
        """
        Render per-engine/per-job DB metrics in Prometheus text format.

        Returns a string of lines that the main MetricsCollector.render()
        can append to its output.
        """
        with cls._registry_lock:
            instances = list(cls._registry)

        if not instances:
            return ""

        # Collect snapshots keyed by (engine, job_id)
        snapshots: list[tuple[str, str, dict[str, int], list[float]]] = []
        for inst in instances:
            counters, resp_times = inst.snapshot()
            snapshots.append((inst.engine, inst.job_id, counters, resp_times))

        # Gather all counter names across all instances
        all_counter_names: set[str] = set()
        for _, _, counters, _ in snapshots:
            all_counter_names.update(counters.keys())

        lines: list[str] = []

        # Emit one HELP/TYPE block per counter, with one line per (engine, job_id)
        for name in sorted(all_counter_names):
            prom_name = f"changes_worker_db_{name}"
            lines.append(
                f"# HELP {prom_name} DB output counter: {name} (per engine/job)"
            )
            lines.append(f"# TYPE {prom_name} counter")
            for engine, job_id, counters, _ in snapshots:
                val = counters.get(name, 0)
                if val:
                    lines.append(
                        f'{prom_name}{{engine="{engine}",job_id="{job_id}"}} {val}'
                    )

        # Emit per-instance response time summaries
        has_resp = any(rt for _, _, _, rt in snapshots)
        if has_resp:
            prom_name = "changes_worker_db_output_response_time_seconds"
            lines.append(f"# HELP {prom_name} DB output response time (per engine/job)")
            lines.append(f"# TYPE {prom_name} summary")
            for engine, job_id, _, resp_times in snapshots:
                if not resp_times:
                    continue
                s = sorted(resp_times)
                count = len(s)
                total = sum(s)
                for q in (0.5, 0.9, 0.99):
                    idx = int(q * (count - 1))
                    lines.append(
                        f'{prom_name}{{engine="{engine}",job_id="{job_id}",'
                        f'quantile="{q}"}} {s[idx]:.6f}'
                    )
                lines.append(
                    f'{prom_name}_sum{{engine="{engine}",job_id="{job_id}"}} {total:.6f}'
                )
                lines.append(
                    f'{prom_name}_count{{engine="{engine}",job_id="{job_id}"}} {count}'
                )

        return "\n".join(lines)


# ── Abstract base forwarder ─────────────────────────────────────────────────


class BaseOutputForwarder(abc.ABC):
    """
    Abstract async RDBMS output forwarder.

    Subclasses must implement:
        _engine          – property returning engine name (e.g. "postgres")
        _connect_pool()  – create the async connection pool
        _close_pool()    – close the pool
        _execute_ops()   – acquire conn, run ops inside a transaction
        _reconnect_pool()– close + re-create the pool on connection errors
        _test_connection()– run a simple health query (SELECT 1)
        _is_transient()  – classify whether an exception is retryable
        _error_class()   – return a short error classification string
    """

    def __init__(self, out_cfg: dict, dry_run: bool = False, metrics=None):
        self._dry_run = dry_run
        self._halt_on_failure = out_cfg.get("halt_on_failure", True)
        self._data_error_action = out_cfg.get("data_error_action", "dlq")
        self._metrics_global = metrics

        # Engine-specific config is read by the subclass __init__.
        # Common config shared across all engines:
        engine_cfg = self._get_engine_cfg(out_cfg)
        self._max_retries = engine_cfg.get("max_retries", 3)
        self._backoff_base = engine_cfg.get("backoff_base_seconds", 0.5)
        self._backoff_max = engine_cfg.get("backoff_max_seconds", 10)

        # Mapping config
        self._mappers: list = []
        self._mapping_file = out_cfg.get("mapping_file", "")
        sm = engine_cfg.get("schema_mappings", {})
        self._mappings_dir = sm.get("path", "") if sm.get("enabled") else ""

        # Job ID for per-job metric labels (falls back to engine name)
        self._job_id = out_cfg.get("job_id", "")

        # Per-engine/per-job metrics proxy (created after subclass sets _engine)
        # Subclass __init__ MUST call _init_metrics() at the end.
        self._metrics: DbMetrics | None = None

        # Response time tracking (local to this forwarder)
        self._resp_times: deque[float] = deque(maxlen=10_000)
        self._lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()

    def _init_metrics(self) -> None:
        """Call from subclass __init__ after the engine property is available."""
        self._metrics = DbMetrics(
            engine=self._engine,
            job_id=self._job_id,
            global_metrics=self._metrics_global,
        )

    @property
    @abc.abstractmethod
    def _engine(self) -> str:
        """Return the engine name (e.g. 'postgres', 'mysql')."""

    @property
    def _mode(self) -> str:
        """Alias for _engine used by main.py logging."""
        return self._engine

    @abc.abstractmethod
    def _get_engine_cfg(self, out_cfg: dict) -> dict:
        """Extract the engine-specific config dict from out_cfg."""

    # ── Mapping loading (engine-agnostic) ───────────────────────────────

    def _load_mappers(self) -> None:
        """
        Load schema mappers from CBL or filesystem.
        Called by connect() after the pool is created.
        """
        from schema.mapper import MappingDiagnostics, SchemaMapper

        new_mappers: list = []

        # Prefer CBL (single source of truth), fall back to filesystem
        cbl_loaded = False
        try:
            from cbl_store import USE_CBL, CBLStore

            if USE_CBL:
                entries = CBLStore().list_mappings()
                for entry in entries:
                    name = entry.get("name", "")
                    if name.endswith(".meta.json"):
                        continue
                    raw = entry.get("content", "")
                    if not raw:
                        continue
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    data["_source_name"] = name
                    new_mappers.append(SchemaMapper(data))
                    log_event(
                        logger,
                        "info",
                        "MAPPING",
                        "loaded schema mapping from CBL",
                        doc_id=name,
                        storage="cbl",
                    )
                cbl_loaded = True
        except Exception as exc:
            ic("_load_mappers: CBL fallback", type(exc).__name__, str(exc))
            log_event(
                logger,
                "warn",
                "MAPPING",
                "could not load mappings from CBL – falling back to filesystem",
                error_detail=f"{type(exc).__name__}: {exc}",
            )

        if not cbl_loaded:
            if self._mappings_dir:
                mdir = Path(self._mappings_dir)
                if mdir.is_dir():
                    for f in sorted(mdir.glob("*.json")):
                        if f.name.endswith(".meta.json"):
                            continue
                        try:
                            raw = json.loads(f.read_text())
                            meta = raw.get("meta", {})
                            if not meta.get("active", True):
                                log_event(
                                    logger,
                                    "info",
                                    "MAPPING",
                                    "skipping inactive mapping",
                                    doc_id=str(f),
                                )
                                continue
                            new_mappers.append(SchemaMapper(raw))
                            log_event(
                                logger,
                                "info",
                                "MAPPING",
                                "loaded schema mapping from file",
                                doc_id=str(f),
                                storage="file",
                            )
                        except (json.JSONDecodeError, OSError) as exc:
                            log_event(
                                logger,
                                "warn",
                                "MAPPING",
                                "skipping invalid mapping file",
                                doc_id=str(f),
                                error_detail=f"{type(exc).__name__}: {exc}",
                            )
                            continue
                        except Exception as exc:
                            log_event(
                                logger,
                                "warn",
                                "MAPPING",
                                "skipping invalid mapping definition",
                                doc_id=str(f),
                                error_detail=f"{type(exc).__name__}: {exc}",
                            )
                            continue
            if self._mapping_file:
                try:
                    new_mappers.append(SchemaMapper.from_file(self._mapping_file))
                    log_event(
                        logger,
                        "info",
                        "MAPPING",
                        "loaded schema mapping from file",
                        doc_id=self._mapping_file,
                        storage="file",
                    )
                except Exception as exc:
                    log_event(
                        logger,
                        "warn",
                        "MAPPING",
                        "failed to load mapping file",
                        doc_id=self._mapping_file,
                        error_detail=f"{type(exc).__name__}: {exc}",
                    )

        if not new_mappers:
            log_event(
                logger,
                "warn",
                "MAPPING",
                "no schema mappings loaded – documents will be skipped",
            )

        # Warn about duplicate match filters (first-match wins)
        seen_matches: dict[str, str] = {}
        for m in new_mappers:
            key = f"{m.match.get('field', '')}={m.match.get('value', '')}"
            src = m.mapping.get("_source_name", "?")
            if key in seen_matches:
                log_event(
                    logger,
                    "warn",
                    "MAPPING",
                    "duplicate mapping: '%s' and '%s' both match %s — "
                    "only '%s' will be used (first-match wins)"
                    % (seen_matches[key], src, key, seen_matches[key]),
                )
            else:
                seen_matches[key] = src

        self._mappers = new_mappers
        ic("_load_mappers: done", len(self._mappers))

    # ── Pool lifecycle (subclass hooks) ─────────────────────────────────

    @abc.abstractmethod
    async def _connect_pool(self) -> None:
        """Create the async connection pool."""

    @abc.abstractmethod
    async def _close_pool(self) -> None:
        """Close the async connection pool."""

    @abc.abstractmethod
    async def _reconnect_pool(self) -> None:
        """Close and re-create the pool (called on connection errors)."""

    async def connect(self) -> None:
        """Create the connection pool and load schema mappings."""
        ic("connect", self._engine)
        async with self._pool_lock:
            await self._connect_pool()
            try:
                self._load_mappers()
            except Exception:
                await self._close_pool()
                raise
        log_event(
            logger, "info", "OUTPUT", "connection pool created", mode=self._engine
        )

    async def close(self) -> None:
        """Close the connection pool and unregister metrics."""
        ic("close", self._engine)
        try:
            async with self._pool_lock:
                await self._close_pool()
        finally:
            if self._metrics:
                self._metrics.unregister()
        log_event(logger, "info", "OUTPUT", "connection pool closed", mode=self._engine)

    # ── SQL execution (subclass hook) ───────────────────────────────────

    @abc.abstractmethod
    async def _execute_ops(self, ops: list) -> None:
        """
        Acquire a connection, open a transaction, and execute all
        SqlOperation objects.  Raise on any error.
        """

    # ── Error classification (subclass hooks) ───────────────────────────

    @abc.abstractmethod
    def _is_transient(self, exc: Exception) -> bool:
        """Return True if the error is transient and worth retrying."""

    @abc.abstractmethod
    def _error_class(self, exc: Exception) -> str:
        """Return a short classification string for the error."""

    # ── Health check (subclass hook) ────────────────────────────────────

    @abc.abstractmethod
    async def _test_connection(self) -> None:
        """Run a lightweight health check query (e.g. SELECT 1). Raise on failure."""

    async def test_reachable(self) -> bool:
        """Test that the database server is reachable."""
        ic("test_reachable", self._engine)
        try:
            if not hasattr(self, "_pool") or getattr(self, "_pool", None) is None:
                await self.connect()
            await self._test_connection()
            ic("test_reachable: OK", self._engine)
            log_event(logger, "info", "OUTPUT", "database reachable", mode=self._engine)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ic("test_reachable: FAIL", self._engine, type(exc).__name__, str(exc))
            log_event(
                logger,
                "error",
                "OUTPUT",
                "database unreachable",
                mode=self._engine,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False

    # ── send() — the main document processing method ────────────────────

    async def send(self, doc: dict, method: str = "PUT") -> dict:
        """
        Process a single document: map to SQL ops and execute.

        Transient errors are retried with exponential backoff.
        Permanent errors return immediately for DLQ routing.

        Returns result dict with 'ok' bool plus 'retryable' and
        'error_class' on failure.
        """
        ic("send", doc.get("_id", doc.get("id", "unknown")) if doc else "None", method)
        if doc is None:
            log_event(logger, "debug", "OUTPUT", "received None doc – skipping")
            if self._metrics:
                self._metrics.inc("output_skipped_total")
            return {"ok": True, "doc_id": "unknown", "skipped": True}

        doc_id = doc.get("_id", doc.get("id", "unknown"))
        is_delete = method == "DELETE"

        if not self._mappers:
            log_event(
                logger,
                "warn",
                "MAPPING",
                "no schema mapping loaded – skipping doc",
                doc_id=doc_id,
            )
            if self._metrics:
                self._metrics.inc("output_skipped_total")
            return {
                "ok": False,
                "doc_id": doc_id,
                "error": "no_mapping",
                "retryable": False,
                "error_class": "config",
            }

        # Find the first matching mapper
        mapper = None
        for m in self._mappers:
            if m.matches(doc):
                mapper = m
                break
        if not mapper:
            log_event(
                logger,
                "debug",
                "MAPPING",
                "doc does not match any mapping filter – skipping",
                doc_id=doc_id,
            )
            if self._metrics:
                self._metrics.inc("output_skipped_total")
                self._metrics.inc("mapper_skipped_total")
            return {"ok": True, "doc_id": doc_id, "skipped": True}

        try:
            ops, diag = mapper.map_document(doc, is_delete=is_delete)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._metrics:
                self._metrics.inc("output_requests_total")
                self._metrics.inc("output_errors_total")
                self._metrics.inc("mapper_errors_total")
                self._metrics.inc("db_permanent_errors_total")
            ic("send: mapping error", doc_id, type(exc).__name__, str(exc))
            log_event(
                logger,
                "error",
                "MAPPING",
                "mapping error",
                doc_id=doc_id,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return {
                "ok": False,
                "doc_id": doc_id,
                "error": f"mapping_error: {exc!s}"[:500],
                "retryable": False,
                "error_class": "mapping",
            }

        if diag.has_issues:
            doc_rev = doc.get("_rev", doc.get("rev", "?"))
            log_event(
                logger,
                "warn",
                "MAPPING",
                "mapping issues: %s" % diag.summary(),
                doc_id=doc_id,
            )

        if self._metrics:
            self._metrics.inc("mapper_matched_total")

        if not ops:
            if self._metrics:
                self._metrics.inc("output_skipped_total")
            return {"ok": True, "doc_id": doc_id, "ops": 0}

        if self._dry_run:
            for op in ops:
                sql, params = op.to_sql()
                log_event(
                    logger,
                    "info",
                    "OUTPUT",
                    "[DRY RUN] %s | params=%s" % (sql, params),
                    doc_id=doc_id,
                )
            return {"ok": True, "doc_id": doc_id, "ops": len(ops), "dry_run": True}

        # -- Execute with retry for transient errors --
        t_start = time.monotonic()
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                await self._execute_ops(ops)

                # Success
                elapsed_ms = (time.monotonic() - t_start) * 1000
                async with self._lock:
                    self._resp_times.append(elapsed_ms)
                if self._metrics:
                    self._metrics.inc("output_requests_total")
                    self._metrics.inc("output_success_total")
                    self._metrics.inc("mapper_ops_total", len(ops))
                    self._metrics.record_output_response_time(elapsed_ms / 1000)

                doc_rev = doc.get("_rev", doc.get("rev", "?"))
                ic("send: OK", doc_id, len(ops), round(elapsed_ms, 1))
                log_event(
                    logger,
                    "debug",
                    "OUTPUT",
                    "executed SQL ops",
                    doc_id=doc_id,
                    operation="DELETE" if is_delete else "UPSERT",
                    elapsed_ms=round(elapsed_ms, 1),
                    mode=self._engine,
                    http_method=method,
                )
                return {
                    "ok": True,
                    "doc_id": doc_id,
                    "ops": len(ops),
                    "elapsed_ms": round(elapsed_ms, 1),
                }

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                eclass = self._error_class(exc)

                if not self._is_transient(exc):
                    elapsed_ms = (time.monotonic() - t_start) * 1000
                    if self._metrics:
                        self._metrics.inc("output_requests_total")
                        self._metrics.inc("output_errors_total")
                        self._metrics.inc("db_permanent_errors_total")
                    ic(
                        "send: permanent error",
                        doc_id,
                        eclass,
                        type(exc).__name__,
                        str(exc),
                    )
                    log_event(
                        logger,
                        "error",
                        "OUTPUT",
                        "permanent error",
                        doc_id=doc_id,
                        mode=self._engine,
                        error_detail=f"{type(exc).__name__}: {exc}",
                    )

                    return {
                        "ok": False,
                        "doc_id": doc_id,
                        "error": str(exc)[:500],
                        "retryable": False,
                        "error_class": eclass,
                        "data_error_action": self._data_error_action,
                    }

                # Transient error — retry
                if self._metrics:
                    self._metrics.inc("db_transient_errors_total")
                    self._metrics.inc("db_retries_total")

                if eclass == "connection":
                    try:
                        ic(
                            "send: connection error, reconnecting",
                            doc_id,
                            attempt,
                            self._max_retries,
                        )
                        log_event(
                            logger,
                            "warn",
                            "OUTPUT",
                            "connection error – reconnecting pool",
                            doc_id=doc_id,
                            mode=self._engine,
                            attempt=attempt,
                            error_detail=f"{type(exc).__name__}: {exc}",
                        )
                        await self._reconnect_pool()
                        if self._metrics:
                            self._metrics.inc("db_pool_reconnects_total")
                    except Exception as reconn_exc:
                        ic(
                            "send: pool reconnect failed",
                            type(reconn_exc).__name__,
                            str(reconn_exc),
                        )
                        log_event(
                            logger,
                            "error",
                            "OUTPUT",
                            "pool reconnect failed",
                            mode=self._engine,
                            error_detail=f"{type(reconn_exc).__name__}: {reconn_exc}",
                        )
                        if attempt == self._max_retries:
                            break
                else:
                    ic(
                        "send: transient error",
                        doc_id,
                        eclass,
                        attempt,
                        self._max_retries,
                    )
                    log_event(
                        logger,
                        "warn",
                        "OUTPUT",
                        "transient error",
                        doc_id=doc_id,
                        mode=self._engine,
                        attempt=attempt,
                        error_detail=f"{type(exc).__name__}: {exc}",
                    )

                if attempt < self._max_retries:
                    delay = min(
                        self._backoff_base * (2 ** (attempt - 1)), self._backoff_max
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        elapsed_ms = (time.monotonic() - t_start) * 1000
        eclass = self._error_class(last_exc) if last_exc else "unknown"
        if self._metrics:
            self._metrics.inc("output_requests_total")
            self._metrics.inc("output_errors_total")
            self._metrics.inc("db_retry_exhausted_total")
        ic("send: retries exhausted", doc_id, eclass, self._max_retries)
        log_event(
            logger,
            "error",
            "OUTPUT",
            "retries exhausted",
            doc_id=doc_id,
            mode=self._engine,
            attempt=self._max_retries,
            error_detail=f"{type(last_exc).__name__}: {last_exc}"
            if last_exc
            else "unknown",
        )

        if self._halt_on_failure:
            from rest import OutputEndpointDown

            raise OutputEndpointDown(
                f"{self._engine.upper()} retries exhausted for {doc_id} "
                f"[{eclass}]: {last_exc}"
            ) from last_exc
        return {
            "ok": False,
            "doc_id": doc_id,
            "error": str(last_exc)[:500],
            "retryable": False,
            "error_class": eclass,
        }

    # ── stats logging ───────────────────────────────────────────────────

    def log_stats(self) -> None:
        """Log accumulated response time statistics."""
        if not self._resp_times:
            return
        n = len(self._resp_times)
        avg = sum(self._resp_times) / n
        lo = min(self._resp_times)
        hi = max(self._resp_times)
        log_event(
            logger,
            "info",
            "OUTPUT",
            "%s stats: %d ops | avg=%.1fms | min=%.1fms | max=%.1fms"
            % (self._engine.upper(), n, avg, lo, hi),
            mode=self._engine,
        )
