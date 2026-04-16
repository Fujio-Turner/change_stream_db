# Couchbase Lite CE – Database Reference

This document describes the embedded Couchbase Lite Community Edition database used by Changes Worker for persistent local storage.

---

## Overview

Changes Worker uses **Couchbase Lite CE 3.2.1** as an embedded, in-process key-value store. It replaces all file-based storage (`config.json`, `checkpoint.json`, `mappings/*.yaml`, `failed_docs.jsonl`) with a single CBL database. CBL is chip-set agnostic (x86_64 / arm64), requires no external server, and persists data to a single directory on disk.

When CBL is not available (e.g., local development on macOS), the system falls back to file-based storage automatically via the `USE_CBL` flag.

---

## Database

| Property | Value |
|---|---|
| **Database name** | `changes_worker_db` |
| **Storage directory** | `/app/data` (configurable via `CBL_DB_DIR` env var) |
| **On-disk path** | `/app/data/changes_worker_db.cblite2/` |
| **Scope** | `_default` (CBL CE uses the default scope) |
| **Collection** | `_default` (CBL CE uses the default collection) |
| **Engine** | Couchbase Lite C 3.2.1 with Python CFFI bindings |
| **Access pattern** | Module-level singleton — one `Database` handle per process |

> **Note:** CBL CE Python bindings do not expose N1QL queries or multiple scopes/collections. All documents live in `_default._default`. Document types are distinguished by their `type` field and doc ID prefix.

---

## Document Types

The database stores five types of documents, identified by their `type` field and doc ID naming convention:

| Type | Doc ID pattern | Count | Purpose |
|---|---|---|---|
| `config` | `config` | 1 | Full worker configuration |
| `checkpoint` | `checkpoint:{uuid}` | 1 per connection | Last processed sequence for checkpoint fallback |
| `mapping` | `mapping:{filename}` | 0–N | Schema mapping YAML/JSON definitions |
| `dlq` | `dlq:{doc_id}:{timestamp}` | 0–N | Failed output documents (dead letter queue) |
| `manifest` | `manifest:{type}` | 2 | Index of all doc IDs for a given type (mappings, dlq) |

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
| `time` | `int` | Unix epoch timestamp when the failure occurred |
| `retried` | `bool` | Whether this entry has been marked as retried |
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

An index document that stores a JSON-encoded list of all doc IDs for a given document type. Required because CBL CE Python bindings do not expose N1QL queries — there is no `SELECT * WHERE type = 'mapping'`.

```
doc_id: "manifest:mappings"
doc_id: "manifest:dlq"
```

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Always `"manifest"` |
| `ids` | `str` | JSON array of doc IDs, serialized via `json.dumps()` |

**Example:**

```json
{
  "type": "manifest",
  "ids": "[\"mapping:order.yaml\",\"mapping:product.yaml\"]"
}
```

Manifests are updated atomically whenever a document is created or deleted:
- **Create** → append the new doc ID to the manifest's `ids` array
- **Delete** → remove the doc ID from the manifest's `ids` array
- **Clear all** (DLQ only) → set `ids` to `[]`

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
| `GET` | `/api/dlq` | List all DLQ entries |
| `GET` | `/api/dlq/count` | Count of DLQ entries |
| `GET` | `/api/dlq/{id}` | Get one entry (includes full `doc_data`) |
| `POST` | `/api/dlq/{id}/retry` | Mark entry as retried |
| `DELETE` | `/api/dlq/{id}` | Delete one entry |
| `DELETE` | `/api/dlq` | Clear all DLQ entries |

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

## Migration

On first startup with CBL, the worker auto-imports existing file-based data:

1. If no `"config"` doc exists in CBL and `config.json` is present → imports it
2. If `mappings/` directory exists → imports all `.yaml`, `.yml`, `.json` files
3. If `checkpoint.json` exists → logs the migration (checkpoint is loaded by the `Checkpoint` class)

After migration, file-based storage is no longer used. The `--config` CLI flag still works as a one-time import path.

---

## Concurrency

CBL does not support concurrent access from multiple processes. The worker and admin UI share a volume but write to **different doc ID prefixes**:

- **Worker writes:** `checkpoint:*`, `dlq:*`, `manifest:dlq`
- **Admin UI writes:** `config`, `mapping:*`, `manifest:mappings`

Since they never write to the same document, conflicts are avoided in practice.
