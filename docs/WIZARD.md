# Setup Wizard

A 3-step guided wizard for configuring the entire Changes Worker pipeline — from connecting a Sync Gateway / Capella App Services / Edge Server / CouchDB `_changes` feed, through choosing an output destination, to mapping source document fields onto the target format.

**URL:** `/wizard`

**Related docs:**
- [`ADMIN_UI.md`](ADMIN_UI.md) — Dashboard, Config Editor, Schema Mappings, Transforms
- [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) — Mapping definition format, transforms, JSONPath syntax
- [`DESIGN.md`](DESIGN.md) — Architecture & failure modes

---

## Overview

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
| **SSL** | Enable SSL connections |

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

## Typical Workflow

1. Navigate to `/wizard`
2. **Step 1:** Enter your SG/App Services/Edge Server URL and credentials → click **Test Connection** → verify sample doc appears
3. **Step 2:** Choose output mode → for HTTP: enter target URL and test → for RDBMS: enter DB credentials, test connection, fetch tables
4. **Step 3:** Define source match rule → drag source fields onto mapping inputs → add transforms as needed → click **Save & Apply Config** and **Save Mapping**
5. Restart the worker to pick up the new configuration
