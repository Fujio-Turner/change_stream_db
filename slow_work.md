ocs
/
4_OPTIMIZATION_CHANGES.md
+149
Done! All 3 quick wins implemented:

✅ Checkpoint save skip — eliminates ~8,640 PUTs/day on idle feeds (changes_http.py:792-798)
✅ dlq_count() instead of list_pending() — avoids loading all DLQ docs into memory (main.py:2356-2358)
✅ Cache psutil/dir-walk with TTL — eliminates 5+ syscalls + 2 directory walks per metrics scrape (main.py: cache fields + 2 helper methods + render() updates)
Code compiles, no diagnostics. Details logged in docs/4_OPTIMIZATION_CHANGES.md.

Many times there are operations the are repetitive that a temp memory object would reduce wait for non changeing items can you look at the code for further optimization
I’m going to scan the hot paths in main.py and changes_http.py for repeated work that can be memoized safely, then implement a focused round of additional caching where it reduces I/O or repeated computation without changing behavior.

$
ls -la
in change_stream_db

total 856
drwxr-xr-x@ 39 fujio.turner  staff    1248 Apr 20 17:12 .
drwxr-xr-x@ 30 fujio.turner  staff     960 Apr 16 23:32 ..
-rw-r--r--@  1 fujio.turner  staff    6148 Apr 20 07:37 .DS_Store
drwxr-xr-x  16 fujio.turner  staff     512 Apr 20 17:10 .git
drwxr-xr-x@  3 fujio.turner  staff      96 Apr 16 02:47 .github
-rw-r--r--@  1 fujio.turner  staff     397 Apr 16 02:38 .gitignore
-rw-r--r--   1 fujio.turner  staff     180 Apr 19 01:54 .pre-commit-config.yaml
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 16 02:48 .pytest_cache
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 18 17:11 .ruff_cache
-rw-r--r--@  1 fujio.turner  staff    1504 Apr 16 02:39 Dockerfile
-rw-r--r--   1 fujio.turner  staff   11357 Apr 15 19:35 LICENSE
-rw-r--r--@  1 fujio.turner  staff   19445 Apr 20 01:58 README.md
-rw-r--r--   1 fujio.turner  staff   29236 Apr 20 01:58 RELEASE_NOTES.md
drwxr-xr-x@  9 fujio.turner  staff     288 Apr 20 17:22 __pycache__
-rw-r--r--@  1 fujio.turner  staff  118310 Apr 20 07:58 cbl_store.py
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 19 01:54 cloud
-rw-r--r--@  1 fujio.turner  staff    6080 Apr 19 15:31 config.json
drwxr-xr-x@  9 fujio.turner  staff     288 Apr 19 01:54 db
-rw-r--r--   1 fujio.turner  staff     679 Apr 19 01:54 docker-compose.yml
drwxr-xr-x@ 30 fujio.turner  staff     960 Apr 20 17:22 docs
drwxr-xr-x@  4 fujio.turner  staff     128 Apr 20 01:58 guide
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 20 13:14 guides
drwxr-xr-x@ 10 fujio.turner  staff     320 Apr 19 03:39 img
drwxr-xr-x@  9 fujio.turner  staff     288 Apr 20 07:38 json_schema
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 20 16:57 logs
-rw-r--r--@  1 fujio.turner  staff  118112 Apr 20 17:22 main.py
drwxr-xr-x   4 fujio.turner  staff     128 Apr 19 01:54 mappings
-rw-r--r--@  1 fujio.turner  staff   43385 Apr 15 10:49 metrics.html
-rw-r--r--@  1 fujio.turner  staff   11161 Apr 20 04:46 pipeline.py
-rw-r--r--@  1 fujio.turner  staff   16212 Apr 20 17:04 pipeline_logging.py
-rw-r--r--@  1 fujio.turner  staff   16025 Apr 20 04:46 pipeline_manager.py
drwxr-xr-x@  5 fujio.turner  staff     160 Apr 20 17:12 profiling
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 20 01:49 release_works
-rw-r--r--@  1 fujio.turner  staff    1101 Apr 18 03:43 requirements.txt
drwxr-xr-x@ 14 fujio.turner  staff     448 Apr 20 01:49 rest
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 20 01:49 schema
-rw-r--r--@  1 fujio.turner  staff    6972 Apr 20 16:43 sg_order_loader.py
drwxr-xr-x@ 26 fujio.turner  staff     832 Apr 20 01:58 tests
drwxr-xr-x@  6 fujio.turner  staff     192 Apr 20 09:56 web
$
rg --files -g 'AGENTS.md'
in change_stream_db

/bin/bash: rg: command not found
Process exited with code 127

$
find .. -name AGENTS.md -print
in change_stream_db

../cb_python_sdk_samples/AGENTS.md
../cb_cko_demo/AGENTS.md
$
sed -n '1,260p' docs/4_OPTIMIZATION_CHANGES.md
in change_stream_db

# Optimization Changes Implementation

> Implemented all 3 quick wins from the optimization report (docs/3_OPTIMIZATION_REPORT.md)

## Summary

Applied critical performance optimizations that eliminate ~8,640 unnecessary HTTP requests per day on idle feeds and cache expensive syscalls.

---

## Quick Win #1: Skip Checkpoint Save When Sequence Unchanged

**File:** `rest/changes_http.py` (line 792–798)
**Impact:** 🔴 High — Eliminates ~8,640 unnecessary PUT requests/day on idle feeds

### Change
```python
if not results:
    new_since = str(last_seq)
    # Skip checkpoint save if sequence hasn't changed (eliminates ~8,640 PUTs/day on idle feeds)
    if new_since != checkpoint.seq:
        await checkpoint.save(new_since, http, base_url, basic_auth, auth_headers)
        if metrics:
            metrics.inc("checkpoint_saves_total")
            metrics.set("checkpoint_seq", new_since)
    return new_since, False
```

### Rationale
When an empty batch is received (no new changes), the code was **always** saving the checkpoint even if the `since` value hadn't moved. In longpoll mode with `poll_interval_seconds=10`, idle feeds triggered a checkpoint PUT every 10 seconds — forever.

By comparing `new_since` with `checkpoint.seq`, we skip the network roundtrip entirely when the checkpoint hasn't changed. Over 24 hours of idle:
- **Before:** 8,640 PUT requests (1 every 10s × 24h)
- **After:** 0 requests

---

## Quick Win #2: Use `dlq_count()` Instead of `list_pending()`

**File:** `main.py` (line 2356–2358)
**Impact:** 🟢 Low — Once per startup, but avoids loading all DLQ docs into memory

### Change
```python
# Update DLQ pending count gauge after replay
if metrics:
    # Use dlq_count() instead of list_pending() to avoid loading all docs into memory
    count = self.cbl_store.dlq_count()
    metrics.set("dlq_pending_count", count)
```

### Rationale
`list_pending()` loads **all** pending DLQ entries into memory just to count them. For systems with hundreds of thousands of pending entries, this is wasteful.

`dlq_count()` instead executes a `SELECT COUNT(*)` N1QL query — a single database operation that returns only the count.

---

## Quick Win #3: Cache psutil & Directory Walk Results with TTL

**File:** `main.py`
**Impact:** 🔴 High — Eliminates 5+ syscalls and 2 directory walks on every metrics scrape

### Changes

#### Added cache fields (line 257–260)
```python
# System metrics cache (TTL=15s for psutil, 60s for directory walks)
self._system_metrics_cache: dict | None = None
self._system_metrics_cache_time: float = 0
self._dir_walk_cache: dict | None = None
self._dir_walk_cache_time: float = 0
```

#### Added `_get_cached_system_metrics()` helper (line 307–335)
Caches all psutil calls with 15s TTL:
- `gc.get_count()` + `gc.get_stats()`
- `psutil.cpu_count()`
- `psutil.cpu_percent()`
- `psutil.virtual_memory()`
- `psutil.swap_memory()`
- `psutil.disk_usage()`
- `psutil.net_io_counters()`

#### Added `_get_cached_dir_walk_sizes()` helper (line 337–381)
Caches directory walk results with 60s TTL:
- Log directory size (`os.walk`)
- CBL database directory size (`os.walk`)

#### Updated `render()` to use cached metrics (line 985–1106)
All psutil calls now pull from cache instead of hitting the kernel.

### Rationale

**The problem:** Prometheus scrapes metrics every 15–30 seconds. Each scrape was:
1. Copying 7 deques and sorting them
2. Making 5+ syscalls (`psutil.*`)
3. Walking the log directory counting file sizes
4. Walking the CBL database directory counting file sizes
5. Calling GC statistics
6. Rendering ~200 lines of text

**The solution:** Cache values that don't change frequently:
- **System metrics (psutil):** cache for 15s → recompute only once per 15 seconds
- **Directory walks:** cache for 60s → filesystem hit only once per 60 seconds

For a production system scraping every 15 seconds:
- **Before:** 5+ syscalls + 2 directory walks per scrape
- **After:** Cached values used 100% of the time (except cold-start every 15–60s)

---

## Implementation Details

### Thread-Safe Caching
Both cache helpers check TTL under no lock, but metrics are written from the locked section of `render()`. This is safe because:
1. Staleness of 15–60 seconds is acceptable for these metrics
2. Multiple scrapes hitting the cache simultaneously is benign

### Fallback Handling
All cached operations are wrapped in `try/except` blocks. If cache computation fails, metrics are rendered as empty/zero — same behavior as before.

### Memory Impact
Cache footprint is minimal:
- `_system_metrics_cache`: ~500 bytes (dict of psutil namedtuples)
- `_dir_walk_cache`: ~50 bytes (dict with 2 integers)

---

## Testing Recommendations

1. **Checkpoint skipping:** Monitor HTTP traffic to Sync Gateway during idle periods. Should see 0 checkpoint PUT requests when no changes are flowing.

2. **DLQ count:** After DLQ replay, verify `dlq_pending_count` metric matches actual pending entries. Should use COUNT query, not full load.

3. **Metrics rendering:**
   - Benchmark `/_metrics` scrape time before/after (expect ~50% reduction)
   - Verify metrics values are consistent across scrapes
   - Check that cold-start (first scrape) forces fresh computation

---

## Files Modified

1. **rest/changes_http.py** — Skip checkpoint save when sequence unchanged
2. **main.py** — Cache psutil/directory walk metrics + use dlq_count()

All changes are backward-compatible and do not alter the Prometheus text format or metric semantics.
$
ls -la profiling && find profiling -maxdepth 2 -type f -print
in change_stream_db

total 832
drwxr-xr-x@  5 fujio.turner  staff     160 Apr 20 17:12 .
drwxr-xr-x@ 39 fujio.turner  staff    1248 Apr 20 17:12 ..
-rw-r--r--@  1 fujio.turner  staff   79973 Apr 20 16:50 profile.svg
-rw-r--r--@  1 fujio.turner  staff  189027 Apr 20 16:55 profile2.svg
-rw-r--r--@  1 fujio.turner  staff  148440 Apr 20 17:00 profile3.svg
profiling/profile3.svg
profiling/profile2.svg
profiling/profile.svg
I found the optimization notes and profiling artifacts, so next I’m diving into flamegraph hotspots and current metrics/render paths to identify additional repeated operations we can cache safely.

$
grep -n "render\|_metrics\|deque\|sort\|os.walk\|psutil\|json.dumps\|yaml\|checkpoint\|list_pending\|dlq_count\|cache" main.py | head -n 200
in change_stream_db

8:Supports longpoll with configurable intervals, checkpoint management,
27:from collections import deque
30:import psutil
113:    Thread-safe metrics collector that renders Prometheus text exposition format.
115:    All counters/gauges are stored as simple numeric values and rendered
116:    on demand when the /_metrics endpoint is hit.
125:        self._process = psutil.Process()
140:        self.checkpoint_saves_total: int = 0
141:        self.checkpoint_save_errors_total: int = 0
206:        self.checkpoint_loads_total: int = 0
207:        self.checkpoint_load_errors_total: int = 0
238:        self.checkpoint_seq: str = "0"
243:        self._output_resp_times: deque[float] = deque(maxlen=10000)
245:        # Stage timing deques
246:        self._changes_request_times: deque[float] = deque(maxlen=10000)
247:        self._batch_processing_times: deque[float] = deque(maxlen=10000)
248:        self._doc_fetch_times: deque[float] = deque(maxlen=10000)
249:        self._health_probe_times: deque[float] = deque(maxlen=10000)
251:        # Auth timing deques
252:        self._inbound_auth_times: deque[float] = deque(maxlen=10000)
253:        self._outbound_auth_times: deque[float] = deque(maxlen=10000)
255:        # System metrics cache (TTL=15s for psutil, 60s for directory walks)
256:        self._system_metrics_cache: dict | None = None
257:        self._system_metrics_cache_time: float = 0
258:        self._dir_walk_cache: dict | None = None
259:        self._dir_walk_cache_time: float = 0
307:    def _get_cached_system_metrics(self) -> dict:
308:        """Cache psutil calls with 15s TTL to avoid syscalls on every scrape."""
311:            self._system_metrics_cache is not None
312:            and now - self._system_metrics_cache_time < 15
314:            return self._system_metrics_cache
316:        cache = {}
318:            cache["gc_counts"] = gc.get_count()
319:            cache["gc_stats"] = gc.get_stats()
320:            cache["cpu_count"] = psutil.cpu_count(logical=True)
321:            cache["cpu_percent"] = psutil.cpu_percent(interval=0)
322:            cache["virtual_memory"] = psutil.virtual_memory()
323:            cache["swap_memory"] = psutil.swap_memory()
325:                cache["disk_usage"] = psutil.disk_usage("/")
327:                cache["disk_usage"] = None
328:            cache["net_io_counters"] = psutil.net_io_counters()
331:        self._system_metrics_cache = cache
332:        self._system_metrics_cache_time = now
333:        return cache
335:    def _get_cached_dir_walk_sizes(self) -> dict:
339:            self._dir_walk_cache is not None
340:            and now - self._dir_walk_cache_time < 60
342:            return self._dir_walk_cache
344:        cache = {"log_bytes": 0, "cbl_bytes": 0}
349:                for dirpath, _, filenames in os.walk(log_dir):
357:                cache["log_bytes"] = total_log_bytes
363:                    for dirpath, _, filenames in os.walk(cbl_dir):
376:                cache["cbl_bytes"] = total_cbl_bytes
379:        self._dir_walk_cache = cache
380:        self._dir_walk_cache_time = now
381:        return cache
383:    def render(self) -> str:
389:            # Snapshot all timing deques under the lock
398:        # Pre-compute sorted arrays and stats for each timing deque
402:            sorted_data = sorted(data) if data else []
403:            return count, total, sorted_data
405:        def _quantile(sorted_data: list[float], q: float) -> float:
406:            if not sorted_data:
408:            idx = int(q * (len(sorted_data) - 1))
409:            return sorted_data[idx]
411:        ort_count, ort_sum, ort_sorted = _stats(ort)
412:        crt_count, crt_sum, crt_sorted = _stats(crt)
413:        bpt_count, bpt_sum, bpt_sorted = _stats(bpt)
414:        dft_count, dft_sum, dft_sorted = _stats(dft)
415:        hpt_count, hpt_sum, hpt_sorted = _stats(hpt)
416:        iat_count, iat_sum, iat_sorted = _stats(iat)
417:        oat_count, oat_sum, oat_sorted = _stats(oat)
434:            sorted_data: list[float],
442:                    f'{name}{{{labels},quantile="{q}"}} {_quantile(sorted_data, q):.6f}'
620:            ort_sorted,
627:            "changes_worker_checkpoint_saves_total",
628:            "Total checkpoint save operations.",
629:            self.checkpoint_saves_total,
632:            "changes_worker_checkpoint_save_errors_total",
633:            "Total checkpoint save errors (fell back to local file).",
634:            self.checkpoint_save_errors_total,
637:            "changes_worker_checkpoint_loads_total",
638:            "Total checkpoint load operations.",
639:            self.checkpoint_loads_total,
642:            "changes_worker_checkpoint_load_errors_total",
643:            "Total checkpoint load errors.",
644:            self.checkpoint_load_errors_total,
647:            "# HELP changes_worker_checkpoint_seq Current checkpoint sequence value."
649:        lines.append("# TYPE changes_worker_checkpoint_seq gauge")
652:            f'changes_worker_checkpoint_seq{{{labels},seq="{self.checkpoint_seq}"}} 1'
776:            iat_sorted,
800:            oat_sorted,
833:            crt_sorted,
841:            bpt_sorted,
849:            dft_sorted,
857:            hpt_sorted,
924:        # ── SYSTEM metrics (psutil / gc / threading) ────────────────────
980:            # GC stats per generation (cached with 15s TTL)
981:            sys_metrics = self._get_cached_system_metrics()
982:            gc_counts = sys_metrics.get("gc_counts", gc.get_count())
983:            gc_stats = sys_metrics.get("gc_stats", gc.get_stats())
996:            # System-wide metrics (cached with 15s TTL to avoid syscalls)
1000:                sys_metrics.get("cpu_count", 0),
1005:                sys_metrics.get("cpu_percent", 0),
1008:            vmem = sys_metrics.get("virtual_memory")
1031:            swap = sys_metrics.get("swap_memory")
1044:            disk = sys_metrics.get("disk_usage")
1067:            net = sys_metrics.get("net_io_counters")
1100:            # Directory sizes (cached with 60s TTL to avoid filesystem hits)
1101:            dir_sizes = self._get_cached_dir_walk_sizes()
1119:            db_lines = DbMetrics.render_all()
1130:            cloud_lines = CloudMetrics.render_all()
1141:async def _metrics_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
1142:    """aiohttp handler for GET /_metrics"""
1144:    body = metrics.render()
1178:         - If tasks drain in time → checkpoint was NOT advanced past pending
1184:           * If false the checkpoint was held back; the same docs will be
1198:    # Read shutdown config from the app (set by start_metrics_server)
1247:                "checkpoint was NOT advanced past them" % tasks_remaining
1252:                "checkpoint was NOT advanced – they will be re-fetched on next startup"
1299:async def start_metrics_server(
1310:    """Start a lightweight HTTP server that serves /_metrics in Prometheus format."""
1324:    app.router.add_get("/_metrics", _metrics_handler)
1325:    app.router.add_get("/metrics", _metrics_handler)
1643:    # Check if the required library is installed for binary/yaml formats
1702:                "docs will be skipped and the checkpoint will still advance"
1864:            "checkpoint": {...with job_id suffix},
1893:        "checkpoint": {
1895:            "file": f"checkpoint_{job_id}.json",
1954:    CBL-style checkpoint stored on Sync Gateway as a _local document.
1959:        SG REST path = {keyspace}/_local/checkpoint-{UUID}
1961:    The checkpoint document contains (CBL-compatible):
1974:        self._job_id = job_id  # Phase 6: per-job checkpoint isolation
1985:        channel_str = ",".join(sorted(channels)) if channels else ""
1990:        self._local_doc_id = f"checkpoint-{self._uuid}"
1992:        # Fallback to local file when SG is unreachable for checkpoint ops
1994:        fallback_file = cfg.get("file", "checkpoint.json")
1996:            # Transform "checkpoint.json" -> "checkpoint_<job_id>.json"
2003:        self._metrics = None
2005:    def set_metrics(self, metrics: "MetricsCollector | None") -> None:
2006:        self._metrics = metrics
2010:        """Returns the REST path segment: _local/checkpoint-{uuid}"""
2030:        """GET {keyspace}/_local/checkpoint-{uuid} from Sync Gateway."""
2035:        ic("checkpoint load", url)
2052:                "checkpoint loaded",
2060:                "checkpoint detail",
2066:            if self._metrics:
2067:                self._metrics.inc("checkpoint_loads_total")
2074:                    "no existing checkpoint on SG – starting from 0",
2084:                    "checkpoint load fell back to local storage",
2090:                if self._metrics:
2091:                    self._metrics.inc("checkpoint_loads_total")
2092:                    self._metrics.inc("checkpoint_load_errors_total")
2098:                "checkpoint load fell back to local storage: %s" % exc,
2103:            if self._metrics:
2104:                self._metrics.inc("checkpoint_loads_total")
2105:                self._metrics.inc("checkpoint_load_errors_total")
2117:        """PUT {keyspace}/_local/checkpoint-{uuid} on Sync Gateway."""
2135:            ic("checkpoint save", url, seq, self._internal)
2148:                    "checkpoint saved",
2156:                    "checkpoint save detail",
2167:                    "checkpoint save fell back to local storage: %s" % exc,
2173:                if self._metrics:
2174:                    self._metrics.inc("checkpoint_save_errors_total")
2180:            data = CBLStore().load_checkpoint(self._uuid)
2188:                ic("checkpoint loaded from CBL", seq)
2200:            ic("checkpoint loaded from file", seq)
2206:            CBLStore().save_checkpoint(self._uuid, seq, self._client_id, self._internal)
2207:            ic("checkpoint saved to CBL", seq)
2211:            json.dumps(
2220:        ic("checkpoint saved to file", seq)
2281:    checkpoint = Checkpoint(
2282:        cfg.get("checkpoint", {}), gw, channels, job_id=job_id
2285:        checkpoint.set_metrics(metrics)
2305:            http.set_metrics(metrics)
2370:        every_n_docs = cfg.get("checkpoint", {}).get("every_n_docs", 0)
2393:        # Load checkpoint from SG _local doc (CBL-style)
2395:        if since == "0" and cfg.get("checkpoint", {}).get("enabled", True):
2396:            since = await checkpoint.load(http, base_url, basic_auth, auth_headers)
2411:                # Use dlq_count() instead of list_pending() to avoid loading all docs into memory
2412:                count = self.cbl_store.dlq_count()
2428:        initial_sync = requested_since == "0" and not checkpoint.initial_sync_done
2492:            checkpoint=checkpoint,
2520:                checkpoint.initial_sync_done,
2683:                        "Waiting %ds before retrying (checkpoint held at since=%s)",
2703:                        checkpoint._initial_sync_done = True
2704:                        await checkpoint.save(
2836:        checkpoint = Checkpoint(cfg.get("checkpoint", {}), gw, channels)
2838:            seq = await checkpoint.load(http, base_url, basic_auth, auth_headers)
3045:                start_metrics_server(
$
sed -n '180,460p' main.py
in change_stream_db

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

        # Auth tracking – inbound (gateway / _changes feed)
        self.inbound_auth_total: int = 0
        self.inbound_auth_success_total: int = 0
        self.inbound_auth_failure_total: int = 0

        # Auth tracking – outbound (output endpoint)
        self.outbound_auth_total: int = 0
        self.outbound_auth_success_total: int = 0
        self.outbound_auth_failure_total: int = 0

        # Checkpoint loads
        self.checkpoint_loads_total: int = 0
        self.checkpoint_load_errors_total: int = 0

        # Attachment processing
        self.attachments_detected_total: int = 0
        self.attachments_downloaded_total: int = 0
        self.attachments_download_errors_total: int = 0
        self.attachments_uploaded_total: int = 0
        self.attachments_upload_errors_total: int = 0
        self.attachments_bytes_downloaded_total: int = 0
        self.attachments_bytes_uploaded_total: int = 0
        self.attachments_post_process_total: int = 0
        self.attachments_post_process_errors_total: int = 0
        self.attachments_skipped_total: int = 0
        self.attachments_missing_total: int = 0
        self.attachments_digest_mismatch_total: int = 0
        self.attachments_stale_total: int = 0
        self.attachments_post_process_skipped_total: int = 0
        self.attachments_conflict_retries_total: int = 0
        self.attachments_orphaned_uploads_total: int = 0
        self.attachments_partial_success_total: int = 0
        self.attachments_temp_files_cleaned_total: int = 0

        # Flood / backpressure detection
        self.largest_batch_received: int = 0
        self.flood_batches_total: int = 0  # batches exceeding flood threshold
        self.flood_threshold: int = 10000  # configurable via set()

        # Gauges (can go up and down)
        self.changes_pending: int = 0  # received - processed (backpressure)
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

        # Auth timing deques
        self._inbound_auth_times: deque[float] = deque(maxlen=10000)
        self._outbound_auth_times: deque[float] = deque(maxlen=10000)

        # System metrics cache (TTL=15s for psutil, 60s for directory walks)
        self._system_metrics_cache: dict | None = None
        self._system_metrics_cache_time: float = 0
        self._dir_walk_cache: dict | None = None
        self._dir_walk_cache_time: float = 0

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

    def record_inbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._inbound_auth_times.append(seconds)

    def record_outbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._outbound_auth_times.append(seconds)

    def record_batch_received(self, batch_size: int) -> None:
        with self._lock:
            if batch_size > self.largest_batch_received:
                self.largest_batch_received = batch_size
            if batch_size >= self.flood_threshold:
                self.flood_batches_total += 1
            self.changes_pending = (
                self.changes_received_total - self.changes_processed_total
            )

    def _get_cached_system_metrics(self) -> dict:
        """Cache psutil calls with 15s TTL to avoid syscalls on every scrape."""
        now = time.monotonic()
        if (
            self._system_metrics_cache is not None
            and now - self._system_metrics_cache_time < 15
        ):
            return self._system_metrics_cache

        cache = {}
        try:
            cache["gc_counts"] = gc.get_count()
            cache["gc_stats"] = gc.get_stats()
            cache["cpu_count"] = psutil.cpu_count(logical=True)
            cache["cpu_percent"] = psutil.cpu_percent(interval=0)
            cache["virtual_memory"] = psutil.virtual_memory()
            cache["swap_memory"] = psutil.swap_memory()
            try:
                cache["disk_usage"] = psutil.disk_usage("/")
            except OSError:
                cache["disk_usage"] = None
            cache["net_io_counters"] = psutil.net_io_counters()
        except Exception:
            pass  # system metrics are best-effort
        self._system_metrics_cache = cache
        self._system_metrics_cache_time = now
        return cache

    def _get_cached_dir_walk_sizes(self) -> dict:
        """Cache directory walk results with 60s TTL to avoid filesystem hits."""
        now = time.monotonic()
        if (
            self._dir_walk_cache is not None
            and now - self._dir_walk_cache_time < 60
        ):
            return self._dir_walk_cache

        cache = {"log_bytes": 0, "cbl_bytes": 0}
        try:
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
                cache["log_bytes"] = total_log_bytes

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
                cache["cbl_bytes"] = total_cbl_bytes
        except Exception:
            pass  # directory walks are best-effort
        self._dir_walk_cache = cache
        self._dir_walk_cache_time = now
        return cache

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
            iat = list(self._inbound_auth_times)
            oat = list(self._outbound_auth_times)

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
        iat_count, iat_sum, iat_sorted = _stats(iat)
        oat_count, oat_sum, oat_sorted = _stats(oat)

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
$
python - <<'PY' import re, pathlib for name in ['profiling/profile.svg','profiling/profile2.svg','profiling/profile3.svg']: p=pathlib.Path(name) if not p.exists(): continue text=p.read_text(errors='ignore') titles=re.findall(r'<title>(.*?)</title>',text) entries=[] for t in titles: m=re.match(r'(.+?) \((\d+) samples, ([0-9.]+)%\)',t) if m: fn,s,pct=m.groups(); entries.append((int(s),float(pct),fn)) entries.sort(reverse=True) print('\n',name) for s,pct,fn in entries[:20]: print(f'{pct:5.2f}% {s:8d} {fn}') PY
in change_stream_db

/bin/bash: python: command not found
Process exited with code 127

$
python3 - <<'PY' import re, pathlib for name in ['profiling/profile.svg','profiling/profile2.svg','profiling/profile3.svg']: p=pathlib.Path(name) if not p.exists(): continue text=p.read_text(errors='ignore') titles=re.findall(r'<title>(.*?)</title>',text) entries=[] for t in titles: m=re.match(r'(.+?) \((\d+) samples, ([0-9.]+)%\)',t) if m: fn,s,pct=m.groups(); entries.append((int(s),float(pct),fn)) entries.sort(reverse=True) print('\n'+name) for s,pct,fn in entries[:25]: print(f'{pct:6.2f}% {s:8d} {fn}') PY
in change_stream_db


profiling/profile.svg
100.00%       72 process 1:&quot;python main.py --config config.json&quot;
100.00%       72 all
 86.11%       62 run (threading.py:1012)
 86.11%       62 _bootstrap_inner (threading.py:1075)
 86.11%       62 _bootstrap (threading.py:1032)
 76.39%       55 run_until_complete (asyncio/base_events.py:678)
 76.39%       55 run_forever (asyncio/base_events.py:645)
 76.39%       55 run (pipeline.py:106)
 75.00%       54 _run_once (asyncio/base_events.py:1999)
 75.00%       54 _run (asyncio/events.py:88)
 47.22%       34 poll_changes (main.py:2493)
 43.06%       31 _consume_continuous_stream (rest/changes_http.py:1545)
 26.39%       19 process_one (rest/changes_http.py:951)
 16.67%       12 send (db/db_base.py:607)
 16.67%       12 _process_changes_batch (rest/changes_http.py:888)
 13.89%       10 run_forever (asyncio/base_events.py:645)
 13.89%       10 main (main.py:3019)
 13.89%       10 _run_once (asyncio/base_events.py:1999)
 13.89%       10 _run (asyncio/events.py:88)
 13.89%       10 &lt;module&gt; (main.py:3043)
 12.50%        9 start (aiohttp/web_protocol.py:605)
 12.50%        9 fetch_docs (rest/changes_http.py:247)
 12.50%        9 _metrics_handler (main.py:1090)
 12.50%        9 _handle_request (aiohttp/web_protocol.py:517)
 12.50%        9 _handle (aiohttp/web_app.py:569)

profiling/profile2.svg
 72.15%      948 poll_changes (main.py:2493)
 66.74%      877 _consume_continuous_stream (rest/changes_http.py:1545)
 31.58%      415 _process_changes_batch (rest/changes_http.py:888)
 26.48%      348 fetch_docs (rest/changes_http.py:247)
 20.70%      272 process_one (rest/changes_http.py:951)
 17.73%      233 _process_changes_batch (rest/changes_http.py:1218)
  7.23%       95 save (main.py:2081)
  7.08%       93 __call__ (icecream/icecream.py:322)
  6.93%       91 send (db/db_base.py:607)
  5.94%       78 send (db/db_base.py:545)
  5.86%       77 _process_changes_batch (rest/changes_http.py:834)
  5.78%       76 save (main.py:2098)
  5.78%       76 log_event (pipeline_logging.py:354)
  5.78%       76 log (logging/__init__.py:1609)
  5.63%       74 _format (icecream/icecream.py:351)
  5.56%       73 map_document (schema/mapper.py:591)
  5.56%       73 content_type (aiohttp/helpers.py:765)
  5.56%       73 _parse_content_type (aiohttp/helpers.py:755)
  5.56%       73 _fetch_docs_bulk_get (rest/changes_http.py:372)
  5.48%       72 _map_upsert (schema/mapper.py:639)
  5.33%       70 __call__ (icecream/icecream.py:322)
  4.79%       63 handle (logging/__init__.py:1700)
  4.79%       63 handle (logging/__init__.py:1028)
  4.79%       63 callHandlers (logging/__init__.py:1762)
  4.79%       63 _log (logging/__init__.py:1684)

profiling/profile3.svg
 75.80%      805 poll_changes (main.py:2493)
 74.86%      795 _consume_continuous_stream (rest/changes_http.py:1545)
 40.77%      433 _process_changes_batch (rest/changes_http.py:888)
 30.60%      325 fetch_docs (rest/changes_http.py:247)
 17.61%      187 _process_changes_batch (rest/changes_http.py:1218)
 16.95%      180 process_one (rest/changes_http.py:951)
  8.76%       93 save (main.py:2084)
  8.57%       91 request (rest/changes_http.py:73)
  8.38%       89 send (db/db_base.py:607)
  8.00%       85 _fetch_docs_bulk_get (rest/changes_http.py:422)
  7.91%       84 log_event (pipeline_logging.py:354)
  7.82%       83 log (logging/__init__.py:1609)
  7.16%       76 _log (logging/__init__.py:1684)
  7.06%       75 handle (logging/__init__.py:1700)
  6.87%       73 callHandlers (logging/__init__.py:1762)
  6.78%       72 handle (logging/__init__.py:1028)
  6.12%       65 fetch_docs (rest/changes_http.py:224)
  6.03%       64 log_event (pipeline_logging.py:354)
  6.03%       64 log (logging/__init__.py:1609)
  5.74%       61 emit (logging/handlers.py:75)
  5.65%       60 emit (logging/__init__.py:1280)
  5.37%       57 content_type (aiohttp/helpers.py:765)
  5.37%       57 _parse_content_type (aiohttp/helpers.py:755)
  5.37%       57 _fetch_docs_bulk_get (rest/changes_http.py:372)
  5.08%       54 flush (logging/__init__.py:1144)
$
sed -n '1,520p' rest/changes_http.py
in change_stream_db

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
                "GET",
                url,
                params=params,
                auth=auth,
                headers=headers,
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
$
sed -n '520,1120p' rest/changes_http.py
in change_stream_db


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
                    "GET",
                    url,
                    params=params,
                    auth=auth,
                    headers=headers,
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
        # Skip checkpoint save if sequence hasn't changed (eliminates ~8,640 PUTs/day on idle feeds)
        if new_since != checkpoint.seq:
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
$
sed -n '1120,1760p' rest/changes_http.py
in change_stream_db

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
I found a likely high-impact repeat: failed batches still call len(dlq.list_pending()), which loads all DLQ docs each time. I’m now checking Checkpoint and DLQ internals for similar repeated object creation or expensive calls we can memoize.

$
sed -n '1880,2325p' main.py
in change_stream_db

    output_entry = outputs[0]

    # Build pipeline config by taking the input/output entries
    # and merging with defaults
    return {
        "job_id": job_id,
        "job_name": job_name,
        "gateway": input_entry,  # {url, database, src, scope, collection, auth}
        "auth": input_entry.get("auth", {}),
        "changes_feed": input_entry.get("changes_feed", {}),
        "processing": input_entry.get("processing", {}),
        "output": output_entry,  # {mode, target_url, ...}
        "output_type": job_doc.get("output_type", "http"),
        "checkpoint": {
            "enabled": True,
            "file": f"checkpoint_{job_id}.json",
        },
        "mapping": job_doc.get("mapping"),
        "system": job_doc.get("system", {}),
        "retry": job_doc.get("retry", {}),
        "metrics": job_doc.get("metrics", {}),
        "logging": job_doc.get("logging", {}),
    }

def migrate_legacy_config_to_job(db: CBLStore, cfg: dict) -> dict | None:
    """
    Auto-migrate v1.x config.json to a job document.

    Returns the migrated job document, or None if migration failed.
    """
    try:
        gw = cfg.get("gateway", {})
        out = cfg.get("output", {})

        if not gw or not out:
            logger.warning(
                "Legacy config missing gateway or output – cannot auto-migrate"
            )
            return None

        job_id = "legacy_auto_migrated_" + str(int(time.time()))
        job_name = "Auto-migrated v1.x config"

        job_data = {
            "name": job_name,
            "enabled": True,
            "inputs": [gw],
            "outputs": [out],
            "output_type": out.get("mode", "stdout"),
            "mapping": None,
            "system": cfg.get("system", {}),
            "retry": cfg.get("retry", {}),
        }

        # Save to CBL (save_job expects job_id and job_data separately)
        db.save_job(job_id, job_data)
        logger.info("Auto-migrated legacy config.json to job %s", job_id)

        # Return the full document as it would be retrieved
        job_doc = {"_id": job_id, "id": job_id, **job_data}
        return job_doc
    except Exception as e:
        logger.error("Failed to auto-migrate legacy config: %s", e)
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

    def __init__(
        self, cfg: dict, gw_cfg: dict, channels: list[str], job_id: str | None = None
    ):
        self._enabled = cfg.get("enabled", True)
        self._job_id = job_id  # Phase 6: per-job checkpoint isolation
        self._lock = asyncio.Lock()
        self._seq: str = "0"
        self._rev: str | None = None  # SG doc _rev for updates
        self._internal: int = 0
        self._initial_sync_done: bool = False

        # Build the deterministic UUID the same way CBL does:
        #   HASH(local_client_id + SG URL + channel_names + job_id)
        client_id = cfg.get("client_id", "changes_worker")
        sg_url = build_base_url(gw_cfg)
        channel_str = ",".join(sorted(channels)) if channels else ""
        job_str = job_id or ""  # Phase 6: include job_id in UUID for isolation
        raw = f"{client_id}{sg_url}{channel_str}{job_str}"
        self._uuid = hashlib.sha1(raw.encode()).hexdigest()
        self._client_id = client_id
        self._local_doc_id = f"checkpoint-{self._uuid}"

        # Fallback to local file when SG is unreachable for checkpoint ops
        # Phase 6: use job_id in fallback filename for isolation
        fallback_file = cfg.get("file", "checkpoint.json")
        if job_id:
            # Transform "checkpoint.json" -> "checkpoint_<job_id>.json"
            path = Path(fallback_file)
            fallback_file = str(path.parent / f"{path.stem}_{job_id}{path.suffix}")
        self._fallback_path = Path(fallback_file)

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
    job_id: str | None = None,  # Phase 6: job-specific identifier
) -> None:
    gw = cfg.get(
        "gateway", cfg.get("inputs", [{}])[0]
    )  # Support both old and new configs
    auth_cfg = cfg.get("auth", gw.get("auth", {}))  # Phase 6: auth from gateway
    feed_cfg = cfg.get(
        "changes_feed", gw.get("changes_feed", {})
    )  # Phase 6: changes_feed from gateway
    proc_cfg = cfg.get(
        "processing", gw.get("processing", {})
    )  # Phase 6: processing from gateway
    out_cfg = cfg.get(
        "output", cfg.get("outputs", [{}])[0]
    )  # Support both old and new configs
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

    try:
        base_url = build_base_url(gw)
    except KeyError as e:
        watcher_task.cancel()
        raise KeyError(
            f"Missing gateway field {e} — check that the job's input has "
            f"'url' (or 'host') and 'database' configured"
        ) from e
    ssl_ctx = build_ssl_context(gw)
    basic_auth = build_basic_auth(auth_cfg)
    auth_headers = build_auth_headers(auth_cfg, src, compress=gw.get("compress", False))

    channels = feed_cfg.get("channels", [])
    checkpoint = Checkpoint(
        cfg.get("checkpoint", {}), gw, channels, job_id=job_id
    )  # Phase 6: pass job_id
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

        _DB_ENGINE_ALIASES = {"postgres", "mysql", "mssql", "oracle"}
        if output_mode in _DB_ENGINE_ALIASES:
            db_engine = output_mode
            output_mode = "db"
        elif output_mode == "db":
            db_engine = out_cfg.get("db", {}).get("engine", "postgres")

        if output_mode == "db":
            if db_engine == "postgres":
                from db.db_postgres import PostgresOutputForwarder

                output = PostgresOutputForwarder(out_cfg, dry_run, metrics=metrics)
            elif db_engine == "mysql":
                from db.db_mysql import MySQLOutputForwarder
$
grep -RIn "list_pending\|dlq_count\|flush_insert_meta\|DeadLetterQueue" rest main.py cbl_store.py | head -n 200
in change_stream_db

rest/output_http.py:998:class DeadLetterQueue:
rest/output_http.py:1130:    def flush_insert_meta(self, job_id: str = "") -> None:
rest/output_http.py:1140:    def list_pending(self) -> list[dict]:
rest/changes_http.py:30:from rest import OutputForwarder, OutputEndpointDown, DeadLetterQueue, determine_method
rest/changes_http.py:758:    dlq: DeadLetterQueue,
rest/changes_http.py:1148:                dlq_count = 0
rest/changes_http.py:1167:                    dlq_count += 1
rest/changes_http.py:1169:                    metrics.inc("dead_letter_total", dlq_count)
rest/changes_http.py:1175:                    "DLQ'd %d docs from batch (checkpoint not advanced)" % dlq_count,
rest/changes_http.py:1205:        dlq.flush_insert_meta(_job)
rest/changes_http.py:1207:            metrics.set("dlq_pending_count", len(dlq.list_pending()))
rest/changes_http.py:1245:    dlq: DeadLetterQueue,
rest/changes_http.py:1453:    dlq: DeadLetterQueue,
rest/changes_http.py:1608:    dlq: DeadLetterQueue,
rest/changes_http.py:1828:    dlq: DeadLetterQueue,
rest/changes_http.py:1854:    pending = dlq.list_pending()
rest/__init__.py:4:    DeadLetterQueue,
rest/__init__.py:20:    "DeadLetterQueue",
main.py:38:    DeadLetterQueue,
main.py:2366:        dlq = DeadLetterQueue(
main.py:2411:                # Use dlq_count() instead of list_pending() to avoid loading all docs into memory
main.py:2412:                count = self.cbl_store.dlq_count()
cbl_store.py:530:            "dlq_count": self.dlq_count(),
cbl_store.py:1623:    def dlq_count(self) -> int:
$
sed -n '900,1245p' rest/output_http.py
in change_stream_db

            return resp.status < 500
        except asyncio.TimeoutError:
            ic("_health_check: timeout", self._hc_url)
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check timed out",
                url=self._hc_url,
                error_detail=f"timeout after {self._hc_timeout}s",
            )
            return False
        except aiohttp.ClientConnectorError as exc:
            ic("_health_check: connection error", type(exc).__name__, str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check connection failed",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False
        except aiohttp.ClientSSLError as exc:
            ic("_health_check: SSL error", str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check SSL/TLS error",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False
        except (ConnectionError, aiohttp.ClientError, OSError) as exc:
            ic("_health_check: error", type(exc).__name__, str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check failed",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False

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

def determine_method(
    change: dict, write_method: str = "PUT", delete_method: str = "DELETE"
) -> str:
    if change.get("deleted"):
        return delete_method
    return write_method

def _build_dlq_reason(result: dict) -> str:
    """Build a human-readable reason string for a DLQ entry."""
    error_class = result.get("error_class", "")
    status = result.get("status", 0)
    if error_class:
        return f"data_error:{error_class}"
    if isinstance(status, int) and 400 <= status < 500:
        return f"client_error:{status}"
    if isinstance(status, int) and 300 <= status < 400:
        return f"redirect:{status}"
    if isinstance(status, int) and status >= 500:
        return f"server_error:{status}"
    if result.get("error") == "shutdown_inflight":
        return "shutdown_inflight"
    if isinstance(status, int) and status == 0:
        return "connection_failure"
    return "unknown"

class DeadLetterQueue:
    """
    Dead letter queue for documents that failed output delivery.

    When CBL is available, entries are stored as CBL documents.
    Otherwise falls back to append-only JSONL file.
    """

    def __init__(self, path: str, dlq_cfg: dict | None = None):
        from cbl_store import USE_CBL as _use_cbl

        self._use_cbl = _use_cbl
        self._store = None
        if self._use_cbl:
            from cbl_store import CBLStore

            self._store = CBLStore()
        self._path = Path(path) if path and not self._use_cbl else None
        self._lock = asyncio.Lock()
        cfg = dlq_cfg or {}
        self._retention_seconds = cfg.get("retention_seconds", 86400)
        self._max_replay_attempts = cfg.get("max_replay_attempts", 10)

    @property
    def enabled(self) -> bool:
        return self._use_cbl or self._path is not None

    async def write(
        self,
        doc: dict,
        result: dict,
        seq: str | int,
        target_url: str = "",
        metrics=None,
    ) -> None:
        ic("DLQ.write", result.get("doc_id"), seq, "cbl" if self._use_cbl else "file")
        reason = _build_dlq_reason(result)
        if self._use_cbl and self._store:
            self._store.add_dlq_entry(
                doc_id=result.get("doc_id", "unknown"),
                seq=str(seq),
                method=result.get("method", "PUT"),
                status=result.get("status", 0),
                error=result.get("error", ""),
                doc=doc,
                target_url=target_url,
                ttl_seconds=self._retention_seconds,
                reason=reason,
            )
            log_event(
                logger,
                "warn",
                "DLQ",
                "entry written to CBL",
                operation="INSERT",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="cbl",
                ttl_seconds=self._retention_seconds,
                target_url=target_url,
            )
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
            "reason": reason,
            "time": int(time.time()),
            "target_url": target_url,
            "replay_attempts": 0,
            "doc": doc,
        }
        try:
            async with self._lock:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            ic("DLQ.write: file write failed", self._path, exc)
            log_event(
                logger,
                "error",
                "DLQ",
                "failed to write DLQ entry to file — DATA MAY BE LOST",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="file",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            if metrics:
                metrics.inc("dlq_write_failures_total")
            raise
        log_event(
            logger,
            "warn",
            "DLQ",
            "entry written to file",
            operation="INSERT",
            doc_id=result.get("doc_id"),
            seq=str(seq),
            storage="file",
        )

    async def purge(self, dlq_id: str) -> None:
        """Remove a DLQ entry after successful reprocessing."""
        ic("DLQ.purge", dlq_id, "cbl" if self._use_cbl else "file")
        if self._use_cbl and self._store:
            self._store.delete_dlq_entry(dlq_id)
            log_event(
                logger,
                "info",
                "DLQ",
                "entry purged after successful reprocessing",
                operation="DELETE",
                doc_id=dlq_id,
                storage="cbl",
            )
            return
        # File-based DLQ does not support individual purge
        log_event(
            logger,
            "debug",
            "DLQ",
            "file-based DLQ does not support purge",
            doc_id=dlq_id,
            storage="file",
        )

    def flush_insert_meta(self, job_id: str = "") -> None:
        """Record last_inserted_at once after a batch of DLQ writes."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_inserted_at", job_id)

    def flush_drain_meta(self, job_id: str = "") -> None:
        """Record last_drained_at once after a batch of DLQ drains."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_drained_at", job_id)

    def list_pending(self) -> list[dict]:
        """Return all pending (not yet retried) DLQ entries."""
        if self._use_cbl and self._store:
            return [e for e in self._store.list_dlq() if not e.get("retried")]
        if self._path and self._path.exists():
            entries = []
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            return entries
        return []

    def get_entry_doc(self, dlq_id: str) -> dict | None:
        """Return the full DLQ entry including doc_data for reprocessing."""
        if self._use_cbl and self._store:
            return self._store.get_dlq_entry(dlq_id)
        return None

    def purge_expired(self) -> int:
        """Remove DLQ entries older than retention_seconds. Returns count purged."""
        if self._use_cbl and self._store and self._retention_seconds > 0:
            return self._store.purge_expired_dlq(self._retention_seconds)
        return 0

    def increment_replay_attempts(self, dlq_id: str) -> int:
        """Increment replay_attempts on a CBL DLQ entry. Returns new count."""
        if self._use_cbl and self._store:
            return self._store.increment_dlq_replay_attempts(dlq_id)
        return 0

    @property
    def max_replay_attempts(self) -> int:
        return self._max_replay_attempts

    @property
    def retention_seconds(self) -> int:
        return self._retention_seconds

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
$
sed -n '1,460p' pipeline_logging.py
in change_stream_db

"""
Production logging module for changes_worker.

Inspired by Couchbase Sync Gateway's logging configuration, this module
provides:
  - Multiple log levels including TRACE
  - Per-handler log_key filtering (CHANGES, PROCESSING, MAPPING, OUTPUT, etc.)
  - Per-key level overrides (e.g. HTTP→warn, MAPPING→debug)
  - File rotation with max_size, max_age, and rotated_logs_size_limit
  - Redaction of sensitive data (none / partial / full)
  - Operation tagging (INSERT, UPDATE, DELETE, SELECT)
"""

import atexit
import glob as _glob
import logging
import os
import queue
import re
import time
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

# ---------------------------------------------------------------------------
# Custom TRACE level (below DEBUG)
# ---------------------------------------------------------------------------
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

def _trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)

logging.Logger.trace = _trace  # type: ignore[attr-defined]

# Background queue listener — started by configure_logging(), stopped at exit.
_queue_listener: QueueListener | None = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOG_KEYS = frozenset(
    {
        "CHANGES",  # _changes feed input
        "PROCESSING",  # filtering, routing
        "MAPPING",  # schema mapping
        "OUTPUT",  # stdout / HTTP / DB / cloud output
        "HTTP",  # HTTP requests / responses
        "CHECKPOINT",  # checkpoint load / save
        "RETRY",  # retry / backoff decisions
        "METRICS",  # metrics server
        "CBL",  # Couchbase Lite operations (read/write/open/close/maintenance)
        "DLQ",  # dead letter queue operations (add/retry/purge/list)
    }
)

LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------
_SENSITIVE_FIELDS = re.compile(
    r"(password|passwd|pass|token|bearer_token|session_cookie|"
    r"authorization|cookie|secret|api_key|access_token|refresh_token)",
    re.IGNORECASE,
)

_URL_USERINFO_RE = re.compile(r"(https?://)([^@]+)@")
_HEADER_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

class Redactor:
    """Redacts sensitive data from strings, dicts, and log messages."""

    def __init__(self, level: str = "partial"):
        self.level = level  # none | partial | full

    def redact_string(self, value: str) -> str:
        if self.level == "none":
            return value
        # Redact URL userinfo
        value = _URL_USERINFO_RE.sub(
            r"\1<ud>***:***</ud>@" if self.level == "partial" else r"\1<ud>XXXXX</ud>@",
            value,
        )
        # Redact Bearer tokens in strings
        if self.level == "partial":
            value = _HEADER_BEARER_RE.sub(
                lambda m: (
                    m.group(1)
                    + m.group(0)[-4:].rjust(len(m.group(0)) - len(m.group(1)), "*")
                ),
                value,
            )
        elif self.level == "full":
            value = _HEADER_BEARER_RE.sub(r"\1<ud>XXXXX</ud>", value)
        return value

    def redact_value(self, key: str, value) -> str:
        if self.level == "none":
            return str(value)
        if not _SENSITIVE_FIELDS.search(key):
            return str(value)
        s = str(value)
        if self.level == "full":
            return "<ud>XXXXX</ud>"
        # partial: show first and last char
        if len(s) <= 2:
            return "<ud>XXXXX</ud>"
        return f"<ud>{s[0]}{'*' * (len(s) - 2)}{s[-1]}</ud>"

    def redact_dict(self, d: dict) -> dict:
        if self.level == "none":
            return d
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif _SENSITIVE_FIELDS.search(k):
                out[k] = self.redact_value(k, v)
            else:
                out[k] = v
        return out

# Module-level redactor (configured during setup)
_redactor = Redactor("none")

def get_redactor() -> Redactor:
    return _redactor

# ---------------------------------------------------------------------------
# Log key / level filter
# ---------------------------------------------------------------------------
class LogKeyLevelFilter(logging.Filter):
    """
    Handler-level filter that checks:
      1. Is record.log_key in the allowed set?
      2. Does record.levelno meet the threshold (base or per-key override)?
    """

    def __init__(
        self, log_keys: list[str], base_level: int, key_levels: dict[str, int]
    ):
        super().__init__()
        self.allow_all = "*" in log_keys
        self.log_keys = set(k.upper() for k in log_keys)
        self.base_level = base_level
        self.key_levels = {k.upper(): v for k, v in key_levels.items()}

    def filter(self, record: logging.LogRecord) -> bool:
        log_key = getattr(record, "log_key", None)
        if log_key is None:
            # Messages without a log_key pass if they meet the base level
            return record.levelno >= self.base_level

        log_key = log_key.upper()
        if not self.allow_all and log_key not in self.log_keys:
            return False

        threshold = self.key_levels.get(log_key, self.base_level)
        return record.levelno >= threshold

# ---------------------------------------------------------------------------
# Redacting formatter
# ---------------------------------------------------------------------------
_EXTRA_FIELDS = (
    "log_key",
    "job_id",  # Multi-job aware logging
    "operation",
    "doc_id",
    "seq",
    "status",
    "url",
    "attempt",
    "elapsed_ms",
    "mode",
    "http_method",
    "bytes",
    "storage",
    "batch_size",
    "input_count",
    "filtered_count",
    "host",
    "port",
    "delay_seconds",
    "field_count",
    # CBL-specific fields
    "db_name",
    "db_path",
    "db_size_mb",
    "doc_count",
    "doc_type",
    "manifest_id",
    "maintenance_type",
    "duration_ms",
    "error_detail",
)

class RedactingFormatter(logging.Formatter):
    """
    Formatter that:
      - Prints structured key=value context from record extras
      - Redacts sensitive data in messages
    """

    def __init__(
        self, redactor: Redactor, fmt: str | None = None, datefmt: str | None = None
    ):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.default_msec_format = "%s.%03d"
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        # Build structured suffix from extra fields
        parts: list[str] = []
        log_key = getattr(record, "log_key", None)
        if log_key:
            parts.append(f"[{log_key}]")

        for field in _EXTRA_FIELDS:
            if field == "log_key":
                continue
            val = getattr(record, field, None)
            if val is not None:
                # Redact URL fields
                if field == "url" and isinstance(val, str):
                    val = self.redactor.redact_string(val)
                parts.append(f"{field}={val}")

        # Redact the message itself
        record.msg = self.redactor.redact_string(str(record.msg))

        base = super().format(record)
        if parts:
            return f"{base} {' '.join(parts)}"
        return base

# ---------------------------------------------------------------------------
# Managed rotating file handler
# ---------------------------------------------------------------------------
class ManagedRotatingFileHandler(RotatingFileHandler):
    """
    RotatingFileHandler with SG-style retention:
      - max_size: MB per file before rollover
      - max_age: days to retain rotated files
      - rotated_logs_size_limit: total MB cap for rotated files
    """

    def __init__(
        self,
        filename: str,
        max_size_mb: int = 100,
        max_age_days: int = 7,
        rotated_logs_size_limit_mb: int = 1024,
        **kwargs,
    ):
        self.max_age_days = max_age_days
        self.rotated_logs_size_limit = rotated_logs_size_limit_mb * 1024 * 1024
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        super().__init__(
            filename,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=999,  # we manage cleanup ourselves
            **kwargs,
        )

    def doRollover(self):
        super().doRollover()
        self._cleanup_rotated_files()

    def _cleanup_rotated_files(self):
        base = self.baseFilename
        pattern = f"{base}.*"
        rotated = sorted(_glob.glob(pattern))

        now = time.time()
        max_age_secs = self.max_age_days * 86400

        # Remove files older than max_age
        remaining = []
        for path in rotated:
            try:
                age = now - os.path.getmtime(path)
                if age > max_age_secs:
                    os.remove(path)
                else:
                    remaining.append(path)
            except OSError:
                pass

        # Enforce total size limit (delete oldest first)
        total = sum(os.path.getsize(p) for p in remaining if os.path.exists(p))
        while total > self.rotated_logs_size_limit and remaining:
            oldest = remaining.pop(0)
            try:
                total -= os.path.getsize(oldest)
                os.remove(oldest)
            except OSError:
                pass

# ---------------------------------------------------------------------------
# Operation inference
# ---------------------------------------------------------------------------
def infer_operation(
    change: dict | None = None, doc: dict | None = None, method: str | None = None
) -> str:
    """
    Infer the logical DB operation from a change/doc/method.

    Returns one of: INSERT, UPDATE, DELETE, SELECT.
    """
    if method == "DELETE":
        return "DELETE"
    if change and change.get("deleted"):
        return "DELETE"
    if method == "GET":
        return "SELECT"

    # Check revision to distinguish INSERT vs UPDATE
    rev = None
    if doc:
        rev = doc.get("_rev", "")
    if change and not rev:
        changes_list = change.get("changes", [])
        if changes_list:
            rev = changes_list[0].get("rev", "")

    if rev and rev.startswith("1-"):
        return "INSERT"
    return "UPDATE"

# ---------------------------------------------------------------------------
# Thin helpers for structured logging
# ---------------------------------------------------------------------------
def log_event(
    logger: logging.Logger, level: str, log_key: str, message: str, **fields
) -> None:
    """Log a structured event with a log_key and extra fields."""
    lvl = LEVELS.get(level, logging.INFO)
    if not logger.isEnabledFor(lvl):
        return
    logger.log(lvl, message, extra={"log_key": log_key, **fields})

# ---------------------------------------------------------------------------
# Main configuration entry point
# ---------------------------------------------------------------------------
def configure_logging(cfg: dict) -> None:
    """
    Configure the logging system from the config.logging dict.

    Supports both the legacy {"level": "DEBUG"} format and the full
    SG-inspired config with console/file/rotation/redaction.
    """
    global _redactor, _queue_listener

    # Stop any previous background listener before reconfiguring.
    if _queue_listener is not None:
        _queue_listener.stop()
        _queue_listener = None

    root = logging.getLogger()
    # Clear existing handlers
    root.handlers.clear()
    root.setLevel(TRACE)

    # Collect real handlers; we'll attach them to a background QueueListener
    # instead of the root logger so emit()/flush() never block the event loop.
    real_handlers: list[logging.Handler] = []

    # Legacy mode: simple level string
    if "console" not in cfg and "file" not in cfg:
        level_str = cfg.get("level", "DEBUG").lower()
        level = LEVELS.get(level_str, logging.DEBUG)
        _redactor = Redactor(cfg.get("redaction_level", "none"))

        handler = logging.StreamHandler()
        handler.setLevel(level)
        fmt = RedactingFormatter(
            _redactor,
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        handler.setFormatter(fmt)
        real_handlers.append(handler)

        # Route icecream to TRACE
        try:
            from icecream import ic

            ic.configureOutput(
                prefix="ic| ",
                outputFunction=lambda s: logging.getLogger("changes_worker").log(
                    TRACE, s
                ),
            )
        except ImportError:
            pass

        _queue_listener = _start_queue_logging(root, real_handlers)
        return

    # Full SG-style config
    redaction_level = cfg.get("redaction_level", "partial")
    _redactor = Redactor(redaction_level)

    # Console handler
    console_cfg = cfg.get("console", {})
    if console_cfg.get("enabled", True):
        base_level = LEVELS.get(
            console_cfg.get("log_level", "info").lower(), logging.INFO
        )
        log_keys = console_cfg.get("log_keys", ["*"])
        key_levels = {
            k: LEVELS.get(v.lower(), logging.INFO)
            for k, v in console_cfg.get("key_levels", {}).items()
        }

        handler = logging.StreamHandler()
        handler.setLevel(TRACE)  # actual filtering via LogKeyLevelFilter
        handler.addFilter(LogKeyLevelFilter(log_keys, base_level, key_levels))
        fmt = RedactingFormatter(
            _redactor,
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        handler.setFormatter(fmt)
        real_handlers.append(handler)

    # File handler
    file_cfg = cfg.get("file", {})
    if file_cfg.get("enabled", False):
        file_path = file_cfg.get("path", "logs/changes_worker.log")
        base_level = LEVELS.get(
            file_cfg.get("log_level", "debug").lower(), logging.DEBUG
        )
        log_keys = file_cfg.get("log_keys", ["*"])
        key_levels = {
            k: LEVELS.get(v.lower(), logging.DEBUG)
            for k, v in file_cfg.get("key_levels", {}).items()
        }

        rotation = file_cfg.get("rotation", {})
        max_size = rotation.get("max_size", 100)
        max_age = rotation.get("max_age", 7)
$
sed -n '480,760p' db/db_base.py
in change_stream_db

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
$
grep -n "class DbMetrics\|class CloudMetrics\|def render_all\|render_all()" -n main.py db -R | head -n 200
in change_stream_db

main.py:1119:            db_lines = DbMetrics.render_all()
main.py:1130:            cloud_lines = CloudMetrics.render_all()
db/db_base.py:51:class DbMetrics:
db/db_base.py:114:    def render_all(cls) -> str:
$
sed -n '1,210p' db/db_base.py
in change_stream_db

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
$
grep -RIn "class CloudMetrics\|render_all" cloud | head -n 200
in change_stream_db

cloud/cloud_base.py:34:class CloudMetrics:
cloud/cloud_base.py:87:    def render_all(cls) -> str:
$
sed -n '1,190p' cloud/cloud_base.py
in change_stream_db

"""
Base cloud blob storage output forwarder.

Provides shared logic for key templating, JSON serialization,
retry with exponential backoff, metrics proxy, and stats logging.
Each cloud subclass only needs to implement the driver-specific pieces:
client creation, object upload/delete, error classification, and health check.
"""

import abc
import asyncio
import json
import logging
import re
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime, timezone

from pipeline_logging import log_event, infer_operation

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

logger = logging.getLogger("changes_worker")

# ── Per-provider / per-job metrics proxy ────────────────────────────────────

class CloudMetrics:
    """
    Lightweight metrics wrapper that tracks counters with (provider, job_id)
    labels while also delegating to the global MetricsCollector for totals.

    Usage in Prometheus:
        # Global totals (backward compat – existing dashboards keep working)
        changes_worker_output_requests_total{src="...",database="..."} 500

        # Per-provider / per-job breakdowns
        changes_worker_cloud_uploads_total{provider="s3",job_id="orders_archive"} 300
    """

    _registry_lock = threading.Lock()
    _registry: list["CloudMetrics"] = []

    def __init__(self, provider: str, job_id: str, global_metrics=None):
        self.provider = provider
        self.job_id = job_id or provider
        self._global = global_metrics
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._resp_times: deque[float] = deque(maxlen=10_000)

        with CloudMetrics._registry_lock:
            CloudMetrics._registry.append(self)

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

    def snapshot(self) -> tuple[dict[str, int], list[float]]:
        """Return a copy of counters + resp_times under the lock."""
        with self._lock:
            return dict(self._counters), list(self._resp_times)

    def unregister(self) -> None:
        with CloudMetrics._registry_lock:
            try:
                CloudMetrics._registry.remove(self)
            except ValueError:
                pass

    @classmethod
    def render_all(cls) -> str:
        """
        Render per-provider/per-job cloud metrics in Prometheus text format.

        Returns a string of lines that the main MetricsCollector.render()
        can append to its output.
        """
        with cls._registry_lock:
            instances = list(cls._registry)

        if not instances:
            return ""

        snapshots: list[tuple[str, str, dict[str, int], list[float]]] = []
        for inst in instances:
            counters, resp_times = inst.snapshot()
            snapshots.append((inst.provider, inst.job_id, counters, resp_times))

        # Gather all counter names across all instances
        all_counter_names: set[str] = set()
        for _, _, counters, _ in snapshots:
            all_counter_names.update(counters.keys())

        lines: list[str] = []

        # Emit one HELP/TYPE block per counter, with one line per (provider, job_id)
        for name in sorted(all_counter_names):
            prom_name = f"changes_worker_cloud_{name}"
            lines.append(
                f"# HELP {prom_name} Cloud output counter: {name} (per provider/job)"
            )
            lines.append(f"# TYPE {prom_name} counter")
            for provider, job_id, counters, _ in snapshots:
                val = counters.get(name, 0)
                if val:
                    lines.append(
                        f'{prom_name}{{provider="{provider}",job_id="{job_id}"}} {val}'
                    )

        # Emit per-instance response time summaries
        has_resp = any(rt for _, _, _, rt in snapshots)
        if has_resp:
            prom_name = "changes_worker_cloud_output_response_time_seconds"
            lines.append(
                f"# HELP {prom_name} Cloud output response time (per provider/job)"
            )
            lines.append(f"# TYPE {prom_name} summary")
            for provider, job_id, _, resp_times in snapshots:
                if not resp_times:
                    continue
                s = sorted(resp_times)
                count = len(s)
                total = sum(s)
                for q in (0.5, 0.9, 0.99):
                    idx = int(q * (count - 1))
                    lines.append(
                        f'{prom_name}{{provider="{provider}",job_id="{job_id}",'
                        f'quantile="{q}"}} {s[idx]:.6f}'
                    )
                lines.append(
                    f'{prom_name}_sum{{provider="{provider}",job_id="{job_id}"}} {total:.6f}'
                )
                lines.append(
                    f'{prom_name}_count{{provider="{provider}",job_id="{job_id}"}} {count}'
                )

        return "\n".join(lines)

# ── Key template helpers ────────────────────────────────────────────────────

_KEY_VAR_RE = re.compile(r"\{(\w+)\}")

def _sanitize_key_part(value: str) -> str:
    """Sanitize a value for use in an object key (replace : with _, URL-encode others)."""
    value = value.replace(":", "_")
    return urllib.parse.quote(value, safe="/._-")

def render_key(
    template: str,
    doc: dict,
    cfg: dict,
    sanitize: bool = True,
    extra_vars: dict[str, object] | None = None,
) -> str:
    """Render an object key from a template and document fields.

    *extra_vars* (if provided) are merged after the standard variables,
    allowing callers to add context-specific placeholders such as
    ``{attachment_name}`` or ``{content_type}``.
    """
    now = datetime.now(timezone.utc)
    doc_id = doc.get("_id", doc.get("id", "unknown"))
    variables = {
        "doc_id": doc_id,
        "rev": doc.get("_rev", doc.get("rev", "")),
        "seq": str(doc.get("_seq", doc.get("seq", ""))),
        "timestamp": str(int(now.timestamp())),
        "iso_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
$
sed -n '1,260p' docs/3_OPTIMIZATION_REPORT.md
in change_stream_db

# Optimization Report — Changes Worker v2.0

> *"There are only three optimizations: Do less. Do it less often. Do it faster.*
> *The largest gains come from #1, but we spend all our time on #3."*

---

## Executive Summary

The codebase is **well-architected** for its mission — a production-grade CDC pipeline with proper separation of concerns (Pipeline, PipelineManager, CBLStore, OutputForwarder). The v2.0 redesign (job-centric, multi-pipeline) is sound. This report focuses on **"do less"** and **"do it less often"** opportunities across the 7 core Python files.

---

## 1. DO LESS

### 1.1 MetricsCollector.render() — Rebuild Everything on Every Scrape

**File:** `main.py` lines 301–1084
**Impact:** 🔴 High (called on every `/_metrics` request)

The `render()` method is **~780 lines** that rebuilds the entire Prometheus text output from scratch on every scrape. Each call:

- Copies 7 deques under lock → sorts each → computes quantiles
- Calls `psutil.Process()` methods (cpu_percent, memory_info, num_fds)
- Calls `psutil.cpu_percent()`, `psutil.virtual_memory()`, `psutil.swap_memory()`, `psutil.disk_usage()`, `psutil.net_io_counters()` — **5 syscalls**
- Walks the log directory (`os.walk`) counting file sizes
- Walks the CBL database directory counting file sizes
- Calls `gc.get_count()` + `gc.get_stats()`
- Imports and calls `DbMetrics.render_all()` + `CloudMetrics.render_all()`

**Recommendation:** Most of these values don't change between scrapes (15-30s intervals). Split into:

- **Hot path** (counters/gauges): render from current values — instant
- **Cold path** (psutil, disk walks, GC): cache for 10-15s, recompute only on TTL expiry
- **Deque snapshots**: Sort on insert (use `sortedcontainers.SortedList`) or cache sorted result until deque changes

The directory walks (`os.walk` for logs + CBL) are the worst offenders — they hit the filesystem on every scrape. Cache these with a 60s TTL.

### 1.2 `_process_changes_batch()` — Duplicated DLQ Logic × 3

**File:** `rest/changes_http.py` lines 750–1200
**Impact:** 🟡 Medium (code complexity, not runtime)

The DLQ write + metrics update pattern is copy-pasted in 3 branches:

1. Sequential + `every_n_docs` (line 998–1007)
2. Sequential without `every_n_docs` (line 1081–1090)
3. Parallel mode (line 1111–1120)

Plus the shutdown-DLQ pattern is duplicated twice (lines 1021–1053 and 1132–1174).

**Recommendation:** Extract `_dlq_write_and_track(result, change, output, dlq, metrics)` helper. Not a perf issue but reduces the surface area for bugs in the hottest code path.

### 1.3 CBLStore — Redundant Doc Reads

**File:** `cbl_store.py`
**Impact:** 🟡 Medium

Several methods read a document twice — once to check existence, once to modify:

- `delete_dlq_entry()` (line 1583): reads doc to confirm it exists, then purges by ID — the purge itself would be a no-op if missing.
- `delete_mapping()` (line 982): same pattern.
- `delete_schema()` (line 1069): same pattern.
- `delete_source()` (line 1169): same pattern.

**Recommendation:** `_coll_purge_doc` already handles missing docs (the CBL C API returns an error). Wrap in try/except instead of pre-reading. Each `_coll_get_doc` + `_coll_purge_doc` pair is 2 C FFI calls that could be 1.

### 1.4 Pipeline._build_job_config() — Defensive Copying Without Need

**File:** `pipeline.py` lines 274–320
**Impact:** 🟢 Low (once per job start)

Builds a legacy config dict from the job document. The method copies auth, changes_feed, processing into top-level keys *and* keeps them nested inside `gateway`. Both paths are read by `poll_changes()`. This is fine functionally but creates redundant dict entries. Not a runtime concern — just cognitive overhead.

### 1.5 `ic()` Calls Throughout — Disabled But Still Evaluated

**File:** All `.py` files
**Impact:** 🟢 Low (already handled)

The `main()` function already disables `ic()` when trace logging isn't configured (line 2846). Good. However, `ic()` calls inside `cbl_store.py` still evaluate their arguments before the no-op lambda discards them. For expensive arguments this matters, but the current usages pass simple strings — acceptable.

---

## 2. DO IT LESS OFTEN

### 2.1 Checkpoint Saves — Potentially Every Empty Batch

**File:** `rest/changes_http.py` lines 792–798
**Impact:** 🔴 High (network I/O on every poll cycle)

When `_process_changes_batch()` receives an **empty** `results` list, it immediately saves the checkpoint:

```python
if not results:
    new_since = str(last_seq)
    await checkpoint.save(new_since, http, base_url, basic_auth, auth_headers)
```

In longpoll mode with `poll_interval_seconds=10`, an idle feed triggers a checkpoint PUT to Sync Gateway every 10 seconds — forever. The checkpoint value (`since`) hasn't changed.

**Recommendation:** Skip the save if `new_since == self._seq` (checkpoint hasn't moved). This eliminates a PUT request every poll cycle during idle periods. Over 24h of idle: **8,640 unnecessary HTTP requests eliminated**.

### 2.2 DLQ Indexes — Ensured Every CBLStore Instantiation

**File:** `cbl_store.py` lines 358–374 + line 447
**Impact:** 🟢 Low (guarded by boolean flag)

`_ensure_dlq_indexes()` is called in `CBLStore.__init__()`. It's guarded by `_DLQ_INDEXES_ENSURED` so it only runs once per process. However, `CBLStore()` is instantiated in multiple places:

- `main.py` line 2947: `db = CBLStore()`
- `main.py` line 2126: `CBLStore().load_checkpoint(...)` (inside Checkpoint._load_fallback)
- `main.py` line 2152: `CBLStore().save_checkpoint(...)` (inside Checkpoint._save_fallback)
- `main.py` line 1331: `store = CBLStore()` (in load_config)

Each instantiation calls `get_db()` + `_ensure_dlq_indexes()`. The DB is cached as a singleton, but the pattern of creating throwaway `CBLStore()` instances in fallback paths adds unnecessary overhead.

**Recommendation:** Pass the existing `CBLStore` instance to `Checkpoint` instead of creating new ones in fallback methods.

### 2.3 Log Directory Size Walk — Every Metrics Scrape

**File:** `main.py` lines 1018–1033
**Impact:** 🟡 Medium

`os.walk()` over the log directory runs on every `/_metrics` GET. For a production system with log rotation enabled (100MB per file, 1024MB cap), this walks potentially dozens of files every 15 seconds.

**Recommendation:** Cache with 60s TTL. Log directory size doesn't need sub-minute resolution.

### 2.4 CBL Database Size Walk — Every Metrics Scrape

**File:** `main.py` lines 1036–1057
**Impact:** 🟡 Medium

Same issue as 2.3 but for the CBL `.cblite2` directory.

### 2.5 DLQ Count After Replay — Full N1QL Query

**File:** `main.py` lines 2356–2358 (inside `poll_changes`)
**Impact:** 🟢 Low (once per startup)

```python
pending = dlq.list_pending()
metrics.set("dlq_pending_count", len(pending))
```

`list_pending()` loads all pending DLQ entries to get a count. Should use `dlq_count()` instead which does a `SELECT COUNT(*)`.

### 2.6 `_load_enabled_jobs()` — Loads Full Doc Per Summary Row

**File:** `pipeline_manager.py` lines 484–509
**Impact:** 🟡 Medium (at startup and job reload)

```python
for summary in job_summaries:
    raw_id = summary.get("id") or ...
    full_doc = self.cbl_store.load_job(raw_id)
```

`list_jobs()` returns summaries, then each one triggers a separate `load_job()` call (individual doc read). For N jobs, this is N+1 CBL reads.

**Recommendation:** Add a `list_jobs_full()` method to CBLStore that returns complete documents in a single N1QL query, or batch the reads.

---

## 3. DO IT FASTER

### 3.1 Prometheus Render — String Concatenation

**File:** `main.py` lines 337–1084
**Impact:** 🟢 Low

Uses `list[str]` + `"\n".join(lines)` which is already the right pattern in Python. No issue here.

### 3.2 `_fetch_docs_bulk_get()` — JSON Fallback Line-by-Line

**File:** `rest/changes_http.py` lines 398–410
**Impact:** 🟢 Low

When the response isn't `application/json`, it falls back to splitting by newlines and trying `json.loads()` on each line. This is fine — it's a rare fallback path for older SG versions.

### 3.3 Deque maxlen=10000 for Timing Data

**File:** `main.py` lines 243–253
**Impact:** 🟢 Low

Seven deques capped at 10,000 entries for timing data. On each `render()`, all are copied and sorted. For 10K float entries, `sorted()` takes ~1ms — acceptable for a metrics endpoint. If scrape frequency increases, consider keeping a running p50/p90/p99 digest (T-Digest or HDR histogram) instead.

---

## 4. ARCHITECTURAL OBSERVATIONS

### 4.1 `main.py` is 3,050 Lines — God File

`main.py` contains:

- `MetricsCollector` (800+ lines including render)
- `Checkpoint` class (200+ lines)
- `validate_config()` (300+ lines)
- `poll_changes()` (500+ lines)
- `test_connection()` (120+ lines)
- `main()` entrypoint (120+ lines)
- HTTP route handlers (150+ lines)
- Config helpers (60+ lines)
- Build helpers (60+ lines)

**Recommendation:** `MetricsCollector` alone could be its own module. `Checkpoint` is already logically independent. `validate_config` could live alongside its schema definitions. This doesn't affect runtime performance but impacts developer velocity and review quality.

### 4.2 `cbl_store.py` is 1,700+ Lines — Growing

All CBL operations live in one file. The DLQ methods alone are ~400 lines. The inputs/outputs/jobs CRUD is another ~500 lines. Consider splitting by concern: `cbl_dlq.py`, `cbl_jobs.py`, `cbl_config.py`.

### 4.3 Config Loaded from CBL → Ignores config.json Silently

**File:** `main.py` line 1334

```python
logger.info("Config loaded from CBL (config.json is ignored)")
```

This is correct behavior but worth noting: after the first seed, `config.json` edits have no effect. Operators who edit `config.json` expecting changes will be confused. The log message helps, but a `--reseed` flag would be more user-friendly.

---

## Priority Matrix

| # | Finding | Category | Impact | Effort |
|---|---------|----------|--------|--------|
| 2.1 | Checkpoint saves on empty results | Do Less Often | 🔴 High | 🟢 Trivial (3-line fix) |
| 1.1 | MetricsCollector.render() rebuilds everything | Do Less | 🔴 High | 🟡 Moderate (cache layer) |
| 2.3 | Log dir walk every scrape | Do Less Often | 🟡 Medium | 🟢 Trivial (TTL cache) |
| 2.4 | CBL dir walk every scrape | Do Less Often | 🟡 Medium | 🟢 Trivial (TTL cache) |
| 2.6 | N+1 job loading | Do Less Often | 🟡 Medium | 🟡 Moderate |
| 1.3 | Redundant doc reads before purge | Do Less | 🟡 Medium | 🟢 Trivial |
| 1.2 | Duplicated DLQ logic × 3 | Do Less (code) | 🟡 Medium | 🟡 Moderate |
| 2.2 | Throwaway CBLStore instances | Do Less Often | 🟢 Low | 🟡 Moderate |
| 2.5 | list_pending() for count | Do Less | 🟢 Low | 🟢 Trivial |

---

## Top 3 Quick Wins

1. **Skip checkpoint save when sequence hasn't changed** (2.1) — 1 `if` statement, eliminates thousands of HTTP PUTs per day on idle feeds.

2. **Cache psutil + directory walk results in render()** (1.1 + 2.3 + 2.4) — wrap system metrics section in a `_cached_system_metrics()` with 15–60s TTL. Most of render() cost disappears.

3. **Use `dlq_count()` instead of `list_pending()`** (2.5) — swap one method call, avoid loading all DLQ docs into memory just to count them.
$
sed -n '1580,1675p' cbl_store.py
in change_stream_db

        return attempts

    def delete_dlq_entry(self, dlq_id: str) -> None:
        ic("delete_dlq_entry: entry", dlq_id)
        doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return
        _coll_purge_doc(self.db, COLL_DLQ, dlq_id)

        log_event(
            logger,
            "info",
            "DLQ",
            "entry purged",
            operation="DELETE",
            doc_id=dlq_id,
            doc_type="dlq",
        )

    def clear_dlq(self) -> None:
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'",
        )
        count = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    count += 1
        if count > 0:
            self.update_dlq_meta("last_drained_at")
        log_event(
            logger,
            "info",
            "DLQ",
            "queue cleared",
            operation="DELETE",
            doc_type="dlq",
            doc_count=count,
        )

    def dlq_count(self) -> int:
        return (
            _run_n1ql_scalar(
                self.db, f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            )
            or 0
        )

    def purge_expired_dlq(self, max_age_seconds: int) -> int:
        """Purge DLQ entries older than max_age_seconds. Returns count purged."""
        if max_age_seconds <= 0:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d"
            f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
            {"cutoff": cutoff},
        )
        purged = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    purged += 1
        if purged > 0:
            log_event(
                logger,
                "info",
                "DLQ",
                "purged %d expired entries (older than %ds)"
                % (purged, max_age_seconds),
                operation="DELETE",
                doc_type="dlq",
                doc_count=purged,
            )
        return purged

    def get_dlq_meta(self) -> dict:
        """Return DLQ metadata (last_inserted_at, last_drained_at as epoch)."""
        doc = _coll_get_doc(self.db, COLL_DLQ, "dlq:meta")
        if not doc:
            return {
                "last_inserted_at": None,
                "last_drained_at": None,
                "last_inserted_job": None,
                "last_drained_job": None,
            }
        props = doc.properties
        return {
            "last_inserted_at": props.get("last_inserted_at", None),
            "last_drained_at": props.get("last_drained_at", None),
$
sed -n '1,140p' main.py
in change_stream_db

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

__version__ = "2.0.0"

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
    DeadLetterQueue,
    VALID_OUTPUT_FORMATS,
)
from rest.output_http import check_serialization_library
from rest.api_v2 import (
    api_get_inputs_changes,
    api_post_inputs_changes,
    api_put_inputs_changes_entry,
    api_delete_inputs_changes_entry,
    api_get_outputs,
    api_post_outputs,
    api_put_outputs_entry,
    api_delete_outputs_entry,
    api_get_jobs,
    api_get_job,
    api_post_jobs,
    api_put_job,
    api_delete_job,
    api_refresh_job_input,
    api_refresh_job_output,
)
from rest.api_v2_jobs_control import register_job_control_routes
from rest.changes_http import (
    ShutdownRequested,
    RetryableHTTP,
    ClientHTTPError,
    RedirectHTTPError,
    ServerHTTPError,
    fetch_docs,
    fetch_db_update_seq,
    _fetch_docs_bulk_get,
    _fetch_docs_individually,
    _build_changes_body,
    _parse_seq_number,
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
    migrate_mappings_to_jobs,
    COLL_CHECKPOINTS,
)
from rest.attachment_config import parse_attachment_config
from rest.attachments import AttachmentProcessor
from pipeline_logging import (
    configure_logging,
    log_event,
)
from pipeline_manager import PipelineManager

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
$
sed -n '1760,2060p' rest/changes_http.py
in change_stream_db

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
$
sed -n '100,430p' main.py
in change_stream_db

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

        # Auth tracking – inbound (gateway / _changes feed)
        self.inbound_auth_total: int = 0
        self.inbound_auth_success_total: int = 0
        self.inbound_auth_failure_total: int = 0

        # Auth tracking – outbound (output endpoint)
        self.outbound_auth_total: int = 0
        self.outbound_auth_success_total: int = 0
        self.outbound_auth_failure_total: int = 0

        # Checkpoint loads
        self.checkpoint_loads_total: int = 0
        self.checkpoint_load_errors_total: int = 0

        # Attachment processing
        self.attachments_detected_total: int = 0
        self.attachments_downloaded_total: int = 0
        self.attachments_download_errors_total: int = 0
        self.attachments_uploaded_total: int = 0
        self.attachments_upload_errors_total: int = 0
        self.attachments_bytes_downloaded_total: int = 0
        self.attachments_bytes_uploaded_total: int = 0
        self.attachments_post_process_total: int = 0
        self.attachments_post_process_errors_total: int = 0
        self.attachments_skipped_total: int = 0
        self.attachments_missing_total: int = 0
        self.attachments_digest_mismatch_total: int = 0
        self.attachments_stale_total: int = 0
        self.attachments_post_process_skipped_total: int = 0
        self.attachments_conflict_retries_total: int = 0
        self.attachments_orphaned_uploads_total: int = 0
        self.attachments_partial_success_total: int = 0
        self.attachments_temp_files_cleaned_total: int = 0

        # Flood / backpressure detection
        self.largest_batch_received: int = 0
        self.flood_batches_total: int = 0  # batches exceeding flood threshold
        self.flood_threshold: int = 10000  # configurable via set()

        # Gauges (can go up and down)
        self.changes_pending: int = 0  # received - processed (backpressure)
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

        # Auth timing deques
        self._inbound_auth_times: deque[float] = deque(maxlen=10000)
        self._outbound_auth_times: deque[float] = deque(maxlen=10000)

        # System metrics cache (TTL=15s for psutil, 60s for directory walks)
        self._system_metrics_cache: dict | None = None
        self._system_metrics_cache_time: float = 0
        self._dir_walk_cache: dict | None = None
        self._dir_walk_cache_time: float = 0

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

    def record_inbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._inbound_auth_times.append(seconds)

    def record_outbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._outbound_auth_times.append(seconds)

    def record_batch_received(self, batch_size: int) -> None:
        with self._lock:
            if batch_size > self.largest_batch_received:
                self.largest_batch_received = batch_size
            if batch_size >= self.flood_threshold:
                self.flood_batches_total += 1
            self.changes_pending = (
                self.changes_received_total - self.changes_processed_total
            )

    def _get_cached_system_metrics(self) -> dict:
        """Cache psutil calls with 15s TTL to avoid syscalls on every scrape."""
        now = time.monotonic()
        if (
            self._system_metrics_cache is not None
            and now - self._system_metrics_cache_time < 15
        ):
            return self._system_metrics_cache

        cache = {}
        try:
            cache["gc_counts"] = gc.get_count()
            cache["gc_stats"] = gc.get_stats()
            cache["cpu_count"] = psutil.cpu_count(logical=True)
            cache["cpu_percent"] = psutil.cpu_percent(interval=0)
            cache["virtual_memory"] = psutil.virtual_memory()
            cache["swap_memory"] = psutil.swap_memory()
            try:
                cache["disk_usage"] = psutil.disk_usage("/")
            except OSError:
                cache["disk_usage"] = None
            cache["net_io_counters"] = psutil.net_io_counters()
        except Exception:
            pass  # system metrics are best-effort
        self._system_metrics_cache = cache
        self._system_metrics_cache_time = now
        return cache

    def _get_cached_dir_walk_sizes(self) -> dict:
        """Cache directory walk results with 60s TTL to avoid filesystem hits."""
        now = time.monotonic()
        if (
            self._dir_walk_cache is not None
            and now - self._dir_walk_cache_time < 60
        ):
            return self._dir_walk_cache

        cache = {"log_bytes": 0, "cbl_bytes": 0}
        try:
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
                cache["log_bytes"] = total_log_bytes

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
                cache["cbl_bytes"] = total_cbl_bytes
        except Exception:
            pass  # directory walks are best-effort
        self._dir_walk_cache = cache
        self._dir_walk_cache_time = now
        return cache

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
            iat = list(self._inbound_auth_times)
            oat = list(self._outbound_auth_times)

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
        iat_count, iat_sum, iat_sorted = _stats(iat)
        oat_count, oat_sum, oat_sorted = _stats(oat)

        lines: list[str] = []

        def _counter(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{{{labels}}} {value}")

        def _gauge(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{labels}}} {value}")
$
sed -n '860,1165p' main.py
in change_stream_db

        )

        # -- Attachments --
        _counter(
            "changes_worker_attachments_detected_total",
            "Documents with _attachments seen.",
            self.attachments_detected_total,
        )
        _counter(
            "changes_worker_attachments_downloaded_total",
            "Individual attachment downloads completed.",
            self.attachments_downloaded_total,
        )
        _counter(
            "changes_worker_attachments_download_errors_total",
            "Failed attachment downloads.",
            self.attachments_download_errors_total,
        )
        _counter(
            "changes_worker_attachments_uploaded_total",
            "Attachments uploaded to destination.",
            self.attachments_uploaded_total,
        )
        _counter(
            "changes_worker_attachments_upload_errors_total",
            "Failed attachment uploads.",
            self.attachments_upload_errors_total,
        )
        _counter(
            "changes_worker_attachments_bytes_downloaded_total",
            "Total bytes downloaded from source.",
            self.attachments_bytes_downloaded_total,
        )
        _counter(
            "changes_worker_attachments_bytes_uploaded_total",
            "Total bytes uploaded to destination.",
            self.attachments_bytes_uploaded_total,
        )
        _counter(
            "changes_worker_attachments_post_process_total",
            "Post-processing operations completed.",
            self.attachments_post_process_total,
        )
        _counter(
            "changes_worker_attachments_post_process_errors_total",
            "Failed post-processing operations.",
            self.attachments_post_process_errors_total,
        )
        _counter(
            "changes_worker_attachments_skipped_total",
            "Attachments skipped by filter.",
            self.attachments_skipped_total,
        )
        _counter(
            "changes_worker_attachments_missing_total",
            "Attachments listed in _attachments but returned 404 on fetch.",
            self.attachments_missing_total,
        )
        _counter(
            "changes_worker_attachments_digest_mismatch_total",
            "Downloads where digest didn't match (re-downloaded).",
            self.attachments_digest_mismatch_total,
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

            # GC stats per generation (cached with 15s TTL)
            sys_metrics = self._get_cached_system_metrics()
            gc_counts = sys_metrics.get("gc_counts", gc.get_count())
            gc_stats = sys_metrics.get("gc_stats", gc.get_stats())
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

            # System-wide metrics (cached with 15s TTL to avoid syscalls)
            _gauge(
                "changes_worker_system_cpu_count",
                "Number of logical CPU cores on the host.",
                sys_metrics.get("cpu_count", 0),
            )
            _gauge(
                "changes_worker_system_cpu_percent",
                "Host-wide CPU usage percentage.",
                sys_metrics.get("cpu_percent", 0),
            )

            vmem = sys_metrics.get("virtual_memory")
            if vmem:
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

            swap = sys_metrics.get("swap_memory")
            if swap:
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

            disk = sys_metrics.get("disk_usage")
            if disk:
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

            net = sys_metrics.get("net_io_counters")
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

            # Directory sizes (cached with 60s TTL to avoid filesystem hits)
            dir_sizes = self._get_cached_dir_walk_sizes()
            _gauge(
                "changes_worker_log_dir_size_bytes",
                "Total size of the log directory in bytes.",
                dir_sizes["log_bytes"],
            )
            _gauge(
                "changes_worker_cbl_db_size_bytes",
                "Total size of the Couchbase Lite database in bytes.",
                dir_sizes["cbl_bytes"],
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
I’ve identified three concrete optimization targets that match your “temp memory for non-changing items” idea and show up in your hot paths: caching timing-stat computations in MetricsCollector.render(), avoiding expensive DLQ full-list loads for a simple count, and removing repeated parsing/loop work in _bulk_get response handling. Next I’ll implement these changes in main.py, rest/output_http.py, and rest/changes_http.py, then run compile checks to verify everything.

$
sed -n '200,430p' main.py
in change_stream_db

        # Auth tracking – outbound (output endpoint)
        self.outbound_auth_total: int = 0
        self.outbound_auth_success_total: int = 0
        self.outbound_auth_failure_total: int = 0

        # Checkpoint loads
        self.checkpoint_loads_total: int = 0
        self.checkpoint_load_errors_total: int = 0

        # Attachment processing
        self.attachments_detected_total: int = 0
        self.attachments_downloaded_total: int = 0
        self.attachments_download_errors_total: int = 0
        self.attachments_uploaded_total: int = 0
        self.attachments_upload_errors_total: int = 0
        self.attachments_bytes_downloaded_total: int = 0
        self.attachments_bytes_uploaded_total: int = 0
        self.attachments_post_process_total: int = 0
        self.attachments_post_process_errors_total: int = 0
        self.attachments_skipped_total: int = 0
        self.attachments_missing_total: int = 0
        self.attachments_digest_mismatch_total: int = 0
        self.attachments_stale_total: int = 0
        self.attachments_post_process_skipped_total: int = 0
        self.attachments_conflict_retries_total: int = 0
        self.attachments_orphaned_uploads_total: int = 0
        self.attachments_partial_success_total: int = 0
        self.attachments_temp_files_cleaned_total: int = 0

        # Flood / backpressure detection
        self.largest_batch_received: int = 0
        self.flood_batches_total: int = 0  # batches exceeding flood threshold
        self.flood_threshold: int = 10000  # configurable via set()

        # Gauges (can go up and down)
        self.changes_pending: int = 0  # received - processed (backpressure)
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

        # Auth timing deques
        self._inbound_auth_times: deque[float] = deque(maxlen=10000)
        self._outbound_auth_times: deque[float] = deque(maxlen=10000)

        # System metrics cache (TTL=15s for psutil, 60s for directory walks)
        self._system_metrics_cache: dict | None = None
        self._system_metrics_cache_time: float = 0
        self._dir_walk_cache: dict | None = None
        self._dir_walk_cache_time: float = 0

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

    def record_inbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._inbound_auth_times.append(seconds)

    def record_outbound_auth_time(self, seconds: float) -> None:
        with self._lock:
            self._outbound_auth_times.append(seconds)

    def record_batch_received(self, batch_size: int) -> None:
        with self._lock:
            if batch_size > self.largest_batch_received:
                self.largest_batch_received = batch_size
            if batch_size >= self.flood_threshold:
                self.flood_batches_total += 1
            self.changes_pending = (
                self.changes_received_total - self.changes_processed_total
            )

    def _get_cached_system_metrics(self) -> dict:
        """Cache psutil calls with 15s TTL to avoid syscalls on every scrape."""
        now = time.monotonic()
        if (
            self._system_metrics_cache is not None
            and now - self._system_metrics_cache_time < 15
        ):
            return self._system_metrics_cache

        cache = {}
        try:
            cache["gc_counts"] = gc.get_count()
            cache["gc_stats"] = gc.get_stats()
            cache["cpu_count"] = psutil.cpu_count(logical=True)
            cache["cpu_percent"] = psutil.cpu_percent(interval=0)
            cache["virtual_memory"] = psutil.virtual_memory()
            cache["swap_memory"] = psutil.swap_memory()
            try:
                cache["disk_usage"] = psutil.disk_usage("/")
            except OSError:
                cache["disk_usage"] = None
            cache["net_io_counters"] = psutil.net_io_counters()
        except Exception:
            pass  # system metrics are best-effort
        self._system_metrics_cache = cache
        self._system_metrics_cache_time = now
        return cache

    def _get_cached_dir_walk_sizes(self) -> dict:
        """Cache directory walk results with 60s TTL to avoid filesystem hits."""
        now = time.monotonic()
        if (
            self._dir_walk_cache is not None
            and now - self._dir_walk_cache_time < 60
        ):
            return self._dir_walk_cache

        cache = {"log_bytes": 0, "cbl_bytes": 0}
        try:
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
                cache["log_bytes"] = total_log_bytes

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
                cache["cbl_bytes"] = total_cbl_bytes
        except Exception:
            pass  # directory walks are best-effort
        self._dir_walk_cache = cache
        self._dir_walk_cache_time = now
        return cache

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
            iat = list(self._inbound_auth_times)
            oat = list(self._outbound_auth_times)

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
        iat_count, iat_sum, iat_sorted = _stats(iat)
        oat_count, oat_sum, oat_sorted = _stats(oat)

        lines: list[str] = []

        def _counter(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{{{labels}}} {value}")

        def _gauge(name: str, help_text: str, value):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{labels}}} {value}")
$
sed -n '900,1245p' rest/output_http.py
in change_stream_db

            return resp.status < 500
        except asyncio.TimeoutError:
            ic("_health_check: timeout", self._hc_url)
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check timed out",
                url=self._hc_url,
                error_detail=f"timeout after {self._hc_timeout}s",
            )
            return False
        except aiohttp.ClientConnectorError as exc:
            ic("_health_check: connection error", type(exc).__name__, str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check connection failed",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False
        except aiohttp.ClientSSLError as exc:
            ic("_health_check: SSL error", str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check SSL/TLS error",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False
        except (ConnectionError, aiohttp.ClientError, OSError) as exc:
            ic("_health_check: error", type(exc).__name__, str(exc))
            if self._metrics:
                self._metrics.inc("health_probe_failures_total")
            log_event(
                logger,
                "warn",
                "HTTP",
                "health check failed",
                url=self._hc_url,
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            return False

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

def determine_method(
    change: dict, write_method: str = "PUT", delete_method: str = "DELETE"
) -> str:
    if change.get("deleted"):
        return delete_method
    return write_method

def _build_dlq_reason(result: dict) -> str:
    """Build a human-readable reason string for a DLQ entry."""
    error_class = result.get("error_class", "")
    status = result.get("status", 0)
    if error_class:
        return f"data_error:{error_class}"
    if isinstance(status, int) and 400 <= status < 500:
        return f"client_error:{status}"
    if isinstance(status, int) and 300 <= status < 400:
        return f"redirect:{status}"
    if isinstance(status, int) and status >= 500:
        return f"server_error:{status}"
    if result.get("error") == "shutdown_inflight":
        return "shutdown_inflight"
    if isinstance(status, int) and status == 0:
        return "connection_failure"
    return "unknown"

class DeadLetterQueue:
    """
    Dead letter queue for documents that failed output delivery.

    When CBL is available, entries are stored as CBL documents.
    Otherwise falls back to append-only JSONL file.
    """

    def __init__(self, path: str, dlq_cfg: dict | None = None):
        from cbl_store import USE_CBL as _use_cbl

        self._use_cbl = _use_cbl
        self._store = None
        if self._use_cbl:
            from cbl_store import CBLStore

            self._store = CBLStore()
        self._path = Path(path) if path and not self._use_cbl else None
        self._lock = asyncio.Lock()
        cfg = dlq_cfg or {}
        self._retention_seconds = cfg.get("retention_seconds", 86400)
        self._max_replay_attempts = cfg.get("max_replay_attempts", 10)

    @property
    def enabled(self) -> bool:
        return self._use_cbl or self._path is not None

    async def write(
        self,
        doc: dict,
        result: dict,
        seq: str | int,
        target_url: str = "",
        metrics=None,
    ) -> None:
        ic("DLQ.write", result.get("doc_id"), seq, "cbl" if self._use_cbl else "file")
        reason = _build_dlq_reason(result)
        if self._use_cbl and self._store:
            self._store.add_dlq_entry(
                doc_id=result.get("doc_id", "unknown"),
                seq=str(seq),
                method=result.get("method", "PUT"),
                status=result.get("status", 0),
                error=result.get("error", ""),
                doc=doc,
                target_url=target_url,
                ttl_seconds=self._retention_seconds,
                reason=reason,
            )
            log_event(
                logger,
                "warn",
                "DLQ",
                "entry written to CBL",
                operation="INSERT",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="cbl",
                ttl_seconds=self._retention_seconds,
                target_url=target_url,
            )
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
            "reason": reason,
            "time": int(time.time()),
            "target_url": target_url,
            "replay_attempts": 0,
            "doc": doc,
        }
        try:
            async with self._lock:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            ic("DLQ.write: file write failed", self._path, exc)
            log_event(
                logger,
                "error",
                "DLQ",
                "failed to write DLQ entry to file — DATA MAY BE LOST",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="file",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            if metrics:
                metrics.inc("dlq_write_failures_total")
            raise
        log_event(
            logger,
            "warn",
            "DLQ",
            "entry written to file",
            operation="INSERT",
            doc_id=result.get("doc_id"),
            seq=str(seq),
            storage="file",
        )

    async def purge(self, dlq_id: str) -> None:
        """Remove a DLQ entry after successful reprocessing."""
        ic("DLQ.purge", dlq_id, "cbl" if self._use_cbl else "file")
        if self._use_cbl and self._store:
            self._store.delete_dlq_entry(dlq_id)
            log_event(
                logger,
                "info",
                "DLQ",
                "entry purged after successful reprocessing",
                operation="DELETE",
                doc_id=dlq_id,
                storage="cbl",
            )
            return
        # File-based DLQ does not support individual purge
        log_event(
            logger,
            "debug",
            "DLQ",
            "file-based DLQ does not support purge",
            doc_id=dlq_id,
            storage="file",
        )

    def flush_insert_meta(self, job_id: str = "") -> None:
        """Record last_inserted_at once after a batch of DLQ writes."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_inserted_at", job_id)

    def flush_drain_meta(self, job_id: str = "") -> None:
        """Record last_drained_at once after a batch of DLQ drains."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_drained_at", job_id)

    def list_pending(self) -> list[dict]:
        """Return all pending (not yet retried) DLQ entries."""
        if self._use_cbl and self._store:
            return [e for e in self._store.list_dlq() if not e.get("retried")]
        if self._path and self._path.exists():
            entries = []
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            return entries
        return []

    def get_entry_doc(self, dlq_id: str) -> dict | None:
        """Return the full DLQ entry including doc_data for reprocessing."""
        if self._use_cbl and self._store:
            return self._store.get_dlq_entry(dlq_id)
        return None

    def purge_expired(self) -> int:
        """Remove DLQ entries older than retention_seconds. Returns count purged."""
        if self._use_cbl and self._store and self._retention_seconds > 0:
            return self._store.purge_expired_dlq(self._retention_seconds)
        return 0

    def increment_replay_attempts(self, dlq_id: str) -> int:
        """Increment replay_attempts on a CBL DLQ entry. Returns new count."""
        if self._use_cbl and self._store:
            return self._store.increment_dlq_replay_attempts(dlq_id)
        return 0

    @property
    def max_replay_attempts(self) -> int:
        return self._max_replay_attempts

    @property
    def retention_seconds(self) -> int:
        return self._retention_seconds

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
$
sed -n '330,500p' rest/changes_http.py
in change_stream_db


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
$
grep -n "def add_dlq_entry\|retried\|replay_attempts" -n cbl_store.py | head -n 80
in change_stream_db

372:        db, COLL_DLQ, "idx_dlq_type_retried", "type, retried"
459:                f" d.retried, d.replay_attempts, d.target_url"
476:            "count_retried": _run_n1ql_explain(
479:                f" WHERE d.type = 'dlq' AND d.retried = true",
1232:    def add_dlq_entry(
1258:        dlq_doc["retried"] = False
1259:        dlq_doc["replay_attempts"] = 0
1284:            f" d.retried, d.replay_attempts, d.target_url"
1304:        "replay_attempts": "d.replay_attempts",
1310:        "retried": "d.retried",
1342:                f" d.retried, d.replay_attempts, d.target_url"
1416:            retried = (
1420:                    f" WHERE d.type = 'dlq' AND d.retried = true",
1424:            pending = total - retried
1461:                "retried": retried,
1478:                "retried": 0,
1539:                "retried": props.get("retried", False),
1540:                "replay_attempts": props.get("replay_attempts", 0),
1556:    def mark_dlq_retried(self, dlq_id: str) -> None:
1560:        doc["retried"] = True
1566:            "entry marked retried",
1572:    def increment_dlq_replay_attempts(self, dlq_id: str) -> int:
1573:        """Increment the replay_attempts counter on a DLQ entry. Returns new count."""
1577:        attempts = doc.properties.get("replay_attempts", 0) + 1
1578:        doc["replay_attempts"] = attempts
$
sed -n '1205,1665p' cbl_store.py
in change_stream_db

        """Delete all saved source configurations."""
        ic("clear_all_sources: entry")
        try:
            sources = self.load_sources()
            for source_name in sources.keys():
                self.delete_source(source_name)
            log_event(
                logger,
                "info",
                "CBL",
                f"cleared all sources ({len(sources)} deleted)",
                operation="DELETE",
                doc_type="source",
                count=len(sources),
            )
        except Exception as e:
            log_event(
                logger,
                "error",
                "CBL",
                f"Failed to clear all sources: {e}",
                operation="DELETE",
                doc_type="source",
            )

        # ── Dead Letter Queue ─────────────────────────────────────

    def add_dlq_entry(
        self,
        doc_id: str,
        seq: str,
        method: str,
        status: int,
        error: str,
        doc: dict,
        target_url: str = "",
        ttl_seconds: int = 0,
        reason: str = "",
    ) -> None:
        ic("add_dlq_entry: entry", doc_id)
        ts = int(time.time())
        dlq_id = f"dlq:{doc_id}:{ts}"
        t0 = time.monotonic()
        dlq_doc = MutableDocument(dlq_id)
        dlq_doc["type"] = "dlq"
        dlq_doc["doc_id_original"] = doc_id
        dlq_doc["seq"] = seq
        dlq_doc["method"] = method
        dlq_doc["status"] = status
        dlq_doc["error"] = error
        dlq_doc["reason"] = reason
        dlq_doc["time"] = ts
        dlq_doc["expires_at"] = (ts + ttl_seconds) if ttl_seconds > 0 else 0
        dlq_doc["retried"] = False
        dlq_doc["replay_attempts"] = 0
        dlq_doc["target_url"] = target_url
        dlq_doc["doc_data"] = json.dumps(doc)
        _coll_save_doc(self.db, COLL_DLQ, dlq_doc)
        if ttl_seconds > 0:
            _set_doc_expiration(self.db, COLL_DLQ, dlq_id, ttl_seconds)
        elapsed = (time.monotonic() - t0) * 1000

        log_event(
            logger,
            "warn",
            "DLQ",
            "entry added",
            operation="INSERT",
            doc_id=dlq_id,
            doc_type="dlq",
            seq=seq,
            status=status,
            duration_ms=round(elapsed, 1),
        )

    def list_dlq(self) -> list[dict]:
        sql = (
            f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
            f" d.status, d.error, d.reason, d.time, d.expires_at,"
            f" d.retried, d.replay_attempts, d.target_url"
            f" FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            f" ORDER BY d.time DESC, META(d).id DESC"
        )
        result = _run_n1ql(self.db, sql)
        log_event(
            logger,
            "debug",
            "DLQ",
            "listed entries",
            operation="SELECT",
            doc_type="dlq",
            doc_count=len(result),
        )
        return result

    # Whitelist of allowed sort columns for N1QL ORDER BY
    _DLQ_SORT_COLS = {
        "time": "d.time",
        "expires_at": "d.expires_at",
        "replay_attempts": "d.replay_attempts",
        "status": "d.status",
        "reason": "d.reason",
        "method": "d.method",
        "doc_id_original": "d.doc_id_original",
        "error": "d.error",
        "retried": "d.retried",
    }

    def list_dlq_page(
        self,
        limit: int = 20,
        offset: int = 0,
        sort: str = "time",
        order: str = "desc",
        reason_filter: str = "",
    ) -> dict:
        """Return a page of DLQ entries with server-side sort/filter/pagination.

        Uses N1QL queries with collection-level indexes.  Returns
        ``{"entries": [...], "total": N, "filtered": N}``.
        Returns empty page on query error.
        """
        try:
            sort_col = self._DLQ_SORT_COLS.get(sort, "d.time")
            direction = "ASC" if order.lower() == "asc" else "DESC"

            # Build WHERE clause
            where = "d.type = 'dlq'"
            params: dict | None = None
            if reason_filter:
                where += " AND LOWER(d.reason) LIKE $reason_like"
                params = {"reason_like": reason_filter.lower() + "%"}

            # Page query (LIMIT/OFFSET must be literal ints in CBL N1QL)
            sql = (
                f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
                f" d.status, d.error, d.reason, d.time, d.expires_at,"
                f" d.retried, d.replay_attempts, d.target_url"
                f" FROM {_DLQ_FROM} AS d WHERE {where}"
                f" ORDER BY {sort_col} {direction}, META(d).id {direction}"
                f" LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            entries = _run_n1ql(self.db, sql, params)

            # Count queries
            total_sql = f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            total_count = _run_n1ql_scalar(self.db, total_sql) or 0

            if reason_filter:
                filter_sql = f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE {where}"
                filtered_count = _run_n1ql_scalar(self.db, filter_sql, params) or 0
            else:
                filtered_count = total_count

            log_event(
                logger,
                "debug",
                "DLQ",
                "listed page",
                operation="SELECT",
                doc_type="dlq",
                offset=offset,
                limit=limit,
                filtered=filtered_count,
                total=total_count,
            )
            return {
                "entries": entries,
                "total": total_count,
                "filtered": filtered_count,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"Failed to list DLQ page: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
                doc_type="dlq",
                offset=offset,
                limit=limit,
            )
            # Return empty page on error
            return {
                "entries": [],
                "total": 0,
                "filtered": 0,
            }

    def dlq_stats(self) -> dict:
        """Return lightweight aggregation data for DLQ charts and summary cards.

        Uses N1QL aggregation queries instead of scanning all documents.
        Returns defaults on query error.
        """
        try:
            f = _DLQ_FROM

            # Totals + oldest in one query
            row = (
                _run_n1ql(
                    self.db,
                    f"SELECT COUNT(*) AS total, MIN(d.time) AS oldest_time"
                    f" FROM {f} AS d WHERE d.type = 'dlq'",
                )
                or [{}]
            )[0]
            total = row.get("total", 0)
            oldest_time = row.get("oldest_time")

            # Retried count
            retried = (
                _run_n1ql_scalar(
                    self.db,
                    f"SELECT COUNT(*) FROM {f} AS d"
                    f" WHERE d.type = 'dlq' AND d.retried = true",
                )
                or 0
            )
            pending = total - retried

            # Reason breakdown
            reason_rows = _run_n1ql(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" GROUP BY d.reason",
            )
            reason_counts: dict[str, int] = {}
            for r in reason_rows:
                key = r.get("reason", "") or "unknown"
                reason_counts[key] = r.get("count", 0)

            # Timeline by reason (stacked bar chart) — grouped by time buckets
            timeline_rows = _run_n1ql(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count,"
                f" FLOOR(d.time / 300) * 300 AS time_bucket"
                f" FROM {f} AS d WHERE d.type = 'dlq' AND d.time > 0"
                f" GROUP BY d.reason, time_bucket"
                f" ORDER BY time_bucket",
            )
            timeline: dict[str, dict[str, int]] = {}  # {time_key: {reason: count}}
            for r in timeline_rows:
                bucket = r.get("time_bucket", 0)
                reason = r.get("reason", "") or "unknown"
                count = r.get("count", 0)
                if bucket:
                    time_key = time.strftime("%Y-%m-%d %H:%M", time.gmtime(bucket))
                    if time_key not in timeline:
                        timeline[time_key] = {}
                    timeline[time_key][reason] = count

            return {
                "total": total,
                "pending": pending,
                "retried": retried,
                "oldest_time": oldest_time,
                "reason_counts": reason_counts,
                "timeline": timeline,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"DLQ stats query failed: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
            )
            # Return empty stats on error
            return {
                "total": 0,
                "pending": 0,
                "retried": 0,
                "oldest_time": None,
                "reason_counts": {},
                "timeline": {},
            }

    def get_dlq_entry(self, dlq_id: str) -> dict | None:
        """Get a DLQ entry by ID. Returns dict or None if not found.

        Handles JSON parsing errors gracefully.
        """
        try:
            doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
            if not doc:
                log_event(
                    logger,
                    "debug",
                    "DLQ",
                    "entry not found",
                    operation="SELECT",
                    doc_id=dlq_id,
                    doc_type="dlq",
                )
                return None
            props = doc.properties
            doc_data = props.get("doc_data", "{}")

            # Parse JSON with error handling
            try:
                parsed_data = json.loads(doc_data)
            except json.JSONDecodeError as e:
                log_event(
                    logger,
                    "warn",
                    "DLQ",
                    f"doc_data is malformed JSON: {e}",
                    operation="SELECT",
                    doc_id=dlq_id,
                    doc_type="dlq",
                )
                parsed_data = {}

            log_event(
                logger,
                "debug",
                "DLQ",
                "entry loaded",
                operation="SELECT",
                doc_id=dlq_id,
                doc_type="dlq",
            )
            return {
                "id": dlq_id,
                "doc_id_original": props.get("doc_id_original", ""),
                "seq": props.get("seq", ""),
                "method": props.get("method", ""),
                "status": props.get("status", 0),
                "error": props.get("error", ""),
                "reason": props.get("reason", ""),
                "time": props.get("time", 0),
                "expires_at": props.get("expires_at", 0),
                "retried": props.get("retried", False),
                "replay_attempts": props.get("replay_attempts", 0),
                "target_url": props.get("target_url", ""),
                "doc_data": parsed_data,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"Failed to get DLQ entry: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
                doc_id=dlq_id,
                doc_type="dlq",
            )
            return None

    def mark_dlq_retried(self, dlq_id: str) -> None:
        doc = _coll_get_mutable_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return
        doc["retried"] = True
        _coll_save_doc(self.db, COLL_DLQ, doc)
        log_event(
            logger,
            "info",
            "DLQ",
            "entry marked retried",
            operation="UPDATE",
            doc_id=dlq_id,
            doc_type="dlq",
        )

    def increment_dlq_replay_attempts(self, dlq_id: str) -> int:
        """Increment the replay_attempts counter on a DLQ entry. Returns new count."""
        doc = _coll_get_mutable_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return 0
        attempts = doc.properties.get("replay_attempts", 0) + 1
        doc["replay_attempts"] = attempts
        _coll_save_doc(self.db, COLL_DLQ, doc)
        return attempts

    def delete_dlq_entry(self, dlq_id: str) -> None:
        ic("delete_dlq_entry: entry", dlq_id)
        doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return
        _coll_purge_doc(self.db, COLL_DLQ, dlq_id)

        log_event(
            logger,
            "info",
            "DLQ",
            "entry purged",
            operation="DELETE",
            doc_id=dlq_id,
            doc_type="dlq",
        )

    def clear_dlq(self) -> None:
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'",
        )
        count = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    count += 1
        if count > 0:
            self.update_dlq_meta("last_drained_at")
        log_event(
            logger,
            "info",
            "DLQ",
            "queue cleared",
            operation="DELETE",
            doc_type="dlq",
            doc_count=count,
        )

    def dlq_count(self) -> int:
        return (
            _run_n1ql_scalar(
                self.db, f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            )
            or 0
        )

    def purge_expired_dlq(self, max_age_seconds: int) -> int:
        """Purge DLQ entries older than max_age_seconds. Returns count purged."""
        if max_age_seconds <= 0:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d"
            f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
            {"cutoff": cutoff},
        )
        purged = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    purged += 1
        if purged > 0:
            log_event(
                logger,
                "info",
                "DLQ",
                "purged %d expired entries (older than %ds)"
                % (purged, max_age_seconds),
                operation="DELETE",
                doc_type="dlq",
                doc_count=purged,
            )
        return purged

    def get_dlq_meta(self) -> dict:
        """Return DLQ metadata (last_inserted_at, last_drained_at as epoch)."""
        doc = _coll_get_doc(self.db, COLL_DLQ, "dlq:meta")
        if not doc:
$
sed -n '300,520p' cbl_store.py
in change_stream_db

            sql=sql[:200],
            params=str(params)[:100] if params else None,
        )
        raise RuntimeError(f"N1QL query failed: {e}") from e

def _run_n1ql_scalar(db, sql: str, params: dict | None = None):
    """Execute a N1QL (SQL++) query and return the first column of the first row.

    Returns None if no rows match.
    Raises RuntimeError on query failure.
    """
    try:
        q = N1QLQuery(db, sql)
        if params:
            q.setParameters(params)
        for row in q.execute():
            return row[0]
        return None
    except Exception as e:
        log_event(
            logger,
            "error",
            "CBL",
            f"N1QL scalar query failed: {type(e).__name__}: {str(e)[:200]}",
            sql=sql[:200],
            params=str(params)[:100] if params else None,
        )
        raise RuntimeError(f"N1QL scalar query failed: {e}") from e

def _run_n1ql_explain(db, sql: str, params: dict | None = None) -> str:
    """Return the CBLQuery_Explain output for a N1QL (SQL++) query.

    Raises RuntimeError if explain fails.
    """
    try:
        q = N1QLQuery(db, sql)
        if params:
            q.setParameters(params)
        return q.explanation or ""
    except Exception as e:
        log_event(
            logger,
            "error",
            "CBL",
            f"N1QL explain failed: {type(e).__name__}: {str(e)[:200]}",
            sql=sql[:200],
        )
        raise RuntimeError(f"N1QL explain failed: {e}") from e

# N1QL FROM clause for the DLQ collection (scope name needs backtick-quoting)
_DLQ_FROM = "`changes-worker`.dlq"

_DLQ_INDEXES_ENSURED = False

def _ensure_dlq_indexes(db) -> None:
    """Create value indexes on the DLQ collection (idempotent, once per process).

    ``type`` must be the leading column so the planner can use the index
    for ``WHERE d.type = 'dlq'`` which appears in every DLQ query.
    """
    global _DLQ_INDEXES_ENSURED
    if _DLQ_INDEXES_ENSURED:
        return
    _create_collection_value_index(db, COLL_DLQ, "idx_dlq_type_time", "type, time")
    _create_collection_value_index(
        db, COLL_DLQ, "idx_dlq_type_reason_time", "type, reason, time"
    )
    _create_collection_value_index(
        db, COLL_DLQ, "idx_dlq_type_retried", "type, retried"
    )
    _DLQ_INDEXES_ENSURED = True
    log_event(logger, "info", "CBL", "DLQ indexes ensured")

class _transaction:
    """Context manager for CBL database transactions.

    Wraps ``CBLDatabase_BeginTransaction`` / ``CBLDatabase_EndTransaction``.
    Commits on clean exit, rolls back on exception.

    Usage::

        with _transaction(db):
            _coll_save_doc(db, coll, doc1)
            _coll_save_doc(db, coll, doc2)
    """

    __slots__ = ("_db_ref",)

    def __init__(self, db):
        self._db_ref = db._ref

    def __enter__(self):
        err = ffi.new("CBLError*")
        if not lib.CBLDatabase_BeginTransaction(self._db_ref, err):
            raise RuntimeError("Failed to begin CBL transaction")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        commit = exc_type is None
        err = ffi.new("CBLError*")
        ok = lib.CBLDatabase_EndTransaction(self._db_ref, commit, err)
        if not ok:
            log_event(
                logger,
                "error",
                "CBL",
                f"Failed to end CBL transaction (commit={commit})",
                error_code=err.code if err else None,
            )
            # Don't raise — the transaction state may be inconsistent
            # Log the error but allow any original exception to propagate
        return False  # don't suppress exceptions

def _set_doc_expiration(
    db, collection_name: str, doc_id: str, ttl_seconds: int
) -> bool:
    """Set document expiration (TTL) on a specific collection using the CBL C API.

    Args:
        db: CBL database handle.
        collection_name: Collection within CBL_SCOPE.
        doc_id: Document ID.
        ttl_seconds: Seconds from now until the document expires and is auto-purged.
                     Pass 0 to clear expiration.
    """
    if ttl_seconds <= 0:
        return True
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    expiration_ms = int((time.time() + ttl_seconds) * 1000)
    err = ffi.new("CBLError*")
    ok = lib.CBLCollection_SetDocumentExpiration(
        coll, stringParam(doc_id), expiration_ms, err
    )
    return bool(ok)

class CBLStore:
    """High-level API for all CBL storage operations."""

    def __init__(self):
        self.db = get_db()
        _ensure_dlq_indexes(self.db)

    # ── Info / diagnostics ────────────────────────────────────

    def dlq_explain_queries(self) -> dict[str, str]:
        """Return EXPLAIN output for the key DLQ queries to verify index usage."""
        f = _DLQ_FROM
        return {
            "list_page_by_time": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
                f" d.status, d.error, d.reason, d.time, d.expires_at,"
                f" d.retried, d.replay_attempts, d.target_url"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" ORDER BY d.time DESC, META(d).id DESC"
                f" LIMIT 20 OFFSET 0",
            ),
            "list_page_by_reason_filter": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id, d.doc_id_original"
                f" FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND LOWER(d.reason) LIKE $reason_like"
                f" ORDER BY d.time DESC, META(d).id DESC"
                f" LIMIT 20 OFFSET 0",
                {"reason_like": "data_error%"},
            ),
            "count_total": _run_n1ql_explain(
                self.db, f"SELECT COUNT(*) FROM {f} AS d WHERE d.type = 'dlq'"
            ),
            "count_retried": _run_n1ql_explain(
                self.db,
                f"SELECT COUNT(*) FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND d.retried = true",
            ),
            "stats_totals": _run_n1ql_explain(
                self.db,
                f"SELECT COUNT(*) AS total, MIN(d.time) AS oldest_time"
                f" FROM {f} AS d WHERE d.type = 'dlq'",
            ),
            "stats_reason_group": _run_n1ql_explain(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" GROUP BY d.reason",
            ),
            "purge_expired": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
                {"cutoff": 1000000000},
            ),
        }

    def db_info(self) -> dict:
        """Return database path, size, and document counts."""
        info = {
            "db_name": CBL_DB_NAME,
            "db_path": _db_file_path(),
            "db_size_mb": _db_size_mb(),
            "scope": CBL_SCOPE,
            "collections": [
                COLL_CONFIG,
                COLL_INPUTS_CHANGES,
                COLL_OUTPUTS_RDBMS,
                COLL_OUTPUTS_HTTP,
                COLL_OUTPUTS_CLOUD,
                COLL_OUTPUTS_STDOUT,
                COLL_JOBS,
                COLL_CHECKPOINTS,
                COLL_DLQ,
                COLL_DATA_QUALITY,
                COLL_ENRICHMENTS,
                COLL_SESSIONS,
                COLL_USERS,
1 file 92 changes

main.py
+70
-22

$
sed -n '980,1235p' rest/output_http.py
in change_stream_db

    """Build a human-readable reason string for a DLQ entry."""
    error_class = result.get("error_class", "")
    status = result.get("status", 0)
    if error_class:
        return f"data_error:{error_class}"
    if isinstance(status, int) and 400 <= status < 500:
        return f"client_error:{status}"
    if isinstance(status, int) and 300 <= status < 400:
        return f"redirect:{status}"
    if isinstance(status, int) and status >= 500:
        return f"server_error:{status}"
    if result.get("error") == "shutdown_inflight":
        return "shutdown_inflight"
    if isinstance(status, int) and status == 0:
        return "connection_failure"
    return "unknown"

class DeadLetterQueue:
    """
    Dead letter queue for documents that failed output delivery.

    When CBL is available, entries are stored as CBL documents.
    Otherwise falls back to append-only JSONL file.
    """

    def __init__(self, path: str, dlq_cfg: dict | None = None):
        from cbl_store import USE_CBL as _use_cbl

        self._use_cbl = _use_cbl
        self._store = None
        if self._use_cbl:
            from cbl_store import CBLStore

            self._store = CBLStore()
        self._path = Path(path) if path and not self._use_cbl else None
        self._lock = asyncio.Lock()
        cfg = dlq_cfg or {}
        self._retention_seconds = cfg.get("retention_seconds", 86400)
        self._max_replay_attempts = cfg.get("max_replay_attempts", 10)

    @property
    def enabled(self) -> bool:
        return self._use_cbl or self._path is not None

    async def write(
        self,
        doc: dict,
        result: dict,
        seq: str | int,
        target_url: str = "",
        metrics=None,
    ) -> None:
        ic("DLQ.write", result.get("doc_id"), seq, "cbl" if self._use_cbl else "file")
        reason = _build_dlq_reason(result)
        if self._use_cbl and self._store:
            self._store.add_dlq_entry(
                doc_id=result.get("doc_id", "unknown"),
                seq=str(seq),
                method=result.get("method", "PUT"),
                status=result.get("status", 0),
                error=result.get("error", ""),
                doc=doc,
                target_url=target_url,
                ttl_seconds=self._retention_seconds,
                reason=reason,
            )
            log_event(
                logger,
                "warn",
                "DLQ",
                "entry written to CBL",
                operation="INSERT",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="cbl",
                ttl_seconds=self._retention_seconds,
                target_url=target_url,
            )
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
            "reason": reason,
            "time": int(time.time()),
            "target_url": target_url,
            "replay_attempts": 0,
            "doc": doc,
        }
        try:
            async with self._lock:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            ic("DLQ.write: file write failed", self._path, exc)
            log_event(
                logger,
                "error",
                "DLQ",
                "failed to write DLQ entry to file — DATA MAY BE LOST",
                doc_id=result.get("doc_id"),
                seq=str(seq),
                storage="file",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            if metrics:
                metrics.inc("dlq_write_failures_total")
            raise
        log_event(
            logger,
            "warn",
            "DLQ",
            "entry written to file",
            operation="INSERT",
            doc_id=result.get("doc_id"),
            seq=str(seq),
            storage="file",
        )

    async def purge(self, dlq_id: str) -> None:
        """Remove a DLQ entry after successful reprocessing."""
        ic("DLQ.purge", dlq_id, "cbl" if self._use_cbl else "file")
        if self._use_cbl and self._store:
            self._store.delete_dlq_entry(dlq_id)
            log_event(
                logger,
                "info",
                "DLQ",
                "entry purged after successful reprocessing",
                operation="DELETE",
                doc_id=dlq_id,
                storage="cbl",
            )
            return
        # File-based DLQ does not support individual purge
        log_event(
            logger,
            "debug",
            "DLQ",
            "file-based DLQ does not support purge",
            doc_id=dlq_id,
            storage="file",
        )

    def flush_insert_meta(self, job_id: str = "") -> None:
        """Record last_inserted_at once after a batch of DLQ writes."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_inserted_at", job_id)

    def flush_drain_meta(self, job_id: str = "") -> None:
        """Record last_drained_at once after a batch of DLQ drains."""
        if self._use_cbl and self._store:
            self._store.update_dlq_meta("last_drained_at", job_id)

    def list_pending(self) -> list[dict]:
        """Return all pending (not yet retried) DLQ entries."""
        if self._use_cbl and self._store:
            return [e for e in self._store.list_dlq() if not e.get("retried")]
        if self._path and self._path.exists():
            entries = []
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            return entries
        return []

    def get_entry_doc(self, dlq_id: str) -> dict | None:
        """Return the full DLQ entry including doc_data for reprocessing."""
        if self._use_cbl and self._store:
            return self._store.get_dlq_entry(dlq_id)
        return None

    def purge_expired(self) -> int:
        """Remove DLQ entries older than retention_seconds. Returns count purged."""
        if self._use_cbl and self._store and self._retention_seconds > 0:
            return self._store.purge_expired_dlq(self._retention_seconds)
        return 0

    def increment_replay_attempts(self, dlq_id: str) -> int:
        """Increment replay_attempts on a CBL DLQ entry. Returns new count."""
        if self._use_cbl and self._store:
            return self._store.increment_dlq_replay_attempts(dlq_id)
        return 0

    @property
    def max_replay_attempts(self) -> int:
        return self._max_replay_attempts

    @property
    def retention_seconds(self) -> int:
        return self._retention_seconds

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
1 file 9 changes

rest
/
output_http.py
+9

$
sed -n '1180,1225p' rest/changes_http.py
in change_stream_db

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
$
sed -n '340,495p' rest/changes_http.py
in change_stream_db

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
1 file 8 changes

rest
/
output_http.py
+4
-4

$
sed -n '1595,1675p' cbl_store.py
in change_stream_db

            doc_id=dlq_id,
            doc_type="dlq",
        )

    def clear_dlq(self) -> None:
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'",
        )
        count = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    count += 1
        if count > 0:
            self.update_dlq_meta("last_drained_at")
        log_event(
            logger,
            "info",
            "DLQ",
            "queue cleared",
            operation="DELETE",
            doc_type="dlq",
            doc_count=count,
        )

    def dlq_count(self) -> int:
        return (
            _run_n1ql_scalar(
                self.db, f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            )
            or 0
        )

    def purge_expired_dlq(self, max_age_seconds: int) -> int:
        """Purge DLQ entries older than max_age_seconds. Returns count purged."""
        if max_age_seconds <= 0:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d"
            f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
            {"cutoff": cutoff},
        )
        purged = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    purged += 1
        if purged > 0:
            log_event(
                logger,
                "info",
                "DLQ",
                "purged %d expired entries (older than %ds)"
                % (purged, max_age_seconds),
                operation="DELETE",
                doc_type="dlq",
                doc_count=purged,
            )
        return purged

    def get_dlq_meta(self) -> dict:
        """Return DLQ metadata (last_inserted_at, last_drained_at as epoch)."""
        doc = _coll_get_doc(self.db, COLL_DLQ, "dlq:meta")
        if not doc:
            return {
                "last_inserted_at": None,
                "last_drained_at": None,
                "last_inserted_job": None,
                "last_drained_job": None,
            }
        props = doc.properties
        return {
            "last_inserted_at": props.get("last_inserted_at", None),
            "last_drained_at": props.get("last_drained_at", None),
1 file 11 changes

cbl_store.py
+11

1 file 15 changes

rest
/
changes_http.py
+11
-4

I’ve applied the first pass of optimizations: timing-stat caching in metrics rendering, server-side pending DLQ count support, and less repeated work in _bulk_get missing-doc detection. I’m now running compile checks and a quick diff review to ensure these are clean and behaviorally safe.

$
python3 -m compileall main.py rest/changes_http.py rest/output_http.py cbl_store.py
in change_stream_db

Compiling 'main.py'...
Compiling 'rest/changes_http.py'...
Compiling 'rest/output_http.py'...
Compiling 'cbl_store.py'...
$
grep -RIn "_output_resp_times\.append\|_changes_request_times\.append\|_batch_processing_times\.append\|_doc_fetch_times\.append\|_health_probe_times\.append\|_inbound_auth_times\.append\|_outbound_auth_times\.append" main.py db cloud rest | head -n 80
in change_stream_db

main.py:283:            self._output_resp_times.append(seconds)
main.py:288:            self._changes_request_times.append(seconds)
main.py:293:            self._batch_processing_times.append(seconds)
main.py:298:            self._doc_fetch_times.append(seconds)
main.py:303:            self._health_probe_times.append(seconds)
main.py:308:            self._inbound_auth_times.append(seconds)
main.py:313:            self._outbound_auth_times.append(seconds)
1 file 34 changes

rest
/
changes_http.py
+18
-16

$
python3 -m compileall rest/changes_http.py
in change_stream_db

Compiling 'rest/changes_http.py'...
$
git status --short
in change_stream_db

 D API_ENDPOINTS_VERIFICATION.md
 D API_V2_COMPLETE_FIX_REPORT.md
 D API_V2_FIX_SUMMARY.md
 D BACKEND_JOB_ID_LOGGING.md
 D BACKEND_UPDATE_COMPLETE.md
 D ENDPOINT_TIMEOUT_FIX.md
 D FINAL_SUMMARY.md
 D IMPLEMENTATION_COMPLETE.md
 D IMPLEMENTATION_SUMMARY.md
 D JOB_BUILDER_IMPLEMENTATION_REPORT.md
 D JOB_CONTROL_IMPROVEMENTS.md
 D JOB_ID_LOGGING_FIX.md
 D LOGS_FILTERING_FIXES.md
 D META_FIELD_REFACTOR_COMPLETE.md
 D META_FIELD_REFACTOR_REPORT.md
 D README_IMPLEMENTATION.md
 D SCHEMA_STANDARDS_COMPLETE.md
 D UPDATE_SUMMARY.md
 D VERIFICATION_CHECKLIST.md
 M cbl_store.py
 M main.py
 D profile.svg
 D profile2.svg
 D profile3.svg
 M rest/changes_http.py
 M rest/output_http.py
?? docs/3_OPTIMIZATION_REPORT.md
?? docs/4_OPTIMIZATION_CHANGES.md
?? docs/ARCHIVED/
?? profiling/
$
ls tests
in change_stream_db

__init__.py
__pycache__
test_api_v2_inputs.py
test_api_v2_jobs.py
test_api_v2_outputs.py
test_attachment_phase4.py
test_attachment_postprocess.py
test_attachment_stream.py
test_attachment_upload.py
test_attachments.py
test_cbl_store_v2.py
test_changes_feed_logic.py
test_changes_worker.py
test_db_mssql.py
test_db_mysql.py
test_db_oracle.py
test_migration_v1_to_v2.py
test_phase_10_threading.py
test_phase_6_job_based_startup.py
test_phase_7_config_cleanup.py
test_phase_8_dashboard.py
test_pipeline_logging.py
test_schema_mapper.py
test_schema_validator.py
$
python3 -m pytest tests/test_changes_feed_logic.py -q
in change_stream_db

............................................................. [ 65%]
................................                                                   [100%]
93 passed, 73 subtests passed in 0.49s
I ran unit coverage for _changes logic and it’s green. I’m making one more low-risk pass to remove repeated CBLStore() creation inside checkpoint fallback paths, then I’ll rerun compile/tests and summarize concrete gains plus next candidates.

$
sed -n '1935,2235p' main.py
in change_stream_db

        "gateway": input_entry,  # {url, database, src, scope, collection, auth}
        "auth": input_entry.get("auth", {}),
        "changes_feed": input_entry.get("changes_feed", {}),
        "processing": input_entry.get("processing", {}),
        "output": output_entry,  # {mode, target_url, ...}
        "output_type": job_doc.get("output_type", "http"),
        "checkpoint": {
            "enabled": True,
            "file": f"checkpoint_{job_id}.json",
        },
        "mapping": job_doc.get("mapping"),
        "system": job_doc.get("system", {}),
        "retry": job_doc.get("retry", {}),
        "metrics": job_doc.get("metrics", {}),
        "logging": job_doc.get("logging", {}),
    }

def migrate_legacy_config_to_job(db: CBLStore, cfg: dict) -> dict | None:
    """
    Auto-migrate v1.x config.json to a job document.

    Returns the migrated job document, or None if migration failed.
    """
    try:
        gw = cfg.get("gateway", {})
        out = cfg.get("output", {})

        if not gw or not out:
            logger.warning(
                "Legacy config missing gateway or output – cannot auto-migrate"
            )
            return None

        job_id = "legacy_auto_migrated_" + str(int(time.time()))
        job_name = "Auto-migrated v1.x config"

        job_data = {
            "name": job_name,
            "enabled": True,
            "inputs": [gw],
            "outputs": [out],
            "output_type": out.get("mode", "stdout"),
            "mapping": None,
            "system": cfg.get("system", {}),
            "retry": cfg.get("retry", {}),
        }

        # Save to CBL (save_job expects job_id and job_data separately)
        db.save_job(job_id, job_data)
        logger.info("Auto-migrated legacy config.json to job %s", job_id)

        # Return the full document as it would be retrieved
        job_doc = {"_id": job_id, "id": job_id, **job_data}
        return job_doc
    except Exception as e:
        logger.error("Failed to auto-migrate legacy config: %s", e)
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

    def __init__(
        self, cfg: dict, gw_cfg: dict, channels: list[str], job_id: str | None = None
    ):
        self._enabled = cfg.get("enabled", True)
        self._job_id = job_id  # Phase 6: per-job checkpoint isolation
        self._lock = asyncio.Lock()
        self._seq: str = "0"
        self._rev: str | None = None  # SG doc _rev for updates
        self._internal: int = 0
        self._initial_sync_done: bool = False

        # Build the deterministic UUID the same way CBL does:
        #   HASH(local_client_id + SG URL + channel_names + job_id)
        client_id = cfg.get("client_id", "changes_worker")
        sg_url = build_base_url(gw_cfg)
        channel_str = ",".join(sorted(channels)) if channels else ""
        job_str = job_id or ""  # Phase 6: include job_id in UUID for isolation
        raw = f"{client_id}{sg_url}{channel_str}{job_str}"
        self._uuid = hashlib.sha1(raw.encode()).hexdigest()
        self._client_id = client_id
        self._local_doc_id = f"checkpoint-{self._uuid}"

        # Fallback to local file when SG is unreachable for checkpoint ops
        # Phase 6: use job_id in fallback filename for isolation
        fallback_file = cfg.get("file", "checkpoint.json")
        if job_id:
            # Transform "checkpoint.json" -> "checkpoint_<job_id>.json"
            path = Path(fallback_file)
            fallback_file = str(path.parent / f"{path.stem}_{job_id}{path.suffix}")
        self._fallback_path = Path(fallback_file)

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
$
sed -n '2235,2295p' main.py
in change_stream_db

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
    job_id: str | None = None,  # Phase 6: job-specific identifier
) -> None:
    gw = cfg.get(
        "gateway", cfg.get("inputs", [{}])[0]
    )  # Support both old and new configs
    auth_cfg = cfg.get("auth", gw.get("auth", {}))  # Phase 6: auth from gateway
    feed_cfg = cfg.get(
        "changes_feed", gw.get("changes_feed", {})
    )  # Phase 6: changes_feed from gateway
    proc_cfg = cfg.get(
        "processing", gw.get("processing", {})
    )  # Phase 6: processing from gateway
    out_cfg = cfg.get(
        "output", cfg.get("outputs", [{}])[0]
$
grep -RIn "Checkpoint(" main.py | cat
in change_stream_db

main.py:2329:    checkpoint = Checkpoint(
main.py:2884:        checkpoint = Checkpoint(cfg.get("checkpoint", {}), gw, channels)
$
sed -n '2308,2368p' main.py
in change_stream_db

            waiters.append(asyncio.ensure_future(restart_event.wait()))
        done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        stop_event.set()
        for f in pending:
            f.cancel()

    watcher_task = asyncio.create_task(_watch_events())

    try:
        base_url = build_base_url(gw)
    except KeyError as e:
        watcher_task.cancel()
        raise KeyError(
            f"Missing gateway field {e} — check that the job's input has "
            f"'url' (or 'host') and 'database' configured"
        ) from e
    ssl_ctx = build_ssl_context(gw)
    basic_auth = build_basic_auth(auth_cfg)
    auth_headers = build_auth_headers(auth_cfg, src, compress=gw.get("compress", False))

    channels = feed_cfg.get("channels", [])
    checkpoint = Checkpoint(
        cfg.get("checkpoint", {}), gw, channels, job_id=job_id
    )  # Phase 6: pass job_id
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

        _DB_ENGINE_ALIASES = {"postgres", "mysql", "mssql", "oracle"}
        if output_mode in _DB_ENGINE_ALIASES:
            db_engine = output_mode
            output_mode = "db"
        elif output_mode == "db":
            db_engine = out_cfg.get("db", {}).get("engine", "postgres")

        if output_mode == "db":
            if db_engine == "postgres":
$
sed -n '2848,2915p' main.py
in change_stream_db

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
1 file 13 changes

main.py
+11
-2

$
python3 -m compileall main.py rest/changes_http.py rest/output_http.py cbl_store.py
in change_stream_db

Compiling 'main.py'...
$
python3 -m pytest tests/test_changes_feed_logic.py tests/test_changes_worker.py -q
in change_stream_db

............................................................. [ 19%]
.................................................................................. [ 45%]
........................................................................ [ 68%]
........................................................................ [ 90%]
.............................                                            [100%]
=============================== warnings summary ===============================
tests/test_changes_worker.py::TestMetricsServer::test_metrics_endpoint
  /Users/fujio.turner/Documents/GitHub/change_stream_db/tests/../main.py:1362: NotAppKeyWarning: It is recommended to use web.AppKey instances for keys.
  https://docs.aiohttp.org/en/stable/web_advanced.html#application-s-config
    app["metrics"] = metrics

tests/test_changes_worker.py::TestMetricsServer::test_metrics_endpoint
  /Users/fujio.turner/Documents/GitHub/change_stream_db/tests/../main.py:1363: NotAppKeyWarning: It is recommended to use web.AppKey instances for keys.
  https://docs.aiohttp.org/en/stable/web_advanced.html#application-s-config
    app["shutdown_cfg"] = shutdown_cfg or {}

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
316 passed, 2 warnings, 73 subtests passed in 31.90s
$
nl -ba main.py | sed -n '246,460p'
in change_stream_db

   246          self._changes_request_times: deque[float] = deque(maxlen=10000)
   247          self._batch_processing_times: deque[float] = deque(maxlen=10000)
   248          self._doc_fetch_times: deque[float] = deque(maxlen=10000)
   249          self._health_probe_times: deque[float] = deque(maxlen=10000)
   250
   251          # Auth timing deques
   252          self._inbound_auth_times: deque[float] = deque(maxlen=10000)
   253          self._outbound_auth_times: deque[float] = deque(maxlen=10000)
   254
   255          # Timing summary cache: avoid re-sorting unchanged deques on every scrape
   256          self._timing_versions: dict[str, int] = {
   257              "output": 0,
   258              "changes": 0,
   259              "batch": 0,
   260              "fetch": 0,
   261              "health": 0,
   262              "inbound_auth": 0,
   263              "outbound_auth": 0,
   264          }
   265          self._timing_stats_cache: dict[str, tuple[int, int, float, list[float]]] = {}
   266
   267          # System metrics cache (TTL=15s for psutil, 60s for directory walks)
   268          self._system_metrics_cache: dict | None = None
   269          self._system_metrics_cache_time: float = 0
   270          self._dir_walk_cache: dict | None = None
   271          self._dir_walk_cache_time: float = 0
   272
   273      def inc(self, name: str, value: int = 1) -> None:
   274          with self._lock:
   275              setattr(self, name, getattr(self, name) + value)
   276
   277      def set(self, name: str, value) -> None:
   278          with self._lock:
   279              setattr(self, name, value)
   280
   281      def record_output_response_time(self, seconds: float) -> None:
   282          with self._lock:
   283              self._output_resp_times.append(seconds)
   284              self._timing_versions["output"] += 1
   285
   286      def record_changes_request_time(self, seconds: float) -> None:
   287          with self._lock:
   288              self._changes_request_times.append(seconds)
   289              self._timing_versions["changes"] += 1
   290
   291      def record_batch_processing_time(self, seconds: float) -> None:
   292          with self._lock:
   293              self._batch_processing_times.append(seconds)
   294              self._timing_versions["batch"] += 1
   295
   296      def record_doc_fetch_time(self, seconds: float) -> None:
   297          with self._lock:
   298              self._doc_fetch_times.append(seconds)
   299              self._timing_versions["fetch"] += 1
   300
   301      def record_health_probe_time(self, seconds: float) -> None:
   302          with self._lock:
   303              self._health_probe_times.append(seconds)
   304              self._timing_versions["health"] += 1
   305
   306      def record_inbound_auth_time(self, seconds: float) -> None:
   307          with self._lock:
   308              self._inbound_auth_times.append(seconds)
   309              self._timing_versions["inbound_auth"] += 1
   310
   311      def record_outbound_auth_time(self, seconds: float) -> None:
   312          with self._lock:
   313              self._outbound_auth_times.append(seconds)
   314              self._timing_versions["outbound_auth"] += 1
   315
   316      def record_batch_received(self, batch_size: int) -> None:
   317          with self._lock:
   318              if batch_size > self.largest_batch_received:
   319                  self.largest_batch_received = batch_size
   320              if batch_size >= self.flood_threshold:
   321                  self.flood_batches_total += 1
   322              self.changes_pending = (
   323                  self.changes_received_total - self.changes_processed_total
   324              )
   325
   326      def _get_cached_system_metrics(self) -> dict:
   327          """Cache psutil calls with 15s TTL to avoid syscalls on every scrape."""
   328          now = time.monotonic()
   329          if (
   330              self._system_metrics_cache is not None
   331              and now - self._system_metrics_cache_time < 15
   332          ):
   333              return self._system_metrics_cache
   334
   335          cache = {}
   336          try:
   337              cache["gc_counts"] = gc.get_count()
   338              cache["gc_stats"] = gc.get_stats()
   339              cache["cpu_count"] = psutil.cpu_count(logical=True)
   340              cache["cpu_percent"] = psutil.cpu_percent(interval=0)
   341              cache["virtual_memory"] = psutil.virtual_memory()
   342              cache["swap_memory"] = psutil.swap_memory()
   343              try:
   344                  cache["disk_usage"] = psutil.disk_usage("/")
   345              except OSError:
   346                  cache["disk_usage"] = None
   347              cache["net_io_counters"] = psutil.net_io_counters()
   348          except Exception:
   349              pass  # system metrics are best-effort
   350          self._system_metrics_cache = cache
   351          self._system_metrics_cache_time = now
   352          return cache
   353
   354      def _get_cached_dir_walk_sizes(self) -> dict:
   355          """Cache directory walk results with 60s TTL to avoid filesystem hits."""
   356          now = time.monotonic()
   357          if (
   358              self._dir_walk_cache is not None
   359              and now - self._dir_walk_cache_time < 60
   360          ):
   361              return self._dir_walk_cache
   362
   363          cache = {"log_bytes": 0, "cbl_bytes": 0}
   364          try:
   365              log_dir = self._log_dir
   366              if log_dir and os.path.isdir(log_dir):
   367                  total_log_bytes = 0
   368                  for dirpath, _, filenames in os.walk(log_dir):
   369                      for fname in filenames:
   370                          try:
   371                              total_log_bytes += os.path.getsize(
   372                                  os.path.join(dirpath, fname)
   373                              )
   374                          except OSError:
   375                              pass
   376                  cache["log_bytes"] = total_log_bytes
   377
   378              cbl_dir = self._cbl_db_dir
   379              if cbl_dir and os.path.exists(cbl_dir):
   380                  total_cbl_bytes = 0
   381                  if os.path.isdir(cbl_dir):
   382                      for dirpath, _, filenames in os.walk(cbl_dir):
   383                          for fname in filenames:
   384                              try:
   385                                  total_cbl_bytes += os.path.getsize(
   386                                      os.path.join(dirpath, fname)
   387                                  )
   388                              except OSError:
   389                                  pass
   390                  else:
   391                      try:
   392                          total_cbl_bytes = os.path.getsize(cbl_dir)
   393                      except OSError:
   394                          pass
   395                  cache["cbl_bytes"] = total_cbl_bytes
   396          except Exception:
   397              pass  # directory walks are best-effort
   398          self._dir_walk_cache = cache
   399          self._dir_walk_cache_time = now
   400          return cache
   401
   402      def _get_cached_timing_stats(self) -> dict[str, tuple[int, float, list[float]]]:
   403          """Return timing stats, recomputing only for deques that changed."""
   404          with self._lock:
   405              series = {
   406                  "output": self._output_resp_times,
   407                  "changes": self._changes_request_times,
   408                  "batch": self._batch_processing_times,
   409                  "fetch": self._doc_fetch_times,
   410                  "health": self._health_probe_times,
   411                  "inbound_auth": self._inbound_auth_times,
   412                  "outbound_auth": self._outbound_auth_times,
   413              }
   414              versions = dict(self._timing_versions)
   415
   416              cached_stats: dict[str, tuple[int, float, list[float]]] = {}
   417              pending: dict[str, tuple[int, list[float]]] = {}
   418
   419              for key, data in series.items():
   420                  version = versions[key]
   421                  entry = self._timing_stats_cache.get(key)
   422                  if entry is not None and entry[0] == version:
   423                      cached_stats[key] = (entry[1], entry[2], entry[3])
   424                  else:
   425                      pending[key] = (version, list(data))
   426
   427          computed_cache_entries: dict[str, tuple[int, int, float, list[float]]] = {}
   428          for key, (version, data) in pending.items():
   429              sorted_data = sorted(data) if data else []
   430              count = len(data)
   431              total = sum(data) if data else 0.0
   432              computed_cache_entries[key] = (version, count, total, sorted_data)
   433              cached_stats[key] = (count, total, sorted_data)
   434
   435          if computed_cache_entries:
   436              with self._lock:
   437                  for key, entry in computed_cache_entries.items():
   438                      # Only publish if no newer samples arrived while computing.
   439                      if self._timing_versions.get(key) == entry[0]:
   440                          self._timing_stats_cache[key] = entry
   441
   442          return cached_stats
   443
   444      def render(self) -> str:
   445          """Render all metrics in Prometheus text exposition format."""
   446          with self._lock:
   447              uptime = time.monotonic() - self._start_time
   448              labels = self._labels
   449
   450          # Pre-compute sorted arrays/stats once per data change (not per scrape)
   451          timing_stats = self._get_cached_timing_stats()
   452
   453          def _quantile(sorted_data: list[float], q: float) -> float:
   454              if not sorted_data:
   455                  return 0.0
   456              idx = int(q * (len(sorted_data) - 1))
   457              return sorted_data[idx]
   458
   459          ort_count, ort_sum, ort_sorted = timing_stats["output"]
   460          crt_count, crt_sum, crt_sorted = timing_stats["changes"]
$
nl -ba main.py | sed -n '2036,2275p'
in change_stream_db

  2036          self._uuid = hashlib.sha1(raw.encode()).hexdigest()
  2037          self._client_id = client_id
  2038          self._local_doc_id = f"checkpoint-{self._uuid}"
  2039
  2040          # Fallback to local file when SG is unreachable for checkpoint ops
  2041          # Phase 6: use job_id in fallback filename for isolation
  2042          fallback_file = cfg.get("file", "checkpoint.json")
  2043          if job_id:
  2044              # Transform "checkpoint.json" -> "checkpoint_<job_id>.json"
  2045              path = Path(fallback_file)
  2046              fallback_file = str(path.parent / f"{path.stem}_{job_id}{path.suffix}")
  2047          self._fallback_path = Path(fallback_file)
  2048          self._fallback_store: CBLStore | None = None
  2049
  2050          ic(self._uuid, self._local_doc_id, raw)
  2051
  2052          self._metrics = None
  2053
  2054      def set_metrics(self, metrics: "MetricsCollector | None") -> None:
  2055          self._metrics = metrics
  2056
  2057      @property
  2058      def local_doc_path(self) -> str:
  2059          """Returns the REST path segment: _local/checkpoint-{uuid}"""
  2060          return f"_local/{self._local_doc_id}"
  2061
  2062      @property
  2063      def seq(self) -> str:
  2064          return self._seq
  2065
  2066      @property
  2067      def initial_sync_done(self) -> bool:
  2068          return self._initial_sync_done
  2069
  2070      # -- SG-backed load/save ---------------------------------------------------
  2071
  2072      async def load(
  2073          self,
  2074          http: "RetryableHTTP",
  2075          base_url: str,
  2076          auth: aiohttp.BasicAuth | None,
  2077          headers: dict,
  2078      ) -> str:
  2079          """GET {keyspace}/_local/checkpoint-{uuid} from Sync Gateway."""
  2080          if not self._enabled:
  2081              return self._seq
  2082
  2083          url = f"{base_url}/{self.local_doc_path}"
  2084          ic("checkpoint load", url)
  2085          try:
  2086              resp = await http.request("GET", url, auth=auth, headers=headers)
  2087              data = await resp.json()
  2088              resp.release()
  2089              self._seq = str(data.get("SGs_Seq", "0"))
  2090              self._rev = data.get("_rev")
  2091              self._internal = data.get("remote", data.get("local_internal", 0))
  2092              raw_isd = data.get("initial_sync_done", None)
  2093              if raw_isd is None:
  2094                  self._initial_sync_done = self._seq != "0"
  2095              else:
  2096                  self._initial_sync_done = bool(raw_isd)
  2097              log_event(
  2098                  logger,
  2099                  "info",
  2100                  "CHECKPOINT",
  2101                  "checkpoint loaded",
  2102                  operation="SELECT",
  2103                  storage="sg",
  2104              )
  2105              log_event(
  2106                  logger,
  2107                  "debug",
  2108                  "CHECKPOINT",
  2109                  "checkpoint detail",
  2110                  operation="SELECT",
  2111                  seq=self._seq,
  2112                  doc_id=self._local_doc_id,
  2113                  storage="sg",
  2114              )
  2115              if self._metrics:
  2116                  self._metrics.inc("checkpoint_loads_total")
  2117          except ClientHTTPError as exc:
  2118              if exc.status == 404:
  2119                  log_event(
  2120                      logger,
  2121                      "info",
  2122                      "CHECKPOINT",
  2123                      "no existing checkpoint on SG – starting from 0",
  2124                      operation="SELECT",
  2125                      storage="sg",
  2126                  )
  2127                  self._seq = "0"
  2128              else:
  2129                  log_event(
  2130                      logger,
  2131                      "warn",
  2132                      "CHECKPOINT",
  2133                      "checkpoint load fell back to local storage",
  2134                      operation="SELECT",
  2135                      status=exc.status,
  2136                      storage="fallback",
  2137                  )
  2138                  self._seq = self._load_fallback()
  2139                  if self._metrics:
  2140                      self._metrics.inc("checkpoint_loads_total")
  2141                      self._metrics.inc("checkpoint_load_errors_total")
  2142          except Exception as exc:
  2143              log_event(
  2144                  logger,
  2145                  "warn",
  2146                  "CHECKPOINT",
  2147                  "checkpoint load fell back to local storage: %s" % exc,
  2148                  operation="SELECT",
  2149                  storage="fallback",
  2150              )
  2151              self._seq = self._load_fallback()
  2152              if self._metrics:
  2153                  self._metrics.inc("checkpoint_loads_total")
  2154                  self._metrics.inc("checkpoint_load_errors_total")
  2155
  2156          return self._seq
  2157
  2158      async def save(
  2159          self,
  2160          seq: str,
  2161          http: "RetryableHTTP",
  2162          base_url: str,
  2163          auth: aiohttp.BasicAuth | None,
  2164          headers: dict,
  2165      ) -> None:
  2166          """PUT {keyspace}/_local/checkpoint-{uuid} on Sync Gateway."""
  2167          if not self._enabled:
  2168              return
  2169
  2170          async with self._lock:
  2171              self._internal += 1
  2172              self._seq = seq
  2173              body: dict = {
  2174                  "client_id": self._client_id,
  2175                  "SGs_Seq": seq,
  2176                  "time": int(time.time()),
  2177                  "remote": self._internal,
  2178                  "initial_sync_done": self._initial_sync_done,
  2179              }
  2180              if self._rev:
  2181                  body["_rev"] = self._rev
  2182
  2183              url = f"{base_url}/{self.local_doc_path}"
  2184              ic("checkpoint save", url, seq, self._internal)
  2185              try:
  2186                  req_headers = {**headers, "Content-Type": "application/json"}
  2187                  resp = await http.request(
  2188                      "PUT", url, json=body, auth=auth, headers=req_headers
  2189                  )
  2190                  resp_data = await resp.json()
  2191                  resp.release()
  2192                  self._rev = resp_data.get("rev", self._rev)
  2193                  log_event(
  2194                      logger,
  2195                      "info",
  2196                      "CHECKPOINT",
  2197                      "checkpoint saved",
  2198                      operation="UPDATE",
  2199                      storage="sg",
  2200                  )
  2201                  log_event(
  2202                      logger,
  2203                      "debug",
  2204                      "CHECKPOINT",
  2205                      "checkpoint save detail",
  2206                      operation="UPDATE",
  2207                      seq=seq,
  2208                      doc_id=self._local_doc_id,
  2209                      storage="sg",
  2210                  )
  2211              except Exception as exc:
  2212                  log_event(
  2213                      logger,
  2214                      "warn",
  2215                      "CHECKPOINT",
  2216                      "checkpoint save fell back to local storage: %s" % exc,
  2217                      operation="UPDATE",
  2218                      seq=seq,
  2219                      storage="fallback",
  2220                  )
  2221                  self._save_fallback(seq)
  2222                  if self._metrics:
  2223                      self._metrics.inc("checkpoint_save_errors_total")
  2224
  2225      # -- Local file fallback ---------------------------------------------------
  2226
  2227      def _get_fallback_store(self) -> CBLStore:
  2228          """Lazily create and reuse a CBLStore for fallback checkpoint operations."""
  2229          if self._fallback_store is None:
  2230              self._fallback_store = CBLStore()
  2231          return self._fallback_store
  2232
  2233      def _load_fallback(self) -> str:
  2234          if USE_CBL:
  2235              data = self._get_fallback_store().load_checkpoint(self._uuid)
  2236              if data:
  2237                  seq = data.get("SGs_Seq", "0")
  2238                  raw_isd = data.get("initial_sync_done", None)
  2239                  if raw_isd is None:
  2240                      self._initial_sync_done = seq != "0"
  2241                  else:
  2242                      self._initial_sync_done = bool(raw_isd)
  2243                  ic("checkpoint loaded from CBL", seq)
  2244                  return seq
  2245              return "0"
  2246          # Original file fallback
  2247          if self._fallback_path.exists():
  2248              data = json.loads(self._fallback_path.read_text())
  2249              seq = str(data.get("SGs_Seq", data.get("last_seq", "0")))
  2250              raw_isd = data.get("initial_sync_done", None)
  2251              if raw_isd is None:
  2252                  self._initial_sync_done = seq != "0"
  2253              else:
  2254                  self._initial_sync_done = bool(raw_isd)
  2255              ic("checkpoint loaded from file", seq)
  2256              return seq
  2257          return "0"
  2258
  2259      def _save_fallback(self, seq: str) -> None:
  2260          if USE_CBL:
  2261              self._get_fallback_store().save_checkpoint(
  2262                  self._uuid, seq, self._client_id, self._internal
  2263              )
  2264              ic("checkpoint saved to CBL", seq)
  2265              return
  2266          # Original file fallback
  2267          self._fallback_path.write_text(
  2268              json.dumps(
  2269                  {
  2270                      "SGs_Seq": seq,
  2271                      "time": int(time.time()),
  2272                      "remote": self._internal,
  2273                      "initial_sync_done": self._initial_sync_done,
  2274                  }
  2275              )
$
nl -ba rest/output_http.py | sed -n '1140,1190p'
in change_stream_db

  1140      def list_pending(self) -> list[dict]:
  1141          """Return all pending (not yet retried) DLQ entries."""
  1142          if self._use_cbl and self._store:
  1143              return [e for e in self._store.list_dlq() if not e.get("retried")]
  1144          if self._path and self._path.exists():
  1145              entries = []
  1146              with open(self._path) as f:
  1147                  for line in f:
  1148                      line = line.strip()
  1149                      if line:
  1150                          try:
  1151                              entries.append(json.loads(line))
  1152                          except json.JSONDecodeError:
  1153                              pass
  1154              return entries
  1155          return []
  1156
  1157      def pending_count(self) -> int:
  1158          """Return count of pending DLQ entries without loading all documents."""
  1159          if self._use_cbl and self._store:
  1160              # Use server-side COUNT query when available.
  1161              if hasattr(self._store, "pending_dlq_count"):
  1162                  return self._store.pending_dlq_count()
  1163              return self._store.dlq_count()
  1164          return len(self.list_pending())
  1165
  1166      def get_entry_doc(self, dlq_id: str) -> dict | None:
  1167          """Return the full DLQ entry including doc_data for reprocessing."""
  1168          if self._use_cbl and self._store:
  1169              return self._store.get_dlq_entry(dlq_id)
  1170          return None
  1171
  1172      def purge_expired(self) -> int:
  1173          """Remove DLQ entries older than retention_seconds. Returns count purged."""
  1174          if self._use_cbl and self._store and self._retention_seconds > 0:
  1175              return self._store.purge_expired_dlq(self._retention_seconds)
  1176          return 0
  1177
  1178      def increment_replay_attempts(self, dlq_id: str) -> int:
  1179          """Increment replay_attempts on a CBL DLQ entry. Returns new count."""
  1180          if self._use_cbl and self._store:
  1181              return self._store.increment_dlq_replay_attempts(dlq_id)
  1182          return 0
  1183
  1184      @property
  1185      def max_replay_attempts(self) -> int:
  1186          return self._max_replay_attempts
  1187
  1188      @property
  1189      def retention_seconds(self) -> int:
  1190          return self._retention_seconds
$
nl -ba cbl_store.py | sed -n '1616,1665p'
in change_stream_db

  1616              "DLQ",
  1617              "queue cleared",
  1618              operation="DELETE",
  1619              doc_type="dlq",
  1620              doc_count=count,
  1621          )
  1622
  1623      def dlq_count(self) -> int:
  1624          return (
  1625              _run_n1ql_scalar(
  1626                  self.db, f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
  1627              )
  1628              or 0
  1629          )
  1630
  1631      def pending_dlq_count(self) -> int:
  1632          """Return count of pending (retried=false) DLQ entries."""
  1633          return (
  1634              _run_n1ql_scalar(
  1635                  self.db,
  1636                  f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d"
  1637                  f" WHERE d.type = 'dlq' AND d.retried = false",
  1638              )
  1639              or 0
  1640          )
  1641
  1642      def purge_expired_dlq(self, max_age_seconds: int) -> int:
  1643          """Purge DLQ entries older than max_age_seconds. Returns count purged."""
  1644          if max_age_seconds <= 0:
  1645              return 0
  1646          cutoff = int(time.time()) - max_age_seconds
  1647          rows = _run_n1ql(
  1648              self.db,
  1649              f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d"
  1650              f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
  1651              {"cutoff": cutoff},
  1652          )
  1653          purged = 0
  1654          with _transaction(self.db):
  1655              for row in rows:
  1656                  dlq_id = row.get("id")
  1657                  if dlq_id:
  1658                      _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
  1659                      purged += 1
  1660          if purged > 0:
  1661              log_event(
  1662                  logger,
  1663                  "info",
  1664                  "DLQ",
  1665                  "purged %d expired entries (older than %ds)"
$
nl -ba rest/changes_http.py | sed -n '332,490p'
in change_stream_db

   332      http: RetryableHTTP,
   333      base_url: str,
   334      rows: list[dict],
   335      auth: aiohttp.BasicAuth | None,
   336      headers: dict,
   337      metrics: MetricsCollector | None = None,
   338  ) -> list[dict]:
   339      """Fetch full docs via _bulk_get (Sync Gateway / App Services)."""
   340      docs_req = []
   341      docs_req_ids: set[str] = set()
   342      for row in rows:
   343          doc_id = row["id"]
   344          docs_req_ids.add(doc_id)
   345          docs_req.append({"id": doc_id, "rev": row["changes"][0]["rev"]})
   346      if not docs_req:
   347          return []
   348      url = f"{base_url}/_bulk_get?revs=false"
   349      payload = {"docs": docs_req}
   350      requested_count = len(docs_req)
   351      log_event(
   352          logger,
   353          "info",
   354          "HTTP",
   355          "_bulk_get: requesting %d docs" % requested_count,
   356          doc_count=requested_count,
   357      )
   358      # DEBUG: log the individual _id,_rev pairs being requested
   359      if logger.isEnabledFor(logging.DEBUG):
   360          for dr in docs_req:
   361              log_event(
   362                  logger,
   363                  "debug",
   364                  "HTTP",
   365                  "_bulk_get request item",
   366                  doc_id=dr["id"],
   367              )
   368      ic(url, requested_count)
   369      t0 = time.monotonic()
   370      resp = await http.request(
   371          "POST",
   372          url,
   373          json=payload,
   374          auth=auth,
   375          headers={**headers, "Content-Type": "application/json"},
   376      )
   377      # _bulk_get returns multipart/mixed or JSON depending on SG version
   378      ct = resp.content_type or ""
   379      results: list[dict] = []
   380      response_bytes = 0
   381      if "application/json" in ct:
   382          raw_bytes = await resp.read()
   383          response_bytes = len(raw_bytes)
   384          resp.release()
   385          if metrics:
   386              metrics.inc("bytes_received_total", response_bytes)
   387          try:
   388              body = json.loads(raw_bytes)
   389          except json.JSONDecodeError as exc:
   390              logger.warning(
   391                  "bulk_get: malformed JSON response (%d bytes): %s",
   392                  len(raw_bytes),
   393                  exc,
   394              )
   395              if metrics:
   396                  metrics.inc("doc_fetch_errors_total")
   397              return []
   398          for item in body.get("results", []):
   399              for doc_entry in item.get("docs", []):
   400                  ok = doc_entry.get("ok")
   401                  if ok:
   402                      results.append(ok)
   403      else:
   404          # Fallback: read raw text and attempt JSON extraction
   405          raw = await resp.text()
   406          response_bytes = len(raw.encode("utf-8"))
   407          resp.release()
   408          if metrics:
   409              metrics.inc("bytes_received_total", response_bytes)
   410          for line in raw.splitlines():
   411              line = line.strip()
   412              if line.startswith("{"):
   413                  try:
   414                      results.append(json.loads(line))
   415                  except json.JSONDecodeError:
   416                      pass
   417      if metrics:
   418          metrics.inc("doc_fetch_requests_total")
   419          metrics.record_doc_fetch_time(time.monotonic() - t0)
   420
   421      log_event(
   422          logger,
   423          "info",
   424          "HTTP",
   425          "_bulk_get: received %d docs" % len(results),
   426          doc_count=len(results),
   427      )
   428      log_event(
   429          logger,
   430          "debug",
   431          "HTTP",
   432          "_bulk_get response detail",
   433          doc_count=len(results),
   434          input_count=requested_count,
   435          bytes=response_bytes,
   436      )
   437      if logger.isEnabledFor(logging.DEBUG):
   438          for doc in results:
   439              log_event(
   440                  logger,
   441                  "debug",
   442                  "HTTP",
   443                  "_bulk_get result doc",
   444                  doc_id=doc.get("_id", ""),
   445              )
   446
   447      returned_ids = {doc.get("_id", "") for doc in results if doc.get("_id")}
   448
   449      # -- Verify we got all requested docs back --
   450      returned_count = len(results)
   451      if returned_count < requested_count:
   452          missing_count = requested_count - returned_count
   453          ic("🍦 bulk_get missing docs", requested_count, returned_count, missing_count)
   454          log_event(
   455              logger,
   456              "warn",
   457              "HTTP",
   458              "🍦 _bulk_get returned fewer docs than requested",
   459              batch_size=requested_count,
   460              doc_count=returned_count,
   461              input_count=missing_count,
   462          )
   463
   464          # Determine which doc IDs are missing
   465          missing_ids = docs_req_ids - returned_ids
   466          missing_rows = [r for r in rows if r["id"] in missing_ids]
   467
   468          ic("bulk_get: fetching missing docs individually", len(missing_rows))
   469
   470          recovered: list[dict] = []
   471          failed_ids: list[str] = []
   472          for row in missing_rows:
   473              doc_id = row["id"]
   474              rev = row["changes"][0]["rev"] if row.get("changes") else ""
   475              doc = await _fetch_single_doc_with_retry(
   476                  http, base_url, doc_id, rev, auth, headers, metrics=metrics
   477              )
   478              if doc is not None:
   479                  recovered.append(doc)
   480              else:
   481                  failed_ids.append(doc_id)
   482
   483          if recovered:
   484              ic("bulk_get fallback: recovered docs", len(recovered))
   485              log_event(
   486                  logger,
   487                  "info",
   488                  "HTTP",
   489                  "got %d document(s) from failed _bulk_get via individual GET"
   490                  % len(recovered),
$
nl -ba rest/changes_http.py | sed -n '1196,1222p'
in change_stream_db

  1196          log_event(
  1197              logger,
  1198              "info",
  1199              "PROCESSING",
  1200              "batch complete: %d/%d succeeded, %d failed%s"
  1201              % (
  1202                  batch_success,
  1203                  total,
  1204                  batch_fail,
  1205                  " (%d written to dead letter queue)" % batch_fail
  1206                  if batch_fail and dlq.enabled
  1207                  else "",
  1208              ),
  1209          )
  1210
  1211      # Flush DLQ meta once per batch (not per doc) to minimise CBL writes
  1212      if batch_fail > 0 and dlq.enabled:
  1213          _job = job_id or getattr(checkpoint, "_client_id", "")
  1214          dlq.flush_insert_meta(_job)
  1215          if metrics:
  1216              metrics.set("dlq_pending_count", dlq.pending_count())
  1217
  1218      output.log_stats()
  1219
  1220      if output_failed:
  1221          if metrics:
  1222              metrics.record_batch_processing_time(time.monotonic() - batch_t0)
Implemented another optimization pass focused on repeated, non-changing work by using in-memory reuse and server-side counts.

Added a timing-summary cache in main.py so quantile inputs (sorted(...), sum(...), len(...)) are recomputed only when new timing samples arrive, instead of on every /_metrics scrape.
Reduced repeated fallback object creation in main.py by caching a single CBLStore instance inside Checkpoint fallback logic instead of constructing CBLStore() for each fallback load/save.
Added DeadLetterQueue.pending_count() in rest/output_http.py and backed it with pending_dlq_count() in cbl_store.py, so pending DLQ gauge updates no longer need to load all entries.
Updated hot-path usage in rest/changes_http.py to use dlq.pending_count() and optimized _bulk_get handling in the same file (_fetch_docs_bulk_get) by reusing ID sets and gating per-doc debug loops behind logger.isEnabledFor(logging.DEBUG).
Validation run:

python3 -m compileall main.py rest/changes_http.py rest/output_http.py cbl_store.py
python3 -m pytest tests/test_changes_feed_logic.py tests/test_changes_worker.py -q → 316 passed (with 2 existing aiohttp key warnings)
If you want, I can do one more pass specifically on logging overhead (log_event call volume in hot loops) and DB/cloud metrics rendering to add similar “compute only when changed” caching there too.


Fork

Restore

step please
4 files
changed
+114
-28
~14

$1.55
•
41% of 272k
config.json
deep

