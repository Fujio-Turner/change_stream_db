#!/usr/bin/env python3
"""
Pipeline — per-job thread wrapper for running a single job's _changes feed.

One Pipeline per job. Each has:
  - A threading.Thread that runs asyncio.run(poll_changes(...))
  - Its own HTTP session, checkpoint, metrics, output connection
  - Its own ThreadPoolExecutor for async middleware
  - Exception handling → DLQ
"""

import asyncio
import logging
import threading
import time
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from cbl_store import CBLStore
from pipeline_logging import log_event


class Pipeline:
    """Wraps one job's _changes feed in its own thread."""

    def __init__(
        self,
        job_id: str,
        job_doc: Dict[str, Any],
        cbl_store: CBLStore,
        metrics: Optional[Any],
        logger: logging.Logger,
        middleware_threads: int = 2,
    ):
        """
        Parameters:
            job_id: UUID of the job (from job::{uuid})
            job_doc: Full job document with inputs[0], outputs[0], system config
            cbl_store: CBLStore instance
            metrics: MetricsCollector instance
            logger: Logger instance
            middleware_threads: Number of threads for async middleware pool
        """
        self.job_id = job_id
        self.job_doc = job_doc
        self.cbl_store = cbl_store
        self.metrics = metrics
        self.logger = logger
        self.middleware_threads = middleware_threads

        # Thread state
        self._thread: Optional[threading.Thread] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._running = False
        self._error: Optional[str] = None
        self._error_count = 0
        self._start_time: Optional[float] = None
        self._lock = threading.Lock()

        # Middleware executor (shared across all middleware in this job)
        self.middleware_executor = ThreadPoolExecutor(
            max_workers=middleware_threads,
            thread_name_prefix=f"middleware-{job_id[:8]}",
        )

        # Per-job logger with job_id tag
        self.logger = logging.getLogger(f"pipeline.{job_id[:8]}")

    def run(self, poll_changes_func: Optional[any] = None) -> None:
        """
        Main thread entry point.

        Parameters:
            poll_changes_func: Optional callback to the poll_changes coroutine.
                              If not provided, the Pipeline just waits for shutdown.
                              This allows testing without needing the full main.py stack.
        """
        try:
            self._running = True
            self._start_time = time.time()
            self._error = None

            # Build the config for this job from resolved input/output/system
            cfg = self._build_job_config()

            log_event(
                self.logger,
                "info",
                "PIPELINE_START",
                f"job {self.job_id} starting",
            )

            # Create and run the event loop for this job
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._shutdown_event = asyncio.Event()

            try:
                if poll_changes_func:
                    # Call the provided poll_changes function
                    loop.run_until_complete(
                        poll_changes_func(
                            cfg,
                            src=self.job_doc.get("inputs", [{}])[0].get(
                                "source_type", "unknown"
                            ),
                            shutdown_event=self._shutdown_event,
                            metrics=self.metrics,
                            job_id=self.job_id,
                        )
                    )
                else:
                    # No poll_changes_func provided; just wait for shutdown
                    loop.run_until_complete(self._shutdown_event.wait())
            finally:
                loop.close()

            log_event(
                self.logger,
                "info",
                "PIPELINE_STOP",
                f"job {self.job_id} stopped cleanly",
            )

        except Exception as e:
            self._error = str(e)
            self._error_count += 1
            log_event(
                self.logger,
                "error",
                "PIPELINE_ERROR",
                f"job {self.job_id} crashed: {e}",
            )
            # Write to DLQ if available
            try:
                self._write_crash_to_dlq(e)
            except Exception as dlq_err:
                log_event(
                    self.logger,
                    "error",
                    "DLQ_WRITE_ERROR",
                    f"failed to write crash to DLQ: {dlq_err}",
                )

        finally:
            self._running = False

    def stop(self, timeout_seconds: float = 30) -> bool:
        """
        Signal the pipeline to shut down and wait for thread to finish.

        Returns:
            True if stopped cleanly, False if timeout occurred.
        """
        with self._lock:
            if not self._running or self._thread is None:
                return True

            log_event(
                self.logger,
                "info",
                "PIPELINE_STOP_SIGNAL",
                f"signaling job {self.job_id} to stop",
            )

        # Store thread reference outside lock
        thread = self._thread

        # Note: We can't directly call _shutdown_event.set() from another thread
        # if it's in a different event loop. The event loop will check it on the
        # next iteration. For now, just wait for the thread to exit.
        # In the real implementation, you'd use thread-safe mechanisms like
        # loop.call_soon_threadsafe() or a queue.

        # Wait for thread to exit
        if thread:
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                log_event(
                    self.logger,
                    "warning",
                    "PIPELINE_TIMEOUT",
                    f"job {self.job_id} did not stop within {timeout_seconds}s",
                )
                return False

        with self._lock:
            self._running = False

        log_event(
            self.logger,
            "info",
            "PIPELINE_STOPPED",
            f"job {self.job_id} stopped",
        )
        return True

    def start(self) -> None:
        """Create and start the pipeline thread."""
        with self._lock:
            if self._running or self._thread is not None:
                log_event(
                    self.logger,
                    "warning",
                    "PIPELINE_ALREADY_RUNNING",
                    f"job {self.job_id} already running",
                )
                return

            self._thread = threading.Thread(
                target=self.run,
                name=f"pipeline-{self.job_id[:8]}",
                daemon=False,
            )
            self._thread.start()

    def restart(self, timeout_seconds: float = 30) -> None:
        """Stop and restart the pipeline."""
        log_event(
            self.logger,
            "info",
            "PIPELINE_RESTART",
            f"restarting job {self.job_id}",
        )
        self.stop(timeout_seconds)
        self.start()

    def is_running(self) -> bool:
        """Check if the pipeline thread is alive."""
        with self._lock:
            if self._thread is None:
                return False
            return self._thread.is_alive() and self._running

    def get_state(self) -> Dict[str, Any]:
        """Get the current state of the pipeline."""
        with self._lock:
            uptime = None
            if self._start_time:
                uptime = time.time() - self._start_time

            status = "stopped"
            if self._running:
                status = "running"
            elif self._error:
                status = "error"

            return {
                "job_id": self.job_id,
                "status": status,
                "uptime_seconds": uptime,
                "error_count": self._error_count,
                "last_error": self._error,
            }

    def _build_job_config(self) -> Dict[str, Any]:
        """
        Build the config dict for poll_changes from the job document.

        This extracts inputs[0], outputs[0], system config and builds
        the legacy config shape that poll_changes expects.
        """
        cfg = {}

        # Extract input
        if self.job_doc.get("inputs"):
            cfg["gateway"] = self.job_doc["inputs"][0]

        # Extract output
        if self.job_doc.get("outputs"):
            cfg["output"] = self.job_doc["outputs"][0]

        # Extract system config
        if self.job_doc.get("system"):
            system = self.job_doc["system"]
            cfg["checkpoint"] = system.get("checkpoint", {})
            cfg["processing"] = system.get("processing", {})
            cfg["retry"] = system.get("retry", {})

        # Extract mapping
        if self.job_doc.get("mapping"):
            cfg["mapping"] = self.job_doc["mapping"]

        return cfg

    def _write_crash_to_dlq(self, error: Exception) -> None:
        """Write a pipeline crash entry to the DLQ."""
        dlq_entry = {
            "job_id": self.job_id,
            "doc_id": f"pipeline_crash_{self.job_id}",
            "seq": "crash",
            "error": str(error),
            "error_type": type(error).__name__,
            "doc_data": None,
            "timestamp": int(time.time() * 1000),
        }
        # TODO: use cbl_store to write to DLQ when the API is available
        # self.cbl_store.add_dlq_entry(dlq_entry)
