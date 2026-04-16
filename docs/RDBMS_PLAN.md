# RDBMS Output – Design Plan

This document outlines the plan for forwarding Couchbase `_changes` feed documents into relational databases (MS SQL, PostgreSQL, Oracle, MySQL) as an alternative output mode alongside the existing REST/HTTP output.

---

## Goal

The changes_worker already consumes the `_changes` feed from Sync Gateway / App Services / Edge Server and forwards each document to a REST endpoint (`rest/` module) or stdout. The goal is to add **RDBMS output** so the same feed can write directly into a relational database table — no intermediate REST service required.

```
┌──────────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│  Sync Gateway /      │         │                  │         │  REST endpoint      │
│  App Services /      │ ──GET── │  changes_worker  │ ──PUT── │  (rest/ module)     │
│  Edge Server         │ _changes│                  │         └─────────────────────┘
│                      │ ◄─JSON─ │                  │
│  /{db}.{scope}.      │         │                  │         ┌─────────────────────┐
│   {collection}/      │         │                  │ ──SQL── │  RDBMS              │
│   _changes           │         │                  │         │  (db/ module)       │
└──────────────────────┘         └──────────────────┘         │  PostgreSQL / MySQL │
                                                              │  MS SQL / Oracle    │
                                                              └─────────────────────┘
```

---

## Architecture

### Module Layout

```
db/
├── __init__.py           # Common base class + factory function
├── db_postgres.py        # PostgreSQL output (asyncpg)
├── db_mysql.py           # MySQL output (aiomysql)
├── db_mssql.py           # MS SQL Server output (aioodbc or pymssql)
└── db_oracle.py          # Oracle output (oracledb)
```

Each `db_*.py` module implements a common interface so the changes_worker can swap output targets via config without changing the core loop.

### Common Interface

Every RDBMS module will implement the same base class:

```python
class DBOutputBase:
    """Base class for all RDBMS output modules."""

    async def connect(self) -> None:
        """Establish the database connection / pool."""

    async def send(self, doc: dict, method: str = "PUT") -> dict:
        """
        Write a single document to the database.

        - method="PUT"    → UPSERT (insert or update)
        - method="DELETE" → DELETE the row by doc_id

        Returns: {"ok": bool, "doc_id": str, "method": str, ...}
        """

    async def send_batch(self, docs: list[dict], methods: list[str]) -> list[dict]:
        """
        Write a batch of documents in a single transaction.
        Falls back to per-doc send() if not overridden.
        """

    async def test_reachable(self) -> bool:
        """Verify the database is reachable (used by --test)."""

    async def close(self) -> None:
        """Close the connection / pool."""

    def log_stats(self) -> None:
        """Log accumulated write statistics."""
```

This mirrors the `OutputForwarder` interface in `rest/output_http.py` so the changes_worker main loop doesn't need to know which output type is active.

---

## Config Changes

A new `output.mode` value — `"db"` — selects RDBMS output. Database-specific settings go under `output.db`:

```jsonc
{
  "output": {
    "mode": "db",                        // "stdout" | "http" | "db"
    "db": {
      "engine": "postgres",              // "postgres" | "mysql" | "mssql" | "oracle"
      "host": "localhost",
      "port": 5432,                      // default per engine
      "database": "mydb",
      "username": "app_user",
      "password": "secret",
      "schema": "public",               // optional, default varies by engine
      "table": "couchbase_docs",         // target table name
      "ssl": false,                      // use SSL connection
      "pool_size": 5,                    // connection pool size
      "connect_timeout_seconds": 10,

      "mapping": {
        "mode": "jsonb",                 // "jsonb" | "columns"
        "doc_id_column": "doc_id",       // column for the Couchbase doc _id
        "rev_column": "rev",             // column for _rev (optional)
        "body_column": "body",           // column for the full JSON doc (jsonb mode)
        "timestamp_column": "updated_at" // auto-set on upsert (optional)
      }
    },

    // Existing fields still apply:
    "halt_on_failure": true,
    "dead_letter_path": "failed_docs.jsonl",
    "retry": {
      "max_retries": 3,
      "backoff_base_seconds": 1,
      "backoff_max_seconds": 30
    }
  }
}
```

### Mapping Modes

#### `jsonb` Mode (default)

Store the entire Couchbase document as a single JSON/JSONB column. Simplest approach — no schema migration needed when document fields change.

```sql
CREATE TABLE couchbase_docs (
    doc_id   TEXT PRIMARY KEY,
    rev      TEXT,
    body     JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Operations:

| Change Type | SQL |
|---|---|
| PUT (insert/update) | `INSERT INTO couchbase_docs (doc_id, rev, body, updated_at) VALUES ($1, $2, $3, NOW()) ON CONFLICT (doc_id) DO UPDATE SET rev = $2, body = $3, updated_at = NOW()` |
| DELETE | `DELETE FROM couchbase_docs WHERE doc_id = $1` |

#### `columns` Mode (future)

Map specific document fields to individual table columns. Requires the user to define the mapping and maintain the table schema.

```jsonc
"mapping": {
  "mode": "columns",
  "doc_id_column": "doc_id",
  "field_map": {
    "name": "product_name",     // doc.name → table.product_name
    "price": "unit_price",      // doc.price → table.unit_price
    "category": "category"      // doc.category → table.category
  }
}
```

This mode is more complex (schema migrations, type mismatches, missing fields) and will be implemented after `jsonb` mode is stable.

---

## Engine-Specific Notes

### PostgreSQL (`db_postgres.py`)

- **Library:** `asyncpg` (async, fast, native PostgreSQL protocol)
- **JSON column type:** `JSONB` (indexable, queryable)
- **Upsert:** `INSERT ... ON CONFLICT DO UPDATE`
- **Batch support:** `executemany()` or `COPY` for bulk loads
- **Default port:** `5432`

### MySQL (`db_mysql.py`)

- **Library:** `aiomysql` (async wrapper around PyMySQL)
- **JSON column type:** `JSON` (MySQL 5.7+)
- **Upsert:** `INSERT ... ON DUPLICATE KEY UPDATE`
- **Batch support:** `executemany()`
- **Default port:** `3306`

### MS SQL Server (`db_mssql.py`)

- **Library:** `aioodbc` (async ODBC) or `pymssql` (FreeTDS-based)
- **JSON column type:** `NVARCHAR(MAX)` with `ISJSON()` check constraint
- **Upsert:** `MERGE ... WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT`
- **Batch support:** Table-valued parameters or bulk insert
- **Default port:** `1433`
- **Note:** MS SQL has no native JSON column type. JSON is stored as `NVARCHAR(MAX)` and queried with `JSON_VALUE()` / `OPENJSON()`.

### Oracle (`db_oracle.py`)

- **Library:** `oracledb` (official Oracle async driver, formerly cx_Oracle)
- **JSON column type:** `JSON` (Oracle 21c+) or `CLOB` (older versions)
- **Upsert:** `MERGE INTO ... USING ... WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT`
- **Batch support:** `executemany()` with bind arrays
- **Default port:** `1521`

---

## How It Plugs Into changes_worker

The main loop in `poll_changes()` currently creates an `OutputForwarder` and calls `output.send(doc, method)` for each change. The RDBMS output will follow the same pattern:

```python
# In poll_changes():
if out_cfg.get("mode") == "db":
    from db import create_db_output
    output = create_db_output(out_cfg, metrics=metrics)
    await output.connect()
elif out_cfg.get("mode") == "http":
    output = OutputForwarder(session, out_cfg, ...)
```

The rest of the loop (`process_one()`, checkpoint logic, DLQ, `halt_on_failure`) stays unchanged — it just calls `output.send(doc, method)` regardless of the output type.

### Factory Function

`db/__init__.py` will expose a factory:

```python
def create_db_output(out_cfg: dict, metrics=None) -> DBOutputBase:
    engine = out_cfg.get("db", {}).get("engine", "postgres")
    if engine == "postgres":
        from .db_postgres import PostgresOutput
        return PostgresOutput(out_cfg, metrics=metrics)
    elif engine == "mysql":
        from .db_mysql import MySQLOutput
        return MySQLOutput(out_cfg, metrics=metrics)
    elif engine == "mssql":
        from .db_mssql import MSSQLOutput
        return MSSQLOutput(out_cfg, metrics=metrics)
    elif engine == "oracle":
        from .db_oracle import OracleOutput
        return OracleOutput(out_cfg, metrics=metrics)
    else:
        raise ValueError(f"Unknown db engine: {engine}")
```

---

## Failure Handling

RDBMS output follows the same failure semantics as HTTP output:

| Scenario | `halt_on_failure=true` | `halt_on_failure=false` |
|---|---|---|
| Connection lost mid-batch | Stop batch, hold checkpoint, reconnect on next cycle | Log error, write to DLQ, skip doc, continue |
| Constraint violation (e.g., FK) | Stop batch, hold checkpoint | Write to DLQ, skip doc |
| Timeout on INSERT/UPDATE | Retry with backoff, then stop or skip | Retry, then DLQ |
| Auth failure (bad credentials) | Stop batch, hold checkpoint | Same — can't recover without config change |

### Transaction Strategy

- **Per-doc commits (default):** Each `send()` call is an auto-committed upsert. Simple, consistent with the HTTP output model.
- **Batch commits (future optimization):** Wrap `send_batch()` in a single transaction. All-or-nothing per sub-batch. Faster but more complex rollback handling.

---

## Metrics

New Prometheus metrics for RDBMS output (extending the existing `MetricsCollector`):

| Metric | Type | Description |
|---|---|---|
| `changes_worker_db_upserts_total` | counter | Total UPSERT operations |
| `changes_worker_db_deletes_total` | counter | Total DELETE operations |
| `changes_worker_db_errors_total` | counter | Total DB write errors |
| `changes_worker_db_write_time_seconds` | summary | DB write latency |
| `changes_worker_db_connection_pool_size` | gauge | Current pool size |
| `changes_worker_db_connection_pool_available` | gauge | Available connections in pool |

---

## Dependencies (additions to `requirements.txt`)

```txt
# Install only the driver you need:
asyncpg          # PostgreSQL
aiomysql         # MySQL
aioodbc          # MS SQL Server (requires unixODBC + ODBC driver)
oracledb         # Oracle
```

Startup validation will check that the required driver is installed for the configured `engine`, just like the serialization library checks for `output_format`.

---

## Implementation Order

1. **`DBOutputBase`** — abstract base class in `db/__init__.py`
2. **`db_postgres.py`** — PostgreSQL first (most common target, best async driver)
3. **`db_mysql.py`** — MySQL second
4. **`db_mssql.py`** — MS SQL third
5. **`db_oracle.py`** — Oracle fourth
6. **Integration into `changes_worker.py`** — add `mode=db` routing, config validation, `--test` support
7. **`columns` mapping mode** — after `jsonb` mode is proven stable

---

## Open Questions

- **Schema auto-creation:** Should the worker auto-create the target table on first run, or require it to exist? Recommendation: require it to exist, but provide example `CREATE TABLE` statements per engine.
- **Batch size for DB writes:** Should `get_batch_number` also control DB batch size, or add a separate `db.batch_size` setting?
- **Connection pooling lifecycle:** Pool created once at startup, or reconnect on each poll cycle? Recommendation: pool at startup, with health checks.
- **Mixed output:** Should the worker support sending to both HTTP and DB simultaneously? Recommendation: not in v1 — one output mode at a time. Multiple outputs can be handled by running multiple worker instances off the same `_changes` feed (each with its own checkpoint `client_id`).
