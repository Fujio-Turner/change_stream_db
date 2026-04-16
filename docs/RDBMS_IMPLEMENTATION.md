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
3. Acquires a connection from the asyncpg pool and iterates the `SqlOperation` list inside `conn.transaction()` — both single-table and multi-table writes use this same transactional path (simple and correct)
4. In `dry_run` mode, logs the SQL without executing
5. Returns `{"ok": True/False, "doc_id": ..., "method": ...}` — same interface as the HTTP output

The rest of the pipeline (checkpoint, DLQ, halt_on_failure, metrics) works identically regardless of output mode.

---

## Example Configs

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
