# Setup Wizard

The `/wizard` page is a four-mode guided setup experience for the Changes Worker pipeline.

**URL:** `/wizard`

**Related docs:**
- [`ADMIN_UI.md`](ADMIN_UI.md) — Dashboard, Config Editor, Schema Mappings, Transforms
- [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) — Mapping definition format, transforms, JSONPath syntax
- [`DESIGN.md`](DESIGN.md) — Architecture & failure modes

---

## Overview

The wizard landing page presents four options in a 2×2 grid layout:

```
┌─────────────────────────┬─────────────────────────┐
│  ⚙️ Settings Wizard      │  🗄️ RDBMS Schema Import │
│                         │                         │
│  Optimize pipeline      │  Import SQL CREATE      │
│  settings with guided   │  TABLE definitions &    │
│  questions              │  auto-detect RDBMS type │
├─────────────────────────┼─────────────────────────┤
│  🗂️ Schema Mapping       │  ☁️ Cloud Storage        │
│                         │                         │
│  Connect source,        │  Configure attachment   │
│  configure output,      │  extraction & JSON      │
│  and map fields         │  archival strategies    │
└─────────────────────────┴─────────────────────────┘
```

### Order & Recommended Flow

1. **⚙️ Settings Wizard** (top-left) — Start here for pipeline optimization
2. **🗄️ RDBMS Schema** (top-right) — Optional, for RDBMS target introspection
3. **🗂️ Schema Mapping** (bottom-left) — Define source-to-output mappings
4. **☁️ Cloud Storage** (bottom-right) — Optional, for attachment/archive strategies

---

## Settings Wizard

An 8-question guided Q&A that generates optimized `config.json` settings based on your use case. Answers are merged into the existing config (read via `GET /api/config`, deep-merged, then saved via `PUT /api/config`).

The wizard features a wider layout (40% increase) for improved text readability and prevents option descriptions from wrapping awkwardly.

### Q1: Branch Point

| Question | Answers |
|---|---|
| **Data (JSON) only or Data + Attachments?** | Branches into **Data Only** path or **Attachments** path |

### Data Only Path (Q2–Q8)

| # | Question | Answers → Config Effect |
|---|---|---|
| 2 | **Large initial sync (100Ks–millions)?** | Yes → `changes_feed.optimize_initial_sync = true` |
| 3 | **Average doc size under 1KB?** | Yes → `changes_feed.include_docs = true` (inline in feed) |
| 4 | **Track deletes/tombstones?** | No → `changes_feed.active_only = true`, `processing.ignore_delete = true` |
| 5 | **Continuous or batch feed?** | Continuous → `changes_feed.feed_type = "continuous"`, Batches → `"longpoll"` |
| 6 | **Accuracy or speed?** | Accuracy → `processing.sequential = true` |
| 7 | **Large docs (100KB+)?** | Yes → `changes_feed.throttle_feed = 1000` (smaller batches) |
| 8 | **Save failed data (DLQ) or skip?** | DLQ → `output.data_error_action = "dlq"`, Skip → `"skip"` |

### Attachments Path (Q2–Q8)

When attachments are selected, the wizard forces `include_docs = false` and `active_only = true` (attachments imply large data; you don't process attachments for deleted docs).

| # | Question | Answers → Config Effect |
|---|---|---|
| 2 | **Large initial sync (100Ks–millions)?** | Yes → `changes_feed.optimize_initial_sync = true` |
| 3 | **How many attachments per document?** | 1–3 → `attachments.mode = "individual"`, Many → `"multipart"` |
| 4 | **How large are your attachments?** (Couchbase max 20MB) | Small (≤50KB) → `fetch.request_timeout_seconds = 30`, Medium (50KB–1MB) → `120` + `throttle_feed = 1000`, Large (1MB+) → `300` + `stream_to_disk` + `sequential = true` + `throttle_feed = 500` |
| 5 | **Attachment destination?** | S3 → `destination.type = "s3"`, HTTP → `"http"`, Filesystem → `"filesystem"` |
| 6 | **Post-process action?** | Update doc → `post_process.action = "update_doc"` + `update_field = "attachments_external"` (shows JSON preview example before advancing), Set TTL → `"set_ttl"` + number input for `ttl_seconds` (default 86400), Delete attachments, Delete doc, Purge (marked as **Irreversible** in red), or Nothing |
| 7 | **Missing attachment handling?** | Skip → `on_missing_attachment = "skip"` + `partial_success = "continue"`, Fail → `"fail"` + `"fail_doc"` |
| 8 | **Error handling?** | DLQ → `output.data_error_action = "dlq"` + `halt_on_failure = true` + number input for `dlq.retention_seconds` (default 86400), Skip → `"skip"` + `halt_on_failure = false` |

### Summary Page

After all questions are answered, users see:

1. **Your Choices** — A recap of all Q&A selections as key-value pairs
2. **Your Configuration Story** — A human-readable narrative describing what will happen based on the answers. For example:
   - *Attachment workflow:* Describes attachment size, fetch strategy, destination, error handling, post-processing action, and failure handling
   - *Data-only workflow:* Describes feed mode, document fetching, delete tracking, processing strategy, and error handling
3. **Generated Config Preview** — The complete `config.json` that will be saved
4. **Save & Apply Settings** — Merges the generated config into the existing config and saves it via `PUT /api/config`

---

## Schema Mapping Wizard

A 3-step guided wizard for configuring the entire Changes Worker pipeline — from connecting a `_changes` feed, through choosing an output destination, to mapping source document fields onto the target format.

The wizard produces two artifacts:

1. **`config.json`** — Full worker configuration (gateway, auth, output, checkpoint, metrics, logging)
2. **Mapping file** — A schema mapping JSON saved to `mappings/{name}_mapping.json`

Both can be saved directly from the wizard UI.

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Step 1           │     │  Step 2           │     │  Step 3           │
│  Connect Source   │────▶│  Configure Output │────▶│  Map Fields       │
│                   │     │                   │     │                   │
│  • SG / App Svc   │     │  • Stdout         │     │  • Source fields  │
│  • Edge Server    │     │  • HTTP endpoint  │     │  • JSON mapping   │
│  • Auth config    │     │  • RDBMS          │     │  • Table mapping  │
│  • Test & sample  │     │  • Test conn      │     │  • Transforms     │
└──────────────────┘     └──────────────────┘     │  • Save config    │
                                                   └──────────────────┘
```

---

## Step 1: Connect Source

Connect to a `_changes` feed and verify the connection by fetching a sample document.

### Fields

| Field | Description | Default |
|---|---|---|
| **Source Type** | `sync_gateway`, `app_services`, `edge_server`, or `couchdb` | `sync_gateway` |
| **URL** | Base URL of the gateway (e.g., `http://localhost:4984`) | — |
| **Database** | Database name on the gateway | — |
| **Scope** | Keyspace scope | `_default` |
| **Collection** | Keyspace collection | `_default` |
| **Accept Self-Signed Certs** | Skip TLS verification | off |
| **Auth Method** | `basic`, `bearer`, `session`, or `none` | `basic` |
| **Username / Password** | Shown when auth = basic | — |
| **Bearer Token** | Shown when auth = bearer | — |
| **Session Cookie** | Shown when auth = session | — |

### Actions

- **🔌 Test Connection** — Calls `POST /api/wizard/test-source`. On success displays "✅ Connected! Got N docs" and stores a sample document for Step 3.
- **🎲 Fetch Random Sample** — Same endpoint, returns a different random doc from the pool each click. The sample JSON is displayed in a read-only textarea.

### URL Construction

The wizard builds the `_changes` URL based on source type:

| Source Type | URL Pattern |
|---|---|
| `sync_gateway` | `{url}/{database}.{scope}.{collection}/_changes` |
| `app_services` | `{url}/{database}/_changes` |
| `edge_server` | `{url}/{database}/_changes` |
| `couchdb` | `{url}/{database}/_changes` |

> **CouchDB notes:** CouchDB does not support scopes/collections (ignored if set), `active_only`, SG channels, or `version_type`. Auth supports `basic` and `none` (no SyncGatewaySession cookies). Supported feed types: `normal`, `longpoll`, `continuous`, `eventsource`. Documents are fetched via `POST /{db}/_bulk_get` (same JSON response format as SG).

---

## Step 2: Configure Output

Choose where processed documents are sent. Three output modes:

### Stdout

No configuration needed. Documents are printed to stdout (console / logs). This is the simplest mode for testing and development.

### HTTP

Forward documents to an HTTP endpoint.

| Field | Description |
|---|---|
| **Target URL** | The base URL to send documents to |
| **Output Format** | `json`, `xml`, `form`, `msgpack`, or `csv` |
| **Write Method** | `PUT` or `POST` |
| **Accept Self-Signed Certs** | Skip TLS verification for the target |
| **Auth Method** | `none`, `basic`, or `bearer` for the target endpoint |

**🔌 Test Output** — Calls `POST /api/wizard/test-output` which sends an HTTP `HEAD` request to the target URL and reports the HTTP status code.

### RDBMS

Write documents to a relational database.

| Field | Description |
|---|---|
| **Database Type** | Auto-populated from `/api/db/drivers` — only shows engines with installed Python drivers |
| **Host / Port** | Database server address (port auto-set per engine) |
| **Database** | Database / service name |
| **User / Password** | Database credentials |
| **Schema** | Schema name (e.g., `public` for PostgreSQL, `dbo` for SQL Server) |
| **Pool Min** | Minimum connections in the connection pool |
| **Pool Max** | Maximum connections in the connection pool |
| **SSL** | Enable SSL connections |
| **Mode** | Auto-set from the selected engine (e.g., `postgresql`, `mysql`). Displayed as a badge under the engine selector — not user-editable. |

Actions:
- **🔌 Test Connection** — Reuses `POST /api/db/test`. Shows database version on success.
- **📥 Fetch Tables** — Reuses `POST /api/db/introspect`. Displays discovered tables with PK/FK badges and column types. Tables are selectable via checkboxes for pre-population in Step 3.

---

## Step 3: Map Source → Output

A split-pane mapping editor that adapts to the output mode chosen in Step 2.

### Left Panel — Source (45%)

- Read-only display of the sample JSON document fetched in Step 1
- **Source Fields** — Auto-extracted JSON paths with type badges, displayed in a hierarchical list
- Fields are **draggable** — drag onto any source path input on the right panel

### Right Panel — Target (55%)

#### Source Match

Define which documents this mapping applies to (e.g., field = `type`, value = `order`). Only documents matching this rule will be processed by this mapping.

#### JSON Mode (Stdout / HTTP)

Shown when output mode is Stdout or HTTP. A flat list of field mappings:

| Column | Description |
|---|---|
| **Target Key** | Key name in the output JSON |
| **Source Path** | JSONPath to extract from the source doc (e.g., `$.customer.name`) |
| **Transform ▾** | Dropdown with 58 built-in transforms organized by category |
| **Transform (edit)** | Editable text field for the transform function — auto-populated when selecting from dropdown |

Click **+ Field** to add rows. Drag source fields from the left panel onto Source Path inputs.

#### Tables Mode (RDBMS)

Shown when output mode is RDBMS. If tables were fetched in Step 2, they are pre-populated with column names, primary keys, and foreign key relationships.

Each table has its own tab with:

| Section | Fields |
|---|---|
| **Table Settings** | Table name, primary key, on-delete behavior |
| **Parent / FK** | Parent table, source array, FK column, FK references, replace strategy |
| **Column Mappings** | Column name → source path → transform (same dropdown + editable input) |

Click **+ Table** to add tables, **+ Column** to add column mappings.

### Transform Functions

All 58 transform functions are available in the dropdown, organized into 6 categories:

- **String** (19) — `trim`, `lowercase`, `uppercase`, `concat`, `replace`, etc.
- **Numeric** (9) — `to_int`, `to_float`, `to_decimal`, `round`, etc.
- **Date / Time** (9) — `to_iso8601`, `to_epoch`, `from_epoch`, `format_date`, etc.
- **Array / Object** (4) — `flatten`, `slice`, `keys`, `values`
- **Encoding / Hash** (8) — `json_safe`, `base64_encode`, `md5`, `sha256`, etc.
- **Conditional** (1) — `if`

Selecting a transform from the dropdown auto-injects the source path into the function (e.g., selecting `trim()` with source path `$.name` → `trim($.name)`).

### Saving

- **💾 Save & Apply Config** — Generates a complete `config.json` from all wizard state and saves it via `PUT /api/config`. The worker will use this config on next restart.
- **💾 Save Mapping** — Saves the field/table mapping as `{match_value}_mapping.json` via `PUT /api/mappings/{name}`.

The **Generated config.json** collapsible section at the bottom shows a live preview of the complete configuration that will be saved.

---

## API Endpoints

### Wizard-Specific

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/wizard/test-source` | Test SG/App Services/Edge Server connectivity and return a random sample doc |
| `POST` | `/api/wizard/test-output` | Test HTTP output endpoint reachability (HEAD request) |

### Reused from Existing APIs

| Method | Path | Used In |
|---|---|---|
| `GET` | `/api/db/drivers` | Step 2 — populate RDBMS engine dropdown |
| `POST` | `/api/db/test` | Step 2 — test RDBMS connection |
| `POST` | `/api/db/introspect` | Step 2 — fetch table schemas |
| `PUT` | `/api/config` | Step 3 — save generated config |
| `PUT` | `/api/mappings/{name}` | Step 3 — save mapping file |

### Request / Response Examples

#### `POST /api/wizard/test-source`

**Request:**
```json
{
  "gateway": {
    "src": "sync_gateway",
    "url": "http://localhost:4984",
    "database": "db",
    "scope": "us",
    "collection": "prices",
    "accept_self_signed_certs": false
  },
  "auth": {
    "method": "basic",
    "username": "bob",
    "password": "password"
  }
}
```

**Success Response:**
```json
{
  "ok": true,
  "doc": { "_id": "order::1001", "type": "order", "status": "shipped", ... },
  "pool_size": 100
}
```

**Error Response:**
```json
{
  "error": "fetch_failed",
  "detail": "Cannot connect to host localhost:4984 ssl:default ..."
}
```

#### `POST /api/wizard/test-output`

**Request:**
```json
{
  "target_url": "http://localhost:8000/api/docs",
  "accept_self_signed_certs": false,
  "auth": {
    "method": "none"
  }
}
```

**Success Response:**
```json
{
  "ok": true,
  "status": 200,
  "content_type": "application/json"
}
```

---

## Cloud Storage Wizard

A 6-question guided Q&A that helps users understand and configure cloud storage for attachments and/or JSON archival.

### Questions

| # | Question | Answers → Config Effect |
|---|---|---|
| 1 | **What do you want to store?** | Attachments only → `use_case = "attachments"`, Archived JSON → `"archive"`, Both → `"both"` |
| 2 | **Which cloud provider?** | S3 → `provider = "s3"`, GCS → `"gcs"`, Azure → `"azure"`, Local → `"local"` |
| 3 | **What is your throughput?** | Low → `max_concurrent = 1`, Medium → `5`, High → `20` |
| 4 | **File retention strategy?** | Forever → no expiry, TTL → `ttl_days = 90`, Manual → organize by folder structure |
| 5 | **Archive compression?** | None → `compression = "none"`, GZIP → `"gzip"`, ZSTD → `"zstd"` |
| 6 | **File organization?** | Flat → `partitioning = "flat"`, By date → `"date_daily"`, By date+type → `"date_type"`, By doc ID → `"doc_id"` |

### Summary Page

After all questions are answered, users see:

1. **Your Choices** — A recap of all selections
2. **Your Cloud Storage Story** — A human-readable narrative explaining:
   - What will be stored and where
   - Performance characteristics
   - Retention strategy
   - File organization approach
3. **Generated Config Preview** — The cloud storage configuration section that will be saved
4. **Save & Apply Config** — Merges the config and saves it

---

## RDBMS Schema Import Wizard

A simple drag-and-drop interface to upload SQL `CREATE TABLE` definitions for schema discovery and validation.

### Features

- **Drag & Drop**: Drop `.sql` or `.txt` files directly onto the upload zone
- **Copy & Paste**: Manually paste SQL CREATE TABLE statements into a textarea
- **SQL Formatting**: Uses local sql-formatter library to beautify SQL
  - Supports all major SQL dialects (PostgreSQL, MySQL, Oracle, SQL Server, etc.)
  - UPPERCASE keywords, standard indentation, 2-space tabs
  - Works fully offline (no CDN dependencies)
- **Dialect Detection**: Automatically detects SQL dialect from keywords
  - PostgreSQL: SERIAL, BYTEA, UUID, JSONB, ARRAY, etc.
  - MySQL: AUTO_INCREMENT, CHARSET, UNSIGNED, FULLTEXT, etc.
  - SQL Server: IDENTITY, NVARCHAR, DATETIME2, UNIQUEIDENTIFIER, etc.
  - Oracle: NUMBER, VARCHAR2, SEQUENCE, NEXTVAL, etc.
- **Table Extraction**: Automatically detects and lists all `CREATE TABLE` statements
- **Database Naming**: User provides a database/schema name
- **Schema Document Storage**: Saves to Couchbase with document key `rdbms_schema` and includes detected dialect

### Document Structure

Saved schema is stored as:

```json
{
  "type": "rdbms",
  "dialect": "postgresql",
  "data": {
    "my_database": {
      "users": {
        "sql": "CREATE TABLE users (id SERIAL PRIMARY KEY, name VARCHAR(255), created_at TIMESTAMP, ...)",
        "dateTime": "2026-04-19T12:34:56.789Z"
      },
      "orders": {
        "sql": "CREATE TABLE orders (id SERIAL, user_id INT REFERENCES users(id), total DECIMAL(10, 2), ...)",
        "dateTime": "2026-04-19T12:34:57.123Z"
      }
    }
  }
}
```

The `dialect` field is auto-detected from SQL keywords and helps the system understand:
- Data type mapping (e.g., PostgreSQL `SERIAL` → auto-increment)
- Constraint syntax variations
- Index creation patterns
- Function availability

This allows the system to understand RDBMS table structures for:
- Schema mapping validation
- Table introspection during Step 2 of Schema Mapping Wizard
- Data type inference for transforms
- Foreign key relationship discovery

---

## Jobs Manager

The wizard includes a jobs manager interface for controlling pipeline jobs. The jobs list displays all configured v2 jobs with inline controls.

### Job List

Each job row shows:

| Element | Description |
|---|---|
| **Job Name / ID** | The job identifier |
| **Status Badge** | Color-coded: green = running, grey = stopped, red = error |
| **▶ Start** | Start the job — calls `POST /api/jobs/{id}/start` |
| **⏹ Stop** | Stop the job — calls `POST /api/jobs/{id}/stop` |
| **⋯ Overflow Menu** | Additional actions (see below) |

### Overflow Menu Actions

| Action | API Endpoint |
|---|---|
| **Refresh Input** | `POST /api/v2/jobs/{id}/refresh-input` |
| **Refresh Output** | `POST /api/v2/jobs/{id}/refresh-output` |
| **Restart** | `POST /api/jobs/{id}/restart` |
| **Edit** | Opens the job editor |
| **Delete** | `DELETE /api/v2/jobs/{id}` |

---

## API Endpoint Reference

The wizard uses the following corrected API endpoints for managing inputs, outputs, and jobs.

### Inputs

| Method | Path | Response |
|---|---|---|
| `GET` | `/api/inputs_changes` | `{"src": [...]}` — list of configured change feed inputs |

### Outputs

| Method | Path | Response |
|---|---|---|
| `GET` | `/api/outputs_{type}` | `{"src": [...]}` — list of configured outputs for the given type (e.g., `/api/outputs_http`, `/api/outputs_rdbms`) |

### Jobs (v2)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v2/jobs` | List all jobs |
| `POST` | `/api/v2/jobs` | Create a new job |
| `PUT` | `/api/v2/jobs/{id}` | Update an existing job |
| `DELETE` | `/api/v2/jobs/{id}` | Delete a job |

### Job Control

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs/{id}/start` | Start a job |
| `POST` | `/api/jobs/{id}/stop` | Stop a job |
| `POST` | `/api/jobs/{id}/restart` | Restart a job |
| `POST` | `/api/v2/jobs/{id}/refresh-input` | Refresh the job's input configuration |
| `POST` | `/api/v2/jobs/{id}/refresh-output` | Refresh the job's output configuration |

---

## Typical Workflow

### Recommended Order (Based on Grid Layout)

1. **Navigate to `/wizard`** — Land on 2×2 grid with 4 wizard options

2. **⚙️ Settings Wizard (top-left)** ⭐ START HERE
   - Answer 8 questions about your pipeline settings
   - Review your configuration story
   - Save optimized settings

3. **🗄️ RDBMS Schema Import (top-right)** ⭐ OPTIONAL (if using RDBMS targets)
   - Upload `.sql` files or paste CREATE TABLE statements
   - Auto-detects RDBMS dialect (PostgreSQL, MySQL, Oracle, SQL Server)
   - Stores schema definitions for introspection in Step 2 of Schema Mapping

4. **🗂️ Schema Mapping Wizard (bottom-left)** ⭐ CORE WORKFLOW
   - Step 1: Connect to source (_changes feed)
   - Step 2: Configure output destination (Stdout, HTTP, RDBMS, S3, etc.)
   - Step 3: Map source fields to output schema with transforms
   - Save mapping and config

5. **☁️ Cloud Storage Wizard (bottom-right)** ⭐ OPTIONAL (if using cloud storage)
   - Configure attachment extraction strategies
   - Configure JSON archival for cold storage
   - Auto-generate optimized storage config

6. **Restart the worker** to pick up the new configuration
