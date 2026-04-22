# RDBMS Implementation Guide – Sending _changes to Relational Tables

This document covers the practical implementation of writing `_changes` feed documents into RDBMS tables, focusing on three key scenarios: single-table writes, multi-table transactional writes, and insert-vs-update handling.

**Prerequisites:** Read [`RDBMS_PLAN.md`](RDBMS_PLAN.md) (architecture, config, engine-specific notes) and [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) (mapping definition format, transforms, JSONPath syntax) first. Note: `schema/mapper.py` and `schema/validator.py` are now implemented — see the checklist at the bottom for current status.

**Related docs:**
- [`DESIGN.md`](DESIGN.md) -- Pipeline architecture, failure modes, checkpoint strategy
- [`ADMIN_UI.md`](ADMIN_UI.md) -- Config editor with DB output fields, Schema Mappings visual editor

---

## Two Document-to-Table Patterns

Every document from the `_changes` feed falls into one of two patterns:

### Pattern 1: One Document → One Table

The simplest case. A flat or shallow JSON document maps entirely to a single row in a single table. No child tables, no arrays-of-objects.

```json
{
  "_id": "product::A100",
  "_rev": "2-def456",
  "type": "product",
  "name": "Widget A",
  "sku": "WA-100",
  "price": 19.99,
  "category": "hardware",
  "in_stock": true,
  "updated_at": "2026-04-15T10:00:00Z"
}
```

Mapping (`mappings/product.json`):

```json
{
  "source": { "match": { "field": "type", "value": "product" } },
  "output_format": "tables",
  "tables": [
    {
      "name": "products",
      "primary_key": "doc_id",
      "columns": {
        "doc_id": "$._id",
        "rev": "$._rev",
        "name": "$.name",
        "sku": "$.sku",
        "price": { "path": "$.price", "transform": "to_decimal(,2)" },
        "category": "$.category",
        "in_stock": "$.in_stock",
        "updated_at": { "path": "$.updated_at", "transform": "to_iso8601()" }
      },
      "on_delete": "delete"
    }
  ]
}
```

**SQL generated (PostgreSQL):**

The actual SQL is produced by `SqlOperation.to_sql()` in `schema/mapper.py`, which generates `$1, $2, ...` asyncpg-style positional placeholders:

```sql
-- UPSERT (insert or update)
INSERT INTO products (doc_id, rev, name, sku, price, category, in_stock, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (doc_id) DO UPDATE SET
    rev = EXCLUDED.rev,
    name = EXCLUDED.name,
    sku = EXCLUDED.sku,
    price = EXCLUDED.price,
    category = EXCLUDED.category,
    in_stock = EXCLUDED.in_stock,
    updated_at = EXCLUDED.updated_at;

-- DELETE
DELETE FROM products WHERE doc_id = $1;
```

No transaction needed — a single statement is atomic by default.

### Pattern 2: One Document → Multiple Tables

When the document contains nested objects and arrays of objects, the data must be split across a parent table and one or more child tables. This is the common case for rich domain documents.

```json
{
  "_id": "order::12345",
  "_rev": "3-abc123",
  "type": "order",
  "status": "shipped",
  "customer": {
    "id": "cust::789",
    "name": "Alice",
    "email": "alice@example.com"
  },
  "items": [
    { "product_id": "p:100", "name": "Widget A", "qty": 2, "price": 19.99 },
    { "product_id": "p:200", "name": "Widget B", "qty": 1, "price": 49.50 }
  ],
  "tags": ["priority", "wholesale"]
}
```

This produces writes to **three tables** in a single transaction:

```
orders             order_items              order_tags
─────────────      ──────────────────       ──────────────
doc_id (PK)        id (PK, auto)           id (PK, auto)
rev                order_doc_id (FK)       order_doc_id (FK)
status             product_id              tag
customer_id        product_name
customer_name      qty
customer_email     price
```

**SQL generated (PostgreSQL) — inside a transaction:**

```sql
BEGIN;

-- 1. UPSERT parent table
INSERT INTO orders (doc_id, rev, status, customer_id, customer_name, customer_email)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (doc_id) DO UPDATE SET
    rev = EXCLUDED.rev, status = EXCLUDED.status,
    customer_id = EXCLUDED.customer_id,
    customer_name = EXCLUDED.customer_name,
    customer_email = EXCLUDED.customer_email;

-- 2. Replace child rows: delete old, insert new
DELETE FROM order_items WHERE order_doc_id = $1;
INSERT INTO order_items (order_doc_id, product_id, product_name, qty, price)
    VALUES ($1, $2, $3, $4, $5);  -- repeated per array element

DELETE FROM order_tags WHERE order_doc_id = $1;
INSERT INTO order_tags (order_doc_id, tag)
    VALUES ($1, $2);  -- repeated per array element

COMMIT;
```

**This MUST be wrapped in a transaction.** If the parent upsert succeeds but the child inserts fail, the database is left in an inconsistent state — the parent row references data that doesn't exist. The transaction guarantees all-or-nothing.

---

## Why Transactions Are Required for Multi-Table Writes

| Without transaction | With transaction |
|---|---|
| Parent row inserted, child insert fails → orphaned parent with no items | Entire operation rolls back → database unchanged |
| Partial child inserts → order has 1 of 3 items | All children inserted or none |
| Concurrent reader sees incomplete data | Reader sees old state or new state, never partial |
| Recovery requires manual cleanup | Recovery is automatic (rollback) |

The `db/` module wraps every multi-table document write in a single database transaction. For single-table writes, the individual UPSERT statement is inherently atomic — no explicit transaction needed.

---

## Insert vs. Update — Full Record Replace Strategy

### The Problem

The `_changes` feed always delivers the **full current document**, not a delta. When a document is updated in Couchbase (e.g., only the `status` field changed from `"pending"` to `"shipped"`), the `_changes` feed still returns the entire document with all fields.

### The Simple Strategy: Full Record Replace (Initial Implementation)

Every document from the `_changes` feed is treated as a **full replace** — whether the record already exists in the RDBMS or not. This is implemented using UPSERT semantics:

| Scenario | What happens |
|---|---|
| **New record** (doc_id doesn't exist in RDBMS) | `INSERT` — creates a new row with all fields |
| **Updated record** (doc_id already exists) | `UPDATE` — overwrites **every column** with the values from the current document, even if only one field changed |

**For single-table documents:**

```sql
-- PostgreSQL: INSERT ... ON CONFLICT DO UPDATE
-- This handles both new records AND updates in one statement
INSERT INTO products (doc_id, rev, name, sku, price, category, in_stock, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (doc_id) DO UPDATE SET
    rev = EXCLUDED.rev,
    name = EXCLUDED.name,
    sku = EXCLUDED.sku,
    price = EXCLUDED.price,
    category = EXCLUDED.category,
    in_stock = EXCLUDED.in_stock,
    updated_at = EXCLUDED.updated_at;
```

**For multi-table documents:**

```sql
BEGIN;

-- Parent: UPSERT (full replace)
INSERT INTO orders (doc_id, rev, status, customer_id, customer_name, customer_email)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (doc_id) DO UPDATE SET
    rev = EXCLUDED.rev, status = EXCLUDED.status,
    customer_id = EXCLUDED.customer_id,
    customer_name = EXCLUDED.customer_name,
    customer_email = EXCLUDED.customer_email;

-- Children: DELETE all existing rows + INSERT current set
-- This is a full replace of the child data regardless of what changed
DELETE FROM order_items WHERE order_doc_id = $1;
INSERT INTO order_items (order_doc_id, product_id, product_name, qty, price)
    VALUES ($1, 'p:100', 'Widget A', 2, 19.99),
           ($1, 'p:200', 'Widget B', 1, 49.50);

DELETE FROM order_tags WHERE order_doc_id = $1;
INSERT INTO order_tags (order_doc_id, tag)
    VALUES ($1, 'priority'),
           ($1, 'wholesale');

COMMIT;
```

### Why Full Replace?

1. **The `_changes` feed doesn't provide diffs.** You get the whole document — there's no way to know which fields changed without fetching and comparing the previous version.

2. **Simplicity.** One code path for both new and existing records. No need to detect whether the record exists, compute a diff, or generate partial UPDATE statements.

3. **Correctness.** The RDBMS row always matches the current document state in Couchbase. No drift from missed partial updates.

4. **Child tables benefit most.** For arrays (order items, tags), the delete-then-reinsert strategy is simpler and safer than trying to diff individual array elements.

5. **Removals are handled automatically.** When an item is removed from a JSON array, the `_changes` feed delivers the document without that item — but with no explicit "this was deleted" signal. The delete+re-insert strategy solves this: all existing child rows are wiped, then only the items currently in the array are re-inserted. Removed items simply aren't re-inserted.

### Handling Multiple Arrays

When a single document contains multiple arrays (e.g., `items[]`, `payments[]`, `shipments[]`), each array maps to its own child table with a foreign key back to the parent. The same delete+re-insert pattern applies to each array, all within a single transaction:

```sql
BEGIN;

-- Parent: UPSERT
INSERT INTO orders (...) VALUES (...) ON CONFLICT (doc_id) DO UPDATE SET ...;

-- Array 1: items
DELETE FROM order_items WHERE order_doc_id = $1;
INSERT INTO order_items (...) VALUES (...);  -- per item

-- Array 2: payments
DELETE FROM order_payments WHERE order_doc_id = $1;
INSERT INTO order_payments (...) VALUES (...);  -- per payment

-- Array 3: shipments  
DELETE FROM order_shipments WHERE order_doc_id = $1;
INSERT INTO order_shipments (...) VALUES (...);  -- per shipment

COMMIT;
```

Adding a new array is just another child table entry in the mapping JSON — no code changes needed. The mapper generates the DELETE + INSERT operations for each child table that has `replace_strategy: "delete_insert"` and a `source_array` path.

### Multi-Row INSERT Batching

When a single document produces multiple INSERT operations for the same child table (e.g., 4 order items), the engine automatically batches them into a **single multi-row INSERT statement**. This is handled by `group_insert_ops()` in `db/db_base.py` — consecutive INSERT ops targeting the same table with the same column set are collapsed into one round-trip to the database.

**Before (6 round-trips):**

```sql
BEGIN;
INSERT INTO orders (...) VALUES (...) ON CONFLICT ...;    -- 1 round-trip
DELETE FROM order_items WHERE order_doc_id = $1;           -- 1 round-trip
INSERT INTO order_items (...) VALUES ($1,$2,$3,$4);        -- 1 round-trip
INSERT INTO order_items (...) VALUES ($1,$2,$3,$4);        -- 1 round-trip
INSERT INTO order_items (...) VALUES ($1,$2,$3,$4);        -- 1 round-trip
INSERT INTO order_items (...) VALUES ($1,$2,$3,$4);        -- 1 round-trip
COMMIT;
```

**After (3 round-trips):**

```sql
BEGIN;
INSERT INTO orders (...) VALUES (...) ON CONFLICT ...;    -- 1 round-trip
DELETE FROM order_items WHERE order_doc_id = $1;           -- 1 round-trip
INSERT INTO order_items (...) VALUES                       -- 1 round-trip (4 rows)
  ($1,$2,$3,$4), ($5,$6,$7,$8), ($9,$10,$11,$12), ($13,$14,$15,$16);
COMMIT;
```

This optimization is **automatic** — no configuration needed. It applies to all four RDBMS engines with the correct dialect:

| Engine | Multi-row syntax |
|---|---|
| PostgreSQL | `INSERT INTO ... VALUES (...), (...), (...)` with `$N` placeholders |
| MySQL | `INSERT INTO ... VALUES (...), (...), (...)` with `%s` placeholders |
| MSSQL | `INSERT INTO ... VALUES (...), (...), (...)` with `?` placeholders |
| Oracle | `INSERT ALL INTO ... VALUES (...) INTO ... VALUES (...) SELECT 1 FROM DUAL` |

The **Validate Mapping** button in the Schema Mapping UI (`schema.html`) reflects this batching — it shows the grouped operations with a "N rows batched" badge and a summary like "batched 6 → 3 statements".

**Key constraints:**
- Only consecutive INSERTs to the **same table** with the **same column set** are merged.
- UPSERT and DELETE operations are never merged (they pass through as-is).
- Ordering is preserved — DELETE always runs before the multi-row INSERT.

### Trade-offs

| Aspect | Full replace | Partial update (future) |
|---|---|---|
| **Simplicity** | ✅ One code path | ❌ Must diff old vs. new |
| **Correctness** | ✅ Always in sync | ⚠️ Drift risk if diff is wrong |
| **DB write load** | ⚠️ Writes all columns every time | ✅ Only changed columns |
| **Child table churn** | ⚠️ Delete + reinsert all rows | ✅ Only changed rows |
| **Triggers / audit logs** | ⚠️ Fires on every update | ✅ Only fires on real changes |
| **Replication / WAL** | ⚠️ Larger WAL entries | ✅ Smaller WAL entries |

For the initial implementation, full replace is the right choice. The overhead is acceptable for most workloads, and the simplicity eliminates an entire class of bugs.

### Future: Partial Update Strategy

A future optimization could compare the incoming document against the existing RDBMS row and only update changed columns. This would require:

1. **Fetch the existing row** before each write (adds a SELECT per doc).
2. **Diff the values** column by column.
3. **Generate a targeted UPDATE** with only the changed columns.
4. **For child tables**, diff the arrays to determine which rows to insert, update, or delete individually.

This is significantly more complex and only worthwhile for high-volume workloads where the DB write amplification from full replaces is a measurable bottleneck.

---

## Processing Flow in the Worker

When `output.mode` is `"db"`, the changes_worker routes documents through the schema mapper before writing to the RDBMS:

```
_changes doc arrives
       │
       ▼
┌─────────────────────┐
│ Match doc to mapping │  ← schema/mapper.py checks source.match rules
│ (type field, _id     │    against mapping files in mappings/
│  prefix, etc.)       │
└──────────┬──────────┘
           │
     ┌─────┴──────┐
     │             │
  Matched?      No match
     │             │
     ▼             ▼
┌──────────┐  ┌──────────────────┐
│ Apply    │  │ default_mode?    │
│ mapping  │  │ jsonb → fallback │
│ def      │  │ strict → reject  │
└────┬─────┘  └──────────────────┘
     │
     ▼
┌──────────────────────────────┐
│ Extract values per table     │  ← JSONPath extraction + transforms
│ Generate SQL ops             │
└──────────┬───────────────────┘
           │
     ┌─────┴──────┐
     │             │
  1 table?    N tables?
     │             │
     ▼             ▼
  Single        BEGIN
  UPSERT        UPSERT parent
  (auto-        DELETE children
  commit)       INSERT children
                COMMIT
```

### How It Connects to `main.py`

The existing `process_one()` function calls `output.send(doc, method)`. For RDBMS output, `PostgresOutputForwarder.send(doc, method)`:

1. Calls `self._mapper.matches(doc)` to check whether the document matches a mapping definition
2. Calls `self._mapper.map_document(doc, is_delete=...)` which extracts field values using JSONPath, applies transforms, and returns a list of `SqlOperation` objects
3. Acquires a connection from the asyncpg pool, groups consecutive same-table INSERTs into multi-row statements via `group_insert_ops()`, and executes the grouped ops inside `conn.transaction()` — both single-table and multi-table writes use this same transactional path (simple and correct)

> **Threaded mapping:** When running inside a Pipeline (v2.0 multi-job mode), the match + map step (steps 1–2) is offloaded to the Pipeline's `middleware_executor` ThreadPoolExecutor via `loop.run_in_executor()`. This frees the asyncio event loop to process other documents concurrently while the CPU-bound JSONPath extraction and transforms run in a separate thread. The executor is set via `output.set_map_executor(executor)` — when not set (e.g., standalone mode), mapping runs inline on the event loop as before.
4. In `dry_run` mode, logs the SQL without executing
5. Returns `{"ok": True/False, "doc_id": ..., "method": ...}` — same interface as the HTTP output

The rest of the pipeline (checkpoint, DLQ, halt_on_failure, metrics) works identically regardless of output mode.

---

## Connection Configuration

The `PostgresOutputForwarder` reads connection fields from the resolved engine config dictionary (`pg_cfg`). The following fields are recognized:

| Field | Default | Notes |
|---|---|---|
| `host` | `"localhost"` | Hostname or IP of the PostgreSQL server |
| `port` | `5432` | Port number |
| `database` | `""` | Target database name |
| `username` | `"postgres"` | **Canonical field** for the database user. The forwarder also accepts `user` as a fallback (`pg_cfg.get("username") or pg_cfg.get("user") or "postgres"`), but `username` is preferred. An empty string value for `username` falls through to `user`, then to the default `"postgres"`. |
| `password` | `""` | Database password |
| `schema` | `"public"` | PostgreSQL schema for table references |
| `ssl` | `false` | Enable SSL connections. When `true`, creates a default SSL context with hostname checking disabled. |
| `pool_min` | `2` | Minimum number of connections in the asyncpg pool |
| `pool_max` | `10` | Maximum number of connections in the asyncpg pool |
| `sync_commit` | `false` | **Advanced.** When `false` (default), sets `synchronous_commit = OFF` on each connection — Postgres does not wait for WAL flush after commit. **2-5x throughput improvement** for high-volume writes. The pipeline's checkpoint-based recovery makes this safe: on a Postgres crash, the last ~10ms of commits may be lost, but the worker resumes from its checkpoint and re-processes them. Set to `true` for full ACID durability. |
| `prepared_statements` | `true` | **Advanced.** When `true` (default), asyncpg caches prepared statements per connection (`statement_cache_size=100`). Since the same mapping always produces the same SQL shape, this eliminates repeated parse+plan overhead. **10-30% throughput improvement.** Set to `false` to disable (e.g., if using PgBouncer in transaction mode, which doesn't support prepared statements). |

> **Important:** The `mode` field must be present in the output config entry (e.g., `"postgres"`, `"mysql"`). This field is required for the pipeline to select the correct output forwarder.

### Engine Equivalents for `sync_commit`

The `sync_commit` setting works across all four RDBMS engines, each using the engine's native mechanism:

| Engine | When `sync_commit: false` (default) | Effect |
|---|---|---|
| PostgreSQL | `SET synchronous_commit = OFF` | Skip WAL flush wait per commit |
| MySQL | `SET innodb_flush_log_at_trx_commit = 2` | Write to log buffer at commit, flush once per second |
| MSSQL | `SET DELAYED_DURABILITY = ON` | Batch log flushes (SQL Server 2014+) |
| Oracle | `ALTER SESSION SET COMMIT_WRITE = 'BATCH, NOWAIT'` | Batch redo log writes, don't wait |

---

## Config Resolution — `_get_engine_cfg()`

The `_get_engine_cfg()` method resolves which dictionary contains the connection fields. It supports three config layouts, checked in order:

### Nested (v1.x) — `out_cfg.db` or `out_cfg.postgres`

Connection fields live under a sub-key. This is the original config format used by `config.json`:

```jsonc
{
  "output": {
    "mode": "db",
    "db": {               // ← _get_engine_cfg returns this dict
      "engine": "postgres",
      "host": "localhost",
      "port": 5432,
      "database": "mydb",
      "username": "app_user",
      "password": "secret"
    }
  }
}
```

The method checks for a `"db"` key first, then `"postgres"`. Either sub-key works.

### Top-level (v2.0) — fields directly on the output entry

Job documents and the v2 API store connection fields at the top level of the output entry — no nested sub-key. The method detects this when `host`, `port`, or `database` exists directly on `out_cfg`:

```json
{
  "id": "output_postgres",
  "name": "Production Postgres",
  "enabled": true,
  "mode": "postgres",
  "engine": "postgres",
  "host": "db.example.com",
  "port": 5432,
  "database": "mydb",
  "username": "app_user",
  "password": "secret",
  "schema": "public",
  "ssl": false,
  "pool_min": 2,
  "pool_max": 10
}
```

### Fallback — empty dict

If neither nested keys nor top-level connection fields are found, `_get_engine_cfg()` returns `{}` and all fields fall back to their defaults.

### Resolution order summary

```
1. out_cfg["db"]       → nested dict (v1.x)
2. out_cfg["postgres"] → nested dict (v1.x alternate key)
3. out_cfg itself      → if "host", "port", or "database" present (v2.0)
4. {}                  → empty fallback (all defaults)
```

---

## Example Configs

### v2.0 Output Entry (Job Documents)

The preferred format for v2.0 job documents. All connection fields sit at the top level alongside `mode` and `engine`:

```json
{
  "id": "output_postgres",
  "name": "Production Postgres",
  "enabled": true,
  "mode": "postgres",
  "engine": "postgres",
  "host": "db.example.com",
  "port": 5432,
  "database": "mydb",
  "username": "app_user",
  "password": "secret",
  "schema": "public",
  "ssl": false,
  "pool_min": 2,
  "pool_max": 10
}
```

### Single-Table JSONB Mode (Simplest)

Every document goes into one table as a JSONB blob. No mapping files needed.

```jsonc
{
  "output": {
    "mode": "db",
    "db": {
      "engine": "postgres",
      "host": "localhost",
      "port": 5432,
      "database": "mydb",
      "username": "app_user",
      "password": "secret",
      "table": "couchbase_docs",
      "mapping": {
        "mode": "jsonb",
        "doc_id_column": "doc_id",
        "rev_column": "rev",
        "body_column": "body"
      }
    }
  }
}
```

### Multi-Table Column Mapping Mode

Documents are split across tables using mapping definitions. Unmapped doc types fall back to the JSONB table.

```jsonc
{
  "output": {
    "mode": "db",
    "db": {
      "engine": "postgres",
      "host": "localhost",
      "port": 5432,
      "database": "mydb",
      "username": "app_user",
      "password": "secret",
      "table": "couchbase_docs",
      "schema_mappings": {
        "enabled": true,
        "path": "mappings/",
        "default_mode": "jsonb",
        "strict": false
      }
    }
  }
}
```

With mapping files:

```
mappings/
├── order.json       # order docs → orders + order_items + order_tags (3 tables, transaction)
├── product.json     # product docs → products (1 table, no transaction needed)
└── customer.json    # customer docs → customers + customer_addresses (2 tables, transaction)
```

---

## Delete Handling

When the `_changes` feed reports `deleted=true`:

### Single-Table

```sql
DELETE FROM products WHERE doc_id = $1;
```

### Multi-Table (transaction, child tables first)

```sql
BEGIN;
DELETE FROM order_tags  WHERE order_doc_id = $1;
DELETE FROM order_items WHERE order_doc_id = $1;
DELETE FROM orders      WHERE doc_id = $1;
COMMIT;
```

Child tables are deleted **before** the parent to satisfy foreign key constraints. The mapper processes tables in reverse order during deletes.

---

## Error Handling

RDBMS writes follow the same failure semantics as HTTP output (see [`DESIGN.md`](DESIGN.md)):

| Error | `halt_on_failure=true` | `halt_on_failure=false` |
|---|---|---|
| Connection lost | Stop, hold checkpoint, reconnect next cycle | Log, DLQ, skip, continue |
| Constraint violation (FK, unique, check) | Stop, hold checkpoint | DLQ, skip |
| Transaction deadlock | Retry with backoff, then stop | Retry, then DLQ |
| Type mismatch (e.g., string in INT column) | Stop, hold checkpoint | DLQ, skip |

**The transaction guarantee means no partial writes.** If any statement within the transaction fails, the entire transaction rolls back. The RDBMS is never left in an inconsistent state.

---

## Implementation Checklist

1. [x] `schema/mapper.py` — Implemented: `SchemaMapper`, `SqlOperation`, `resolve_path()`, `apply_transform()`, `resolve_column()`
2. [x] `schema/validator.py` — Implemented: `validate_schema()`, `validate_file()`
3. [x] `db/db_postgres.py` — Implemented: `PostgresOutputForwarder` with asyncpg pool, transactional multi-table writes
4. [ ] `db/db_mysql.py` — Placeholder (MySQL upsert syntax `ON DUPLICATE KEY UPDATE`)
5. [ ] `db/db_mssql.py` — Placeholder (MERGE syntax)
6. [ ] `db/db_oracle.py` — Placeholder (Oracle MERGE syntax)
7. [ ] `main.py` — Load mappings at startup, route docs through mapper before DB output (not yet wired)
8. [ ] `mappings/` — Example mapping files (order, product, customer) (not yet created)
9. [ ] Integration tests — End-to-end: sample doc → mapper → transaction → verify DB state
