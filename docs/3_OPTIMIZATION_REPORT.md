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
