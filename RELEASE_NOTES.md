# Changes Worker — Release Notes

---

## v2.0.0 — 2026-04-20

### ⚠️ Breaking Changes

- **Job-centric config model** — The monolithic `config.json` is replaced by a composable document model stored in Couchbase Lite collections. Inputs, outputs, and jobs are now separate, reusable entities. A v1.x → v2.0 migration runs automatically on first startup.

- **CBL schema redesign** — 15 collections in the `changes-worker` scope organized by concern: `inputs_changes`, `outputs_rdbms`, `outputs_http`, `outputs_cloud`, `outputs_stdout`, `jobs`, `checkpoints`, `dlq`, `data_quality`, `enrichments`, `config`, plus future-reserved `users`, `sessions`, `audit_log`, `notifications`.

- **`cbl_store.py` API overhaul** — New CRUD methods for inputs, outputs (by type), and jobs. Legacy single-document methods still work for migration but are deprecated.

- **Settings page slimmed** — Settings UI now only shows infrastructure config (logging, metrics, admin_ui, CBL, shutdown). Pipeline configuration has moved to the Wizard.

### New Features

- **Multi-job pipeline architecture** — Run multiple independent `_changes` feed pipelines concurrently. Each job connects one input to one output with its own schema mapping, checkpoint, and lifecycle.

- **`PipelineManager`** (`pipeline_manager.py`) — Orchestrates all job threads. Loads enabled jobs from CBL at startup, enforces global `max_threads`, monitors threads for crashes with exponential-backoff restart, and provides graceful shutdown (drain → checkpoint → close).

- **`Pipeline`** (`pipeline.py`) — Per-job thread wrapper with isolated `asyncio.run()` event loop, dedicated HTTP session, checkpoint state, output forwarder, and `ThreadPoolExecutor` for async middleware.

- **Job control REST API** — 7 new endpoints for programmatic job management:
  - `POST /api/jobs/{id}/start` — Start a single job
  - `POST /api/jobs/{id}/stop` — Graceful stop
  - `POST /api/jobs/{id}/restart` — Stop + start
  - `GET /api/jobs/{id}/state` — Job status, uptime, error count
  - `POST /api/_restart` — Restart all jobs
  - `POST /api/_offline` — Stop all jobs (keep config)
  - `POST /api/_online` — Resume all jobs after offline

- **Inputs management API** (`rest/api_v2.py`) — CRUD endpoints for `_changes` feed source definitions (`GET/POST /api/inputs_changes`, `PUT/DELETE /api/inputs_changes/{id}`).

- **Outputs management API** (`rest/api_v2.py`) — CRUD endpoints for each output type: `outputs_rdbms`, `outputs_http`, `outputs_cloud`, `outputs_stdout`.

- **Jobs management API** (`rest/api_v2.py`) — Full CRUD for job documents (`GET/POST /api/jobs`, `GET/PUT/DELETE /api/jobs/{id}`). Each job references an input ID, output ID + type, and embeds its schema mapping.

- **v1.x → v2.0 migration** — Automatic one-time migration converts legacy `config.json` into the new document model: creates input entries, output entries, a default job, and preserves the checkpoint.

- **Multi-job dashboard** — Dashboard redesigned with per-job status table showing status indicator dots (green/yellow/red/gray), uptime, docs in/out, errors, checkpoint seq, and action buttons (start/stop/restart/kill/edit).

- **Job filtering across UI** — Logs page and DLQ page now include a job filter dropdown populated from `/api/jobs`, allowing per-job log/DLQ views.

- **Wizard overhaul** — Setup wizard updated for the new document model, creating inputs and outputs as reusable entities that can be composed into jobs.

- **Help page** (`web/templates/help.html`) — New help/documentation page in the admin UI.

### Changes

- **Version bump** — All version references updated from v1.7.0 to v2.0.0.

### New Files

- `pipeline.py` — Per-job pipeline thread wrapper
- `pipeline_manager.py` — Multi-job thread orchestrator
- `rest/api_v2.py` — v2.0 REST API for inputs, outputs, and jobs CRUD
- `rest/api_v2_jobs_control.py` — REST endpoints for job lifecycle control
- `web/server.py` — Web server module
- `web/templates/help.html` — Help page
- `tests/test_api_v2_inputs.py` — Input API tests
- `tests/test_api_v2_outputs.py` — Output API tests
- `tests/test_api_v2_jobs.py` — Jobs API tests
- `tests/test_cbl_store_v2.py` — CBL store v2 schema tests
- `tests/test_migration_v1_to_v2.py` — Migration tests
- `tests/test_phase_6_job_based_startup.py` — Job-based startup tests
- `tests/test_phase_7_config_cleanup.py` — Config cleanup tests
- `tests/test_phase_8_dashboard.py` — Dashboard tests
- `tests/test_phase_10_threading.py` — Multi-job threading tests
- `docs/DESIGN_2_0.md` — v2.0 architecture design document
- `docs/UI_JOBS_MANAGEMENT.md` — Multi-job UI management design
- `docs/JOBS.md` — Job document model reference

### ⚠️ Rollback Notes

Rolling back to v1.7.0 is safe — the v1.x `config.json` is not modified during migration. However, any jobs created in the v2.0 CBL schema will not be visible to v1.x. Checkpoints created by v2.0 jobs use `checkpoint::{job_uuid}` keys which v1.x will ignore (it will resume from its own checkpoint).

### Documentation

- `docs/DESIGN_2_0.md` — Full architecture redesign document with all 12 phases
- `docs/UI_JOBS_MANAGEMENT.md` — Multi-job UI management design
- `docs/JOBS.md` — Job document model and lifecycle
- `UPDATE_SUMMARY.md` — HTML update summary for v2.0 UI changes

---

## v1.7.0 — 2026-04-19

### New Features

- **Attachment processing pipeline** — New `attachments` config block enables detect → fetch → upload → post-process of document attachments. Supports S3/MinIO, HTTP, and filesystem destinations with configurable filters (content type, size, name pattern), fetch modes (individual, bulk, stream), and post-processing actions (update_doc, delete_attachments, delete_doc, set_ttl, purge). Post-processing writes back to the source via PUT/DELETE/PURGE.

- **Two pipeline modes** — The worker now operates in **Data Only** mode (default, `attachments.enabled=false`) or **Attachments + Data** mode (`attachments.enabled=true`). The dashboard architecture diagram dynamically reflects the active mode: Data Only hides the Attachments node and draws direct Worker→Output arrows; Attachments + Data shows the Attachments node between Worker and Outputs with live metrics.

- **Dashboard mode indicator** — The architecture graph hides/shows the Attachments column based on config. Worker stat label shows a 📎 prefix in Attachments + Data mode. Worker column auto-resizes to fill space in Data Only mode.

- **Settings mode banner** — The Attachments settings tab now shows a mode indicator banner: green "Attachments + Data" when enabled, yellow "Data Only" when disabled. Updates live when the toggle is flipped.

- **Updated architecture diagrams** — New `img/architecture.png` (Data Only with DLQ) and `img/architecture_attach.png` (Attachments + Data with DLQ and dotted post-process arrow back to source).

### Bug Fixes

- **`_catch_up_normal()` missing parameter** — Fixed `TypeError: _catch_up_normal() got an unexpected keyword argument 'attachment_processor'` by adding `attachment_processor` to the function signature and forwarding it to `_process_changes_batch`.

- **`_consume_continuous_stream()` missing parameter** — Same fix applied: added `attachment_processor` parameter and forwarded it to inner `_process_changes_batch` call.

- **`_consume_websocket_stream()` missing parameter** — Same fix applied for WebSocket feed mode.

### Changes

- **Version bump** — All footers and version references updated from v1.5.0 to v1.7.0.

---

## v1.6.0 — 2026-04-18

### New Features

- **Optimised initial sync** — When starting from `since=0` (or resuming an interrupted initial pull), the `_changes` feed now uses `active_only=true` (Couchbase) or filters out deletes/removes (CouchDB), `include_docs=false`, and `feed=normal`. By default the worker makes a single large request with no limit (relying on `http_timeout_seconds`, default 300s / ~1.5M entries) to get the true `last_seq` and avoid a consistency gap where deletes between chunks could be missed. Set `optimize_initial_sync: true` to enable chunked paging with `continuous_catchup_limit` for feeds too large for a single request. After catch-up completes (0 results), the worker switches back to the user's configured feed settings.

- **Crash-safe initial sync tracking** — A new `initial_sync_done` flag is persisted in the checkpoint document (SG `_local` doc, file fallback, and CBL). If the process is interrupted mid-initial-pull, it resumes in initial-sync mode from the last checkpoint instead of switching to normal mode prematurely. Legacy checkpoints (without the flag) are treated as already complete to avoid re-syncing.

### Changes

- **Logging levels rebalanced** — Checkpoint INFO logs now show only the operation (loaded/saved) and storage type, with `doc_id` and `seq` moved to DEBUG. `_changes` batch logs at INFO show the change count; individual `_id`/`_rev`/`_seq` rows log at DEBUG. `_bulk_get` logs request/response doc counts at INFO; individual doc IDs and payload sizes at DEBUG. Single-doc GET logs at INFO (count) and DEBUG (`_id`, `_rev`, payload size). Replication config (feed type, active_only, include_docs, since, initial_sync state) is logged at INFO on startup.

---

## v1.5.0 — 2026-04-18

### New Features

- **AWS S3 cloud output** — New `cloud/` package for forwarding documents to S3-compatible blob storage. Supports AWS S3, MinIO, LocalStack, and any S3-compatible endpoint via `endpoint_url`. Features include SSE-S3/SSE-KMS server-side encryption, storage class selection (STANDARD, IA, GLACIER), custom metadata headers, configurable key templating (`{prefix}/{doc_id}.json`), batching (max docs/bytes/seconds), and exponential backoff retry. Cloud-specific Prometheus metrics via `CloudMetrics`.

- **`main.py` refactor** — ~1,300 lines of `_changes` feed HTTP logic extracted from `main.py` into `rest/changes_http.py`. Includes `ShutdownRequested`, `RetryableHTTP`, `fetch_docs`, batch processing, continuous/websocket stream consumers, and DLQ replay. `main.py` is now significantly leaner.

- **DB connection pool safety** — All 4 database engines (PostgreSQL, MySQL, MS SQL, Oracle) now use `_pool_lock` (asyncio.Lock) to serialize reconnects, atomic `_close_pool()` teardown, `ConnectionError` guards when pool is `None`, and `conn.rollback()` on SQL execution failure. Prevents race conditions during reconnects.

- **Schema mapping diagnostics** — `map_document()` now returns a `(ops, MappingDiagnostics)` tuple. `MappingDiagnostics` tracks missing fields and type mismatches during document mapping. ISO date/datetime auto-coercion helpers detect and convert date strings automatically. SQL type validation via `_SQL_TYPE_EXPECT` dictionary.

- **Auto-Map API** (`POST /api/auto-map`) — Score-based heuristic matching of JSON source fields to SQL column names without any ML library. Uses token normalization, synonym dictionary (~30 domain-specific synonyms), semantic groups (id, date, name, email, price, qty, status), false-friend penalties, and `difflib.SequenceMatcher` for fuzzy name similarity.

- **AI assist consolidation** — Duplicated AI instructions and helpers from `schema.html` and `wizard.html` extracted into shared `web/static/js/ai-assist.js`. Includes `AI_INSTRUCTIONS` (full LLM prompt), `AI_RESPONSE_FORMAT`, `aiAnalyzeFields()`, `aiBuildContext()`, `aiCategorizeTransforms()`, and UI helpers (`aiSwitchTab`, `aiCopy`, `aiDownload`, `aiUploadFile`). Eliminates ~100+ lines of duplication.

- **Setup Wizard improvements** — The wizard now uses the shared AI-assist module and includes improved field mapping with AI context export for LLM-powered schema mapping generation.

### Changes

- **`ic()` traces added** — IceCream debug traces added across `cbl_store.py`, all 4 DB engines, `schema/mapper.py`, and `rest/output_http.py` for comprehensive TRACE-level diagnostics.

- **Structured logging throughout** — All `logger.info/debug/warning` calls in DB engines and REST modules converted to structured `log_event()` calls with proper log keys.

- **Config UI redesigned** — `config.html` restructured from section groups to tab-based layout (Source, Output, Checkpoint, Logging tabs). Inline `?` help tooltips added. New S3 output configuration section.

- **DaisyUI v5 CSS compatibility** — `oklch(var(--color-*))` → `var(--color-*)` and `oklch(var(--color-primary) / 0.12)` → `color-mix(in oklab, ...)` in sidebar CSS. Nav icons switched from `<img>` to mask-based `<span>` for color inheritance.

- **Feed health diagnostics** — Dashboard correctly identifies `websocket`/`continuous` feed types as streaming. Idle-timeout reconnects shown as informational, not warnings.

- **Version bump** — All footers and version references updated from v1.4.0 to v1.5.0.

### Configuration

New `config.json` section for S3 output:

```json
"s3": {
  "bucket": "",
  "region": "us-east-1",
  "key_prefix": "",
  "key_template": "{prefix}/{doc_id}.json",
  "content_type": "application/json",
  "storage_class": "STANDARD",
  "server_side_encryption": "",
  "endpoint_url": "",
  "on_delete": "delete_object",
  "batch": {
    "enabled": false,
    "max_docs": 100,
    "max_bytes": 5242880,
    "max_seconds": 30
  },
  "max_retries": 5,
  "backoff_base_seconds": 1,
  "backoff_max_seconds": 60
}
```

### Documentation

- **`docs/CLOUD_BLOB_PLAN.md`** — Full design document for cloud blob storage output: architecture, module layout, key templating, batching, retry/backoff, metrics, config schema.
- **`docs/LOGGING.md`** — Developer logging guide: `ic()` (TRACE) vs `log_event()` (structured production), all 10 log keys, safe import pattern, output format examples.

### New Files

- `cloud/__init__.py` — Cloud output factory
- `cloud/cloud_base.py` — Abstract base forwarder + `CloudMetrics` (Prometheus counters)
- `cloud/cloud_s3.py` — AWS S3 implementation (boto3, SSE, storage classes, MinIO/LocalStack)
- `rest/changes_http.py` — Extracted `_changes` feed HTTP client logic
- `web/static/js/ai-assist.js` — Shared AI-assist module
- `docs/CLOUD_BLOB_PLAN.md` — Cloud blob storage design document
- `docs/LOGGING.md` — Developer logging guide

---

## v1.4.0 — 2026-04-16

### New Features

- **Apache CouchDB source** — Added `gateway.src: "couchdb"` as a new input source type. Connects to CouchDB's `_changes` feed with support for `longpoll`, `continuous`, and `eventsource` feed types. CouchDB-specific behavior: skips `active_only`, `channels`, `version_type`, and scope/collection params (not supported by CouchDB). Documents are fetched via CouchDB's `POST /{db}/_bulk_get` (JSON response). Checkpoints are stored as `_local/` docs on CouchDB, same as SG.

- **Setup Wizard** (`/wizard`) — New 3-step guided setup page in the admin UI:
  1. **Connect Source** — Configure and test SG/App Services/Edge Server/CouchDB connectivity, fetch a sample document
  2. **Configure Output** — Choose stdout, HTTP, or RDBMS output with connection testing
  3. **Map Fields** — Drag-and-drop field mapping with transform functions, generates complete `config.json` and mapping file

- **Wizard API endpoints** — `POST /api/wizard/test-source` (test source connectivity with ad-hoc config, returns sample doc) and `POST /api/wizard/test-output` (test HTTP output endpoint reachability).

- **CouchDB-aware UI** — Config editor and wizard auto-hide unsupported fields (scope, collection, channels, active_only) when `couchdb` is selected. Feed type dropdown includes `eventsource (CouchDB only)`. Session cookie auth is disabled for CouchDB.

### Changes

- **Navbar updated** — All pages now include a "Wizard" link in the navigation bar.

- **Version bump** — All footers and version references updated from v1.1.0/v1.3.0 to v1.4.0.

- **Compatibility matrix** — README expanded to 4-column matrix covering Sync Gateway, App Services, Edge Server, and CouchDB with new rows for `active_only`, session auth, and channels support.

### Documentation

- **`docs/WIZARD.md`** — Full wizard documentation: all 3 steps, API payloads, CouchDB notes, typical workflow.
- **`docs/ADMIN_UI.md`** — Updated with wizard page, wizard API endpoints, and CouchDB-aware field visibility.
- **`docs/DESIGN.md`** — Updated pipeline description to include CouchDB and eventsource feed type.
- **`README.md`** — Updated architecture diagram, compatibility matrix, auto-behavior table, admin UI section, and project structure.

---

## v1.3.0 — 2026-04-16

### New Features

- **Production logging system** — SG-inspired structured logging with per-handler `log_keys` filtering, per-key level overrides, file rotation (`max_size`, `max_age`, `rotated_logs_size_limit`), and sensitive data redaction (`none` / `partial` / `full`). Console and file handlers are independently configurable via `config.json`. Replaces the previous single-level `logging.basicConfig`.

- **Log keys** — 10 pipeline log categories for granular filtering: `CHANGES`, `PROCESSING`, `MAPPING`, `OUTPUT`, `HTTP`, `CHECKPOINT`, `RETRY`, `METRICS`, `CBL`, `DLQ`. Each can be enabled/disabled and given its own log level per handler.

- **TRACE log level** — New level below DEBUG for verbose diagnostics. `icecream` output is routed to TRACE. Controlled independently per console/file handler.

- **Redaction** — Sensitive data (passwords, tokens, Bearer headers, URL credentials) is automatically masked in log output. Modes: `none` (dev), `partial` (production — `p*****d`), `full` (audit — `<ud>XXXXX</ud>`).

- **Operation tagging** — Every output/checkpoint/DLQ log event includes an `operation` field (`INSERT`, `UPDATE`, `DELETE`, `SELECT`) inferred from the document revision and HTTP method.

- **Couchbase Lite structured logging** — All CBL operations (open, close, read, write, query, purge) now emit structured log events under the `CBL` key with `operation`, `doc_id`, `doc_type`, `db_size_mb`, `duration_ms`, and `error_detail` fields. Missing configs are logged as warnings.

- **CBL database maintenance** — New `CBLStore` methods: `compact()`, `reindex()`, `integrity_check()`, `optimize()`, `full_optimize()`, and `run_all_maintenance()`. Mirrors `CBLMaintenanceType` from the Couchbase Lite SDK.

- **CBL maintenance scheduler** — Background thread runs `compact` + `optimize` on a configurable interval (default: every 24 hours). Configured via `cbl_maintenance.enabled` and `cbl_maintenance.interval_hours`. Logs size before/after compact with % reduction.

- **DLQ log key** — Dead letter queue operations (add, retry, purge, list, clear) now log under their own `DLQ` key instead of being mixed into `OUTPUT` or `CBL`.

- **Proper CBL lifecycle** — Database is explicitly closed on shutdown via `close_db()`, and the maintenance scheduler is stopped cleanly.

### Changes

- **Renamed `changes_worker.py` → `main.py`** — Entrypoint is now `main.py`. Dockerfile, tests, and lazy imports updated. Logger name and metric prefixes remain `changes_worker` (product identity).

- **File logging enabled by default** — `config.json` now ships with `logging.file.enabled: true`, writing to `logs/changes_worker.log` with debug-level, all log keys, and rotation (100 MB / 7 days / 1 GB cap).

- **`.gitignore`** — Added `logs/*.log` and `logs/*.log.*` for rotated log files.

### Configuration

New `config.json` sections:

```json
"cbl_maintenance": {
  "enabled": true,
  "interval_hours": 24
}
```

```json
"logging": {
  "redaction_level": "partial",
  "console": {
    "enabled": true,
    "log_level": "info",
    "log_keys": ["*"],
    "key_levels": {}
  },
  "file": {
    "enabled": true,
    "path": "logs/changes_worker.log",
    "log_level": "debug",
    "log_keys": ["*"],
    "key_levels": {},
    "rotation": {
      "max_size": 100,
      "max_age": 7,
      "rotated_logs_size_limit": 1024
    }
  }
}
```

### New Files

- `pipeline_logging.py` — Logging module: `configure_logging()`, `log_event()`, `infer_operation()`, `Redactor`, `LogKeyLevelFilter`, `RedactingFormatter`, `ManagedRotatingFileHandler`

---

## v1.2.0 — 2026-04-16

### New Features

- **Transform auto-injection** — Selecting a transform from the dropdown in the Schema Mapping editor now auto-injects the source path into the function. For example, selecting `split(,"")` with source path `$._id` populates `split($._id,"")` in the editable field, so users only need to fill in the remaining arguments (e.g., `split($._id,"::")[2]`). Works for both Tables and JSON output modes.

- **JSON sample templates** — Three new JSON-mode templates added to the Sample Templates dropdown: **Orders (JSON)** (epoch vs ISO-8601 date conversion, `from_epoch().format_date()` chaining), **Events (JSON)** (chained `trim().lowercase()`, `replace().propercase()`, `join()` on arrays), and **Sensors (JSON)** (`to_decimal()` precision, `to_string()` coercion, `sha256()` hashing). The dropdown is now organized into Tables and JSON sections.

- **Mapping coverage stats** — Live coverage indicators on the Schema Mapping editor show mapping completeness. **Source Coverage** (left panel) displays what percentage of source fields are mapped with an expandable list of unmapped fields. **Target Coverage** (right panel) displays what percentage of target columns have source paths filled in, with red warning badges for empty columns. Both update in real time as mappings are edited.

### Documentation

- Updated `ADMIN_UI.md` with new coverage stats section, JSON templates, and transform auto-injection behavior
- Updated `SCHEMA_MAPPING.md` admin UI editor section with new features

---

## v1.1.0 — 2026-04-15

### New Features

- **Custom request options** (`output.request_options`) — Inject query-string parameters and custom HTTP headers into every output request. Configure `params` (e.g., `{"batch": "ok"}` → `?batch=ok`) and `headers` (e.g., `{"X-Source": "changes-worker"}`) via config.json.

- **Dead letter queue** (`output.dead_letter_path`) — Failed output documents are written to an append-only JSONL file (`failed_docs.jsonl`) with full doc body, error details, seq, method, and timestamp. Prevents silent data loss when `halt_on_failure=false`.

- **Per-doc result tracking** — `send()` now returns a result dict (`{"ok": true/false, "doc_id": ..., "status": ..., "error": ...}`) enabling fine-grained batch tracking. Every batch logs a summary: `BATCH SUMMARY: 7/10 succeeded, 3 failed (3 written to dead letter queue)`.

- **Sub-batch checkpointing** (`checkpoint.every_n_docs`) — Save the checkpoint every N docs within a batch instead of only at the end. Reduces data loss on crash during large catch-ups (e.g., `every_n_docs: 1000` on a 100K batch → max 1,000 docs re-processed on restart vs all 100K). Requires `sequential: true`.

- **New Prometheus metrics:**
  - `output_success_total` — Total output requests that succeeded
  - `dead_letter_total` — Total documents written to the dead letter queue

- **Docker Compose support** — Added `docker-compose.yml` for containerized deployment with config bind-mount and metrics port exposure.

### Changes

- **CBL-compatible checkpoint format** — Checkpoint documents now use `time` (epoch integer) instead of `dateTime` (ISO string), and `remote` instead of `local_internal`, matching the Couchbase Lite convention where `remote` indicates a pull replication. Existing checkpoints with the old field names are read correctly (backward compatible).

- **Explicit `aiohttp.web` import** — Fixed `AttributeError: module aiohttp has no attribute web` when running in containers.

### Documentation

- **One Process Per Collection** — New section in README explaining that each worker monitors exactly one collection, with a diagram showing multi-instance deployment.

- **Design document** (`docs/DESIGN.md`) — Comprehensive architecture document covering the three-stage pipeline (LEFT/MIDDLE/RIGHT), sequential vs parallel trade-offs, checkpoint strategies, all failure modes, dead letter queue lifecycle, and recommended configurations with diagrams.

- **Architecture diagrams** — Added visual diagrams for pipeline overview, sequential vs parallel processing, checkpoint strategies, failure modes flowchart, and dead letter queue lifecycle.

- **Root README** — Added Examples section linking to changes_worker.

- **`.gitignore`** — Updated with Python, macOS, Windows, IDE, and Docker Compose patterns.

---

## v1.0.0 — 2026-04-15

**Initial release.** A production-ready, async Python 3 processor for the Couchbase `_changes` feed.

### Features

- **Multi-source support** — Works with Sync Gateway, Capella App Services, and Couchbase Edge Server. Automatic compatibility handling (feed type fallbacks, timeout clamping, `_bulk_get` vs individual GETs).

- **Longpoll changes feed** — Configurable poll interval, channel filtering, `active_only`, `include_docs`, and `version_type` (rev/cv) support.

- **Feed throttling** — Consume large feeds (e.g., `since=0` with 100K+ docs) in configurable bite-sized batches via `throttle_feed`, with immediate back-to-back fetching until caught up.

- **CBL-style checkpoint management** — Checkpoints stored as `_local/` documents on Sync Gateway using the same key derivation as Couchbase Lite (`SHA1(client_id + URL + channels)`). Falls back to local `checkpoint.json` when the gateway is unreachable.

- **Output forwarding** — Forward processed documents to any HTTP endpoint (`PUT`/`DELETE` per doc) or to stdout for piping. Supports configurable retry with exponential backoff, halt-on-failure (freezes checkpoint), and reachability checks at startup.

- **Multiple output formats** — JSON (default), XML, form-encoded, msgpack, CBOR, BSON, and YAML. Startup validation blocks launch if the required library isn't installed.

- **Doc fetching** — When `include_docs=false`, fetches full document bodies via `_bulk_get` (SG/App Services) or fanned-out individual GETs (Edge Server), processed in configurable batches (`get_batch_number`).

- **Async concurrency control** — Parallel or sequential processing within each batch, with a configurable semaphore (`max_concurrent`). Checkpoint only advances after the entire batch completes.

- **Startup config validation** — Every setting validated against the selected `gateway.src` before the worker starts. Invalid combinations produce clear error messages and block startup; non-fatal issues log warnings.

- **Connection test mode** (`--test`) — Verifies server root, keyspace, `_changes` endpoint, checkpoint, and output endpoint reachability. Returns exit code 0/1 for CI and Docker health checks.

- **Dry run mode** — `processing.dry_run=true` processes the feed and logs what would be sent without actually sending anything.

- **Retryable HTTP** — Configurable retry with exponential backoff for both gateway and output requests. Separate retry configs for source vs destination.

- **Prometheus metrics endpoint** (`/_metrics`) — Built-in HTTP server exposing all operational metrics in Prometheus text exposition format:

  | Category | Metrics |
  |---|---|
  | **Process** | `uptime_seconds` |
  | **Poll loop** | `poll_cycles_total`, `poll_errors_total`, `last_poll_timestamp_seconds`, `last_batch_size` |
  | **Changes** | `changes_received_total`, `changes_processed_total`, `changes_filtered_total`, `changes_deleted_total`, `changes_removed_total` |
  | **Feed content** | `feed_deletes_seen_total`, `feed_removes_seen_total` (always counted, regardless of filter settings) |
  | **Data volume** | `bytes_received_total` (from `_changes` + `_bulk_get` + GETs), `bytes_output_total` (to downstream) |
  | **Doc fetching** | `docs_fetched_total` |
  | **Output** | `output_requests_total`, `output_errors_total`, `output_endpoint_up`, `output_requests_by_method_total{method=PUT\|DELETE}`, `output_errors_by_method_total{method=PUT\|DELETE}` |
  | **Response time** | `output_response_time_seconds` summary (p50, p90, p99, sum, count) |
  | **Checkpoint** | `checkpoint_saves_total`, `checkpoint_save_errors_total`, `checkpoint_seq` |
  | **Retries** | `retries_total` |

  All metrics labeled with `src` and `database` for multi-instance Grafana dashboards. Full reference with PromQL queries and alerting rules in [`metrics.html`](metrics.html).

- **Graceful shutdown** — Handles `SIGINT`/`SIGTERM`, completes current batch, saves checkpoint, and exits cleanly.

- **Docker support** — Includes `Dockerfile` for containerized deployment.

- **Logging** — Structured logging via Python stdlib with [icecream](https://github.com/gruns/icecream) debug tracing. Configurable log level (DEBUG/INFO/WARNING/ERROR).

### CLI

```
python main.py --config config.json          # Run the worker
python main.py --config config.json --test   # Test connectivity
python main.py --version                     # Print version
```

### Requirements

- Python 3.11+
- `aiohttp>=3.9`
- `icecream>=2.1`
- Optional: `msgpack`, `cbor2`, `pymongo` (bson), `pyyaml` for non-JSON output formats
