# Jobs – Connecting SOURCE → PROCESS → OUTPUT with a Job ID

A **job** is one complete pipeline: a single `_changes` feed (SOURCE), its processing config (PROCESS), and its output destination (OUTPUT). Every job has a **job ID** that ties all three stages together for metrics, logging, and lifecycle management.

**Related docs:**
- [`DESIGN.md`](DESIGN.md) -- Three-stage pipeline architecture & failure modes
- [`MULTI_PIPELINE_PLAN.md`](MULTI_PIPELINE_PLAN.md) -- Future multi-pipeline threading design (v2.x)
- [`RDBMS_PLAN.md`](RDBMS_PLAN.md) -- RDBMS output module design & per-engine config

---

## Why Job IDs?

When you run multiple `_changes` feeds — even today with separate worker instances — you need a way to answer:

- "How many docs/sec is the **orders sync** processing vs the **prices sync**?"
- "Which job is producing errors on the PostgreSQL output?"
- "Two jobs write to the same Postgres but different tables — show me each job's latency independently."

The **job ID** is the label that makes this possible. It flows through all three pipeline stages and appears on every metric, log line, and DLQ entry.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                           job_id = "us-orders-sync"                          │
│                                                                              │
│  ┌──────────────┐       ┌───────────────┐       ┌────────────────────────┐   │
│  │   SOURCE     │──────►│   PROCESS     │──────►│   OUTPUT               │   │
│  │  _changes    │       │  filter,      │       │  PostgreSQL / MySQL /  │   │
│  │  feed        │       │  fetch,       │       │  Oracle / HTTP / stdout│   │
│  │              │       │  transform    │       │                        │   │
│  └──────────────┘       └───────────────┘       └────────────────────────┘   │
│                                                                              │
│  checkpoint: scoped to job_id    metrics: labeled with job_id               │
│  DLQ: scoped to job_id           logs: tagged with job_id                   │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Current State (v1.x) – Single Job

Today the worker runs **one job**. There is no explicit `job_id` in the top-level config — the worker implicitly operates as a single unnamed pipeline.

### What's implemented now

| Stage | Job ID support | Status |
|---|---|---|
| **SOURCE** (`_changes` feed) | Not yet labeled | ⬜ Future — will use `job_id` for per-feed metrics |
| **PROCESS** (filter, fetch, transform) | Not yet labeled | ⬜ Future — will use `job_id` for per-stage timing |
| **OUTPUT** (DB forwarder) | ✅ `output.job_id` | Implemented in `db/db_base.py` |

### OUTPUT – Per-Engine / Per-Job Metrics (implemented)

The `db/db_base.py` module introduced a `DbMetrics` proxy that wraps the global `MetricsCollector`. Every DB output forwarder (PostgreSQL, MySQL, etc.) creates a `DbMetrics` instance with its `engine` and `job_id`. Each counter increment records **both**:

1. **Global total** — the existing counter (backward compatible, existing dashboards keep working)
2. **Per-engine / per-job counter** — a new labeled metric for breakdowns

#### Config

Set `job_id` in the output config:

```jsonc
{
  "output": {
    "mode": "postgres",
    "job_id": "us-orders-sync",       // ← identifies this job in metrics
    "postgres": {
      "host": "db.example.com",
      "port": 5432,
      "database": "mydb"
    }
  }
}
```

If `job_id` is omitted, it defaults to the engine name (e.g. `"postgres"`).

#### Prometheus Output

The `/_metrics` endpoint now emits per-engine/per-job counters alongside the global totals:

```promql
# ── Global totals (same as before) ──────────────────────────────
changes_worker_output_requests_total{src="sync_gateway",database="db"} 500
changes_worker_output_success_total{src="sync_gateway",database="db"} 490
changes_worker_output_errors_total{src="sync_gateway",database="db"} 10

# ── Per-engine / per-job breakdown (NEW) ────────────────────────
changes_worker_db_output_requests_total{engine="postgres",job_id="us-orders-sync"} 300
changes_worker_db_output_success_total{engine="postgres",job_id="us-orders-sync"} 295
changes_worker_db_output_errors_total{engine="postgres",job_id="us-orders-sync"} 5

changes_worker_db_output_requests_total{engine="postgres",job_id="eu-prices-sync"} 200
changes_worker_db_output_success_total{engine="postgres",job_id="eu-prices-sync"} 195
changes_worker_db_output_errors_total{engine="postgres",job_id="eu-prices-sync"} 5

# ── Per-job response time summaries ─────────────────────────────
changes_worker_db_output_response_time_seconds{engine="postgres",job_id="us-orders-sync",quantile="0.5"} 0.004200
changes_worker_db_output_response_time_seconds{engine="postgres",job_id="us-orders-sync",quantile="0.9"} 0.012000
changes_worker_db_output_response_time_seconds{engine="postgres",job_id="us-orders-sync",quantile="0.99"} 0.045000
```

#### Available Per-Job Counters

Every counter the DB forwarder increments is available with `engine` and `job_id` labels:

| Counter | Description |
|---|---|
| `db_output_requests_total` | Total send() calls |
| `db_output_success_total` | Successful writes |
| `db_output_errors_total` | Failed writes |
| `db_output_skipped_total` | Skipped docs (no mapper match, None doc) |
| `db_mapper_matched_total` | Docs matched by a schema mapper |
| `db_mapper_skipped_total` | Docs that didn't match any mapper |
| `db_mapper_errors_total` | Mapper errors (bad JSONPath, transform failure) |
| `db_mapper_ops_total` | Total SQL operations generated |
| `db_retries_total` | Transient error retry attempts |
| `db_retry_exhausted_total` | All retries exhausted |
| `db_transient_errors_total` | Transient errors (connection, deadlock) |
| `db_permanent_errors_total` | Permanent errors (constraint violation, type mismatch) |
| `db_pool_reconnects_total` | Connection pool reconnections |

#### Grafana Query Examples

```promql
# Total DB writes/sec across ALL jobs and engines
sum(rate(changes_worker_db_output_requests_total[5m]))

# Writes/sec broken down by engine
sum by (engine) (rate(changes_worker_db_output_requests_total[5m]))

# Writes/sec broken down by job
sum by (job_id) (rate(changes_worker_db_output_requests_total[5m]))

# Error rate for a specific job
rate(changes_worker_db_output_errors_total{job_id="us-orders-sync"}[5m])

# P99 latency comparison across jobs
changes_worker_db_output_response_time_seconds{quantile="0.99"}

# Two jobs writing to the same Postgres — compare throughput
sum by (job_id) (rate(changes_worker_db_output_success_total{engine="postgres"}[5m]))
```

---

## How It Works – `DbMetrics` Architecture

```
                          ┌─────────────────────────┐
                          │   MetricsCollector       │
                          │   (global, in main.py)   │
                          │                          │
                          │   output_requests_total  │◄── inc() delegated
                          │   output_errors_total    │    from DbMetrics
                          │   ...                    │
                          └──────────┬──────────────┘
                                     │
                                     │ render() calls
                                     │ DbMetrics.render_all()
                                     ▼
              ┌─────────────────────────────────────────────┐
              │           DbMetrics._registry               │
              │  (class-level list of all active instances)  │
              ├─────────────────────────────────────────────┤
              │                                             │
              │  DbMetrics(engine="postgres",               │
              │            job_id="us-orders-sync")         │
              │    ._counters = {output_requests: 300, ...} │
              │    ._resp_times = [0.004, 0.005, ...]       │
              │                                             │
              │  DbMetrics(engine="postgres",               │
              │            job_id="eu-prices-sync")         │
              │    ._counters = {output_requests: 200, ...} │
              │    ._resp_times = [0.003, 0.006, ...]       │
              │                                             │
              └─────────────────────────────────────────────┘
```

Each `BaseOutputForwarder` subclass (Postgres, MySQL, etc.) creates one `DbMetrics` instance at init time. When `send()` calls `self._metrics.inc("output_requests_total")`, it:

1. Increments the **local** labeled counter (`{engine, job_id}`)
2. Delegates to the **global** `MetricsCollector.inc()` for the unlabeled total

At render time, `MetricsCollector.render()` calls `DbMetrics.render_all()` which iterates the registry and emits the per-engine/per-job Prometheus lines.

---

## Future State – Full Job ID Across All Stages

When multi-pipeline support lands (see [`MULTI_PIPELINE_PLAN.md`](MULTI_PIPELINE_PLAN.md)), the `job_id` will be promoted to a top-level pipeline concept and flow through all three stages:

```jsonc
// v2.x config
{
  "pipelines": [
    {
      "name": "us-orders-sync",         // ← this becomes the job_id everywhere
      "gateway": { "url": "...", "database": "db", "scope": "us", "collection": "orders" },
      "processing": { "sequential": false, "max_concurrent": 20 },
      "output": { "mode": "postgres", "postgres": { ... } }
    }
  ]
}
```

| Stage | Current (v1.x) | Future (v2.x) |
|---|---|---|
| **SOURCE** | No job label | `pipeline="us-orders-sync"` on all `_changes` metrics |
| **PROCESS** | No job label | `pipeline="us-orders-sync"` on filter/fetch/transform metrics |
| **OUTPUT** | `engine` + `job_id` labels on DB metrics | Same, plus `pipeline` label on HTTP output metrics |
| **Checkpoint** | Global `client_id` | Scoped to `jobs.{name}.checkpoint` in CBL |
| **DLQ** | Global file or CBL collection | Scoped to `jobs.{name}.dlq` in CBL |
| **Logs** | No pipeline tag | Every log line includes `pipeline=name` |

The existing `output.job_id` config will be superseded by the pipeline `name` — if both are set, `name` wins. This ensures backward compatibility: v1.x configs with `output.job_id` continue to work, and v2.x configs use the pipeline name automatically.
