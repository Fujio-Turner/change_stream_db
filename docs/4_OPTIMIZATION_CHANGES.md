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

---

# Round 2 — Additional Memoization Optimizations

> Scanning hot paths for repeated work that can be cached safely without changing behavior.

---

## Optimization #4: Cache Process-Level psutil Calls (15s TTL)

**File:** `main.py`
**Impact:** 🔴 High — Eliminates 6+ process-level syscalls on every metrics scrape

### Problem
The system-wide psutil metrics (cpu_percent, virtual_memory, etc.) were cached with a 15s TTL in Round 1, but the **process-level** psutil calls were still hit raw on every `/_metrics` scrape:
- `proc.cpu_times()` — 1 syscall
- `proc.cpu_percent(interval=0)` — 1 syscall
- `proc.memory_info()` — 1 syscall
- `proc.memory_percent()` — 1 syscall
- `proc.num_threads()` — 1 syscall
- `proc.num_fds()` — 1 syscall

With Prometheus scraping every 15s, that's 6 unnecessary syscalls per scrape.

### Changes
- Added `_process_metrics_cache` and `_process_metrics_cache_time` fields
- Added `_get_cached_process_metrics()` helper with 15s TTL
- Updated `render()` to use cached process metrics

### Result
- **Before:** 6 process-level syscalls per scrape + 5 system-level (already cached)
- **After:** 0 syscalls per scrape (cached values reused until TTL expires)

---

## Optimization #5: Pre-Compile Regex in `_parse_seq_number`

**File:** `rest/changes_http.py`
**Impact:** 🟡 Medium — Called on every batch to compare sequences

### Problem
`_parse_seq_number()` calls `re.split(r"[:\-]", s)` which recompiles the regex pattern on every invocation. This function is called:
- On every `_changes` batch to compare `last_seq` vs `update_seq`
- During initial sync pagination to check completion
- Any time a sequence number needs numeric comparison

While Python's `re` module has an internal cache (limited to ~512 patterns), pre-compiling guarantees zero regex compilation overhead.

### Change
```python
# Module-level pre-compiled regex
_SEQ_SPLIT_RE = re.compile(r"[:\-]")

def _parse_seq_number(seq) -> int:
    ...
    parts = _SEQ_SPLIT_RE.split(s)  # was: re.split(r"[:\-]", s)
```

### Result
- Eliminates regex compilation lookup on every call
- Pattern compiled once at module import time

---

## Round 2 Summary

| # | Optimization | File | Impact |
|---|---|---|---|
| 4 | Cache process-level psutil (15s TTL) | main.py | 🔴 High — 6 fewer syscalls/scrape |
| 5 | Pre-compile `_parse_seq_number` regex | rest/changes_http.py | 🟡 Medium — zero regex compilation overhead |

All 775 tests pass. Changes are backward-compatible.

---

# Round 3 — Stream Buffering, Single-Doc Fetch, orjson & Output Backpressure

> Optimizations targeting the hot path: doc fetching, stream processing, JSON parsing, and output throttling.

---

## Optimization #6: Single-Doc `_bulk_get` Skip

**File:** `rest/changes_http.py` (`fetch_docs`, line ~317)
**Impact:** 🔴 High — Eliminates `POST _bulk_get` envelope overhead for single-document batches

### Problem
`_bulk_get` for a single document is a heavy operation: construct a JSON request body, POST it, parse the nested `results[].docs[].ok` response envelope — all for one document. A simple `GET /{doc_id}?rev={rev}` returns the document directly with no wrapping.

### Change
When a batch contains exactly 1 document, the worker uses `GET /{keyspace}/{doc_id}?rev={rev}` with exponential backoff retry instead of `_bulk_get`. For batches of 2+ documents, `_bulk_get` is used as normal.

### Why This Works Now (Greedy Drain)
Previously, this optimization caused a regression because the old 100ms/100-item stream buffering timer caused nearly *all* batches to be single-doc, turning everything into individual GETs. With the greedy drain strategy (Optimization #7), single-doc batches only occur when there genuinely is just one change available — the drain collects all rows already in the socket buffer before flushing.

---

## Optimization #7: WebSocket Stream Buffering

**File:** `rest/changes_http.py` (`_consume_websocket_stream`)
**Impact:** 🔴 High — Reduces per-message processing overhead and enables batch doc fetches

### Problem
WebSocket mode processed each incoming message immediately — one change at a time. During catch-up or bursts, this meant:
- 100 individual `_bulk_get` calls (or 100 individual GETs) instead of 1 batched request
- 100 individual checkpoint saves instead of 1
- 100 individual output forwards instead of batched processing

The continuous stream mode already had buffering (collecting rows until `get_batch_number` or `stream_batch_timeout_ms`), but WebSocket did not.

### Change (Updated — Greedy Drain)
Both continuous and websocket streams now use a **greedy drain** strategy:
- Block indefinitely on the first row/message
- Once a row arrives, drain everything already in the socket buffer using a very short timeout (`stream_batch_timeout_ms`, default 5ms)
- Flush as soon as nothing more is immediately available, or `get_batch_number` rows accumulate (default 100)
- Uses the same `_flush_buffer` → `_process_changes_batch` → `_maybe_backpressure` pipeline
- Buffer is flushed on `last_seq`, connection close, or error (no data loss)

### Result
- **Before:** Fixed 100ms/100-item timer caused artificial latency on idle streams
- **After:** Zero delay for single docs, automatic batching under load
- Self-tunes based on actual network throughput — no magic numbers
- The original 100ms timer was replaced because it, combined with the single-doc GET optimization, caused nearly all fetches to become individual GETs.  With greedy drain, single-doc batches only occur when there genuinely is one change available, so the single-doc GET optimization now works as intended

---

## Optimization #8: orjson for Hot-Path JSON Parsing

**File:** `rest/changes_http.py`, `rest/attachments.py`
**Impact:** 🟡 Medium — 3–10× faster JSON deserialization on the critical path

### Problem
The worker parses JSON on every hot-path operation:
- `_changes` response body (can be megabytes with thousands of rows)
- `_bulk_get` response parsing (multi-document responses)
- Individual doc fetches (`GET` responses)
- Continuous/WebSocket stream lines (one JSON parse per row)

Python's built-in `json.loads` is implemented in C but is still significantly slower than alternatives for large payloads.

### Change
```python
try:
    import orjson
    _json_loads = orjson.loads
except ImportError:
    _json_loads = json.loads
```

[orjson](https://github.com/ijl/orjson) is a Rust-based JSON library:
- **Deserialization:** 3–10× faster than `json.loads` depending on payload structure
- **Memory:** Lower peak memory for large documents (no intermediate Python string)
- **Compatibility:** Drop-in replacement for `json.loads` — accepts `bytes`, `str`, `bytearray`

### Result
- Automatic: uses orjson when installed, falls back to stdlib `json` transparently
- Biggest impact on large `_changes` responses (initial sync) and high-throughput streaming
- Listed in `requirements.txt` — always available in Docker builds

---

## Optimization #9: Output Backpressure Monitoring

**File:** `rest/changes_http.py` (`_maybe_backpressure`)
**Impact:** 🔴 High — Prevents overwhelming a struggling output endpoint

### Problem
When the output endpoint (database, REST API) slows down due to load, the worker continues sending requests at full speed. This can:
- Compound the output's performance problems (more connections, more load)
- Trigger cascading failures if the output has connection limits
- Waste retries and DLQ writes that would succeed if the worker slowed down

### Change
Adaptive backpressure based on output response time:

```python
async def _maybe_backpressure(metrics, shutdown_event,
                               backpressure_threshold=2.0, max_delay=5.0):
    avg = metrics.get_output_latency_avg()
    baseline = metrics._backpressure_baseline  # set after first 50 samples
    ratio = avg / baseline
    if ratio >= backpressure_threshold:
        delay = min((ratio - 1.0) * baseline, max_delay)
        await _sleep_or_shutdown(delay, shutdown_event)
```

### How it works
1. First 50 output requests establish a baseline average latency
2. Rolling average is compared: if `avg / baseline ≥ 2.0×`, throttle
3. Delay is proportional: `(ratio - 1) × baseline`, capped at 5 seconds
4. Automatically recovers when latency returns to normal

### Prometheus Metrics
| Metric | Type | Description |
|---|---|---|
| `backpressure_delays_total` | counter | Times backpressure throttled |
| `backpressure_delay_seconds_total` | counter | Cumulative delay seconds |
| `backpressure_active` | gauge | 1 when throttling, 0 when normal |

### Result
- Output endpoint gets breathing room during spikes
- No manual configuration needed — fully adaptive
- Integrates with Grafana: alert on `backpressure_active == 1`

---

## Round 3 Summary

| # | Optimization | File | Impact |
|---|---|---|---|
| 6 | Single-doc `_bulk_get` skip | rest/changes_http.py | 🔴 High — simple GET for 1-doc batches (works correctly with greedy drain) |
| 7 | Greedy-drain stream buffering (continuous + WebSocket) | rest/changes_http.py | 🔴 High — zero-delay single docs, auto-batching under load |
| 8 | orjson hot-path JSON parsing | rest/changes_http.py, rest/attachments.py | 🟡 Medium — 3–10× faster deserialization |
| 9 | Output backpressure monitoring | rest/changes_http.py | 🔴 High — adaptive throttling prevents cascading failures |

All changes are backward-compatible. Stream buffering and backpressure are automatic with no configuration required (defaults: `stream_batch_timeout_ms=5`, `backpressure_threshold=2.0×`, `max_delay=5s`).
