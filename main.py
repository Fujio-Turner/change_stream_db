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

__version__ = "1.5.0"

import argparse
import asyncio
import gc
import hashlib
import json
import logging
import os
import signal
import ssl
import sys
import time
import threading
from collections import deque
from pathlib import Path

import psutil

import aiohttp
import aiohttp.web
from icecream import ic

from rest import (
    OutputForwarder,
    OutputEndpointDown,
    DeadLetterQueue,
    VALID_OUTPUT_FORMATS,
)
from rest.output_http import check_serialization_library
from rest.changes_http import (
    ShutdownRequested,
    RetryableHTTP,
    ClientHTTPError,
    RedirectHTTPError,
    ServerHTTPError,
    fetch_docs,
    _fetch_single_doc_with_retry,
    _fetch_docs_bulk_get,
    _fetch_docs_individually,
    _build_changes_body,
    _sleep_with_backoff,
    _process_changes_batch,
    _catch_up_normal,
    _consume_continuous_stream,
    _consume_websocket_stream,
    _replay_dead_letter_queue,
    _sleep_or_shutdown,
    _chunked,
)
from rest import determine_method  # re-export for backward compat
from cbl_store import (
    USE_CBL,
    CBLStore,
    CBLMaintenanceScheduler,
    close_db,
    migrate_files_to_cbl,
    migrate_default_to_collections,
)
from pipeline_logging import (
    configure_logging,
    log_event,
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

    def __init__(
        self, src: str, database: str, log_dir: str = "logs", cbl_db_dir: str = ""
    ):
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._labels = f'src="{src}",database="{database}"'
        self._process = psutil.Process()
        self._log_dir = log_dir
        self._cbl_db_dir = cbl_db_dir

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
        self.retry_exhausted_total: int = 0

        # Output by HTTP method (PUT / DELETE)
        self.output_put_total: int = 0
        self.output_delete_total: int = 0
        self.output_put_errors_total: int = 0
        self.output_delete_errors_total: int = 0
        self.output_success_total: int = 0
        self.output_skipped_total: int = 0
        self.dead_letter_total: int = 0
        self.dlq_write_failures_total: int = 0
        self.dlq_pending_count: int = 0
        self.dlq_last_write_epoch: float = 0  # unix timestamp of last DLQ write

        # Batch processing
        self.batches_total: int = 0
        self.batches_failed_total: int = 0

        # Bytes tracking
        self.bytes_received_total: int = 0  # bytes from _changes + bulk_get/GETs
        self.bytes_output_total: int = 0  # bytes sent to output endpoint

        # _changes feed content tracking (always counted, regardless of filter settings)
        self.feed_deletes_seen_total: int = 0  # changes with deleted=true in the feed
        self.feed_removes_seen_total: int = 0  # changes with removed=true in the feed

        # Doc fetch
        self.doc_fetch_requests_total: int = 0
        self.doc_fetch_errors_total: int = 0

        # Mapper (DB mode)
        self.mapper_matched_total: int = 0
        self.mapper_skipped_total: int = 0
        self.mapper_errors_total: int = 0
        self.mapper_ops_total: int = 0

        # DB transaction retry / error classification
        self.db_retries_total: int = 0
        self.db_retry_exhausted_total: int = 0
        self.db_transient_errors_total: int = 0
        self.db_permanent_errors_total: int = 0
        self.db_pool_reconnects_total: int = 0

        # Stream (continuous/websocket)
        self.stream_reconnects_total: int = 0
        self.stream_messages_total: int = 0
        self.stream_parse_errors_total: int = 0

        # Health check probes
        self.health_probes_total: int = 0
        self.health_probe_failures_total: int = 0

        # Checkpoint loads
        self.checkpoint_loads_total: int = 0
        self.checkpoint_load_errors_total: int = 0

        # Gauges (can go up and down)
        self.last_batch_size: int = 0
        self.last_poll_timestamp: float = 0.0
        self.checkpoint_seq: str = "0"
        self.output_endpoint_up: int = 1
        self.active_tasks: int = 0

        # Output response time tracking (for summary) – capped to avoid unbounded growth
        self._output_resp_times: deque[float] = deque(maxlen=10000)

        # Stage timing deques
        self._changes_request_times: deque[float] = deque(maxlen=10000)
        self._batch_processing_times: deque[float] = deque(maxlen=10000)
        self._doc_fetch_times: deque[float] = deque(maxlen=10000)
        self._health_probe_times: deque[float] = deque(maxlen=10000)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + value)

    def set(self, name: str, value) -> None:
        with self._lock:
            setattr(self, name, value)

    def record_output_response_time(self, seconds: float) -> None:
        with self._lock:
            self._output_resp_times.append(seconds)

    def record_changes_request_time(self, seconds: float) -> None:
        with self._lock:
            self._changes_request_times.append(seconds)

    def record_batch_processing_time(self, seconds: float) -> None:
        with self._lock:
            self._batch_processing_times.append(seconds)

    def record_doc_fetch_time(self, seconds: float) -> None:
        with self._lock:
            self._doc_fetch_times.append(seconds)

    def record_health_probe_time(self, seconds: float) -> None:
        with self._lock:
            self._health_probe_times.append(seconds)

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            labels = self._labels

            # Snapshot all timing deques under the lock
            ort = list(self._output_resp_times)
            crt = list(self._changes_request_times)
            bpt = list(self._batch_processing_times)
            dft = list(self._doc_fetch_times)
            hpt = list(self._health_probe_times)

        # Pre-compute sorted arrays and stats for each timing deque
        def _stats(data: list[float]) -> tuple[int, float, list[float]]:
            count = len(data)
            total = sum(data) if data else 0.0
            sorted_data = sorted(data) if data else []
            return count, total, sorted_data

        def _quantile(sorted_data: list[float], q: float) -> float:
            if not sorted_data:
                return 0.0
            idx = int(q * (len(sorted_data) - 1))
            return sorted_data[idx]

        ort_count, ort_sum, ort_sorted = _stats(ort)
        crt_count, crt_sum, crt_sorted = _stats(crt)
        bpt_count, bpt_sum, bpt_sorted = _stats(bpt)
        dft_count, dft_sum, dft_sorted = _stats(dft)
        hpt_count, hpt_sum, hpt_sorted = _stats(hpt)

        lines: list[str] = []

        def _counter(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{{{labels}}} {value}")

        def _gauge(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{labels}}} {value}")

        def _summary(
            name: str,
            help_text: str,
            sorted_data: list[float],
            s_count: int,
            s_sum: float,
        ):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} summary")
            for q in (0.5, 0.9, 0.99):
                lines.append(
                    f'{name}{{{labels},quantile="{q}"}} {_quantile(sorted_data, q):.6f}'
                )
            lines.append(f"{name}_sum{{{labels}}} {s_sum:.6f}")
            lines.append(f"{name}_count{{{labels}}} {s_count}")

        # -- Process info --
        _gauge(
            "changes_worker_uptime_seconds",
            "Time in seconds since the worker started.",
            f"{uptime:.3f}",
        )

        # -- Poll loop --
        _counter(
            "changes_worker_poll_cycles_total",
            "Total number of _changes poll cycles completed.",
            self.poll_cycles_total,
        )
        _counter(
            "changes_worker_poll_errors_total",
            "Total number of _changes poll errors.",
            self.poll_errors_total,
        )
        _gauge(
            "changes_worker_last_poll_timestamp_seconds",
            "Unix timestamp of the last successful _changes poll.",
            f"{self.last_poll_timestamp:.3f}",
        )
        _gauge(
            "changes_worker_last_batch_size",
            "Number of changes in the last batch received.",
            self.last_batch_size,
        )

        # -- Changes --
        _counter(
            "changes_worker_changes_received_total",
            "Total number of changes received from the _changes feed.",
            self.changes_received_total,
        )
        _counter(
            "changes_worker_changes_processed_total",
            "Total number of changes processed and forwarded.",
            self.changes_processed_total,
        )
        _counter(
            "changes_worker_changes_filtered_total",
            "Total number of changes filtered out (deletes + removes).",
            self.changes_filtered_total,
        )
        _counter(
            "changes_worker_changes_deleted_total",
            "Total number of deleted changes filtered out.",
            self.changes_deleted_total,
        )
        _counter(
            "changes_worker_changes_removed_total",
            "Total number of removed changes filtered out.",
            self.changes_removed_total,
        )

        # -- Feed content (always counted, regardless of filter settings) --
        _counter(
            "changes_worker_feed_deletes_seen_total",
            "Total changes with deleted=true seen in the feed.",
            self.feed_deletes_seen_total,
        )
        _counter(
            "changes_worker_feed_removes_seen_total",
            "Total changes with removed=true seen in the feed.",
            self.feed_removes_seen_total,
        )

        # -- Bytes --
        _counter(
            "changes_worker_bytes_received_total",
            "Total bytes received from _changes feed, bulk_get, and individual doc GETs.",
            self.bytes_received_total,
        )
        _counter(
            "changes_worker_bytes_output_total",
            "Total bytes sent to the output endpoint.",
            self.bytes_output_total,
        )

        # -- Doc fetching --
        _counter(
            "changes_worker_docs_fetched_total",
            "Total documents fetched via bulk_get or individual GET.",
            self.docs_fetched_total,
        )
        _counter(
            "changes_worker_doc_fetch_requests_total",
            "Total doc fetch requests (bulk_get or individual batch).",
            self.doc_fetch_requests_total,
        )
        _counter(
            "changes_worker_doc_fetch_errors_total",
            "Total doc fetch errors.",
            self.doc_fetch_errors_total,
        )

        # -- Output --
        _counter(
            "changes_worker_output_requests_total",
            "Total output requests sent to the downstream endpoint.",
            self.output_requests_total,
        )
        _counter(
            "changes_worker_output_errors_total",
            "Total output request errors.",
            self.output_errors_total,
        )

        # Output by HTTP method
        lines.append(
            "# HELP changes_worker_output_requests_by_method_total Output requests broken down by HTTP method."
        )
        lines.append("# TYPE changes_worker_output_requests_by_method_total counter")
        lines.append(
            f'changes_worker_output_requests_by_method_total{{{labels},method="PUT"}} {self.output_put_total}'
        )
        lines.append(
            f'changes_worker_output_requests_by_method_total{{{labels},method="DELETE"}} {self.output_delete_total}'
        )

        lines.append(
            "# HELP changes_worker_output_errors_by_method_total Output errors broken down by HTTP method."
        )
        lines.append("# TYPE changes_worker_output_errors_by_method_total counter")
        lines.append(
            f'changes_worker_output_errors_by_method_total{{{labels},method="PUT"}} {self.output_put_errors_total}'
        )
        lines.append(
            f'changes_worker_output_errors_by_method_total{{{labels},method="DELETE"}} {self.output_delete_errors_total}'
        )

        _counter(
            "changes_worker_output_success_total",
            "Total output requests that succeeded.",
            self.output_success_total,
        )
        _counter(
            "changes_worker_output_skipped_total",
            "Total documents skipped at output (no mapper match or empty ops).",
            self.output_skipped_total,
        )
        _counter(
            "changes_worker_dead_letter_total",
            "Total documents written to the dead letter queue.",
            self.dead_letter_total,
        )
        _counter(
            "changes_worker_dlq_write_failures_total",
            "Total DLQ write failures (data potentially lost).",
            self.dlq_write_failures_total,
        )
        _gauge(
            "changes_worker_dlq_pending_count",
            "Current number of pending entries in the dead letter queue.",
            self.dlq_pending_count,
        )
        _gauge(
            "changes_worker_dlq_last_write_epoch",
            "Unix timestamp of the last DLQ write (0 = never).",
            self.dlq_last_write_epoch,
        )

        _gauge(
            "changes_worker_output_endpoint_up",
            "Whether the output endpoint is reachable (1=up, 0=down).",
            self.output_endpoint_up,
        )

        # Output response time summary
        _summary(
            "changes_worker_output_response_time_seconds",
            "Output HTTP response time in seconds.",
            ort_sorted,
            ort_count,
            ort_sum,
        )

        # -- Checkpoint --
        _counter(
            "changes_worker_checkpoint_saves_total",
            "Total checkpoint save operations.",
            self.checkpoint_saves_total,
        )
        _counter(
            "changes_worker_checkpoint_save_errors_total",
            "Total checkpoint save errors (fell back to local file).",
            self.checkpoint_save_errors_total,
        )
        _counter(
            "changes_worker_checkpoint_loads_total",
            "Total checkpoint load operations.",
            self.checkpoint_loads_total,
        )
        _counter(
            "changes_worker_checkpoint_load_errors_total",
            "Total checkpoint load errors.",
            self.checkpoint_load_errors_total,
        )
        lines.append(
            "# HELP changes_worker_checkpoint_seq Current checkpoint sequence value."
        )
        lines.append("# TYPE changes_worker_checkpoint_seq gauge")
        # Sequence can be a non-numeric string (e.g. "12:34"), expose as info label
        lines.append(
            f'changes_worker_checkpoint_seq{{{labels},seq="{self.checkpoint_seq}"}} 1'
        )

        # -- Retries --
        _counter(
            "changes_worker_retries_total",
            "Total HTTP retry attempts across all requests.",
            self.retries_total,
        )
        _counter(
            "changes_worker_retry_exhausted_total",
            "Total times all retries were exhausted.",
            self.retry_exhausted_total,
        )

        # -- Batches --
        _counter(
            "changes_worker_batches_total",
            "Total batches processed.",
            self.batches_total,
        )
        _counter(
            "changes_worker_batches_failed_total",
            "Total batches that failed (output down).",
            self.batches_failed_total,
        )

        # -- Mapper (DB mode) --
        _counter(
            "changes_worker_mapper_matched_total",
            "Total documents matched by a schema mapper.",
            self.mapper_matched_total,
        )
        _counter(
            "changes_worker_mapper_skipped_total",
            "Total documents skipped (no mapper match).",
            self.mapper_skipped_total,
        )
        _counter(
            "changes_worker_mapper_errors_total",
            "Total mapper errors.",
            self.mapper_errors_total,
        )
        _counter(
            "changes_worker_mapper_ops_total",
            "Total SQL operations generated by mappers.",
            self.mapper_ops_total,
        )

        # -- DB transaction resilience --
        _counter(
            "changes_worker_db_retries_total",
            "Total DB transaction retry attempts.",
            self.db_retries_total,
        )
        _counter(
            "changes_worker_db_retry_exhausted_total",
            "Total times all DB retries were exhausted.",
            self.db_retry_exhausted_total,
        )
        _counter(
            "changes_worker_db_transient_errors_total",
            "Total transient DB errors (connection, deadlock, serialization).",
            self.db_transient_errors_total,
        )
        _counter(
            "changes_worker_db_permanent_errors_total",
            "Total permanent DB errors (constraint, type mismatch).",
            self.db_permanent_errors_total,
        )
        _counter(
            "changes_worker_db_pool_reconnects_total",
            "Total DB connection pool reconnections.",
            self.db_pool_reconnects_total,
        )

        # -- Stream (continuous/websocket) --
        _counter(
            "changes_worker_stream_reconnects_total",
            "Total stream reconnections.",
            self.stream_reconnects_total,
        )
        _counter(
            "changes_worker_stream_messages_total",
            "Total stream messages received.",
            self.stream_messages_total,
        )
        _counter(
            "changes_worker_stream_parse_errors_total",
            "Total stream message parse errors.",
            self.stream_parse_errors_total,
        )

        # -- Health check probes --
        _counter(
            "changes_worker_health_probes_total",
            "Total health check probes sent.",
            self.health_probes_total,
        )
        _counter(
            "changes_worker_health_probe_failures_total",
            "Total health check probe failures.",
            self.health_probe_failures_total,
        )

        # -- Active tasks gauge --
        _gauge(
            "changes_worker_active_tasks",
            "Number of currently active document processing tasks.",
            self.active_tasks,
        )

        # -- Timing summaries --
        _summary(
            "changes_worker_changes_request_time_seconds",
            "Time to complete a _changes HTTP request in seconds.",
            crt_sorted,
            crt_count,
            crt_sum,
        )

        _summary(
            "changes_worker_batch_processing_time_seconds",
            "Time to process a batch of changes in seconds.",
            bpt_sorted,
            bpt_count,
            bpt_sum,
        )

        _summary(
            "changes_worker_doc_fetch_time_seconds",
            "Time to fetch documents (bulk_get or individual) in seconds.",
            dft_sorted,
            dft_count,
            dft_sum,
        )

        _summary(
            "changes_worker_health_probe_time_seconds",
            "Time for a health check probe in seconds.",
            hpt_sorted,
            hpt_count,
            hpt_sum,
        )

        # ── SYSTEM metrics (psutil / gc / threading) ────────────────────
        try:
            proc = self._process
            cpu_times = proc.cpu_times()
            mem_info = proc.memory_info()

            _gauge(
                "changes_worker_process_cpu_percent",
                "Process CPU usage as a percentage of one core.",
                proc.cpu_percent(interval=0),
            )
            _counter(
                "changes_worker_process_cpu_user_seconds_total",
                "User-space CPU seconds consumed by the worker process.",
                f"{cpu_times.user:.3f}",
            )
            _counter(
                "changes_worker_process_cpu_system_seconds_total",
                "Kernel-space CPU seconds consumed by the worker process.",
                f"{cpu_times.system:.3f}",
            )
            _gauge(
                "changes_worker_process_memory_rss_bytes",
                "Resident Set Size of the worker process in bytes.",
                mem_info.rss,
            )
            _gauge(
                "changes_worker_process_memory_vms_bytes",
                "Virtual Memory Size of the worker process in bytes.",
                mem_info.vms,
            )
            _gauge(
                "changes_worker_process_memory_percent",
                "Percentage of system RAM used by the worker process.",
                f"{proc.memory_percent():.2f}",
            )
            _gauge(
                "changes_worker_process_threads",
                "Number of OS threads used by the worker process.",
                proc.num_threads(),
            )
            try:
                _gauge(
                    "changes_worker_process_open_fds",
                    "Number of open file descriptors.",
                    proc.num_fds(),
                )
            except AttributeError:
                pass  # num_fds() not available on Windows

            _gauge(
                "changes_worker_python_threads_active",
                "Number of active Python threads.",
                threading.active_count(),
            )

            # GC stats per generation
            gc_counts = gc.get_count()
            gc_stats = gc.get_stats()
            for gen in range(3):
                _gauge(
                    f"changes_worker_python_gc_gen{gen}_count",
                    f"Number of objects tracked by GC generation {gen}.",
                    gc_counts[gen],
                )
                _counter(
                    f"changes_worker_python_gc_gen{gen}_collections_total",
                    f"Total GC collection runs for generation {gen}.",
                    gc_stats[gen]["collections"],
                )

            # System-wide metrics
            _gauge(
                "changes_worker_system_cpu_count",
                "Number of logical CPU cores on the host.",
                psutil.cpu_count(logical=True),
            )
            _gauge(
                "changes_worker_system_cpu_percent",
                "Host-wide CPU usage percentage.",
                psutil.cpu_percent(interval=0),
            )

            vmem = psutil.virtual_memory()
            _gauge(
                "changes_worker_system_memory_total_bytes",
                "Total physical memory on the host.",
                vmem.total,
            )
            _gauge(
                "changes_worker_system_memory_available_bytes",
                "Available physical memory on the host.",
                vmem.available,
            )
            _gauge(
                "changes_worker_system_memory_used_bytes",
                "Used physical memory on the host.",
                vmem.used,
            )
            _gauge(
                "changes_worker_system_memory_percent",
                "Host memory usage percentage.",
                vmem.percent,
            )

            swap = psutil.swap_memory()
            _gauge(
                "changes_worker_system_swap_total_bytes",
                "Total swap space on the host.",
                swap.total,
            )
            _gauge(
                "changes_worker_system_swap_used_bytes",
                "Used swap space on the host.",
                swap.used,
            )

            try:
                disk = psutil.disk_usage("/")
                _gauge(
                    "changes_worker_system_disk_total_bytes",
                    "Total disk space.",
                    disk.total,
                )
                _gauge(
                    "changes_worker_system_disk_used_bytes",
                    "Used disk space.",
                    disk.used,
                )
                _gauge(
                    "changes_worker_system_disk_free_bytes",
                    "Free disk space.",
                    disk.free,
                )
                _gauge(
                    "changes_worker_system_disk_percent",
                    "Disk usage percentage.",
                    disk.percent,
                )
            except OSError:
                pass

            net = psutil.net_io_counters()
            if net:
                _counter(
                    "changes_worker_system_network_bytes_sent_total",
                    "Total bytes sent over all network interfaces.",
                    net.bytes_sent,
                )
                _counter(
                    "changes_worker_system_network_bytes_recv_total",
                    "Total bytes received over all network interfaces.",
                    net.bytes_recv,
                )
                _counter(
                    "changes_worker_system_network_packets_sent_total",
                    "Total packets sent over all network interfaces.",
                    net.packets_sent,
                )
                _counter(
                    "changes_worker_system_network_packets_recv_total",
                    "Total packets received over all network interfaces.",
                    net.packets_recv,
                )
                _counter(
                    "changes_worker_system_network_errin_total",
                    "Total incoming network errors.",
                    net.errin,
                )
                _counter(
                    "changes_worker_system_network_errout_total",
                    "Total outgoing network errors.",
                    net.errout,
                )

            # Log directory size
            log_dir = self._log_dir
            if log_dir and os.path.isdir(log_dir):
                total_log_bytes = 0
                for dirpath, _, filenames in os.walk(log_dir):
                    for fname in filenames:
                        try:
                            total_log_bytes += os.path.getsize(
                                os.path.join(dirpath, fname)
                            )
                        except OSError:
                            pass
                _gauge(
                    "changes_worker_log_dir_size_bytes",
                    "Total size of the log directory in bytes.",
                    total_log_bytes,
                )

            # CBL database size
            cbl_dir = self._cbl_db_dir
            if cbl_dir and os.path.exists(cbl_dir):
                total_cbl_bytes = 0
                if os.path.isdir(cbl_dir):
                    for dirpath, _, filenames in os.walk(cbl_dir):
                        for fname in filenames:
                            try:
                                total_cbl_bytes += os.path.getsize(
                                    os.path.join(dirpath, fname)
                                )
                            except OSError:
                                pass
                else:
                    try:
                        total_cbl_bytes = os.path.getsize(cbl_dir)
                    except OSError:
                        pass
                _gauge(
                    "changes_worker_cbl_db_size_bytes",
                    "Total size of the Couchbase Lite database in bytes.",
                    total_cbl_bytes,
                )
        except Exception:
            pass  # system metrics are best-effort

        # ── Per-engine / per-job DB metrics ────────────────────────────────
        try:
            from db.db_base import DbMetrics

            db_lines = DbMetrics.render_all()
            if db_lines:
                lines.append("")
                lines.append(db_lines)
        except Exception:
            pass  # db_base may not be loaded if no DB output is configured

        # ── Per-provider / per-job cloud metrics ──────────────────────────
        try:
            from cloud.cloud_base import CloudMetrics

            cloud_lines = CloudMetrics.render_all()
            if cloud_lines:
                lines.append("")
                lines.append(cloud_lines)
        except Exception:
            pass  # cloud_base may not be loaded if no cloud output is configured

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


async def _restart_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """POST /_restart — signal the changes feed to restart with fresh config."""
    restart_event: asyncio.Event | None = request.app.get("restart_event")
    if restart_event is None:
        return aiohttp.web.json_response({"error": "restart not supported"}, status=500)
    # If offline, clear the offline flag so the restart loop resumes
    offline_event: asyncio.Event | None = request.app.get("offline_event")
    if offline_event is not None and offline_event.is_set():
        offline_event.clear()
    log_event(logger, "info", "CONTROL", "restart requested via /_restart endpoint")
    restart_event.set()
    return aiohttp.web.json_response({"ok": True, "message": "restart signal sent"})


async def _shutdown_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """POST /_shutdown — graceful shutdown: stop feed, wait for in-flight
    processing and outputs to finish, then respond.

    CBL database close is handled by main()'s finally block after the
    event loop finishes, ensuring all async generators (which may write
    DLQ entries) complete before the database is closed.

    Behaviour:
      1. Set shutdown_event → _changes loops stop, RetryableHTTP aborts retries.
      2. Wait up to ``drain_timeout_seconds`` for active tasks to finish.
         - If tasks drain in time → checkpoint was NOT advanced past pending
           work, so nothing is lost.
         - If drain times out → remaining docs could not be delivered.
           * If ``dlq_inflight_on_shutdown`` is true the batch handler already
             wrote them to the dead-letter queue (CBL or .jsonl) so they can
             be reprocessed later even if a newer revision arrives on the feed.
           * If false the checkpoint was held back; the same docs will be
             re-fetched on the next startup.
      3. Return summary JSON.
    """
    shutdown_event: asyncio.Event | None = request.app.get("shutdown_event")
    if shutdown_event is None:
        return aiohttp.web.json_response(
            {"error": "shutdown not supported"}, status=500
        )

    log_event(
        logger, "info", "CONTROL", "graceful shutdown requested via /_shutdown endpoint"
    )

    # Read shutdown config from the app (set by start_metrics_server)
    shutdown_cfg: dict = request.app.get("shutdown_cfg", {})
    drain_timeout = shutdown_cfg.get("drain_timeout_seconds", 60)
    dlq_policy = shutdown_cfg.get("dlq_inflight_on_shutdown", False)

    # 1. Signal the _changes feed loops & retry loops to stop
    shutdown_event.set()

    # 2. Wait for all in-flight processing / output tasks to drain
    metrics: MetricsCollector | None = request.app.get("metrics")
    drained = True
    tasks_remaining = 0
    if metrics is not None:
        t0 = time.monotonic()
        while metrics.active_tasks > 0:
            elapsed = time.monotonic() - t0
            if elapsed > drain_timeout:
                tasks_remaining = metrics.active_tasks
                drained = False
                log_event(
                    logger,
                    "warn",
                    "CONTROL",
                    "shutdown drain timed out after %ds with %d tasks still active"
                    % (drain_timeout, tasks_remaining),
                )
                break
            log_event(
                logger,
                "debug",
                "CONTROL",
                "waiting for %d active tasks to finish" % metrics.active_tasks,
            )
            await asyncio.sleep(0.5)
        if drained:
            log_event(logger, "info", "CONTROL", "all active tasks drained")

    # 3. Build response summary
    summary: dict = {
        "ok": True,
        "drained": drained,
        "drain_timeout_seconds": drain_timeout,
        "dlq_inflight_on_shutdown": dlq_policy,
    }
    if not drained:
        summary["tasks_remaining"] = tasks_remaining
        if dlq_policy:
            summary["message"] = (
                "shutdown complete – drain timed out, %d in-flight docs written to dead-letter queue, "
                "checkpoint was NOT advanced past them" % tasks_remaining
            )
        else:
            summary["message"] = (
                "shutdown complete – drain timed out, %d in-flight docs NOT delivered, "
                "checkpoint was NOT advanced – they will be re-fetched on next startup"
                % tasks_remaining
            )
    else:
        summary["message"] = (
            "shutdown complete – feeds stopped, outputs drained, database closed"
        )

    log_event(
        logger, "info", "CONTROL", "graceful shutdown complete: %s" % summary["message"]
    )
    return aiohttp.web.json_response(summary)


async def _offline_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """POST /_offline — pause the changes feed. Worker stays alive."""
    offline_event: asyncio.Event | None = request.app.get("offline_event")
    restart_event: asyncio.Event | None = request.app.get("restart_event")
    if offline_event is None or restart_event is None:
        return aiohttp.web.json_response({"error": "offline not supported"}, status=500)
    if offline_event.is_set():
        return aiohttp.web.json_response({"ok": True, "message": "already offline"})
    log_event(logger, "info", "CONTROL", "offline requested via /_offline endpoint")
    offline_event.set()
    restart_event.set()  # break the current feed loop
    return aiohttp.web.json_response({"ok": True, "message": "going offline"})


async def _online_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """POST /_online — resume the changes feed with current config."""
    offline_event: asyncio.Event | None = request.app.get("offline_event")
    if offline_event is None:
        return aiohttp.web.json_response({"error": "online not supported"}, status=500)
    if not offline_event.is_set():
        return aiohttp.web.json_response({"ok": True, "message": "already online"})
    log_event(logger, "info", "CONTROL", "online requested via /_online endpoint")
    offline_event.clear()
    return aiohttp.web.json_response({"ok": True, "message": "going online"})


async def _status_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """GET /_status — return worker online/offline state."""
    offline_event: asyncio.Event | None = request.app.get("offline_event")
    is_offline = offline_event.is_set() if offline_event is not None else False
    return aiohttp.web.json_response({"online": not is_offline})


async def start_metrics_server(
    metrics: MetricsCollector,
    host: str,
    port: int,
    restart_event: asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
    offline_event: asyncio.Event | None = None,
    cbl_scheduler: CBLMaintenanceScheduler | None = None,
    shutdown_cfg: dict | None = None,
) -> aiohttp.web.AppRunner:
    """Start a lightweight HTTP server that serves /_metrics in Prometheus format."""
    from aiohttp import web

    app = web.Application()
    app["metrics"] = metrics
    app["shutdown_cfg"] = shutdown_cfg or {}
    if restart_event is not None:
        app["restart_event"] = restart_event
    if shutdown_event is not None:
        app["shutdown_event"] = shutdown_event
    if offline_event is not None:
        app["offline_event"] = offline_event
    if cbl_scheduler is not None:
        app["cbl_scheduler"] = cbl_scheduler
    app.router.add_get("/_metrics", _metrics_handler)
    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_post("/_restart", _restart_handler)
    app.router.add_post("/_shutdown", _shutdown_handler)
    app.router.add_post("/_offline", _offline_handler)
    app.router.add_post("/_online", _online_handler)
    app.router.add_get("/_status", _status_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log_event(
        logger, "info", "METRICS", "metrics server listening", host=host, port=port
    )
    return runner


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_config(path: str | None = None) -> dict:
    """Load config from CBL (source of truth) or fall back to config.json.

    When CBL is available, config.json is only used as a seed on the very
    first startup.  After that, all config changes go through CBL via the
    Admin UI.  To re-seed from config.json, delete the CBL volume.
    """
    if USE_CBL:
        store = CBLStore()
        cfg = store.load_config()
        if cfg:
            logger.info("Config loaded from CBL (config.json is ignored)")
            ic(cfg)
            return cfg
        # First run: seed from file → CBL
        if path:
            with open(path) as f:
                cfg = json.load(f)
            store.save_config(cfg)
            logger.info("First start — seeded config from %s into CBL", path)
            ic(cfg)
            return cfg
    # Fallback: no CBL — read from file directly
    with open(path or "config.json") as f:
        cfg = json.load(f)
    ic(cfg)
    return cfg


def _ensure_full_logging_config(cfg: dict) -> None:
    """Upgrade legacy ``{"level": "DEBUG"}`` logging config to full SG-style format."""
    logging_cfg = cfg.get("logging", {})
    if "console" in logging_cfg or "file" in logging_cfg:
        return  # already in full format

    old_level = logging_cfg.get("level", "info").lower()

    cfg["logging"] = {
        "redaction_level": "partial",
        "console": {
            "enabled": True,
            "log_level": old_level,
            "log_keys": ["*"],
            "key_levels": {},
            "color_enabled": False,
        },
        "file": {
            "enabled": True,
            "path": "logs/changes_worker.log",
            "log_level": old_level,
            "log_keys": ["*"],
            "key_levels": {},
            "rotation": {
                "max_size": 100,
                "max_age": 7,
                "rotated_logs_size_limit": 1024,
            },
        },
    }

    if USE_CBL:
        CBLStore().save_config(cfg)


VALID_SOURCES = ("sync_gateway", "app_services", "edge_server", "couchdb")


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
        errors.append(f"gateway.src must be one of {VALID_SOURCES}, got '{src}'")
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

    # CouchDB does not have scopes/collections
    if src == "couchdb" and (gw.get("scope") or gw.get("collection")):
        warnings.append(
            "CouchDB does not support scopes or collections – "
            "gateway.scope and gateway.collection will be ignored"
        )

    # -- auth ------------------------------------------------------------------
    auth_method = auth_cfg.get("method", "basic")

    if auth_method == "bearer" and src == "edge_server":
        errors.append(
            "auth.method=bearer is not supported by Edge Server – "
            "use 'basic' or 'session' instead"
        )

    if auth_method == "session" and src == "couchdb":
        errors.append(
            "auth.method=session is not supported by CouchDB – "
            "use 'basic' or 'bearer' instead"
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

    if feed_type == "websocket" and src == "couchdb":
        errors.append(
            "changes_feed.feed_type=websocket is not supported by CouchDB – "
            "use 'longpoll', 'continuous', or 'eventsource'"
        )

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
        "couchdb": ("longpoll", "continuous", "eventsource", "normal"),
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
    if version_type != "rev" and src == "couchdb":
        errors.append(
            f"changes_feed.version_type='{version_type}' is not supported by CouchDB – "
            "CouchDB does not support the version_type parameter"
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
    if not include_docs and src == "couchdb":
        warnings.append(
            "changes_feed.include_docs=false with CouchDB – "
            "docs will be fetched via POST /{db}/_bulk_get"
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
    if out_mode not in ("stdout", "http", "db"):
        errors.append(
            f"output.mode must be 'stdout', 'http', or 'db', got '{out_mode}'"
        )
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
        valid_methods = ("PUT", "POST", "PATCH", "DELETE")
        write_method = out_cfg.get("write_method", "PUT").upper()
        delete_method = out_cfg.get("delete_method", "DELETE").upper()
        if write_method not in valid_methods:
            errors.append(
                f"output.write_method must be one of {valid_methods}, got '{write_method}'"
            )
        if delete_method not in valid_methods:
            errors.append(
                f"output.delete_method must be one of {valid_methods}, got '{delete_method}'"
            )

        req_timeout = out_cfg.get("request_timeout_seconds", 30)
        if req_timeout <= 0:
            errors.append(
                f"output.request_timeout_seconds must be > 0, got {req_timeout}"
            )

        out_auth_method = out_cfg.get("target_auth", {}).get("method", "none")
        if out_auth_method == "basic":
            if not out_cfg.get("target_auth", {}).get("username"):
                errors.append(
                    "output.target_auth.username is required when target_auth.method=basic"
                )
            if not out_cfg.get("target_auth", {}).get("password"):
                errors.append(
                    "output.target_auth.password is required when target_auth.method=basic"
                )
        elif out_auth_method == "session":
            if not out_cfg.get("target_auth", {}).get("session_cookie"):
                errors.append(
                    "output.target_auth.session_cookie is required when target_auth.method=session"
                )
        elif out_auth_method == "bearer":
            if not out_cfg.get("target_auth", {}).get("bearer_token"):
                errors.append(
                    "output.target_auth.bearer_token is required when target_auth.method=bearer"
                )

        out_retry = out_cfg.get("retry", {})
        out_max_retries = out_retry.get("max_retries", 3)
        if out_max_retries < 0:
            errors.append(
                f"output.retry.max_retries must be >= 0, got {out_max_retries}"
            )

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
        if (
            not isinstance(metrics_port, int)
            or metrics_port < 1
            or metrics_port > 65535
        ):
            errors.append(
                f"metrics.port must be an integer between 1 and 65535, got {metrics_port}"
            )

    return src, warnings, errors


def build_base_url(gw: dict) -> str:
    """Build the keyspace URL: {url}/{db}.{scope}.{collection}"""
    base = gw["url"].rstrip("/")
    db = gw["database"]
    src = gw.get("src", "sync_gateway")
    # CouchDB has no scopes/collections concept
    if src == "couchdb":
        return f"{base}/{db}"
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
            logger.warning(
                "Bearer token auth is not supported by Edge Server – falling back to basic"
            )
        else:
            headers["Authorization"] = f"Bearer {auth_cfg['bearer_token']}"
    elif method == "session":
        if src == "couchdb":
            logger.warning(
                "Session cookie auth is not supported by CouchDB – falling back to basic"
            )
        else:
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
        self._initial_sync_done: bool = False

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

        self._metrics = None

    def set_metrics(self, metrics: "MetricsCollector | None") -> None:
        self._metrics = metrics

    @property
    def local_doc_path(self) -> str:
        """Returns the REST path segment: _local/checkpoint-{uuid}"""
        return f"_local/{self._local_doc_id}"

    @property
    def seq(self) -> str:
        return self._seq

    @property
    def initial_sync_done(self) -> bool:
        return self._initial_sync_done

    # -- SG-backed load/save ---------------------------------------------------

    async def load(
        self,
        http: "RetryableHTTP",
        base_url: str,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> str:
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
            raw_isd = data.get("initial_sync_done", None)
            if raw_isd is None:
                self._initial_sync_done = self._seq != "0"
            else:
                self._initial_sync_done = bool(raw_isd)
            log_event(
                logger,
                "info",
                "CHECKPOINT",
                "checkpoint loaded",
                operation="SELECT",
                storage="sg",
            )
            log_event(
                logger,
                "debug",
                "CHECKPOINT",
                "checkpoint detail",
                operation="SELECT",
                seq=self._seq,
                doc_id=self._local_doc_id,
                storage="sg",
            )
            if self._metrics:
                self._metrics.inc("checkpoint_loads_total")
        except ClientHTTPError as exc:
            if exc.status == 404:
                log_event(
                    logger,
                    "info",
                    "CHECKPOINT",
                    "no existing checkpoint on SG – starting from 0",
                    operation="SELECT",
                    storage="sg",
                )
                self._seq = "0"
            else:
                log_event(
                    logger,
                    "warn",
                    "CHECKPOINT",
                    "checkpoint load fell back to local storage",
                    operation="SELECT",
                    status=exc.status,
                    storage="fallback",
                )
                self._seq = self._load_fallback()
                if self._metrics:
                    self._metrics.inc("checkpoint_loads_total")
                    self._metrics.inc("checkpoint_load_errors_total")
        except Exception as exc:
            log_event(
                logger,
                "warn",
                "CHECKPOINT",
                "checkpoint load fell back to local storage: %s" % exc,
                operation="SELECT",
                storage="fallback",
            )
            self._seq = self._load_fallback()
            if self._metrics:
                self._metrics.inc("checkpoint_loads_total")
                self._metrics.inc("checkpoint_load_errors_total")

        return self._seq

    async def save(
        self,
        seq: str,
        http: "RetryableHTTP",
        base_url: str,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> None:
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
                "initial_sync_done": self._initial_sync_done,
            }
            if self._rev:
                body["_rev"] = self._rev

            url = f"{base_url}/{self.local_doc_path}"
            ic("checkpoint save", url, seq, self._internal)
            try:
                req_headers = {**headers, "Content-Type": "application/json"}
                resp = await http.request(
                    "PUT", url, json=body, auth=auth, headers=req_headers
                )
                resp_data = await resp.json()
                resp.release()
                self._rev = resp_data.get("rev", self._rev)
                log_event(
                    logger,
                    "info",
                    "CHECKPOINT",
                    "checkpoint saved",
                    operation="UPDATE",
                    storage="sg",
                )
                log_event(
                    logger,
                    "debug",
                    "CHECKPOINT",
                    "checkpoint save detail",
                    operation="UPDATE",
                    seq=seq,
                    doc_id=self._local_doc_id,
                    storage="sg",
                )
            except Exception as exc:
                log_event(
                    logger,
                    "warn",
                    "CHECKPOINT",
                    "checkpoint save fell back to local storage: %s" % exc,
                    operation="UPDATE",
                    seq=seq,
                    storage="fallback",
                )
                self._save_fallback(seq)
                if self._metrics:
                    self._metrics.inc("checkpoint_save_errors_total")

    # -- Local file fallback ---------------------------------------------------

    def _load_fallback(self) -> str:
        if USE_CBL:
            data = CBLStore().load_checkpoint(self._uuid)
            if data:
                seq = data.get("SGs_Seq", "0")
                raw_isd = data.get("initial_sync_done", None)
                if raw_isd is None:
                    self._initial_sync_done = seq != "0"
                else:
                    self._initial_sync_done = bool(raw_isd)
                ic("checkpoint loaded from CBL", seq)
                return seq
            return "0"
        # Original file fallback
        if self._fallback_path.exists():
            data = json.loads(self._fallback_path.read_text())
            seq = str(data.get("SGs_Seq", data.get("last_seq", "0")))
            raw_isd = data.get("initial_sync_done", None)
            if raw_isd is None:
                self._initial_sync_done = seq != "0"
            else:
                self._initial_sync_done = bool(raw_isd)
            ic("checkpoint loaded from file", seq)
            return seq
        return "0"

    def _save_fallback(self, seq: str) -> None:
        if USE_CBL:
            CBLStore().save_checkpoint(self._uuid, seq, self._client_id, self._internal)
            ic("checkpoint saved to CBL", seq)
            return
        # Original file fallback
        self._fallback_path.write_text(
            json.dumps(
                {
                    "SGs_Seq": seq,
                    "time": int(time.time()),
                    "remote": self._internal,
                    "initial_sync_done": self._initial_sync_done,
                }
            )
        )
        ic("checkpoint saved to file", seq)


# ---------------------------------------------------------------------------
# Core: changes feed loop
# ---------------------------------------------------------------------------


async def poll_changes(
    cfg: dict,
    src: str,
    shutdown_event: asyncio.Event,
    metrics: MetricsCollector | None = None,
    restart_event: asyncio.Event | None = None,
) -> None:
    gw = cfg["gateway"]
    auth_cfg = cfg["auth"]
    feed_cfg = cfg["changes_feed"]
    proc_cfg = cfg["processing"]
    out_cfg = cfg["output"]
    retry_cfg = cfg.get("retry", {})

    log_event(logger, "info", "PROCESSING", "source type: %s" % src)

    # Combine shutdown + restart into a single stop_event so all inner loops
    # (catch-up, continuous, websocket, longpoll) break on either signal.
    stop_event = asyncio.Event()

    async def _watch_events() -> None:
        waiters = [asyncio.ensure_future(shutdown_event.wait())]
        if restart_event is not None:
            waiters.append(asyncio.ensure_future(restart_event.wait()))
        done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        stop_event.set()
        for f in pending:
            f.cancel()

    watcher_task = asyncio.create_task(_watch_events())

    base_url = build_base_url(gw)
    ssl_ctx = build_ssl_context(gw)
    basic_auth = build_basic_auth(auth_cfg)
    auth_headers = build_auth_headers(auth_cfg, src)

    channels = feed_cfg.get("channels", [])
    checkpoint = Checkpoint(cfg.get("checkpoint", {}), gw, channels)
    if metrics:
        checkpoint.set_metrics(metrics)

    # Session-level timeout is kept loose; the _changes request uses its own.
    timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
    connector = aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else aiohttp.TCPConnector()

    # Per-request timeout for _changes calls.
    # since=0 can return 100K+ rows and take minutes, so this must be much
    # higher than a typical 30-75s HTTP timeout.  Default 300s (5 min).
    changes_http_timeout = aiohttp.ClientTimeout(
        total=feed_cfg.get("http_timeout_seconds", 300),
    )

    max_concurrent = proc_cfg.get("max_concurrent", 20)
    dry_run = proc_cfg.get("dry_run", False)
    semaphore = asyncio.Semaphore(max_concurrent)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        http = RetryableHTTP(session, retry_cfg)
        if metrics:
            http.set_metrics(metrics)
        http.set_shutdown_event(stop_event)

        output_mode = out_cfg.get("mode", "stdout")
        db_output = None  # track DB forwarder for cleanup
        cloud_output = None  # track cloud forwarder for cleanup

        if output_mode == "db":
            db_engine = out_cfg.get("db", {}).get("engine", "postgres")
            if db_engine == "postgres":
                from db.db_postgres import PostgresOutputForwarder

                output = PostgresOutputForwarder(out_cfg, dry_run, metrics=metrics)
            elif db_engine == "mysql":
                from db.db_mysql import MySQLOutputForwarder

                output = MySQLOutputForwarder(out_cfg, dry_run, metrics=metrics)
            elif db_engine == "mssql":
                from db.db_mssql import MSSQLOutputForwarder

                output = MSSQLOutputForwarder(out_cfg, dry_run, metrics=metrics)
            elif db_engine == "oracle":
                from db.db_oracle import OracleOutputForwarder

                output = OracleOutputForwarder(out_cfg, dry_run, metrics=metrics)
            else:
                raise ValueError(f"Unsupported db engine: {db_engine}")
            await output.connect()
            db_output = output
            log_event(
                logger, "info", "OUTPUT", f"database output ready (engine={db_engine})"
            )
        elif output_mode in ("s3", "gcs", "azure"):
            from cloud import create_cloud_output

            output = create_cloud_output(out_cfg, dry_run, metrics=metrics)
            await output.connect()
            cloud_output = output
            log_event(
                logger, "info", "OUTPUT", f"cloud output ready (provider={output_mode})"
            )
        else:
            output = OutputForwarder(
                session,
                out_cfg,
                dry_run,
                metrics=metrics,
                build_basic_auth_fn=build_basic_auth,
                build_auth_headers_fn=build_auth_headers,
                retryable_http_cls=RetryableHTTP,
            )
            # Make output retries shutdown-aware
            if hasattr(output, "_http") and output._http is not None:
                output._http.set_shutdown_event(shutdown_event)

        dlq = DeadLetterQueue(
            out_cfg.get("dead_letter_path", ""),
            dlq_cfg=out_cfg.get("dlq"),
        )
        every_n_docs = cfg.get("checkpoint", {}).get("every_n_docs", 0)

        # If output is HTTP, verify the endpoint is reachable before starting
        if output_mode == "http":
            if not await output.test_reachable():
                if out_cfg.get("halt_on_failure", True):
                    log_event(
                        logger,
                        "error",
                        "OUTPUT",
                        "output endpoint unreachable at startup – aborting",
                    )
                    return
                else:
                    log_event(
                        logger,
                        "warn",
                        "OUTPUT",
                        "output endpoint unreachable at startup – continuing (halt_on_failure=false)",
                    )
            # Start periodic heartbeat if configured
            await output.start_heartbeat(stop_event)

        # Load checkpoint from SG _local doc (CBL-style)
        since = feed_cfg.get("since", "0")
        if since == "0" and cfg.get("checkpoint", {}).get("enabled", True):
            since = await checkpoint.load(http, base_url, basic_auth, auth_headers)

        # ── Replay dead-letter queue before processing new changes ────
        if dlq.enabled and not stop_event.is_set():
            dlq_summary = await _replay_dead_letter_queue(
                dlq,
                output,
                metrics,
                shutdown_event,
                current_target_url=out_cfg.get("target_url", ""),
            )
            if dlq_summary["total"] > 0:
                log_event(logger, "info", "DLQ", "DLQ replay summary: %s" % dlq_summary)
            # Update DLQ pending count gauge after replay
            if metrics:
                pending = dlq.list_pending()
                metrics.set("dlq_pending_count", len(pending))

        throttle = feed_cfg.get("throttle_feed", 0)

        # Initial sync: when starting from since=0 (or resuming an
        # interrupted initial pull), optimise the _changes feed:
        #   Couchbase: active_only=true so deleted/removed are excluded
        #   CouchDB:   no active_only support → ignore deletes in processing
        # In both cases: include_docs=false.
        #
        # optimize_initial_sync=true  → chunked with limit (faster for
        #   huge feeds but may miss deletes between chunks)
        # optimize_initial_sync=false (default) → single large request,
        #   no limit, long timeout — simpler and no consistency gap
        requested_since = feed_cfg.get("since", "0")
        initial_sync = requested_since == "0" and not checkpoint.initial_sync_done
        optimize_initial = feed_cfg.get("optimize_initial_sync", False)
        if initial_sync:
            if optimize_initial:
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "initial sync mode (optimized/chunked) – %s, "
                    "include_docs=false, limit=%d"
                    % (
                        "active_only=true"
                        if src != "couchdb"
                        else "ignoring deletes/removes (CouchDB)",
                        feed_cfg.get("continuous_catchup_limit", 10000),
                    ),
                )
            else:
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "initial sync mode – %s, include_docs=false, "
                    "single request (http_timeout=%ds)"
                    % (
                        "active_only=true"
                        if src != "couchdb"
                        else "ignoring deletes/removes (CouchDB)",
                        feed_cfg.get("http_timeout_seconds", 300),
                    ),
                )

        # Source-specific feed type validation
        feed_type = feed_cfg.get("feed_type", "longpoll")
        if src == "edge_server" and feed_type == "websocket":
            logger.warning(
                "Edge Server does not support feed=websocket, falling back to longpoll"
            )
            feed_type = "longpoll"
        if src != "edge_server" and feed_type == "sse":
            logger.warning(
                "SSE feed is only supported by Edge Server, falling back to longpoll"
            )
            feed_type = "longpoll"
        if src == "couchdb" and feed_type == "websocket":
            logger.warning(
                "CouchDB does not support feed=websocket, falling back to longpoll"
            )
            feed_type = "longpoll"
        if src == "couchdb" and feed_type == "sse":
            logger.warning(
                "CouchDB does not support feed=sse, use feed=eventsource instead"
            )
            feed_type = "eventsource"

        # Edge Server caps timeout at 900000ms (15 min)
        timeout_ms = feed_cfg.get("timeout_ms", 60000)
        if src == "edge_server" and timeout_ms > 900000:
            logger.warning(
                "Edge Server max timeout is 900000ms – clamping from %d", timeout_ms
            )
            timeout_ms = 900000

        changes_url = f"{base_url}/_changes"

        # Shared kwargs for _process_changes_batch / catch-up / continuous
        shutdown_cfg = cfg.get("shutdown", {})
        batch_kwargs = dict(
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
        )

        # Log replication settings at startup
        log_event(
            logger,
            "info",
            "CHANGES",
            "replication config: feed=%s, active_only=%s, include_docs=%s, "
            "since=%s, initial_sync=%s, initial_sync_done=%s, "
            "optimize_initial_sync=%s"
            % (
                feed_type,
                feed_cfg.get("active_only", False),
                feed_cfg.get("include_docs", False),
                since,
                initial_sync,
                checkpoint.initial_sync_done,
                optimize_initial,
            ),
        )

        try:
            # ── Continuous mode: 2-phase catch-up then stream ────────────────
            if feed_type == "continuous":
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "feed mode: continuous (catch-up → stream)",
                )
                while not stop_event.is_set():
                    since = await _catch_up_normal(
                        since=since,
                        changes_url=changes_url,
                        retry_cfg=retry_cfg,
                        shutdown_event=stop_event,
                        timeout_ms=timeout_ms,
                        changes_http_timeout=changes_http_timeout,
                        initial_sync=initial_sync,
                        **batch_kwargs,
                    )
                    initial_sync = False
                    if stop_event.is_set():
                        break
                    since = await _consume_continuous_stream(
                        since=since,
                        changes_url=changes_url,
                        retry_cfg=retry_cfg,
                        session=session,
                        shutdown_event=stop_event,
                        timeout_ms=timeout_ms,
                        **batch_kwargs,
                    )
                return

            # ── WebSocket mode: catch-up then stream via ws:// ───────────────
            if feed_type == "websocket":
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "feed mode: websocket (catch-up → ws stream)",
                )
                # Phase 1: catch up using normal HTTP requests
                since = await _catch_up_normal(
                    since=since,
                    changes_url=changes_url,
                    retry_cfg=retry_cfg,
                    shutdown_event=stop_event,
                    timeout_ms=timeout_ms,
                    changes_http_timeout=changes_http_timeout,
                    initial_sync=initial_sync,
                    **batch_kwargs,
                )
                initial_sync = False
                if not stop_event.is_set():
                    # Phase 2: switch to WebSocket stream
                    since = await _consume_websocket_stream(
                        since=since,
                        changes_url=changes_url,
                        retry_cfg=retry_cfg,
                        session=session,
                        shutdown_event=stop_event,
                        timeout_ms=timeout_ms,
                        **batch_kwargs,
                    )
                return

            # ── Polled mode (longpoll / normal / sse) ────────────────────────
            while not stop_event.is_set():
                # During initial sync use feed=normal so the server
                # returns immediately with a limited result set instead
                # of blocking on longpoll.
                effective_feed = "normal" if initial_sync else feed_type
                body_payload = _build_changes_body(
                    feed_cfg,
                    src,
                    since,
                    effective_feed,
                    timeout_ms,
                    active_only_override=(
                        True if initial_sync and src != "couchdb" else None
                    ),
                    include_docs_override=False if initial_sync else None,
                )
                # throttle_feed overrides limit – eat the feed one bite at a time
                if throttle > 0:
                    body_payload["limit"] = throttle
                elif initial_sync and optimize_initial:
                    # Chunked initial sync: page through the feed.
                    body_payload["limit"] = feed_cfg.get(
                        "continuous_catchup_limit", 10000
                    )
                elif feed_cfg.get("limit", 0) > 0:
                    body_payload["limit"] = feed_cfg["limit"]

                ic(changes_url, body_payload, since)
                log_event(
                    logger,
                    "info",
                    "CHANGES",
                    "polling _changes (since=%s, feed=%s)" % (since, feed_type),
                )

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
                        metrics.record_changes_request_time(
                            time.monotonic() - t0_changes
                        )
                    resp.release()
                except (ClientHTTPError, RedirectHTTPError) as exc:
                    logger.error("Non-retryable error polling _changes: %s", exc)
                    if metrics:
                        metrics.inc("poll_errors_total")
                    break
                except (ConnectionError, ServerHTTPError, asyncio.TimeoutError) as exc:
                    logger.error("Retries exhausted polling _changes: %s", exc)
                    if metrics:
                        metrics.inc("poll_errors_total")
                    await _sleep_or_shutdown(
                        feed_cfg.get("poll_interval_seconds", 10), stop_event
                    )
                    continue

                results = body.get("results", [])
                last_seq = body.get("last_seq", since)
                ic(len(results), last_seq)

                since, output_failed = await _process_changes_batch(
                    results,
                    str(last_seq),
                    since,
                    initial_sync=initial_sync,
                    **batch_kwargs,
                )

                if output_failed:
                    logger.warning(
                        "Waiting %ds before retrying (checkpoint held at since=%s)",
                        feed_cfg.get("poll_interval_seconds", 10),
                        since,
                    )
                    await _sleep_or_shutdown(
                        feed_cfg.get("poll_interval_seconds", 10), stop_event
                    )
                    continue

                if not results:
                    if initial_sync:
                        initial_sync = False
                        checkpoint._initial_sync_done = True
                        await checkpoint.save(
                            since, http, base_url, basic_auth, auth_headers
                        )
                        log_event(
                            logger,
                            "info",
                            "CHANGES",
                            "initial sync complete – reverting to config settings",
                        )
                    await _sleep_or_shutdown(
                        feed_cfg.get("poll_interval_seconds", 10), stop_event
                    )
                    continue

                # When throttling: if we got a full batch there are more rows
                # waiting — loop immediately for the next bite. Only sleep once
                # we get a partial batch (caught up).
                if throttle > 0 and len(results) >= throttle:
                    logger.info(
                        "Throttle: got full batch (%d), fetching next bite immediately",
                        len(results),
                    )
                    continue

                await _sleep_or_shutdown(
                    feed_cfg.get("poll_interval_seconds", 10), stop_event
                )
        finally:
            watcher_task.cancel()
            await output.stop_heartbeat() if hasattr(output, "stop_heartbeat") else None
            if db_output is not None:
                await db_output.close()
            if cloud_output is not None:
                await cloud_output.close()


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
        print(f"\n{'=' * 60}")
        print(f"  Source type:           {src_label}")
        print(f"  Testing connection to: {root_url}")
        print(f"  Keyspace:              {base_url}")
        print(f"  Auth method:           {auth_cfg.get('method', 'basic')}")
        print(f"{'=' * 60}\n")

        try:
            resp = await http.request(
                "GET", f"{root_url}/", auth=basic_auth, headers=auth_headers
            )
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
            resp = await http.request(
                "GET", f"{base_url}/", auth=basic_auth, headers=auth_headers
            )
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
                "POST",
                f"{base_url}/_changes",
                json={"since": "0", "limit": 1},
                auth=basic_auth,
                headers={**auth_headers, "Content-Type": "application/json"},
            )
            body = await resp.json()
            resp.release()
            last_seq = body.get("last_seq", "?")
            n_results = len(body.get("results", []))
            print(
                f"  [✓] _changes endpoint OK  (last_seq={last_seq}, sample_results={n_results})"
            )
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
            output = OutputForwarder(
                session,
                out_cfg,
                dry_run=False,
                build_basic_auth_fn=build_basic_auth,
                build_auth_headers_fn=build_auth_headers,
                retryable_http_cls=RetryableHTTP,
            )
            if await output.test_reachable():
                print(
                    f"  [✓] Output endpoint reachable ({out_cfg.get('target_url', '')})"
                )
            else:
                print(
                    f"  [✗] Output endpoint UNREACHABLE ({out_cfg.get('target_url', '')})"
                )
                ok = False
        else:
            print(f"  [–] Output mode=stdout (no endpoint to check)")

    print(f"\n{'=' * 60}")
    if ok:
        print("  Result: ALL CHECKS PASSED ✓")
    else:
        print("  Result: SOME CHECKS FAILED ✗")
    print(f"{'=' * 60}\n")
    return ok


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Couchbase _changes feed worker")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument(
        "--test", action="store_true", help="Test connectivity and exit"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    _ensure_full_logging_config(cfg)
    configure_logging(cfg.get("logging", {}))

    log_event(
        logger,
        "info",
        "PROCESSING",
        "changes_worker v%s starting (CBL=%s)" % (__version__, USE_CBL),
    )

    # Run migrations after logging is configured so we can see output
    if USE_CBL:
        migrate_files_to_cbl(args.config)
        migrate_default_to_collections()

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
        logger.error("Fix the errors above in %s and try again.", args.config)
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
    restart_event = asyncio.Event()
    offline_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    # ── CBL maintenance scheduler ───────────────────────────────────────
    cbl_scheduler: CBLMaintenanceScheduler | None = None
    if USE_CBL:
        # Apply CBL config (db_dir / db_name) before any DB access
        cbl_cfg = cfg.get("couchbase_lite", {})
        # Backward compat: fall back to legacy "cbl_maintenance" key
        maint_cfg = cbl_cfg.get("maintenance", cfg.get("cbl_maintenance", {}))
        if cbl_cfg.get("db_dir") or cbl_cfg.get("db_name"):
            from cbl_store import configure_cbl

            configure_cbl(cbl_cfg.get("db_dir"), cbl_cfg.get("db_name"))
        if maint_cfg.get("enabled", True):
            interval = maint_cfg.get("interval_hours", 24)
            cbl_scheduler = CBLMaintenanceScheduler(interval_hours=interval)
            cbl_scheduler.start()

    # ── Metrics server ───────────────────────────────────────────────────
    metrics_cfg = cfg.get("metrics", {})
    metrics: MetricsCollector | None = None
    metrics_runner: aiohttp.web.AppRunner | None = None

    if metrics_cfg.get("enabled", False):
        database = cfg.get("gateway", {}).get("database", "")
        log_dir = (
            cfg.get("logging", {})
            .get("file", {})
            .get("path", "logs/changes_worker.log")
        )
        log_dir = os.path.dirname(log_dir) or "logs"
        cbl_db_dir = ""
        if USE_CBL:
            from cbl_store import CBL_DB_DIR, CBL_DB_NAME

            cbl_db_dir = os.path.join(CBL_DB_DIR, f"{CBL_DB_NAME}.cblite2")
        metrics = MetricsCollector(
            src, database, log_dir=log_dir, cbl_db_dir=cbl_db_dir
        )

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        if metrics is not None:
            metrics_host = metrics_cfg.get("host", "0.0.0.0")
            metrics_port = metrics_cfg.get("port", 9090)
            metrics_runner = loop.run_until_complete(
                start_metrics_server(
                    metrics,
                    metrics_host,
                    metrics_port,
                    restart_event=restart_event,
                    shutdown_event=shutdown_event,
                    offline_event=offline_event,
                    cbl_scheduler=cbl_scheduler,
                    shutdown_cfg=cfg.get("shutdown", {}),
                )
            )

        # ── Restart loop: reload config & re-enter poll_changes ──────
        while not shutdown_event.is_set():
            restart_event.clear()
            log_event(
                logger,
                "info",
                "PROCESSING",
                f"starting changes feed (feed_type={cfg.get('changes_feed', {}).get('feed_type', 'longpoll')})",
            )

            loop.run_until_complete(
                poll_changes(
                    cfg,
                    src,
                    shutdown_event,
                    metrics=metrics,
                    restart_event=restart_event,
                )
            )

            if shutdown_event.is_set():
                break

            # If offline, wait until online or shutdown
            if offline_event.is_set():
                log_event(
                    logger,
                    "info",
                    "CONTROL",
                    "worker is offline – waiting for /_online signal",
                )
                while offline_event.is_set() and not shutdown_event.is_set():
                    loop.run_until_complete(asyncio.sleep(0.5))
                if shutdown_event.is_set():
                    break
                restart_event.clear()
                log_event(logger, "info", "CONTROL", "worker is back online")

            # restart_event was set — reload config and restart
            log_event(logger, "info", "CONTROL", "reloading config for restart")
            cfg = load_config(args.config)
            _ensure_full_logging_config(cfg)
            configure_logging(cfg.get("logging", {}))
            src, warnings, errors = validate_config(cfg)
            if errors:
                for e in errors:
                    logger.error("CONFIG ERROR: %s", e)
                logger.error(
                    "Config has errors – keeping previous feed running would have stopped; shutting down"
                )
                break
            for w in warnings:
                logger.warning("CONFIG WARNING: %s", w)
            log_event(logger, "info", "CONTROL", "config reloaded – restarting feed")

    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        if cbl_scheduler is not None:
            cbl_scheduler.stop()
        if metrics_runner is not None:
            loop.run_until_complete(metrics_runner.cleanup())
        loop.run_until_complete(loop.shutdown_asyncgens())
        if USE_CBL:
            close_db()
        loop.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
