# High Availability via CBL Replication – v3.0 Design

> **Status:** Future planning (v3.0)  
> **Depends on:** v2.0 (job-centric document model), Phase 10 (multi-job threading)  
> **Key insight:** ALL worker state lives in Couchbase Lite collections. CBL has built-in WebSocket replication. Replicate the CBL database = replicate the entire worker.

**Related docs:**
- [`DESIGN_2_0.md`](DESIGN_2_0.md) – v2.0 architecture (job-centric document model)
- [`CBL_DATABASE.md`](CBL_DATABASE.md) – CBL collection layout
- [`MULTI_PIPELINE_PLAN.md`](MULTI_PIPELINE_PLAN.md) – Multi-pipeline threading design

---

## Why HA Matters

Today, if the changes_worker process stops — crash, OOM, host failure, deployment — **processing stops**. Period. There is no standby, no failover, no handoff. The DLQ catches output failures, but it doesn't help when the entire worker is down.

This is acceptable for dev/staging but not for production workloads where `_changes` feed latency must stay under SLA (e.g., "orders must appear in PostgreSQL within 60 seconds of mutation in Couchbase").

---

## The Insight: CBL IS the State

After the v2.0 redesign, **every piece of worker state** lives in Couchbase Lite collections:

| What | Collection | Without it the worker... |
|---|---|---|
| Where it left off | `checkpoints` | ...re-processes from `since=0` (full re-sync) |
| What to process | `jobs` | ...has no jobs to run |
| Where to read from | `inputs_changes` | ...doesn't know the source |
| Where to write to | `outputs_rdbms/http/cloud` | ...doesn't know the destination |
| Processing rules | `jobs.schema_mapping` | ...can't map JSON → output |
| Infrastructure config | `config` | ...uses defaults |
| Failed documents | `dlq` | ...loses failure history |
| Data coercions | `data_quality` | ...loses coercion audit trail |
| ML/AI results | `enrichments` | ...loses enrichment data |
| Auth sessions | `sessions` | ...needs fresh auth |

**CBL has built-in WebSocket replication** to:
- Another Couchbase Lite instance (peer-to-peer)
- Sync Gateway (cloud/on-prem)
- Edge Server

So: **replicate the CBL database → a second worker has everything it needs to take over**.

---

## Architecture Options

### Option A: Active/Passive (Warm Standby)

```
                     CBL WebSocket Replication
                     ─────────────────────────
                          (continuous)

┌────────────────────┐                      ┌────────────────────┐
│  PRIMARY WORKER    │ ───── replicate ────► │  STANDBY WORKER    │
│                    │                       │                    │
│  CBL: changes_worker_db                   │  CBL: changes_worker_db
│  ├── jobs          │                       │  ├── jobs          │
│  ├── checkpoints   │ ◄──── replicate ──── │  ├── checkpoints   │
│  ├── inputs_changes│                       │  ├── inputs_changes│
│  ├── outputs_*     │                       │  ├── outputs_*     │
│  ├── dlq           │                       │  ├── dlq           │
│  ├── config        │                       │  ├── config        │
│  └── ...           │                       │  └── ...           │
│                    │                       │                    │
│  Status: ACTIVE    │                       │  Status: STANDBY   │
│  Processing jobs ✅│                       │  Idle, watching 👀 │
└────────────────────┘                       └────────────────────┘
         │                                            │
         │ health check fails                         │
         ▼                                            ▼
    ┌─────────┐                                 ┌───────────┐
    │  DOWN   │ ────── lease expires ──────────► │  PROMOTE  │
    └─────────┘                                 │  to ACTIVE│
                                                └───────────┘
```

**How it works:**

1. **Primary** runs all jobs normally. CBL continuously replicates to standby via WebSocket.
2. **Standby** has a copy of the entire CBL database (jobs, checkpoints, config, DLQ, everything). It does NOT process jobs — it just receives replicated data.
3. **Heartbeat:** Primary writes a heartbeat document to CBL every N seconds (e.g., `heartbeat::primary`, `time: <epoch>`). This replicates to standby.
4. **Failure detection:** Standby watches the heartbeat. If `now() - heartbeat.time > failover_timeout_seconds`, the primary is presumed dead.
5. **Promotion:** Standby promotes itself to active, loads jobs from its local CBL, and starts processing from the **last replicated checkpoint**.
6. **Recovery gap:** The gap between the primary's last checkpoint save and its death is the "replay window." At worst, the standby re-processes `every_n_docs` documents (duplicates, but the pipeline is designed for at-least-once delivery).

**Pros:**
- Simple. One primary, one standby.
- Zero data loss for config/jobs/DLQ (replicated continuously).
- Minimal replay window for checkpoints (depends on checkpoint frequency).
- Standby can serve read-only dashboard/metrics while in standby mode.

**Cons:**
- Standby is idle (wasted resources when primary is healthy).
- Single standby — no protection against double failure.
- Failover is not instant (detection timeout + job startup time).

---

### Option B: Active/Active (Job Partitioning)

```
┌─────────────────────────────────────────────────┐
│              Sync Gateway / Edge Server          │
│              (central CBL replication hub)       │
│                                                  │
│   All workers replicate TO and FROM here         │
└──────────┬──────────────────────┬────────────────┘
           │                      │
    ┌──────▼──────┐        ┌──────▼──────┐
    │  WORKER A   │        │  WORKER B   │
    │             │        │             │
    │  Owns:      │        │  Owns:      │
    │  job::aaa   │        │  job::ccc   │
    │  job::bbb   │        │  job::ddd   │
    │             │        │             │
    │  CBL (local)│        │  CBL (local)│
    └─────────────┘        └─────────────┘
```

**How it works:**

1. **Job assignment:** Each job document has an `owner` field: `{"owner": "worker-a", "owner_lease_expires": <epoch>}`.
2. **Lease-based ownership:** A worker claims a job by writing its ID and a lease expiry to the job document. The lease is renewed periodically (e.g., every 30s with a 90s TTL).
3. **Replication hub:** All workers replicate their CBL to a central Sync Gateway (or Edge Server). This is the single source of truth for job assignments.
4. **Failover:** If Worker A's lease on `job::aaa` expires (Worker A is dead), Worker B sees the expired lease via replication and claims the job.
5. **Checkpoint continuity:** Worker B reads `checkpoint::aaa` from its replicated CBL and resumes from `last_seq`.
6. **Conflict resolution:** CBL uses last-write-wins for the `owner` field. Sync Gateway channels can enforce that only the owner can write to a job's checkpoint/DLQ.

**Pros:**
- No idle standby — all workers are productive.
- Horizontal scaling — add workers, they claim unclaimed jobs.
- Automatic rebalancing when a worker joins/leaves.
- Each worker is a full, independent changes_worker process.

**Cons:**
- Requires Sync Gateway or Edge Server as the replication hub.
- Lease management adds complexity (renewal, expiry, race conditions).
- Conflict resolution for job ownership is non-trivial.
- Network partition can cause split-brain (two workers claim the same job).

---

### Option C: Active/Passive via Sync Gateway (Simplest Production Path)

```
┌────────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│  PRIMARY WORKER    │      │  Sync Gateway    │      │  STANDBY WORKER    │
│                    │      │  (or Edge Server)│      │                    │
│  CBL ─── push ────►│      │                  │◄──── pull ─── CBL        │
│          pull ◄────│      │  Bucket:         │      │                    │
│                    │      │  worker_state    │      │                    │
│  Status: ACTIVE    │      │                  │      │  Status: STANDBY   │
└────────────────────┘      └──────────────────┘      └────────────────────┘
```

**How it works:**

1. Primary pushes CBL changes to Sync Gateway. Standby pulls from Sync Gateway.
2. This is the same as Option A, but uses Sync Gateway as the replication transport instead of peer-to-peer CBL replication.
3. **Advantage:** Sync Gateway handles auth, channels, conflict resolution, and access control. You can use Sync Gateway channels to scope replication per job.
4. **Advantage:** Multiple standbys can pull from the same Sync Gateway — no extra config per standby.
5. **Advantage:** Sync Gateway is already in the architecture (it's the `_changes` source). You're already running it.

---

## What Gets Replicated

| Collection | Replication Direction | Conflict Strategy | Notes |
|---|---|---|---|
| `jobs` | Bidirectional | Last-write-wins | Job config rarely changes. Owner field resolves via lease. |
| `checkpoints` | Primary → Standby (push) | Primary always wins | Standby should never write checkpoints while in standby mode. |
| `inputs_changes` | Bidirectional | Last-write-wins | Admin edits from either node. |
| `outputs_*` | Bidirectional | Last-write-wins | Same as inputs. |
| `config` | Bidirectional | Last-write-wins | Infrastructure config. |
| `dlq` | Primary → Standby (push) | No conflict (unique doc IDs) | DLQ doc IDs include timestamp — no collision. |
| `data_quality` | Primary → Standby (push) | No conflict (unique doc IDs) | Same — timestamp in doc ID. |
| `enrichments` | Primary → Standby (push) | No conflict (unique doc IDs) | Same. |
| `sessions` | Bidirectional | Last-write-wins | Session refresh can happen on either node. |
| `users` | Bidirectional | Last-write-wins | Rare changes. |
| `audit_log` | Primary → Standby (push) | No conflict (unique doc IDs) | Append-only. |

---

## Replication Config

New section in the `config` document:

```json
{
  "data": {
    "replication": {
      "enabled": false,
      "mode": "active_passive",
      "role": "primary",
      "target": {
        "type": "sync_gateway",
        "url": "wss://sg.example.com:4984",
        "database": "worker_state",
        "auth": {
          "method": "basic",
          "username": "replicator",
          "password": "secret"
        }
      },
      "continuous": true,
      "heartbeat_interval_seconds": 10,
      "failover_timeout_seconds": 30,
      "collections": [
        "inputs_changes", "outputs_rdbms", "outputs_http", "outputs_cloud",
        "jobs", "checkpoints", "dlq", "data_quality", "enrichments",
        "config", "sessions", "users", "audit_log"
      ],
      "conflict_resolution": "last_write_wins",
      "push_filter": null,
      "pull_filter": null
    }
  }
}
```

---

## Heartbeat Protocol

The active worker writes a heartbeat document to the `config` collection:

```json
{
  "type": "heartbeat",
  "worker_id": "worker-a",
  "hostname": "prod-worker-01.example.com",
  "pid": 12345,
  "time": 1768521600,
  "jobs_running": ["job::aaa", "job::bbb"],
  "uptime_seconds": 86400,
  "version": "2.0.0"
}
```

**Doc ID:** `heartbeat::worker-a`

The standby checks: `if now() - heartbeat.time > failover_timeout_seconds → promote`.

---

## Failover Sequence

```
Time    Primary                           Standby
────    ───────                           ───────
T+0     Processing jobs, writing          Receiving replicated data,
        heartbeats every 10s              monitoring heartbeat

T+10    Heartbeat: time=T+10             Sees heartbeat, all good

T+20    Heartbeat: time=T+20             Sees heartbeat, all good

T+25    💥 CRASH / OOM / network down     Last heartbeat was T+20

T+30    (dead)                            now() - T+20 = 10s < 30s timeout
                                          → still waiting...

T+50    (dead)                            now() - T+20 = 30s = timeout!
                                          → PROMOTE TO ACTIVE

T+51    (dead)                            Load jobs from local CBL
                                          Load checkpoints from local CBL
                                          Start PipelineManager
                                          Resume from last replicated seq

T+55    (dead)                            Processing jobs ✅
                                          Writing own heartbeats
                                          Checkpoint gap: ~25s of data
                                          may be re-processed (at-least-once)
```

---

## Replay Window & Data Guarantees

| Scenario | Replay window | Data guarantee |
|---|---|---|
| **Checkpoint saves every batch** | 0–1 batch of docs re-processed | At-least-once (some docs delivered twice) |
| **`every_n_docs: 1000`** | Up to 1000 docs re-processed | At-least-once |
| **Continuous feed mode** | Up to 1 doc re-processed (checkpoints per row) | Effectively exactly-once |
| **DLQ entries** | None — DLQ is replicated continuously | No data loss |
| **Config/job changes** | None — replicated continuously | No data loss |

**At-least-once is the guarantee.** The system is designed for idempotent outputs (UPSERT, PUT). Duplicate deliveries during failover are safe if:
- RDBMS uses `ON CONFLICT DO UPDATE` (already the default)
- HTTP output uses `PUT` (already the default)
- Cloud output uses `key_template` with `doc_id` (already the default — same key = overwrite)

---

## CBL Collection: `replication_state`

A new collection to track replication status (v3.0):

```
CBL Database: changes_worker_db
│
└── Scope: "changes-worker"
    └── Collection: replication_state   ← replication metadata
```

```json
{
  "type": "replication_state",
  "id": "repl::sg-prod",
  "target_url": "wss://sg.example.com:4984/worker_state",
  "status": "active",
  "direction": "push_and_pull",
  "last_push_seq": "12345",
  "last_pull_seq": "12340",
  "push_pending": 5,
  "pull_pending": 0,
  "last_error": null,
  "connected_at": 1768521600,
  "total_bytes_pushed": 1048576,
  "total_bytes_pulled": 1048000,
  "total_docs_pushed": 5000,
  "total_docs_pulled": 4990
}
```

---

## Network Partitions & Split-Brain

The most dangerous failure mode: both workers think they're the active primary.

```
┌──────────┐          network          ┌──────────┐
│ Worker A │ ──── partition ──── ✂️ ────│ Worker B │
│ ACTIVE   │     (no replication)      │ ACTIVE   │ ← promoted due to missed heartbeats
│          │                           │          │
│ Processing job::aaa                  │ Processing job::aaa  ← DUPLICATE!
└──────────┘                           └──────────┘
```

**Mitigations:**

| Strategy | How | Trade-off |
|---|---|---|
| **Fencing via SG** | Before processing, check a "lock" doc on Sync Gateway. If another worker holds the lock, stand down. | Requires SG reachability. If SG is also partitioned, deadlock. |
| **Lease-based ownership** | Job ownership has a TTL. Only the lease holder processes. If lease can't be renewed (no SG), stop processing after lease expires. | Worker stops if it can't reach SG — may be overly conservative. |
| **Accept duplicates** | Both workers process the same feed. Output is idempotent (UPSERT). Checkpoints diverge, but data converges. | Wastes resources. Some outputs are not idempotent (POST). |
| **STONITH (Shoot The Other Node In The Head)** | Worker A detects partition and kills Worker B via out-of-band mechanism (API call, cloud instance termination). | Complex, fragile, cloud-provider-specific. |

**Recommended for v3.0:** Lease-based ownership + accept duplicates as a safety net. If the worker can't renew its lease within `lease_ttl_seconds`, it pauses all jobs and waits. This is "safe but conservative" — better to stop processing than to create conflicts.

---

## Implementation Plan

### Phase 1: CBL Replication Setup (v3.0)

- [ ] Add `replication` config to `config` document
- [ ] Create `replication_state` collection
- [ ] Implement CBL replicator setup using CFFI bindings (`CBLReplicator_New`, `CBLReplicator_Start`)
- [ ] Configure push/pull for all collections
- [ ] Add replication status to `/_metrics` endpoint
- [ ] Add replication status to admin UI dashboard

### Phase 2: Heartbeat & Failover (v3.0)

- [ ] Implement heartbeat writer (active worker)
- [ ] Implement heartbeat monitor (standby worker)
- [ ] Implement promotion logic (standby → active)
- [ ] Implement demotion logic (active → standby when another active is detected)
- [ ] Add `role` field to config: `"primary"`, `"standby"`, `"auto"`
- [ ] Add `GET /_ha/status` endpoint (role, last heartbeat, replication lag)

### Phase 3: Lease-Based Job Ownership (v3.0)

- [ ] Add `owner`, `owner_lease_expires` fields to job documents
- [ ] Implement lease acquisition (claim unowned/expired jobs)
- [ ] Implement lease renewal (periodic, in pipeline thread)
- [ ] Implement lease release (on graceful shutdown)
- [ ] Implement lease expiry detection (claim jobs from dead workers)
- [ ] Split-brain protection: pause jobs if lease can't be renewed

### Phase 4: Active/Active (v3.1)

- [ ] Multi-worker job partitioning
- [ ] Dynamic rebalancing when workers join/leave
- [ ] Per-worker metrics with `worker_id` label
- [ ] Admin UI: multi-worker view
- [ ] Sync Gateway channel-based access control per worker

---

## Metrics (HA-specific)

| Metric | Type | Description |
|---|---|---|
| `worker_role` | gauge (0=standby, 1=active) | Current HA role |
| `worker_heartbeat_age_seconds` | gauge | Time since last heartbeat from the other worker |
| `worker_failovers_total` | counter | Number of standby→active promotions |
| `replication_push_pending` | gauge | Docs waiting to be pushed |
| `replication_pull_pending` | gauge | Docs waiting to be pulled |
| `replication_push_bytes_total` | counter | Total bytes pushed |
| `replication_pull_bytes_total` | counter | Total bytes pulled |
| `replication_errors_total` | counter | Replication errors |
| `replication_connected` | gauge (0/1) | Replication connection status |
| `job_lease_renewals_total` | counter | Lease renewal attempts |
| `job_lease_expired_total` | counter | Leases that expired (claimed from dead workers) |

---

## Recommended Production Topology

### Small (1–3 jobs, < 1000 docs/sec)

```
┌─────────────┐         ┌──────────────┐
│  Worker A   │ ──push──► Sync Gateway │
│  (active)   │ ◄─pull── │              │
└─────────────┘         └──────────────┘
                              ▲
┌─────────────┐               │
│  Worker B   │ ──push/pull───┘
│  (standby)  │
└─────────────┘

Failover time: ~30 seconds
Replay window: < 1000 docs
```

### Medium (3–10 jobs, 1000–10,000 docs/sec)

```
┌─────────────┐
│  Worker A   │──── owns job::1, job::2, job::3
│  (active)   │
└─────────────┘         ┌──────────────┐
                        │ Sync Gateway │ ← replication hub
┌─────────────┐         │              │
│  Worker B   │──── owns job::4, job::5
│  (active)   │         └──────────────┘
└─────────────┘
                              ▲
┌─────────────┐               │
│  Worker C   │──── standby (claims jobs if A or B dies)
│  (standby)  │───────────────┘
└─────────────┘

Active/active for throughput, standby for safety
```

### Large (10+ jobs, 10,000+ docs/sec)

```
Consider v3.1 Active/Active with dynamic rebalancing,
or run multiple independent worker clusters partitioned
by source (one cluster per Sync Gateway / Edge Server).
```

---

## FAQ

**Q: Can I run HA without Sync Gateway?**  
A: Yes — CBL supports peer-to-peer replication between two CBL databases directly. But Sync Gateway gives you auth, channels, conflict resolution, and a central hub for N>2 workers.

**Q: What if both workers are down?**  
A: When any worker starts, it reads jobs and checkpoints from its local CBL and resumes. No data is lost (CBL is on-disk). You just have a processing gap.

**Q: Does replication add latency to the pipeline?**  
A: No. Replication is asynchronous and continuous. The pipeline writes to local CBL (microseconds), and CBL replicates in the background. The only latency is the replication lag (typically <1 second on a LAN).

**Q: What about the `_changes` feed itself — is that replicated?**  
A: No. The `_changes` feed is consumed from Sync Gateway / Edge Server directly. Only the worker's *internal state* (checkpoints, config, DLQ) is replicated. Both workers consume from the same source.

**Q: Can I use Edge Server instead of Sync Gateway as the replication hub?**  
A: Yes. Edge Server supports CBL replication over WebSocket. It's lighter than Sync Gateway and doesn't require a Couchbase Server bucket.
