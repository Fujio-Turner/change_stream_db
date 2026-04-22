# Multi-Pipeline Threading – v2.x Design Plan

This document outlines the design for supporting **multiple concurrent pipelines** in changes_worker v2.x, where each thread runs an independent `SOURCE (_changes) → PROCESS → OUTPUT` pipeline.

**Related docs:**
- [`DESIGN.md`](DESIGN.md) -- Current single-pipeline architecture & failure modes
- [`JOBS.md`](JOBS.md) -- Job ID concept, current per-engine/per-job OUTPUT metrics
- [`RDBMS_PLAN.md`](RDBMS_PLAN.md) -- RDBMS output module design
- [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) -- JSON-to-relational mapping definitions

---

## Current State (v1.x)

```
┌──────────────┐       ┌───────────┐       ┌──────────────┐
│  1 SOURCE    │──────►│ 1 PROCESS │──────►│  1 OUTPUT    │
│  _changes    │       │  filter,  │       │  HTTP / DB / │
│  feed        │       │  fetch,   │       │  Cloud       │
│              │       │  transform│       │              │
└──────────────┘       └───────────┘       └──────────────┘
```

- **One** `_changes` feed (one gateway + one scope/collection)
- **One** output destination (one REST endpoint or one DB connection)
- `"threads": 4` exists in `config.json` as a placeholder (unused)
- The async event loop handles concurrency _within_ a single pipeline (`max_concurrent` tasks), but there is only one pipeline

---

## Goal (v2.x)

Support **N independent pipelines** running concurrently via Python threads, where each pipeline has its own SOURCE, PROCESS config, and OUTPUT:

```
Thread 1:  SG/db.us.prices/_changes  ──► filter/transform ──► PostgreSQL (prices table)
Thread 2:  SG/db.us.orders/_changes  ──► filter/transform ──► PostgreSQL (orders table)
Thread 3:  SG/db.eu.prices/_changes  ──► filter/transform ──► REST API endpoint
Thread 4:  Edge/inventory/_changes   ──► filter/transform ──► Cloud Storage
```

Each thread runs its own asyncio event loop and is fully isolated — its own HTTP session, checkpoint, metrics, and output connection.

---

## Config Evolution

### v1.x (current) – single pipeline

```jsonc
{
  "gateway": { "url": "...", "database": "db", "scope": "us", "collection": "prices" },
  "auth": { ... },
  "changes_feed": { ... },
  "processing": { ... },
  "checkpoint": { ... },
  "output": { ... },
  "threads": 4   // placeholder, unused
}
```

### v2.x – multi-pipeline

```jsonc
{
  "threads": 4,           // max OS threads (caps how many pipelines run concurrently)
  "pipelines": [
    {
      "name": "us-prices-to-pg",
      "enabled": true,
      "gateway": { "src": "sync_gateway", "url": "...", "database": "db", "scope": "us", "collection": "prices" },
      "auth": { "method": "basic", "username": "bob", "password": "..." },
      "changes_feed": { "feed_type": "longpoll", "poll_interval_seconds": 10, "include_docs": true },
      "processing": { "sequential": false, "max_concurrent": 20 },
      "checkpoint": { "enabled": true, "client_id": "us-prices-worker" },
      "output": { "mode": "postgres", "postgres": { ... } }
    },
    {
      "name": "us-orders-to-rest",
      "enabled": true,
      "gateway": { "src": "sync_gateway", "url": "...", "database": "db", "scope": "us", "collection": "orders" },
      "auth": { "method": "basic", "username": "bob", "password": "..." },
      "changes_feed": { "feed_type": "continuous" },
      "processing": { "sequential": true },
      "checkpoint": { "enabled": true, "client_id": "us-orders-worker" },
      "output": { "mode": "http", "target_url": "https://api.example.com/orders" }
    }
  ],

  // Global defaults – pipelines inherit these unless they override
  "auth": { "method": "basic", "username": "bob", "password": "..." },
  "changes_feed": { "feed_type": "longpoll", "poll_interval_seconds": 10 },
  "processing": { "sequential": false, "max_concurrent": 20, "dry_run": false },
  "retry": { "max_retries": 5, "backoff_base_seconds": 1, "backoff_max_seconds": 60 },
  "metrics": { "enabled": true, "host": "0.0.0.0", "port": 9090 },
  "logging": { ... },
  "couchbase_lite": { ... }
}
```

### Backward Compatibility

If `"pipelines"` is **absent**, the worker falls back to v1.x single-pipeline mode using the top-level `gateway`/`auth`/`changes_feed`/`output` keys. Zero config changes needed for existing users.

```python
if "pipelines" in cfg:
    pipeline_configs = cfg["pipelines"]
else:
    # Legacy single-pipeline: wrap the top-level config as one pipeline
    pipeline_configs = [cfg]
```

---

## Architecture

### Threading Model

```
main()
  │
  ├── validate config
  ├── start shared services (metrics server, CBL maintenance)
  │
  ├── PipelineManager.start()
  │     │
  │     ├── Thread-1: Pipeline("us-prices-to-pg")
  │     │     └── asyncio.run(poll_changes(pipeline_cfg_1))
  │     │
  │     ├── Thread-2: Pipeline("us-orders-to-rest")
  │     │     └── asyncio.run(poll_changes(pipeline_cfg_2))
  │     │
  │     ├── Thread-3: Pipeline("eu-prices-to-pg")
  │     │     └── asyncio.run(poll_changes(pipeline_cfg_3))
  │     │
  │     └── Thread-4: Pipeline("inventory-to-cloud")
  │           └── asyncio.run(poll_changes(pipeline_cfg_4))
  │
  ├── wait for shutdown signal
  └── PipelineManager.stop()  # signals all threads to stop
```

Each `threading.Thread` creates its **own `asyncio` event loop** via `asyncio.run()`. This avoids GIL contention issues with a shared loop and keeps each pipeline fully isolated. Python's GIL is acceptable here because the workload is I/O-bound (HTTP requests, DB writes), not CPU-bound — threads release the GIL during I/O operations.

### Key Classes

| Class | Responsibility |
|---|---|
| `PipelineManager` | Owns all threads. Start/stop/restart individual pipelines. Enforces `threads` cap. |
| `Pipeline` | Wraps a single thread + asyncio loop. Holds its own `shutdown_event`, `MetricsCollector`, `Checkpoint`, `OutputForwarder`. Runs `poll_changes()`. |
| `PipelineConfig` | Merges per-pipeline config with global defaults. Validates each pipeline independently. |

---

## CBL Namespacing per Job

Each pipeline ("job") gets its own **Couchbase Lite scope and collections**, providing complete data isolation:

```
CBL Database: changes_worker_db
│
├── Scope: "jobs.us-prices-to-pg"
│   ├── Collection: checkpoint     ← this job's checkpoint doc
│   ├── Collection: dlq            ← this job's dead letter queue
│   └── Collection: state          ← this job's runtime state (last error, uptime, etc.)
│
├── Scope: "jobs.us-orders-to-rest"
│   ├── checkpoint
│   ├── dlq
│   └── state
│
└── Scope: "_default"
    └── _default                   ← global config, migration state, etc.
```

This means:
- **Zero checkpoint collision** — each job reads/writes its own checkpoint doc in its own scope. No `client_id` uniqueness validation needed at the config level (the scope name _is_ the namespace).
- **Isolated DLQ** — you can view, retry, or purge one job's dead letter queue without touching another's.
- **Clean teardown** — deleting a job means dropping its scope. All checkpoints, DLQ entries, and state go with it.
- **Admin UI** — the pipeline selector maps directly to CBL scopes. Query one scope to show one job's data.

### Migration from v1.x

Existing v1.x CBL data lives in `_default._default` and `changes-worker.checkpoint` / `changes-worker.dlq`. On first v2.x startup, the worker migrates this data into `jobs.{pipeline_name}.*` for the single legacy pipeline.

---

## Shared vs Isolated Resources

| Resource | Shared or Per-Pipeline | Notes |
|---|---|---|
| **Metrics server** (`:9090`) | Shared | One HTTP server, aggregates metrics from all pipelines. Each pipeline's `MetricsCollector` adds a `pipeline="name"` label. |
| **CBL database** | Shared | Single CBL database, but each pipeline gets its own CBL scope (`jobs.{name}`) with isolated `checkpoint`, `dlq`, and `state` collections. |
| **Logging** | Shared | Single logging config, but each pipeline's log messages include `pipeline=name` in structured fields. |
| **HTTP sessions** | Per-Pipeline | Each pipeline creates its own `aiohttp.ClientSession` (different source URLs, auth, SSL contexts). |
| **Checkpoint** | Per-Pipeline | Stored in `jobs.{name}.checkpoint` CBL collection. No collision possible. |
| **Output connection** | Per-Pipeline | Each pipeline owns its own DB pool or HTTP session for output. |
| **Dead Letter Queue** | Per-Pipeline | Stored in `jobs.{name}.dlq` CBL collection. |
| **Shutdown signal** | Shared | `SIGINT`/`SIGTERM` triggers all pipelines to stop via their individual `shutdown_event`s. |

---

## Metrics Changes

The existing Prometheus metrics get a new `pipeline` label:

```
# v1.x
changes_worker_poll_cycles_total{src="sync_gateway",database="db"} 42

# v2.x
changes_worker_poll_cycles_total{src="sync_gateway",database="db",pipeline="us-prices-to-pg"} 42
changes_worker_poll_cycles_total{src="sync_gateway",database="db",pipeline="us-orders-to-rest"} 18
```

A new gauge tracks pipeline state:

```
changes_worker_pipeline_up{pipeline="us-prices-to-pg"} 1
changes_worker_pipeline_up{pipeline="us-orders-to-rest"} 1
changes_worker_pipeline_up{pipeline="eu-prices-to-pg"} 0   # crashed / stopped
```

---

## REST Control API Changes

Extend the existing `/_restart`, `/_offline`, `/_online` endpoints to support per-pipeline control:

| Endpoint | Behavior |
|---|---|
| `POST /_restart` | Restart all pipelines (v1.x compat) |
| `POST /_restart/{pipeline_name}` | Restart a single pipeline |
| `POST /_offline/{pipeline_name}` | Pause a single pipeline |
| `POST /_online/{pipeline_name}` | Resume a single pipeline |
| `GET /_pipelines` | List all pipelines with their status (`running`, `stopped`, `error`) |
| `GET /_pipelines/{name}` | Status of a single pipeline (since, docs processed, errors, uptime) |

---

## Implementation Plan

### Phase 1: Refactor `poll_changes()` for Isolation
**Goal:** Make `poll_changes()` fully self-contained so it can run in any thread.

- [ ] Extract all global/module-level state into parameters or a context object
- [ ] Ensure `poll_changes()` creates its own `aiohttp.ClientSession` (already does this)
- [ ] Pass `MetricsCollector` as a parameter (already does this)
- [ ] Ensure `Checkpoint` uses pipeline-specific `client_id` (already supports this)
- [ ] Add `pipeline_name` parameter for structured logging
- [ ] Unit test: run two `poll_changes()` instances concurrently in-process against a mock server

### Phase 2: `PipelineConfig` – Config Merging & Validation
**Goal:** Support the new `pipelines` array config format with global defaults.

- [ ] Create `PipelineConfig` class that deep-merges pipeline-level overrides onto global defaults
- [ ] Validate each pipeline independently (`validate_config()` per pipeline)
- [ ] Enforce unique `name` and `checkpoint.client_id` across pipelines
- [ ] Backward compat: if no `pipelines` key, synthesize a single-pipeline config from top-level keys
- [ ] Add `--validate` CLI flag that validates multi-pipeline config and exits

### Phase 3: `PipelineManager` – Thread Lifecycle
**Goal:** Start/stop/restart pipelines as threads.

- [ ] `PipelineManager` class with `start()`, `stop()`, `restart(name)` methods
- [ ] Each pipeline runs in a `threading.Thread` with `daemon=True`
- [ ] Thread function: `asyncio.run(poll_changes(pipeline_cfg))`
- [ ] Respect `threads` cap — if more pipelines than threads, queue excess pipelines (log a warning)
- [ ] Crash recovery: if a pipeline thread dies unexpectedly, restart it with backoff
- [ ] Wire `SIGINT`/`SIGTERM` to `PipelineManager.stop()`
- [ ] Integration test: start 2 pipelines, verify both produce output, shut down cleanly

### Phase 4: Metrics Aggregation
**Goal:** One metrics server, per-pipeline labels.

- [ ] Add `pipeline` label to `MetricsCollector`
- [ ] `MetricsAggregator` collects `.render()` output from all pipeline `MetricsCollector` instances
- [ ] Single `/_metrics` endpoint serves combined output
- [ ] Add `changes_worker_pipeline_up` gauge
- [ ] Add `changes_worker_pipelines_total` gauge (count of configured pipelines)

### Phase 5: REST Control API
**Goal:** Per-pipeline lifecycle control via REST.

- [ ] `GET /_pipelines` — list pipeline status
- [ ] `POST /_restart/{name}` — restart one pipeline
- [ ] `POST /_offline/{name}` / `POST /_online/{name}` — pause/resume
- [ ] Admin UI updates to show per-pipeline status

### Phase 6: Admin UI Updates
**Goal:** Dashboard shows all pipelines.

- [ ] Pipeline selector / tabs in the dashboard
- [ ] Per-pipeline metrics charts
- [ ] Config editor supports the `pipelines` array
- [ ] Per-pipeline DLQ viewer

---

## Risks & Mitigations

### 1. GIL (Global Interpreter Lock) – Thread Scheduling Deep Dive

Python's GIL means only **one thread executes Python bytecode at a time**. Here's what actually happens during a pipeline's hot loop:

```
Time →

Thread-1 (us-prices):
  [Python: build HTTP request]  →  RELEASE GIL  →  [I/O: await _changes response]  →  ACQUIRE GIL  →  [Python: parse JSON]  →  RELEASE GIL  →  [I/O: await DB write]  →  ...
       ~0.1ms                                            ~50-500ms                         ~0.1ms              ~0.2ms                                    ~5-50ms

Thread-2 (us-orders):
                                    ← ACQUIRE GIL →  [Python: build request]  →  RELEASE GIL  →  [I/O: await HTTP PUT]  →  ACQUIRE GIL  →  ...
                                                           ~0.1ms                                      ~20-200ms
```

**Key insight:** The GIL is released during _all_ I/O operations — `aiohttp` HTTP requests, `asyncpg` database queries, socket reads, file writes. Since each pipeline spends **~95-99% of its time waiting on I/O**, threads almost never contend for the GIL. The context switch between threads (~5-15 microseconds) is negligible compared to the I/O wait times (~5-500 milliseconds).

**When would GIL become a problem?**
- CPU-heavy transforms (complex JSON manipulation on every doc in Python)
- Very high throughput (10,000+ docs/sec) where the Python bytecode execution time adds up
- Many pipelines (20+) all doing JSON parsing simultaneously

**When to consider `multiprocessing` (v3.x):** If `process_cpu_percent` stays above 80% and throughput plateaus, the GIL is the bottleneck. Switch threads to processes for true parallelism.

### 2. Noisy Neighbor – Per-Job Throttling

A misbehaving or high-volume job can starve other jobs of resources (CPU time, DB connections, network bandwidth). The mitigation is **per-job rate limiting**:

```jsonc
{
  "pipelines": [
    {
      "name": "high-volume-prices",
      "throttle": {
        "max_docs_per_second": 1000,     // cap _changes consumption rate
        "max_output_per_second": 100,    // cap OUTPUT operations (HTTP PUTs or SQL queries)
        "max_batch_size": 500,           // never process more than 500 docs per batch
        "max_db_connections": 3           // cap this job's share of the DB pool
      }
    },
    {
      "name": "critical-orders",
      "throttle": {
        "max_docs_per_second": 0,        // 0 = unlimited (priority job)
        "max_output_per_second": 0,
        "max_batch_size": 0,
        "max_db_connections": 5
      }
    }
  ]
}
```

**Implementation:** Each pipeline wraps its processing loop with a `TokenBucketRateLimiter`:

```python
class TokenBucketRateLimiter:
    """Limits operations to N per second using a token bucket."""
    def __init__(self, rate: float):  # rate = 0 means unlimited
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()

    async def acquire(self):
        if self.rate <= 0:
            return  # unlimited
        # refill tokens, wait if empty
        ...
```

This is applied at two points:
1. **SOURCE side:** After reading a `_changes` batch, throttle how fast we consume the next batch
2. **OUTPUT side:** Before each `output.send()` or DB write, acquire a token

**Metrics for noisy neighbor detection:**

```
changes_worker_throttle_wait_seconds{pipeline="high-volume-prices"} 12.5   # time spent waiting on throttle
changes_worker_throttle_rejected_total{pipeline="high-volume-prices"} 0     # docs delayed (not dropped)
```

### 3. Output Table Collision – ⚠️ DANGER ZONE

**This is the most dangerous risk in multi-pipeline mode.** When two or more jobs write to the **same database table(s)**, concurrent operations on the same rows cause:

```
⚠️  DANGER: Two pipelines writing to the same table

Job A (us.prices):  INSERT INTO products (id, price) VALUES ('p:123', 20.00)
                    ON CONFLICT (id) DO UPDATE SET price = 20.00
                                                                    ← DEADLOCK or LOST UPDATE
Job B (eu.prices):  INSERT INTO products (id, price) VALUES ('p:123', 25.00)
                    ON CONFLICT (id) DO UPDATE SET price = 25.00

Result: price = 20.00 or 25.00? Depends on which transaction commits last.
        Neither job knows the other touched this row.
```

**Specific failure modes:**

| Scenario | What happens |
|---|---|
| Two jobs UPSERT the **same row** | Last-write-wins race condition. Data is not corrupted but one update is silently lost. |
| Two jobs UPSERT **different rows** in the same table | Safe — no conflict. But deadlocks can occur if the DB engine locks page ranges. |
| Job A DELETEs a row that Job B just UPSERTed | Row disappears. Job B's checkpoint has advanced, so it won't re-deliver. Data lost. |
| Two jobs INSERT into a table with **auto-increment PKs** | Safe — each gets its own PK. But foreign key relationships may break if both jobs assume they own the sequence. |

**Mitigation: Config-time table overlap detection**

On startup, the worker scans all pipeline output configs and schema mappings to build a map of which tables each pipeline writes to:

```
Job "us-prices-to-pg"   → writes to: products, price_history
Job "us-orders-to-rest" → writes to: orders, order_items
Job "eu-prices-to-pg"   → writes to: products, price_history    ← OVERLAP with us-prices!
```

**Behavior:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│  STARTUP WARNING                                                        │
│                                                                         │
│  ⚠️  TABLE OVERLAP DETECTED                                            │
│                                                                         │
│  The following tables are written to by multiple pipelines:             │
│                                                                         │
│    products       ← us-prices-to-pg, eu-prices-to-pg                   │
│    price_history  ← us-prices-to-pg, eu-prices-to-pg                   │
│                                                                         │
│  This can cause race conditions, deadlocks, or lost updates.           │
│  See docs/MULTI_PIPELINE_PLAN.md for details.                          │
│                                                                         │
│  To acknowledge this risk and proceed, add to each overlapping         │
│  pipeline config:                                                       │
│    "output": { "shared_tables_acknowledged": true }                    │
│                                                                         │
│  Without this flag, the worker will refuse to start.                   │
└─────────────────────────────────────────────────────────────────────────┘
```

**If table overlap is intentional**, the user must:
1. Set `"shared_tables_acknowledged": true` on each overlapping pipeline
2. Ensure their schema/data guarantees no row-level conflicts (e.g., each job writes to different rows via doc ID prefixes)
3. Use `SERIALIZABLE` transaction isolation or application-level row locking if same-row updates are possible

### 4. Other Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Memory growth** with many pipelines | Each pipeline holds its own HTTP session, connection pool, deques | Per-job `max_db_connections` throttle. Monitor `process_memory_rss_bytes`. Recommend max 8-10 pipelines per container. |
| **Port conflicts** | Multiple metrics servers can't bind to the same port | Single shared metrics server (already planned). |
| **Config complexity** | Users confused by new config format | Backward compat with v1.x format. Wizard / Admin UI generates multi-pipeline config. Comprehensive examples in docs. |
| **Debugging difficulty** | Hard to tell which pipeline logged what | Every log line includes `pipeline=name`. Metrics have `pipeline` label. REST API shows per-pipeline status. |

---

## Future Considerations (v3.x+)

- **`multiprocessing` option:** If GIL becomes a bottleneck, replace `threading.Thread` with `multiprocessing.Process` for true parallelism. Same `PipelineManager` interface, different backend.
- **Fan-out (1 source → N outputs):** One `_changes` feed forwarded to multiple outputs simultaneously (e.g., same data to PostgreSQL + Elasticsearch). Reads the feed once, clones each change to multiple output queues.
- **Fan-in (N sources → 1 output):** Multiple `_changes` feeds merged into a single output (e.g., multi-cluster replication into one DB). Requires merge logic and conflict resolution.
- **Dynamic pipeline management:** Add/remove pipelines at runtime via REST API without restarting the worker.
- **Pipeline dependencies:** Pipeline B starts only after Pipeline A has caught up (e.g., reference data must be loaded before transactional data).
- **Row-level locking for shared tables:** If multiple pipelines intentionally write to the same tables, add an optional advisory lock layer (`SELECT ... FOR UPDATE`) to serialize writes to the same row across pipelines.
