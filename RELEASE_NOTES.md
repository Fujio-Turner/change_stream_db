# Changes Worker — Release Notes

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
