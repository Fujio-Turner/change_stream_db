# RDBMS Output – Design Plan

This document outlines the design for forwarding Couchbase `_changes` feed documents into relational databases (PostgreSQL, MySQL, MS SQL, Oracle) as an alternative output mode alongside the existing REST/HTTP output.

**PostgreSQL is fully implemented** and serves as the reference implementation. MySQL, MSSQL, and Oracle are placeholders awaiting implementation — use `db_postgres.py` as the template.

**Related docs:**
- [`RDBMS_IMPLEMENTATION.md`](RDBMS_IMPLEMENTATION.md) -- Implementation guide: single-table vs. multi-table writes, transactions, insert-vs-update strategy
- [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) -- JSON-to-relational mapping definition format, transforms, JSONPath syntax
- [`ADMIN_UI.md`](ADMIN_UI.md) -- Config editor UI with DB output fields, Schema Mappings visual editor

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
└──────────────────────┘         └──────────────────┘         │  PostgreSQL ✅      │
                                                              │  MySQL / MSSQL /    │
                                                              │  Oracle  ⬜         │
                                                              └─────────────────────┘
```

---

## Architecture

### Module Layout

```
db/
├── __init__.py           # Factory function (create_db_output)
├── db_postgres.py        # PostgreSQL output (asyncpg)  ✅ implemented
├── db_mysql.py           # MySQL output (placeholder)
├── db_mssql.py           # MS SQL Server output (placeholder)
└── db_oracle.py          # Oracle output (placeholder)
```

Each `db_*.py` module implements a common interface so the changes_worker can swap output targets via config without changing the core loop. PostgreSQL is the first complete implementation and serves as the template for the other engines.

### Common Interface

The PostgreSQL implementation (`PostgresOutputForwarder` in `db/db_postgres.py`) defines the interface that all RDBMS modules should follow:

```python
class PostgresOutputForwarder:
    """Async PostgreSQL output forwarder."""

    def __init__(self, out_cfg: dict, dry_run: bool = False, metrics=None):
        """
        Args:
            out_cfg:  The full output config dict (contains 'postgres' key, 'mapping_file', etc.)
            dry_run:  If True, log SQL statements but don't execute.
            metrics:  MetricsCollector instance (optional).
        """

    async def connect(self) -> None:
        """Create the connection pool and load the schema mapping."""

    async def send(self, doc: dict, method: str = "PUT") -> dict:
        """
        Write a single document to the database.

        - method="PUT"    → UPSERT (insert or update)
        - method="DELETE" → DELETE the row(s)

        Returns: {"ok": bool, "doc_id": str, ...}
        """

    async def test_reachable(self) -> bool:
        """Verify the database is reachable (used by --test)."""

    async def close(self) -> None:
        """Close the connection pool."""

    def log_stats(self) -> None:
        """Log accumulated write statistics."""
```

This mirrors the `OutputForwarder` interface in `rest/output_http.py` so the changes_worker main loop doesn't need to know which output type is active.

> **Note:** There is no `send_batch()` method yet. Each document is processed individually within its own transaction. Batch support can be added later as an optimization.

---

## Config Changes

Each RDBMS engine gets its own `output.mode` value (`"postgres"`, `"mysql"`, `"mssql"`, `"oracle"`) with a corresponding config key. The `mapping_file` points to a JSON mapping definition that controls how documents are mapped to SQL operations.

```jsonc
{
  "output": {
    "mode": "postgres",                    // "stdout" | "http" | "postgres" | "mysql" | "mssql" | "oracle"
    "postgres": {
      "host": "localhost",
      "port": 5432,
      "database": "mydb",
      "user": "postgres",
      "password": "secret",
      "schema": "public",
      "ssl": false,
      "pool_min": 2,
      "pool_max": 10
    },
    "mapping_file": "mappings/orders.json",
    "halt_on_failure": true
  }
}
```

> **Admin UI:** The config editor at `/config` provides a form-based interface for all DB settings. When a DB engine is selected as the output mode, the UI dynamically shows the connection and pool fields. See [`ADMIN_UI.md`](ADMIN_UI.md#db-output-fields) for details.

### Schema Mapping

Document-to-table mapping is defined in an external JSON file (referenced by `mapping_file`). The mapping supports JSONPath field extraction, transforms, and multi-table writes. See [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) for the full format.

The old `jsonb` / `columns` mapping modes from the original plan have been replaced by the mapping file approach, which is more flexible and supports multi-table writes with foreign key relationships.

---

## Engine-Specific Notes

### PostgreSQL (`db_postgres.py`) — ✅ Implemented

- **Library:** `asyncpg` (async, fast, native PostgreSQL protocol)
- **Features:**
  - Async connection pool (`pool_min` / `pool_max`)
  - Transactional multi-table writes (all ops for a doc in one transaction)
  - `dry_run` mode — logs SQL without executing
  - `introspect_tables()` — queries `information_schema` to import table/column/PK/FK metadata for the Schema UI
- **Upsert:** `INSERT ... ON CONFLICT DO UPDATE`
- **Default port:** `5432`

### MySQL (`db_mysql.py`) — ⬜ Planned

- **Library:** `aiomysql` (async wrapper around PyMySQL)
- **JSON column type:** `JSON` (MySQL 5.7+)
- **Upsert:** `INSERT ... ON DUPLICATE KEY UPDATE`
- **Batch support:** `executemany()`
- **Default port:** `3306`

### MS SQL Server (`db_mssql.py`) — ⬜ Planned

- **Library:** `aioodbc` (async ODBC) or `pymssql` (FreeTDS-based)
- **JSON column type:** `NVARCHAR(MAX)` with `ISJSON()` check constraint
- **Upsert:** `MERGE ... WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT`
- **Batch support:** Table-valued parameters or bulk insert
- **Default port:** `1433`
- **Note:** MS SQL has no native JSON column type. JSON is stored as `NVARCHAR(MAX)` and queried with `JSON_VALUE()` / `OPENJSON()`.

### Oracle (`db_oracle.py`) — ⬜ Planned

- **Library:** `oracledb` (official Oracle async driver, formerly cx_Oracle)
- **JSON column type:** `JSON` (Oracle 21c+) or `CLOB` (older versions)
- **Upsert:** `MERGE INTO ... USING ... WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT`
- **Batch support:** `executemany()` with bind arrays
- **Default port:** `1521`

---

## How It Plugs Into changes_worker

The main loop in `poll_changes()` currently creates an `OutputForwarder` and calls `output.send(doc, method)` for each change. The RDBMS output follows the same pattern:

```python
# In poll_changes():
if out_cfg.get("mode") == "postgres":
    from db.db_postgres import PostgresOutputForwarder
    output = PostgresOutputForwarder(out_cfg, dry_run, metrics=metrics)
    await output.connect()
elif out_cfg.get("mode") == "http":
    output = OutputForwarder(session, out_cfg, ...)
```

The rest of the loop (`process_one()`, checkpoint logic, DLQ, `halt_on_failure`) stays unchanged — it just calls `output.send(doc, method)` regardless of the output type.

### Factory Function

`db/__init__.py` exposes a factory for creating the right output forwarder based on the configured mode:

```python
def create_db_output(out_cfg: dict, dry_run: bool = False, metrics=None):
    mode = out_cfg.get("mode", "")
    if mode == "postgres":
        from .db_postgres import PostgresOutputForwarder
        return PostgresOutputForwarder(out_cfg, dry_run, metrics=metrics)
    elif mode == "mysql":
        from .db_mysql import MySQLOutputForwarder
        return MySQLOutputForwarder(out_cfg, dry_run, metrics=metrics)
    elif mode == "mssql":
        from .db_mssql import MSSQLOutputForwarder
        return MSSQLOutputForwarder(out_cfg, dry_run, metrics=metrics)
    elif mode == "oracle":
        from .db_oracle import OracleOutputForwarder
        return OracleOutputForwarder(out_cfg, dry_run, metrics=metrics)
    else:
        raise ValueError(f"Unknown db output mode: {mode}")
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

Each `send()` call wraps all SQL operations for that document in a single transaction. For multi-table mappings, this means all inserts/updates across tables succeed or fail atomically.

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

`asyncpg` is listed in `requirements.txt` (commented, install when needed):

```txt
# RDBMS output drivers (install the one you need):
#   pip install asyncpg    # for PostgreSQL output  ✅ in requirements.txt
#   pip install aiomysql   # for MySQL output
#   pip install aioodbc    # for MS SQL Server (requires unixODBC + ODBC driver)
#   pip install oracledb   # for Oracle
```

Startup validation checks that the required driver is installed. `PostgresOutputForwarder.__init__()` raises `RuntimeError` if `asyncpg` is not importable.

---

## Implementation Order

- ✅ `schema/mapper.py` — JSON→SQL mapper with JSONPath, transforms, multi-table
- ✅ `schema/validator.py` — Mapping validation
- ✅ `db/db_postgres.py` — PostgreSQL output with asyncpg, connection pool, transactions
- ✅ Schema UI — DB introspection, DDL import, auto-detect drivers
- ⬜ `db/db_mysql.py` — MySQL output forwarder
- ⬜ `db/db_mssql.py` — MS SQL Server output forwarder
- ⬜ `db/db_oracle.py` — Oracle output forwarder
- ⬜ Integration into `main.py` — routing, config validation, `--test` support

---

## Adding a New RDBMS Engine

Use `db/db_postgres.py` as the starting point for implementing MySQL, MSSQL, or Oracle. The steps are:

### 1. Copy the forwarder class

Copy `db_postgres.py` to `db_<engine>.py` and rename the class (e.g., `MySQLOutputForwarder`). Update the constructor to read from the engine-specific config key (e.g., `out_cfg.get("mysql", {})`).

### 2. Replace the async driver

Swap `asyncpg` for the engine's async driver:

| Engine | Driver | Pool API |
|---|---|---|
| MySQL | `aiomysql` | `aiomysql.create_pool(...)` |
| MSSQL | `aioodbc` | `aioodbc.create_pool(dsn=...)` |
| Oracle | `oracledb` | `oracledb.create_pool_async(...)` |

Update `connect()`, `close()`, `send()`, and `test_reachable()` to use the new driver's API.

### 3. Engine-specific SQL generation

The `SqlOperation.to_sql()` method in `schema/mapper.py` generates PostgreSQL-flavored SQL (e.g., `INSERT ... ON CONFLICT DO UPDATE`). Each engine needs its own upsert syntax:

| Engine | Upsert Syntax |
|---|---|
| PostgreSQL | `INSERT ... ON CONFLICT (pk) DO UPDATE SET ...` |
| MySQL | `INSERT ... ON DUPLICATE KEY UPDATE ...` |
| MSSQL | `MERGE ... WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT` |
| Oracle | `MERGE INTO ... USING DUAL WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT` |

Either add an `engine` parameter to `to_sql()` or override SQL generation in the engine-specific forwarder.

### 4. Add the driver to `requirements.txt`

Add a commented entry following the existing pattern:

```txt
#   pip install <driver>   # for <Engine> output
```

### 5. Add driver detection in `web/server.py`

Update `_detect_db_drivers()` so the Admin UI can show which drivers are installed. Add an import check for the new driver.

### 6. Add introspection queries

Implement `introspect_tables()` for the engine. Each database has its own system catalog:

| Engine | Catalog |
|---|---|
| PostgreSQL | `information_schema.tables`, `information_schema.columns` |
| MySQL | `information_schema.tables`, `information_schema.columns` |
| MSSQL | `INFORMATION_SCHEMA.TABLES`, `sys.columns` |
| Oracle | `ALL_TABLES`, `ALL_TAB_COLUMNS` |

### 7. Register in `create_db_output()`

Add the new engine to the factory function in `db/__init__.py`.

---

## Open Questions

- **Batch size for DB writes:** Should `get_batch_number` also control DB batch size, or add a separate `db.batch_size` setting?
- **Mixed output:** Should the worker support sending to both HTTP and DB simultaneously? Recommendation: not in v1 — one output mode at a time. Multiple outputs can be handled by running multiple worker instances off the same `_changes` feed (each with its own checkpoint `client_id`).

### Resolved

- **Schema auto-creation:** We do **not** auto-create tables. The user creates tables in their database, then uses "Import from Database" or "Upload DDL" in the Schema UI to import the schema into a mapping file.
- **Connection pooling lifecycle:** Pool is created once at startup via `connect()`. The pool handles reconnection on failure internally (`asyncpg` manages this).
- **Table definitions storage:** Table DDL definitions have moved to a standalone `tables_rdbms` CBL collection (reusable library). Tables are copied into jobs on selection — the job owns its copy. The `outputs_rdbms.src[].tables[]` field remains for backward compatibility. See [`SCHEMA_MAPPING_IN_JOBS.md`](SCHEMA_MAPPING_IN_JOBS.md#rdbms-table-definitions-new-tables_rdbms-collection) for the full design and [`cbl_store.py`](../cbl_store.py) for the implementation.
