# Schema Mapping – JSON Documents to Relational Tables

This document describes how the `schema/` module maps Couchbase JSON documents from the `_changes` feed into one or more normalized RDBMS tables or a remapped JSON structure, including the mapping definition format, transform functions, multi-table transaction handling, and the Python libraries that support this.

---

## The Problem

A single Couchbase document can contain:

- Flat key/value fields
- Nested objects (address, customer, metadata)
- Arrays of primitives (tags, phone numbers)
- Arrays of objects (line items, history entries)
- Mixed nesting (arrays of objects containing arrays)

An RDBMS requires all of this to be flattened into rows across one or more tables with proper primary keys, foreign keys, and constraints. A single document mutation in Couchbase may need to touch **multiple tables inside a single transaction**.

### Example Document

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
  "shipping_address": {
    "street": "123 Main St",
    "city": "Springfield",
    "state": "IL",
    "zip": "62704"
  },
  "items": [
    { "product_id": "p:100", "name": "Widget A", "qty": 2, "price": 19.99 },
    { "product_id": "p:200", "name": "Widget B", "qty": 1, "price": 49.50 }
  ],
  "tags": ["priority", "wholesale"],
  "created_at": "2026-04-10T14:30:00Z",
  "updated_at": "2026-04-15T09:15:00Z"
}
```

### Target Tables

```
orders             order_items              order_tags
─────────────      ──────────────────       ──────────────
doc_id (PK)        id (PK, auto)           id (PK, auto)
rev                order_doc_id (FK)       order_doc_id (FK)
status             product_id              tag
customer_id        product_name
customer_name      qty
customer_email     price
ship_street
ship_city
ship_state
ship_zip
created_at
updated_at
```

One document → three tables. Arrays become child tables with a foreign key back to the parent `doc_id`.

---

## Module Layout

```
schema/
├── __init__.py        # Public API: load_schema(), SchemaMapper
├── mapper.py          # Core mapper: reads a mapping def, produces SQL ops per doc
└── validator.py       # Validates a mapping def against a sample doc or table DDL
```

---

## Mapping Definition Format

The user provides a JSON file that describes how document fields map to tables and columns. One mapping file per Couchbase document type.

### Full Example: `mappings/order.json`

```json
{
  "source": {
    "match": {
      "field": "type",
      "value": "order"
    }
  },
  "output_format": "tables",
  "tables": [
    {
      "name": "orders",
      "primary_key": "doc_id",
      "columns": {
        "doc_id": "$._id",
        "rev": "$._rev",
        "status": "$.status",
        "customer_id": "$.customer.id",
        "customer_name": { "path": "$.customer.name", "transform": "propercase()" },
        "customer_email": { "path": "$.customer.email", "transform": "lowercase()" },
        "ship_street": "$.shipping_address.street",
        "ship_city": "$.shipping_address.city",
        "ship_state": { "path": "$.shipping_address.state", "transform": "uppercase()" },
        "ship_zip": "$.shipping_address.zip",
        "created_at": { "path": "$.created_at", "transform": "to_iso8601()" },
        "updated_at": { "path": "$.updated_at", "transform": "to_iso8601()" }
      },
      "on_delete": "delete"
    },
    {
      "name": "order_items",
      "parent": "orders",
      "foreign_key": { "column": "order_doc_id", "references": "doc_id" },
      "source_array": "$.items",
      "replace_strategy": "delete_insert",
      "columns": {
        "order_doc_id": "$._id",
        "product_id": "$.product_id",
        "product_name": "$.name",
        "qty": "$.qty",
        "price": { "path": "$.price", "transform": "to_decimal(,2)" }
      }
    },
    {
      "name": "order_tags",
      "parent": "orders",
      "foreign_key": { "column": "order_doc_id", "references": "doc_id" },
      "source_array": "$.tags",
      "replace_strategy": "delete_insert",
      "columns": {
        "order_doc_id": "$._id",
        "tag": "$"
      }
    }
  ]
}
```

### Key Concepts

| Field | Description |
|---|---|
| `source.match` | How to identify which documents this mapping applies to. Match on `field` value or `id_prefix`. |
| `tables[].name` | Target table name in the RDBMS. |
| `tables[].primary_key` | Column used for upsert conflict detection. |
| `tables[].columns` | Map of `column_name` → `JSONPath expression` to extract values from the doc. |
| `tables[].source_array` | For child tables — the JSONPath to the array in the document. |
| `tables[].foreign_key` | Links the child table back to the parent via `column` → `references`. |
| `tables[].replace_strategy` | `delete_insert` = delete all child rows then re-insert. `merge` = upsert individual rows. |
| `tables[].on_delete` | What to do when the `_changes` feed reports `deleted=true`. |
| `tables[].columns[].transform` | Optional transform function applied to the value before insertion. Can be a string (`"lowercase()"`) or chained (`"trim().lowercase()"`). When a transform is used, the column value becomes an object: `{"path": "$.email", "transform": "lowercase()"}`. |

### JSONPath Expressions

Paths use a simplified JSONPath syntax:

| Path | Meaning |
|---|---|
| `$._id` | Root-level `_id` field |
| `$.customer.name` | Nested field `customer.name` |
| `$.items` | The `items` array |
| `$.items[*].price` | `price` field from every element in `items` |
| `$` | The current element itself (for primitive arrays) |

For child tables, column paths are **relative to each array element** unless they start with `$._id` or `$._rev` (which pull from the root document).

### Transform Functions

Each column mapping can include an optional transform function to convert, clean, or format the value before it reaches the target. Transforms are specified in the column definition:

```json
{
  "columns": {
    "doc_id": "$._id",
    "email": { "path": "$.email", "transform": "lowercase()" },
    "price": { "path": "$.price", "transform": "to_decimal(,2)" },
    "name": { "path": "$.name", "transform": "trim().propercase()" }
  }
}
```

58 built-in transform functions are available across 6 categories: String, Numeric, Date/Time, Array/Object, Encoding/Hash, and Conditional. See the [Transform Functions Reference](/transforms) page in the admin UI or [`ADMIN_UI.md`](ADMIN_UI.md#transform-functions-reference-transforms) for the complete list.

Transforms can be chained using dot notation — each function receives the output of the previous one:
- `trim().lowercase()` — trim whitespace then lowercase
- `to_float().to_decimal(,2)` — parse then format
- `split(,",")[0].trim()` — first CSV value, trimmed

### JSON Output Mode

In addition to table-based mapping, mappings can output to a remapped JSON structure using `"output_format": "json"`:

```json
{
  "source": { "match": { "field": "type", "value": "order" } },
  "output_format": "json",
  "mapping": {
    "orderId": "$._id",
    "orderStatus": { "path": "$.status", "transform": "uppercase()" },
    "customerEmail": { "path": "$.customer.email", "transform": "trim().lowercase()" },
    "items": "$.items"
  }
}
```

JSON mode uses a flat `mapping` object (target key → source path) instead of the `tables` array. The same transform functions are available.

---

## How the Mapper Works

### Processing a Single Document

```
                    ┌──────────────────────────────────┐
                    │  Incoming _changes doc (JSON)     │
                    └────────────────┬─────────────────┘
                                     │
                            ┌────────▼────────┐
                            │  Match doc type  │
                            │  (type field or  │
                            │   _id prefix)    │
                            └────────┬────────┘
                                     │
                         ┌───────────▼───────────┐
                         │  Load mapping def     │
                         │  (order.json, etc.)   │
                         └───────────┬───────────┘
                                     │
               ┌─────────────────────┼─────────────────────┐
               │                     │                     │
      ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
      │  Extract cols   │  │  Extract array  │  │  Extract array  │
      │  for "orders"   │  │  for "items"    │  │  for "tags"     │
      │  (parent table) │  │  (child table)  │  │  (child table)  │
      └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
               │                     │                     │
               └─────────────────────┼─────────────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  BEGIN TRANSACTION   │
                          │                      │
                          │  1. UPSERT orders    │
                          │  2. DELETE old items  │
                          │  3. INSERT new items  │
                          │  4. DELETE old tags   │
                          │  5. INSERT new tags   │
                          │                      │
                          │  COMMIT              │
                          └──────────────────────┘
```

### Processing a Delete

When `_changes` reports `deleted=true`:

1. The mapper checks `on_delete` for each table.
2. Child tables are deleted **first** (respecting foreign key order).
3. The parent table row is deleted last.
4. All within a single transaction.

```
DELETE FROM order_tags  WHERE order_doc_id = :doc_id;
DELETE FROM order_items WHERE order_doc_id = :doc_id;
DELETE FROM orders      WHERE doc_id = :doc_id;
```

### Replace Strategies for Child Tables

| Strategy | Behavior | When to use |
|---|---|---|
| `delete_insert` | Delete all child rows for this `doc_id`, then insert the current set. | Default. Simplest. Works for any array. Guarantees the child table mirrors the document exactly. |
| `merge` | Upsert individual child rows by a composite key (e.g., `order_doc_id` + `product_id`). Rows not in the current document are **not** deleted. | When child rows have their own identity and you don't want to lose rows that were removed from the array. Requires a unique key on the child table. |

---

## Transaction Handling

**Every document is processed inside a single RDBMS transaction.** This is critical because:

1. **Atomicity** — Parent + all child tables either all succeed or all roll back. No partial writes.
2. **Consistency** — Foreign key constraints are satisfied at commit time.
3. **Isolation** — Other queries see either the old state or the new state, never a mix.
4. **Error recovery** — On failure, `rollback()` returns the database to a clean state. The failed document goes to the dead-letter queue (if configured) or the checkpoint is held (if `halt_on_failure=true`).

### Pseudocode

```python
async def process_doc(self, doc: dict, method: str, mapping: SchemaMapping) -> dict:
    conn = await self.pool.acquire()
    try:
        async with conn.transaction():
            if method == "DELETE":
                # Delete children first (reverse table order)
                for table in reversed(mapping.tables):
                    if table.is_child:
                        await conn.execute(
                            f"DELETE FROM {table.name} WHERE {table.fk_column} = $1",
                            doc["_id"]
                        )
                # Delete parent last
                await conn.execute(
                    f"DELETE FROM {mapping.parent_table.name} WHERE {mapping.parent_table.pk} = $1",
                    doc["_id"]
                )
            else:
                # Upsert parent
                await conn.execute(mapping.parent_table.upsert_sql, *extract_values(doc, mapping.parent_table))

                # Replace children
                for table in mapping.child_tables:
                    if table.replace_strategy == "delete_insert":
                        await conn.execute(f"DELETE FROM {table.name} WHERE {table.fk_column} = $1", doc["_id"])
                        array_data = extract_array(doc, table.source_array)
                        for element in array_data:
                            await conn.execute(table.insert_sql, *extract_values(element, table, root_doc=doc))
                    else:
                        # merge strategy
                        ...

        return {"ok": True, "doc_id": doc["_id"], "method": method}

    except Exception as e:
        # transaction auto-rolls back on exception
        return {"ok": False, "doc_id": doc.get("_id", "unknown"), "error": str(e)}
    finally:
        await self.pool.release(conn)
```

---

## Engine-Specific Upsert SQL

The mapper generates different SQL per engine:

### PostgreSQL

```sql
INSERT INTO orders (doc_id, rev, status, customer_id, ...)
VALUES ($1, $2, $3, $4, ...)
ON CONFLICT (doc_id) DO UPDATE SET
    rev = EXCLUDED.rev,
    status = EXCLUDED.status,
    customer_id = EXCLUDED.customer_id,
    ...
```

### MySQL

```sql
INSERT INTO orders (doc_id, rev, status, customer_id, ...)
VALUES (%s, %s, %s, %s, ...)
ON DUPLICATE KEY UPDATE
    rev = VALUES(rev),
    status = VALUES(status),
    customer_id = VALUES(customer_id),
    ...
```

### MS SQL Server

```sql
MERGE INTO orders AS target
USING (SELECT @id AS doc_id, @rev AS rev, @status AS status, ...) AS src
ON target.doc_id = src.doc_id
WHEN MATCHED THEN
    UPDATE SET rev = src.rev, status = src.status, ...
WHEN NOT MATCHED THEN
    INSERT (doc_id, rev, status, ...)
    VALUES (src.doc_id, src.rev, src.status, ...);
```

### Oracle

```sql
MERGE INTO orders o
USING (SELECT :id AS doc_id, :rev AS rev, :status AS status, ... FROM dual) src
ON (o.doc_id = src.doc_id)
WHEN MATCHED THEN
    UPDATE SET o.rev = src.rev, o.status = src.status, ...
WHEN NOT MATCHED THEN
    INSERT (doc_id, rev, status, ...)
    VALUES (src.doc_id, src.rev, src.status, ...);
```

Oracle 23ai users can alternatively use **JSON Relational Duality Views** — insert a full JSON document against the view and let Oracle handle the multi-table split internally.

---

## Python Libraries for JSON → Relational Mapping

These libraries can assist with or replace parts of the custom mapper:

### Strongly Recommended

| Library | What it does | Best for |
|---|---|---|
| [**relationalize**](https://github.com/tulip/relationalize) | Recursively breaks nested JSON into parent/child DataFrames with auto-generated foreign keys. | Automatic flattening of arbitrarily nested docs into relational tables. Works per-document — fits the streaming `_changes` model. |
| [**JSONSchema2DB**](https://jsonschema2db.readthedocs.io/) | Takes a JSON Schema + JSON data, creates Postgres/Redshift tables, and inserts with automatic normalization. | Schema-driven mapping. If you can describe your doc types as JSON Schema, this handles table creation and multi-table inserts. |

### Also Useful

| Library | What it does | Best for |
|---|---|---|
| [**json2db**](https://github.com/mrzhangboss/json2db) | Define a model (or use SQLAlchemy), stores nested JSON into relational tables. | Quick prototyping with SQLAlchemy-backed storage. |
| [**json-relational**](https://github.com/gr0vity-dev/json-relational) | Flattens nested JSON with depth control into a relational-friendly format. | Controlled flattening when you want to decide how deep to normalize. |
| **Pandas `json_normalize`** | Flattens nested dicts/lists into a flat DataFrame. | One-off flattening + `to_sql()` for quick loads. Good for prototyping. |
| **SQLAlchemy ORM** | Define Python classes that map to tables. Populate objects from JSON, commit in a session. | Full ORM approach with relationships, cascading deletes, etc. |

### When to Use a Library vs. Custom Mapper

| Scenario | Recommendation |
|---|---|
| Documents have a **known, stable schema** with 1–3 levels of nesting | Custom mapping YAML + `schema/mapper.py`. Full control. |
| Documents are **highly variable** or **schema-less** | `relationalize` — it figures out the tables automatically. |
| You already have **JSON Schema** definitions for your doc types | `JSONSchema2DB` — schema-driven, auto-creates tables. |
| You want **ORM-level** relationship management (cascades, lazy loading) | SQLAlchemy models with a JSON-to-model hydration layer. |
| Quick **proof of concept** | Pandas `json_normalize` + `to_sql()`. |

---

## Admin UI Editor

Schema mappings can be managed through the visual editor at `/schema`. See [`ADMIN_UI.md`](ADMIN_UI.md#schema-mappings-schema) for full details.

The editor provides:

- **Split-pane layout** -- Source document (left) and target tables/JSON (right)
- **Three source input modes** -- Paste JSON, JSON Schema, or Live Sample (random doc from `_changes` feed)
- **Source field extraction** -- Auto-extracts JSON paths with drag-and-drop onto column mappings
- **Output mode toggle** -- Switch between Tables mode and JSON-to-JSON remapping mode
- **Table tabs** -- Each target table gets its own tab with settings, parent/FK config, and column mappings
- **Transform functions** -- 58 built-in transforms selectable from a categorized dropdown, with editable text input for customization. Selecting a transform auto-injects the source path into the function (e.g., `trim()` + `$.total` → `trim($.total)`)
- **Source path autocomplete** -- Type-ahead suggestions for source fields and transform function names
- **Relationship diagram** -- ECharts force-directed graph showing parent/child tables and foreign keys
- **Mapping coverage stats** -- Live source coverage (% of source fields mapped) and target coverage (% of target columns filled) with progress bars and unmapped field lists
- **Sample templates** -- Pre-built mappings for both Tables (Orders, Profiles, Products) and JSON (Orders, Events, Sensors) output modes, demonstrating date format conversion (epoch vs ISO-8601), transform chaining, and type coercion
- **Save / Download / Delete** -- Full CRUD via `/api/mappings` REST endpoints

When CBL is available, mappings are stored as CBL documents (`mapping:{filename}`). Otherwise they are stored as JSON files in the `mappings/` directory. See [`CBL_DATABASE.md`](CBL_DATABASE.md#mappingfilename) for the CBL document schema.

---

## Config Integration

The schema mapping files are referenced from `config.json`:

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
      "table": "couchbase_docs",       // fallback for unmapped doc types (jsonb mode)

      "schema_mappings": {
        "enabled": true,
        "path": "mappings/",           // folder containing .json mapping files
        "default_mode": "jsonb",       // what to do with docs that don't match any mapping
        "strict": false                // false = unmapped docs go to fallback table
                                       // true  = unmapped docs are rejected (error)
      }
    }
  }
}
```

### Loading Mappings at Startup

1. On startup, `schema/mapper.py` scans the `mappings/` folder for `.json` files.
2. Each file is validated by `schema/validator.py` (check JSONPaths, table references, FK consistency).
3. A mapping registry is built: `{ "order": OrderMapping, "product": ProductMapping, ... }`.
4. When a document arrives from `_changes`, the mapper checks `source.match` rules to find the right mapping.
5. If no mapping matches and `default_mode=jsonb`, the doc is stored as-is in the fallback table.

```
mappings/
├── order.json        # maps "order" docs → orders + order_items + order_tags
├── product.json      # maps "product" docs → products + product_variants
└── customer.json     # maps "customer" docs → customers + customer_addresses
```

---

## Handling Edge Cases

### Missing Fields

If a JSONPath resolves to `None` (field doesn't exist in the document):

- **Nullable column** → insert `NULL`.
- **NOT NULL column** → use a default value from the mapping def, or reject the doc.

```json
{
  "ship_zip": {
    "path": "$.shipping_address.zip",
    "default": "",
    "nullable": true
  }
}
```

### Type Mismatches

Couchbase documents are schema-less — a field that's a string in one doc might be a number in another. The mapper should:

1. Attempt type coercion based on the target column type.
2. If coercion fails, log a warning and either use `NULL` or reject the doc.

### Empty Arrays

If the source array is empty or missing:

- `delete_insert` strategy → deletes all existing child rows (correct — the array is now empty).
- `merge` strategy → no-op (existing rows remain).

### Documents With No Type Field

If `source.match` can't identify the document type:

- `strict=false` → store in the fallback JSONB table.
- `strict=true` → reject the doc (log error, send to DLQ).

### Revision Handling

Store `_rev` in the parent table. On upsert, optionally check that the incoming `_rev` is newer:

```json
{
  "name": "orders",
  "rev_check": true
}
```

This prevents out-of-order updates from the `_changes` feed (rare but possible in parallel mode).

---

## Validation (`schema/validator.py`)

Before the worker starts processing, the validator checks each mapping file for:

| Check | Description |
|---|---|
| **JSONPath syntax** | All paths are valid JSONPath expressions. |
| **Parent-child consistency** | Every child table references a valid parent table. FK column exists in the child's columns. |
| **Primary key exists** | The `primary_key` column is listed in `columns`. |
| **No duplicate table names** | Across all mapping files. |
| **Sample doc test** | If a sample document is provided, extract all paths and verify they resolve to values. |

Run validation explicitly:

```bash
python -m schema.validator mappings/order.yaml --sample sample_order.json
```

---

## Implementation Order

1. **Mapping definition format** — finalize the JSON schema (this document).
2. **`schema/mapper.py`** — core mapper: load JSON, extract values by JSONPath, generate SQL.
3. **`schema/validator.py`** — validate mapping files at startup.
4. **PostgreSQL integration** — end-to-end: `_changes` → mapper → Postgres transaction.
5. **MySQL / MS SQL / Oracle** — add engine-specific upsert SQL generation.
6. **`columns` mapping mode enhancements** — type coercion, defaults, rev checks.
7. **`relationalize` integration** — optional auto-mapping mode for unknown doc structures.

---

## Project Structure (Updated)

```
change_stream_db/
├── changes_worker.py          # Main worker (input: _changes feed)
├── cbl_store.py               # Couchbase Lite CE storage layer
├── config.json                # Configuration
├── rest/                      # REST/HTTP output module
│   ├── __init__.py
│   └── output_http.py
├── db/                        # RDBMS output modules (one per engine)
│   ├── __init__.py
│   ├── db_postgres.py
│   ├── db_mysql.py
│   ├── db_mssql.py
│   └── db_oracle.py
├── schema/                    # Schema mapping (JSON doc -> relational tables)
│   ├── __init__.py
│   ├── mapper.py              # Core: load mapping def, extract values, generate SQL
│   └── validator.py           # Validate mapping defs at startup
├── mappings/                  # User-defined mapping files (one per doc type)
│   └── (order.json, etc.)     # Also stored in CBL when available
├── web/                       # Admin UI
│   ├── server.py              # aiohttp web server (config, mappings, DLQ, status APIs)
│   ├── templates/             # Dashboard, config editor, schema editor, transforms reference HTML
│   └── static/                # CSS (DaisyUI), JS (Tailwind, ECharts)
├── docs/
│   ├── ADMIN_UI.md            # Dashboard & UI documentation
│   ├── CBL_DATABASE.md        # Couchbase Lite database schema
│   ├── CBL_STORE.md           # CBL implementation plan
│   ├── DESIGN.md              # Architecture & failure modes
│   ├── RDBMS_PLAN.md          # RDBMS output plan
│   └── SCHEMA_MAPPING.md     # This file
├── tests/
│   └── test_changes_worker.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
