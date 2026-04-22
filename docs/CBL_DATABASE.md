# Couchbase Lite CE – Database Reference

This document describes the embedded Couchbase Lite Community Edition database used by Changes Worker for persistent local storage.

---

## 📋 JSON Schema Standards

**All documents stored in CBL must follow the [JSON Schema Standards Guide](../guides/JSON_SCHEMA.md).**

When designing new collections or modifying document schemas, ensure:
- Field names are `snake_case` (no camelCase)
- No top-level `_` prefixes except `meta` container
- DateTime fields use ISO-8601 or Unix epoch consistently
- Enum values are lowercase with underscores
- All documents have consistent field ordering

**All collection schemas should have corresponding JSON Schema definitions in [`json_schema/changes-worker/`](../json_schema/changes-worker/) for validation.**

---

## Overview

Changes Worker uses **Couchbase Lite CE 3.2.1** as an embedded, in-process key-value store. It replaces all file-based storage (`config.json`, `checkpoint.json`, `mappings/*.yaml`, `failed_docs.jsonl`) with a single CBL database. CBL is chip-set agnostic (x86_64 / arm64), requires no external server, and persists data to a single directory on disk.

When CBL is not available (e.g., local development on macOS), the system falls back to file-based storage automatically via the `USE_CBL` flag.

---

## Database

| Property | Value |
|---|---|
| **Database name** | `changes_worker_db` (configurable via `couchbase_lite.db_name` or `CBL_DB_NAME` env var) |
| **Storage directory** | `/app/data` (configurable via `couchbase_lite.db_dir` or `CBL_DB_DIR` env var) |
| **On-disk path** | `/app/data/changes_worker_db.cblite2/` |
| **Scope** | `changes-worker` |
| **Engine** | Couchbase Lite C 3.2.1 with Python CFFI bindings |
| **Access pattern** | Module-level singleton — one `Database` handle per process |

> **Note:** The Python CBL bindings do not expose all APIs directly. The worker calls raw CFFI functions for operations not wrapped by the Python layer — including collection management (`lib.CBLCollection_*`), N1QL queries (`N1QLQuery`), collection-level indexes (`lib.CBLCollection_CreateValueIndex`), document expiration (`lib.CBLCollection_SetDocumentExpiration`), database transactions (`lib.CBLDatabase_BeginTransaction` / `EndTransaction`), and maintenance operations (`lib.CBLDatabase_PerformMaintenance`). DLQ entries are queried via N1QL with collection-level value indexes; manifests are still used for mappings and checkpoints.

---

## Scopes & Collections

All worker data lives in the `changes-worker` scope. The full v2.0 collection list is in [`DESIGN_2_0.md`](DESIGN_2_0.md#collections). Core collections:

| Scope | Collection | Document Types | Purpose |
|---|---|---|---|
| `changes-worker` | `config` | `config` | Full worker configuration |
| `changes-worker` | `inputs_changes` | `inputs_changes` | Array of `_changes` feed source definitions |
| `changes-worker` | `outputs_rdbms` | `outputs_rdbms` | Array of RDBMS output connection configs |
| `changes-worker` | `outputs_http` | `outputs_http` | Array of HTTP/REST output configs |
| `changes-worker` | `outputs_cloud` | `outputs_cloud` | Array of cloud blob output configs |
| `changes-worker` | `outputs_stdout` | `outputs_stdout` | Array of stdout output configs |
| `changes-worker` | `tables_rdbms` | `tables_rdbms` | Reusable RDBMS table definitions library (DDL + parsed columns) |
| `changes-worker` | `jobs` | `job::{uuid}` | Pipeline job definitions (input → output with tables + mapping) |
| `changes-worker` | `checkpoints` | `checkpoint::{uuid}` | Per-job checkpoint state |
| `changes-worker` | `mappings` | `mapping:{filename}`, `manifest:mappings` | Schema mapping definitions (legacy — migrating into jobs) |
| `changes-worker` | `dlq` | `dlq:{doc_id}:{timestamp}`, `dlq:meta` | Failed output documents (dead letter queue) |

---

## Document Schemas

### `config`

Stores the full worker configuration as a JSON string. Only one config document exists.

```
doc_id: "config"
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"config"` |
| `data` | `str` | Full config dict serialized via `json.dumps()` |
| `updated_at` | `int` | Unix epoch timestamp of last save |

**Example:**

```json
{
  "type": "config",
  "data": "{\"gateway\":{\"src\":\"sync_gateway\",\"url\":\"http://localhost:4984\",...}}",
  "updated_at": 1768521600
}
```

---

### `checkpoint:{uuid}`

Local fallback checkpoint — used only when the primary checkpoint on Sync Gateway (`_local/checkpoint-{uuid}`) is unreachable. The `uuid` is derived from `SHA1(client_id + SG_URL + channels)`.

```
doc_id: "checkpoint:a1b2c3d4e5f6..."
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"checkpoint"` |
| `client_id` | `str` | The `checkpoint.client_id` from config (default: `"changes_worker"`) |
| `SGs_Seq` | `str` | Last processed sequence value (e.g., `"1500"`, `"12:34"`) |
| `time` | `int` | Unix epoch timestamp of last checkpoint save |
| `remote` | `int` | Monotonically increasing counter (CBL-compatible) |

**Example:**

```json
{
  "type": "checkpoint",
  "client_id": "changes_worker",
  "SGs_Seq": "1500",
  "time": 1768521600,
  "remote": 42
}
```

---

### `mapping:{filename}`

A schema mapping definition. The YAML or JSON content is stored as a raw string — not parsed.

```
doc_id: "mapping:order.yaml"
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"mapping"` |
| `name` | `str` | Original filename (e.g., `"order.yaml"`) |
| `content` | `str` | Full YAML/JSON content as a raw string |

**Example:**

```json
{
  "type": "mapping",
  "name": "order.yaml",
  "content": "source:\n  match:\n    type: order\ntarget:\n  table: orders\n  columns:\n    - name: id\n      value: _id"
}
```

---

### `dlq:{doc_id}:{timestamp}`

A dead letter queue entry — one document per failed output delivery. Created when `halt_on_failure=false` and a doc fails all retry attempts.

```
doc_id: "dlq:order::12345:1768521600"
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"dlq"` |
| `doc_id_original` | `str` | The `_id` of the original Couchbase document that failed |
| `seq` | `str` | The `_changes` sequence value when this doc was processed |
| `method` | `str` | HTTP method that was attempted (`"PUT"` or `"DELETE"`) |
| `status` | `int` | HTTP status code from the output endpoint (0 = connection failure) |
| `error` | `str` | Error message or response body excerpt |
| `reason` | `str` | Machine-readable classification (e.g., `data_error:data_type`, `server_error:500`) |
| `time` | `int` | Unix epoch timestamp when the failure occurred |
| `expires_at` | `int` | Unix epoch when the entry will be auto-purged (0 = no expiration) |
| `retried` | `bool` | Whether this entry has been marked as retried |
| `replay_attempts` | `int` | Number of failed replay attempts |
| `target_url` | `str` | The output URL at write time (for orphan detection) |
| `doc_data` | `str` | Full document body serialized via `json.dumps()` |

**Example:**

```json
{
  "type": "dlq",
  "doc_id_original": "order::12345",
  "seq": "42",
  "method": "PUT",
  "status": 500,
  "error": "Internal Server Error",
  "time": 1768521600,
  "retried": false,
  "doc_data": "{\"_id\":\"order::12345\",\"total\":99.50,\"_rev\":\"3-abc\"}"
}
```

---

### `manifest:{type}`

Manifest documents store a JSON-encoded list of doc IDs for mappings and checkpoints. The DLQ collection no longer uses manifests — it uses N1QL queries with collection-level indexes instead.

```
doc_id: "manifest:mappings"
doc_id: "manifest:checkpoints"
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"manifest"` |
| `ids` | `str` | JSON array of doc IDs, serialized via `json.dumps()` |

Manifests are updated atomically whenever a document is created or deleted:
- **Create** → append the new doc ID to the manifest's `ids` array
- **Delete** → remove the doc ID from the manifest's `ids` array

---

## Value Storage Rules

| Python type | CBL storage | Example |
|---|---|---|
| `str` | Native string field | `doc["name"] = "order.yaml"` |
| `int` | Native integer field | `doc["time"] = 1768521600` |
| `float` | Native float field | `doc["score"] = 0.95` |
| `bool` | Native boolean field | `doc["retried"] = False` |
| `dict` | `json.dumps()` → string field | `doc["data"] = json.dumps(cfg)` |
| `list` | `json.dumps()` → string field | `doc["ids"] = json.dumps(id_list)` |
| YAML content | Raw string field | `doc["content"] = yaml_text` |

---

## API Access

All CBL operations go through the `CBLStore` class in `cbl_store.py`:

```python
from cbl_store import CBLStore, USE_CBL

if USE_CBL:
    store = CBLStore()

    # Config
    cfg = store.load_config()
    store.save_config(cfg)

    # Checkpoints
    data = store.load_checkpoint(uuid)
    store.save_checkpoint(uuid, seq, client_id, remote)

    # Mappings
    mappings = store.list_mappings()
    content = store.get_mapping("order.yaml")
    store.save_mapping("order.yaml", yaml_content)
    store.delete_mapping("order.yaml")

    # Dead Letter Queue
    store.add_dlq_entry(doc_id, seq, method, status, error, doc)
    entries = store.list_dlq()
    entry = store.get_dlq_entry(dlq_id)
    store.mark_dlq_retried(dlq_id)
    store.delete_dlq_entry(dlq_id)
    store.clear_dlq()
    count = store.dlq_count()
```

---

## Web API Endpoints (DLQ)

The admin UI exposes DLQ management via REST when CBL is enabled:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/dlq` | Paginated list with sort/filter (N1QL `LIMIT`/`OFFSET`) |
| `GET` | `/api/dlq/count` | Count of DLQ entries (N1QL `COUNT(*)`) |
| `GET` | `/api/dlq/stats` | Aggregated stats for charts (N1QL `COUNT`/`MIN`/`GROUP BY`) |
| `GET` | `/api/dlq/explain` | N1QL query plans for index verification |
| `GET` | `/api/dlq/{id}` | Get one entry (includes full `doc_data`) |
| `POST` | `/api/dlq/{id}/retry` | Mark entry as retried |
| `DELETE` | `/api/dlq/{id}` | Delete (purge) one entry |
| `DELETE` | `/api/dlq` | Clear all entries (transactional batch purge) |

---

## Indexes

### DLQ Collection Indexes

Three value indexes are created on the `changes-worker.dlq` collection at startup via `CBLCollection_CreateValueIndex` (CFFI):

| Index Name | Columns | Purpose |
|---|---|---|
| `idx_dlq_type_time` | `type, time` | Page listing (`ORDER BY time`), purge expired (`WHERE time < cutoff`), timeline stats |
| `idx_dlq_type_reason_time` | `type, reason, time` | Reason filter (`WHERE reason LIKE ...`), `GROUP BY reason` aggregation |
| `idx_dlq_type_retried` | `type, retried` | Retried count (`WHERE retried = true`), total count |

All indexes use `type` as the leading column so SQLite's query planner can use them for the `WHERE d.type = 'dlq'` predicate that appears in every DLQ query. Index creation is idempotent (safe to call on every startup).

Query plans can be verified at runtime via `GET /api/dlq/explain`, which returns `CBLQuery_Explain()` output for all key queries. Look for `SEARCH ... USING INDEX idx_dlq_*` (good) vs. `SCAN` (bad).

---

## Docker Volume

The CBL database is stored in a named Docker volume shared between the worker and admin UI:

```yaml
volumes:
  cbl-data:

services:
  changes-worker:
    volumes:
      - cbl-data:/app/data
  admin-ui:
    volumes:
      - cbl-data:/app/data
```

The `CBL_DB_DIR` environment variable overrides the default `/app/data` directory if needed.

---

## Configuration

CBL storage is configured via the `couchbase_lite` key in `config.json`:

```jsonc
"couchbase_lite": {
    "db_dir": "/app/data",           // Storage directory (also CBL_DB_DIR env var)
    "db_name": "changes_worker_db",  // Database name (also CBL_DB_NAME env var)
    "maintenance": {
        "enabled": true,             // Run periodic compact + optimize
        "interval_hours": 24         // Hours between maintenance runs
    }
}
```

Environment variables (`CBL_DB_DIR`, `CBL_DB_NAME`) take precedence when set. The `configure_cbl()` function in `cbl_store.py` applies config values before the database is opened. The admin UI exposes these settings under the **Couchbase Lite** section in the config editor.

---

## Migration

### File → CBL Migration

On first startup with CBL, the worker auto-imports existing file-based data via `migrate_files_to_cbl()`:

1. If no `"config"` doc exists in CBL and `config.json` is present → imports it
2. If `mappings/` directory exists → imports all `.yaml`, `.yml`, `.json` files
3. If `checkpoint.json` exists → logs the migration (checkpoint is loaded by the `Checkpoint` class)

### Default Collection → Scoped Collection Migration

On startup, `migrate_default_to_collections()` checks if data exists in `_default._default` but not yet in the scoped collections. If so, it copies all documents to their proper `changes-worker.*` collections:

- `config` → `changes-worker.config`
- `checkpoint:*` + `manifest:checkpoints` → `changes-worker.checkpoints`
- `mapping:*` + `manifest:mappings` → `changes-worker.mappings`
- `dlq:*` + `manifest:dlq` → `changes-worker.dlq`

Both migrations are idempotent — they skip if the target already has data. After migration, the `--config` CLI flag still works as a one-time import path.

---

## Concurrency

CBL does not support concurrent access from multiple processes. The worker and admin UI share a volume but write to **different collections**:

- **Worker writes:** `changes-worker.checkpoints`, `changes-worker.dlq`
- **Admin UI writes:** `changes-worker.config`, `changes-worker.mappings`

Since they write to different collections (and never the same document), conflicts are avoided in practice.
