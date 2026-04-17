# Couchbase Lite CE – Persistent Storage Plan

Replace all file-based storage (`config.json`, `checkpoint.json`, `mappings/*.yaml`, `failed_docs.jsonl`) with **Couchbase Lite Community Edition 3.2.1** as the single embedded data store. CBL is chip-set agnostic (x86_64 / arm64), runs entirely in-process, and requires no external server.

Reference implementation: [Fujio-Turner/image_to_lucid](https://github.com/Fujio-Turner/image_to_lucid/blob/main/app.py)

---

## What Changes

| Data | Before (file-based) | After (CBL) |
|---|---|---|
| Config | `config.json` on disk | CBL doc `"config"` |
| Checkpoint fallback | `checkpoint.json` on disk | CBL doc `"checkpoint:{uuid}"` |
| Schema mappings | `mappings/*.yaml` files | CBL docs `"mapping:{name}"` |
| Dead letter queue | `failed_docs.jsonl` (append-only) | CBL docs `"dlq:{doc_id}:{timestamp}"` |
| Web API | reads/writes files | reads/writes CBL docs |
| Dockerfile | slim, no native deps | adds CBL-C 3.2.1 + CFFI build |

What **stays the same:**
- The `_changes` feed polling loop, retry logic, output forwarding
- The `rest/` and `db/` output modules
- The web UI HTML/JS (only the API backend changes)
- Checkpoint on SG `_local/` docs remains **primary** — CBL is the local fallback
- The `--config` CLI flag works as a one-time import into CBL on first run

---

## CBL Database Layout

One database: `changes_worker_db`, stored at `/app/data/changes_worker_db.cblite2/`.

Data is organized into **scoped collections** under the `changes-worker` scope. The Python CBL bindings don't expose the collections API, so `cbl_store.py` calls the raw CFFI functions (`lib.CBLDatabase_CreateCollection`, `lib.CBLCollection_SaveDocument`, etc.) directly.

### Scope & Collection Schema

```
┌──────────────────────────────────────────────────────────────┐
│  CBL Database: changes_worker_db                             │
│                                                              │
│  Scope: changes-worker                                       │
│  ├── Collection: config                                      │
│  │   └── doc_id: "config"                                    │
│  │       ├── type: "config"                                  │
│  │       └── data: "{...}"     ← full config JSON as string  │
│  │                                                           │
│  ├── Collection: checkpoints                                 │
│  │   ├── doc_id: "checkpoint:{uuid}"                         │
│  │   │   ├── type: "checkpoint"                              │
│  │   │   ├── client_id: "changes_worker"                     │
│  │   │   ├── SGs_Seq: "1500"                                 │
│  │   │   ├── time: 1768521600                                │
│  │   │   └── remote: 42                                      │
│  │   └── doc_id: "manifest:checkpoints"                      │
│  │       ├── type: "manifest"                                │
│  │       └── ids: "[...]"                                    │
│  │                                                           │
│  ├── Collection: mappings                                    │
│  │   ├── doc_id: "mapping:order.yaml"                        │
│  │   │   ├── type: "mapping"                                 │
│  │   │   ├── name: "order.yaml"                              │
│  │   │   └── content: "source:\n  match:..."                 │
│  │   └── doc_id: "manifest:mappings"                         │
│  │       ├── type: "manifest"                                │
│  │       └── ids: "[\"mapping:order.yaml\",...]"             │
│  │                                                           │
│  └── Collection: dlq                                         │
│      ├── doc_id: "dlq:order::12345:1768521600"               │
│      │   ├── type: "dlq"                                     │
│      │   ├── doc_id_original: "order::12345"                 │
│      │   ├── seq: "42"                                       │
│      │   ├── method: "PUT"                                   │
│      │   ├── status: 500                                     │
│      │   ├── error: "Internal Server Error"                  │
│      │   ├── time: 1768521600                                │
│      │   ├── retried: false                                  │
│      │   └── doc_data: "{...}"                               │
│      └── doc_id: "manifest:dlq"                              │
│          ├── type: "manifest"                                │
│          └── ids: "[\"dlq:order::12345:1768521600\",...]"    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Why Manifests?

CBL CE Python bindings don't expose N1QL queries. To list all documents within a collection, we maintain manifest documents that track known doc IDs per type. The manifest lives in the same collection as the documents it indexes (e.g., `manifest:mappings` lives in the `mappings` collection). The manifest is updated atomically whenever a doc is created or deleted.

### Value Storage Rules

Following the `image_to_lucid` pattern:

- **Scalars** (`str`, `int`, `float`, `bool`) → stored natively in CBL fields
- **Dicts / lists** → stored as `json.dumps()` strings
- **YAML content** → stored as raw string in the `content` field

```python
# Storing config
doc = MutableDocument("config")
doc["type"] = "config"
doc["data"] = json.dumps(cfg)   # full config dict as JSON string
db.saveDocument(doc)

# Reading config
doc = db.getDocument("config")
cfg = json.loads(doc.properties.get("data"))
```

---

## Module: `cbl_store.py`

A single module at the project root that wraps all CBL operations. Every other module imports from here.

```python
# cbl_store.py — Couchbase Lite CE storage layer

import json, os, time, logging

try:
    from CouchbaseLite.Database import Database, DatabaseConfiguration
    from CouchbaseLite.Document import MutableDocument
    from CouchbaseLite._PyCBL import ffi, lib
    from CouchbaseLite.common import stringParam, sliceToString, gError as _cbl_gError
    USE_CBL = True
except ImportError:
    USE_CBL = False

CBL_DB_DIR  = os.environ.get("CBL_DB_DIR", "/app/data")
CBL_DB_NAME = os.environ.get("CBL_DB_NAME", "changes_worker_db")
CBL_SCOPE   = "changes-worker"
COLL_CONFIG      = "config"
COLL_CHECKPOINTS = "checkpoints"
COLL_MAPPINGS    = "mappings"
COLL_DLQ         = "dlq"
```

### Public API

```python
class CBLStore:
    """High-level API for all CBL storage operations."""

    def __init__(self):
        self.db = get_db()

    # ── Config ────────────────────────────────────────────────
    def load_config(self) -> dict:
    def save_config(self, cfg: dict) -> None:
    def import_config_file(self, path: str) -> dict:

    # ── Checkpoints ───────────────────────────────────────────
    def load_checkpoint(self, uuid: str) -> dict | None:
    def save_checkpoint(self, uuid: str, seq: str, client_id: str,
                        remote: int) -> None:

    # ── Schema Mappings ───────────────────────────────────────
    def list_mappings(self) -> list[dict]:
    def get_mapping(self, name: str) -> str | None:
    def save_mapping(self, name: str, content: str) -> None:
    def delete_mapping(self, name: str) -> None:

    # ── Dead Letter Queue ─────────────────────────────────────
    def add_dlq_entry(self, doc_id: str, seq: str, method: str,
                      status: int, error: str, doc: dict) -> None:
    def list_dlq(self) -> list[dict]:
    def get_dlq_entry(self, dlq_id: str) -> dict | None:
    def mark_dlq_retried(self, dlq_id: str) -> None:
    def delete_dlq_entry(self, dlq_id: str) -> None:
    def clear_dlq(self) -> None:
    def dlq_count(self) -> int:

    # ── Maintenance ───────────────────────────────────────────
    def compact(self) -> bool:
    def reindex(self) -> bool:
    def integrity_check(self) -> bool:
    def optimize(self) -> bool:
    def full_optimize(self) -> bool:
    def run_all_maintenance(self) -> dict[str, bool]:
```

---

## Implementation Steps

### Step 1: Dockerfile — Add CBL-C + Python Bindings

Update the `Dockerfile` with the arch-agnostic CBL build from `image_to_lucid`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps for CBL-C and CFFI
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget gcc libffi-dev git ca-certificates zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# Download and install Couchbase Lite C CE 3.2.1 (arch-agnostic)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then CBL_ARCH="x86_64"; CBL_LIB="x86_64-linux-gnu"; \
    else CBL_ARCH="arm64"; CBL_LIB="aarch64-linux-gnu"; fi && \
    wget -q "https://packages.couchbase.com/releases/couchbase-lite-c/3.2.1/couchbase-lite-c-community-3.2.1-linux-${CBL_ARCH}.tar.gz" \
        -O /tmp/cblite.tar.gz && \
    mkdir -p /opt/cblite && \
    tar xzf /tmp/cblite.tar.gz -C /opt/cblite --strip-components=1 && \
    cp /opt/cblite/lib/${CBL_LIB}/libcblite.so* /usr/local/lib/ && \
    cp -r /opt/cblite/include/* /usr/local/include/ && \
    ldconfig && \
    rm -rf /tmp/cblite.tar.gz /opt/cblite

# Build CBL Python bindings (CFFI)
RUN pip install --no-cache-dir cffi setuptools && \
    git clone --depth 1 https://github.com/couchbaselabs/couchbase-lite-python.git /opt/cbl-python && \
    cd /opt/cbl-python/CouchbaseLite && \
    python3 ../build.py --include /usr/local/include --library /usr/local/lib/libcblite.so

ENV PYTHONPATH="/opt/cbl-python:${PYTHONPATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

ENTRYPOINT ["python", "main.py"]
CMD ["--config", "config.json"]
```

**Files changed:** `Dockerfile`
**Test:** `docker build -t changes-worker-cbl .` on both x86_64 and arm64

---

### Step 2: Create `cbl_store.py` — Core Storage Module

Create the `CBLStore` class with all CRUD operations. Follow the `image_to_lucid` patterns:

- `try/except ImportError` → `USE_CBL` flag
- Module-level singleton `_db` opened once (not per-request like `image_to_lucid`)
- Upsert: `getMutableDocument(id) or MutableDocument(id)`
- Complex values: `json.dumps()` / `json.loads()`
- Manifest docs for listing (`manifest:mappings`, `manifest:dlq`)

```python
# Upsert pattern (using collection helpers)
def save_config(self, cfg: dict) -> None:
    doc = _coll_get_mutable_doc(self.db, COLL_CONFIG, "config")
    if not doc:
        doc = MutableDocument("config")
    doc["type"] = "config"
    doc["data"] = json.dumps(cfg)
    _coll_save_doc(self.db, COLL_CONFIG, doc)
```

**Files changed:** new `cbl_store.py`
**Test:** unit test that creates a temp DB, writes/reads config, checkpoint, mappings, DLQ entries

---

### Step 3: Config — Replace `config.json` File I/O

Update `main.py`:

| Before | After |
|---|---|
| `load_config(path)` reads `config.json` | `store.load_config()` reads from CBL |
| `--config config.json` is required | `--config config.json` does a **one-time import** into CBL, then CBL is used going forward |
| Config edits require restarting + remounting file | Web UI saves directly to CBL; worker re-reads on next poll cycle |

**Backward compatibility:** If CBL has no `"config"` doc and `--config` is provided, import the file into CBL automatically. If `USE_CBL=False`, fall back to file I/O (same behavior as today).

```python
def load_config(path: str | None = None) -> dict:
    if USE_CBL:
        store = CBLStore()
        cfg = store.load_config()
        if cfg:
            return cfg
        # First run: import from file
        if path:
            with open(path) as f:
                cfg = json.load(f)
            store.save_config(cfg)
            return cfg
    # Fallback: read from file directly
    with open(path or "config.json") as f:
        return json.load(f)
```

**Files changed:** `main.py` (`load_config`, `main`)
**Test:** start worker with `--config config.json` → verify CBL doc created → restart without `--config` → verify it loads from CBL

---

### Step 4: Checkpoint — Replace `checkpoint.json` Fallback

Update the `Checkpoint` class in `main.py`:

| Before | After |
|---|---|
| `_load_fallback()` reads `checkpoint.json` | `store.load_checkpoint(uuid)` reads from CBL |
| `_save_fallback(seq)` writes `checkpoint.json` | `store.save_checkpoint(uuid, seq, ...)` writes to CBL |
| File can be lost on container restart without volume | CBL data directory is a single Docker volume mount |

The **primary** checkpoint path (SG `_local/` doc) is unchanged. CBL replaces only the local fallback.

```python
def _load_fallback(self) -> str:
    if USE_CBL:
        data = CBLStore().load_checkpoint(self._uuid)
        if data:
            seq = data.get("SGs_Seq", "0")
            ic("checkpoint loaded from CBL", seq)
            return seq
        return "0"
    # Original file fallback
    if self._fallback_path.exists():
        ...

def _save_fallback(self, seq: str) -> None:
    if USE_CBL:
        CBLStore().save_checkpoint(self._uuid, seq, self._client_id, self._internal)
        ic("checkpoint saved to CBL", seq)
        return
    # Original file fallback
    self._fallback_path.write_text(...)
```

**Files changed:** `main.py` (`Checkpoint._load_fallback`, `Checkpoint._save_fallback`)
**Test:** block SG checkpoint save → verify fallback writes to CBL → restart → verify it loads from CBL

---

### Step 5: Schema Mappings — Replace `mappings/` Files

Update `web/server.py` API handlers:

| Before | After |
|---|---|
| `list_mappings()` scans `mappings/` directory | `store.list_mappings()` reads from CBL manifest |
| `get_mapping(name)` reads a file | `store.get_mapping(name)` reads from CBL |
| `put_mapping(name)` writes a file | `store.save_mapping(name, content)` writes to CBL |
| `delete_mapping(name)` deletes a file | `store.delete_mapping(name)` purges from CBL |

```python
async def list_mappings(request):
    if USE_CBL:
        store = CBLStore()
        return json_response(store.list_mappings())
    # File fallback
    MAPPINGS_DIR.mkdir(exist_ok=True)
    ...

async def put_mapping(request):
    name = request.match_info["name"]
    content = await request.text()
    if USE_CBL:
        CBLStore().save_mapping(name, content)
        return json_response({"ok": True})
    # File fallback
    ...
```

**Files changed:** `web/server.py`
**Test:** create mapping via web UI → verify CBL doc → list mappings → delete → verify gone

---

### Step 6: Dead Letter Queue — Replace `failed_docs.jsonl`

Update `rest/output_http.py` `DeadLetterQueue` class:

| Before | After |
|---|---|
| Append-only JSONL file | CBL doc per failed doc: `"dlq:{doc_id}:{timestamp}"` |
| Manual replay via `jq` + `curl` | Web UI shows DLQ entries, allows retry/delete |
| Lost on container restart without volume | Persisted in CBL database |
| No way to mark entries as retried | `retried` field on each DLQ doc |
| `wc -l` to count entries | `store.dlq_count()` |

```python
class DeadLetterQueue:
    def __init__(self, path: str):
        self._use_cbl = USE_CBL
        self._store = CBLStore() if self._use_cbl else None
        self._path = Path(path) if path and not self._use_cbl else None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._use_cbl or self._path is not None

    async def write(self, doc: dict, result: dict, seq: str | int) -> None:
        if self._use_cbl:
            self._store.add_dlq_entry(
                doc_id=result.get("doc_id", "unknown"),
                seq=str(seq),
                method=result.get("method", "PUT"),
                status=result.get("status", 0),
                error=result.get("error", ""),
                doc=doc,
            )
            return
        # Original file fallback
        ...
```

New Web API endpoints in `web/server.py`:

```
GET    /api/dlq           → list all DLQ entries
GET    /api/dlq/{id}      → get one entry
POST   /api/dlq/{id}/retry → mark as retried (future: actually re-send)
DELETE /api/dlq/{id}      → delete one entry
DELETE /api/dlq           → clear all DLQ entries
GET    /api/dlq/count     → count of entries
```

New Web UI page: add a "Dead Letters" tab to the dashboard or a dedicated page showing a DaisyUI table of failed docs with retry/delete buttons.

**Files changed:** `rest/output_http.py` (`DeadLetterQueue`), `web/server.py` (new endpoints), `web/templates/index.html` (DLQ section)
**Test:** set `halt_on_failure=false`, send to unreachable endpoint → verify DLQ entries in CBL → view in web UI → delete one → clear all

---

### Step 7: Web API — Update `web/server.py`

Replace all file I/O in the API handlers with `CBLStore` calls:

```python
from cbl_store import CBLStore, USE_CBL

# Config API
async def get_config(request):
    if USE_CBL:
        return json_response(CBLStore().load_config() or {})
    # file fallback ...

async def put_config(request):
    body = await request.json()
    if USE_CBL:
        CBLStore().save_config(body)
        return json_response({"ok": True})
    # file fallback ...
```

**Files changed:** `web/server.py`

---

### Step 8: Docker Compose — Volume for CBL Data

```yaml
services:
  changes-worker:
    build: .
    ports:
      - "9090:9090"
    volumes:
      - cbl-data:/app/data           # CBL database persists here
    restart: unless-stopped

  admin-ui:
    build: .
    entrypoint: ["python", "web/server.py"]
    command: ["--port", "8080"]
    ports:
      - "8080:8080"
    volumes:
      - cbl-data:/app/data           # same CBL database
    restart: unless-stopped

volumes:
  cbl-data:
```

**Important:** Both services share the same volume. However, **CBL does not support concurrent access from multiple processes**. Options:

1. **Recommended:** The admin-ui reads/writes CBL, the worker reads CBL. Only one writer at a time. Since the worker only writes checkpoints/DLQ and the admin-ui only writes config/mappings, conflicts are unlikely (different doc IDs).
2. **Alternative:** The admin-ui writes config/mappings to CBL, then signals the worker to reload (e.g., via a flag file or HTTP endpoint on the worker).
3. **Alternative:** Run the web server inside the worker process (single process, single CBL handle).

---

## Storage Model — Who Owns What

CBL is the **runtime source of truth** for all data. Files on disk serve specific
roles depending on the data type:

| Data | File on disk | Role of file | Role of CBL | Sync direction |
|---|---|---|---|---|
| **Config** | `config.json` | Seed (read once on first start) | Runtime source of truth | File → CBL (once) |
| **Mappings** | `mappings/*.json` | Edit surface (bind-mounted) | Runtime store (worker reads from here) | File → CBL (every startup) |
| **Checkpoints** | `checkpoint.json` | Legacy migration only | Runtime source of truth | File → CBL (once) |
| **Dead Letters** | — | Not on disk | CBL only | — |

### Config: Seed-Only

`config.json` is read **only on the very first startup** (when CBL has no config).
After that, CBL owns the config and `config.json` is ignored — even if it changes.

```
First start:   config.json ──→ CBL   (seed)
Every start:   CBL ──→ worker         (config.json ignored)
Admin UI:      edits go to CBL only
```

**To re-seed config from `config.json`:** delete the CBL volume and restart.

### Mappings: Disk Wins

The `mappings/` directory is **bind-mounted** from the host into the container.
It is the edit surface — you can edit files directly or use the Admin UI.
On every startup, the contents of `mappings/` are synced **into** CBL:

- New files on disk → imported into CBL
- Changed files on disk → updated in CBL
- Files deleted from disk → removed from CBL

```
Every start:   mappings/*.json ──→ CBL   (disk always wins)
Worker:        reads from CBL at runtime
Admin UI:      saves to CBL + writes to mappings/ (both stay in sync)
```

The worker loads mappings from CBL at runtime. If CBL is unavailable, it falls
back to reading the `mappings/` directory directly.

### Duplicate Mapping Detection

If two mapping files match the same source filter (e.g., both match
`type=order`), the worker logs a warning at startup:

```
WARNING: DUPLICATE MAPPING: 'orders.json' and 'order.json' both match
type=order — only 'orders.json' will be used (first-match wins)
```

Delete the stale duplicate from `mappings/` and restart.

---

## Startup Sync Sequence

On every startup with `USE_CBL=True`, `migrate_files_to_cbl()` runs **after**
logging is configured so all sync activity is visible in the logs:

```
1. Config sync:
   - CBL has config?  → use it, ignore config.json
   - CBL empty?       → seed from config.json → CBL

2. Mappings sync (disk → CBL):
   - For each *.json in mappings/:
     - Not in CBL?        → import
     - In CBL but differs? → update CBL from disk
   - For each mapping in CBL:
     - Not on disk?       → delete from CBL
   - Log summary: "mappings sync complete: 2 on disk, 0 added, 1 updated, 1 removed"

3. Checkpoint migration (one-time):
   - checkpoint.json exists? → import into CBL
```

---

## Troubleshooting

### Problem: Stale config or mappings after editing files

**Symptom:** You edited `config.json` or a mapping file, but the worker
uses the old version.

**Cause:** For config, CBL already has a copy and ignores `config.json`.
For mappings, the CBL volume may have cached old data from a previous
sync cycle.

**Fix:**

```bash
# Stop containers
docker compose down

# Delete the CBL volume (destroys all CBL data — config, mappings, checkpoints)
docker volume rm change_stream_db_cbl-data

# Rebuild and start (CBL will re-seed from disk files)
docker compose up --build
```

> ⚠️ This resets checkpoints too. The worker will re-process changes from
> the last Sync Gateway checkpoint (stored on SG, not in CBL).

### Problem: Duplicate mapping warning

**Symptom:** Log shows `DUPLICATE MAPPING: 'a.json' and 'b.json' both match ...`

**Cause:** Two mapping files have the same `source.match` filter. Only the
first one (alphabetical order) is used.

**Fix:** Delete the stale file from `mappings/` and restart:

```bash
rm mappings/old_duplicate.json
docker compose restart changes-worker
```

### Problem: Config changes from Admin UI not reflected

**Symptom:** You changed config in the Admin UI but the worker uses old settings.

**Fix:** The Admin UI sends a restart signal to the worker automatically.
If that fails (e.g., network issue between containers), restart manually:

```bash
docker compose restart changes-worker
```

### Problem: Need to start completely fresh

```bash
docker compose down
docker volume rm change_stream_db_cbl-data
# Optionally edit config.json and mappings/ to your desired state
docker compose up --build
```

---

## Backward Compatibility

The `USE_CBL` flag means the entire system still works without CBL installed:

```python
if USE_CBL:
    # CBL path
else:
    # Original file-based path (unchanged)
```

This is critical for local development on macOS where building CBL-C may not be convenient.

---

## File Summary

| File | Action | Description |
|---|---|---|
| `Dockerfile` | **Modify** | Add CBL-C download, CFFI build, `PYTHONPATH` |
| `cbl_store.py` | **Create** | CBLStore class with all CRUD ops |
| `main.py` | **Modify** | `load_config` → CBL, `Checkpoint` fallback → CBL |
| `rest/output_http.py` | **Modify** | `DeadLetterQueue` → CBL |
| `web/server.py` | **Modify** | API handlers → CBL for config, mappings, DLQ |
| `web/templates/index.html` | **Modify** | Add DLQ section to dashboard |
| `docker-compose.yml` | **Modify** | Shared `cbl-data` volume |
| `docs/CBL_STORE.md` | **Create** | This document |

---

## Execution Order

```
Step 1: Dockerfile          ← do first, everything depends on CBL being available
Step 2: cbl_store.py        ← do second, all other steps import from here
Step 3: Config              ← smallest change, easy to test
Step 4: Checkpoint          ← next smallest, only the fallback path changes
Step 5: Schema Mappings     ← web API changes, easy to test via UI
Step 6: Dead Letter Queue   ← biggest change, new API endpoints + UI
Step 7: Web API             ← already done if steps 3-6 modify server.py
Step 8: Docker Compose      ← last, wire up the shared volume
```

Each step is independently testable. If any step fails, the `USE_CBL=False` fallback keeps the system running on files.
