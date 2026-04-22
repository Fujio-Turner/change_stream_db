# UI & API Inventory — Pre-Refactor Snapshot

> Generated 2026-04-21. Complete audit of every page, every API call, every form field, and every user action before the Inputs/Outputs page refactor.

---

## Table of Contents

1. [Sidebar Navigation](#1-sidebar-navigation)
2. [Dashboard `/`](#2-dashboard-)
3. [Job Builder `/jobs`](#3-job-builder-jobs)
4. [Schema Mapping `/schema`](#4-schema-mapping-schema)
5. [Wizards `/wizard`](#5-wizards-wizard)
6. [Dead Letters `/dlq`](#6-dead-letters-dlq)
7. [Logs `/logs`](#7-logs-logs)
8. [Settings `/settings`](#8-settings-settings)
9. [Glossary `/glossary`](#9-glossary-glossary)
10. [Help `/help`](#10-help-help)
11. [Full API Route Registry](#11-full-api-route-registry)
12. [Data Shapes (JSON schemas saved to CBL)](#12-data-shapes)

---

## 1. Sidebar Navigation

**File:** `web/static/js/sidebar.js`

| Section    | Label           | Route       | Icon                      |
|------------|-----------------|-------------|---------------------------|
| Overview   | Dashboard       | `/`         | `/static/icons/dashboard.svg` |
| Tools      | Job Builder     | `/jobs`     | `/static/icons/jobs.svg`     |
| Tools      | Schema Mapping  | `/schema`   | `/static/icons/schema.svg`   |
| Tools      | Wizards         | `/wizard`   | `/static/icons/wizard.svg`   |
| *(divider)*|                 |             |                           |
| System     | Dead Letters    | `/dlq`      | `/static/icons/dlq.svg`      |
| System     | Logs            | `/logs`     | `/static/icons/logs.svg`     |
| System     | Settings        | `/settings` | `/static/icons/settings.svg` |
| Reference  | Glossary        | `/glossary` | `/static/icons/book.svg`     |
| Reference  | Help            | `/help`     | `/static/icons/help.svg`     |

**Sidebar Footer Controls:**
- Online/Offline toggle → `POST /api/offline`, `POST /api/online`, polls `GET /api/worker-status`
- Restart → `POST /api/restart`
- Shutdown → `POST /api/shutdown`
- Theme toggle (dark/light, stored in `localStorage`)

---

## 2. Dashboard `/`

**File:** `web/templates/index.html` | **Handler:** `page_index`

### API Calls

| API Endpoint                         | Method | Purpose                                      |
|--------------------------------------|--------|----------------------------------------------|
| `/api/status`                        | GET    | System status (feed, process, CBL, output)   |
| `/api/config`                        | GET    | Load config for architecture diagram labels  |
| `/api/v2/jobs`                       | GET    | List all v2 job documents                    |
| `/api/v2/jobs/{id}`                  | GET    | Load individual job for detail               |
| `/api/jobs/status`                   | GET    | Job runtime status (running/stopped/uptime)  |
| `/api/metrics`                       | GET    | Prometheus-style counters & gauges           |
| `/api/jobs/{job_id}/start`           | POST   | Start a specific job                         |
| `/api/jobs/{job_id}/stop`            | POST   | Stop a specific job                          |
| `/api/dlq/meta`                      | GET    | DLQ metadata for architecture node           |
| `/api/dlq/count`                     | GET    | DLQ count for badge display                  |

### UI Features

- **Status Bar:** 4 status dots (Changes Feed, Processing, CBL, Output) + Job Selector dropdown
- **Config Badges:** Source type counts (SG, AppSer, Edge, CouchDB), Process types, Output types
- **Architecture Diagram:** Live SVG node graph (Source → Worker/DLQ → Attachments → Outputs)
  - Clickable nodes open detail modals with ECharts charts
  - Nodes dim/highlight based on active output mode
  - Live rate display (docs/s)
- **Pipeline Health:** Summary ribbon (jobs, docs, errors, DLQ) + per-job health cards
  - Each card: funnel bar, sparkline chart, key stats, start/stop controls
- **Job Selector:** Filter dashboard to single job or ALL

### No Create/Edit capabilities — read-only monitoring page

---

## 3. Job Builder `/jobs`

**File:** `web/templates/jobs.html` | **Handler:** `page_jobs`

### API Calls

| API Endpoint                               | Method | Purpose                                    |
|--------------------------------------------|--------|--------------------------------------------|
| `/api/jobs/status`                         | GET    | Load all jobs with runtime status          |
| `/api/inputs_changes`                      | GET    | Load all input resources (for source picker)|
| `/api/outputs_{type}` (rdbms/http/cloud/stdout) | GET | Load outputs per type (for output picker) |
| `/api/wizard/test-source`                  | POST   | Test connectivity to a source              |
| `/api/inputs_changes/{id}`                 | DELETE | Delete a single input resource             |
| `/api/outputs_{type}/{id}`                 | DELETE | Delete a single output resource            |
| `/api/wizard/test-output`                  | POST   | Test connectivity to an output             |
| `/api/mappings`                            | GET    | Load mapping files for dropdown            |
| `/api/v2/jobs`                             | POST   | Create new job                             |
| `/api/v2/jobs/{id}`                        | GET    | Load job for editing                       |
| `/api/v2/jobs/{id}`                        | PUT    | Update existing job                        |
| `/api/v2/jobs/{id}`                        | DELETE | Delete a job                               |
| `/api/jobs/{job_id}/start`                 | POST   | Start job                                  |
| `/api/jobs/{job_id}/stop`                  | POST   | Stop job (graceful)                        |
| `/api/jobs/{job_id}/restart`               | POST   | Restart job                                |
| `/api/jobs/{job_id}/kill`                  | POST   | Kill job (non-graceful)                    |

### UI Sections

#### Jobs Table
- Columns: Status, Job Name, Source Name/Type, Process, Output Name/Type, Last Run Date, Threads, Actions
- Actions: Start, Stop, Restart, Kill, Edit, Delete

#### Job Builder Panel (3-column layout)
- **Progress Steps:** Source Selected ✓/✕ → Process Configured ✓/✕ → Output Selected ✓/✕
- **Job Name** input + Save/Cancel buttons

##### Source Card (green border)
- Source type picker: Sync Gateway, App Services, Edge Server, CouchDB
- Each shows count badge (from `/api/inputs_changes`)
- Drill-down table: Name, In Job, Select, Actions (Test, Edit, Delete)
- `+ New` button → **opens `/wizard` in new tab** ⚠️ (no context passed)
- `Edit` button → **opens `/wizard` in new tab** ⚠️ (no ID passed)
- `Test` button → calls `/api/wizard/test-source` with source config
- `Delete` button → calls `DELETE /api/inputs_changes/{id}`

##### Process Card (blue border)
- Radio: Data Only / Data & Attachments
- Mapping File dropdown (from `/api/mappings`)
- Threads input
- Attachment Settings (max size, store inline) — shown only when DA selected

##### Output Card (amber border)
- Output type picker: RDBMS, HTTP, Cloud Storage, Stdout
- Each shows count badge (from `/api/outputs_{type}`)
- Drill-down table varies by type:
  - **HTTP:** Name, Target URL, Methods, Format, Auth, In Job, Actions
  - **RDBMS:** Name, In Job, Select, Tables(N), Actions
  - **Cloud:** Name, Provider/Region, Bucket, In Job, Actions
- `+ New` button → **opens `/wizard` in new tab** ⚠️ (no context passed)
- `Edit` button → **opens `/wizard` in new tab** ⚠️ (no ID passed)
- RDBMS Tables panel: Table Name, Columns, Primary Key, In Job, Actions (View DDL, Edit, Remove)

##### Advanced Section (collapsed)
- Feed Type: longpoll / normal / continuous / websocket / sse / eventsource
- Poll Interval (sec), Heartbeat (ms), Timeout (ms)
- HTTP Timeout (sec), Throttle Feed (ms), Limit
- Flood Threshold, Catchup Limit
- Active Only checkbox, Include Docs checkbox, Optimize Initial Sync checkbox
- Channel Filter (Tagify input, shown for couchdb/app_services/sync_gateway)

### What's MISSING on this page
- ❌ Cannot CREATE inputs (only select existing or open wizard in new tab)
- ❌ Cannot CREATE outputs (only select existing or open wizard in new tab)
- ❌ Cannot EDIT inputs inline (Edit opens wizard with no context)
- ❌ Cannot EDIT outputs inline (Edit opens wizard with no context)
- ❌ No "Refresh Input/Output" button for running jobs (API exists but no UI)
- ❌ `findJobsUsing()` always returns `[]` (stub)

---

## 4. Schema Mapping `/schema`

**File:** `web/templates/schema.html` | **Handler:** `page_schema`

### API Calls

| API Endpoint                          | Method | Purpose                                |
|---------------------------------------|--------|----------------------------------------|
| `/api/sample-doc`                     | GET    | Fetch a sample document from source    |
| `/api/mappings`                       | GET    | List all mapping files                 |
| `/api/mappings/{name}`               | GET    | Load a specific mapping file           |
| `/api/mappings/{name}`               | PUT    | Save/update a mapping file             |
| `/api/mappings/{name}`               | DELETE | Delete a mapping file                  |
| `/api/mappings/{name}/active`        | PATCH  | Toggle mapping active/inactive         |
| `/api/mappings/validate`             | POST   | Validate mapping against sample doc    |

### UI Features
- Mapping file list with active/inactive toggle
- Visual field mapping editor (drag source fields → target columns)
- SQL preview with EXPLAIN validation
- Sample doc viewer

---

## 5. Wizards `/wizard`

**File:** `web/templates/wizard.html` (5,675 lines) | **Handler:** `page_wizard`

### Landing Page — 8 Wizard Cards

| Wizard Card      | Function                  | Creates/Modifies            |
|------------------|---------------------------|-----------------------------|
| 📥 Inputs        | `startInputsWizard()`    | `inputs_changes` doc (CBL)  |
| 📤 Outputs       | `startOutputsWizard()`   | `outputs_{type}` docs (CBL) |
| ⚙️ Settings      | `startSettingsWizard()`  | `config.json` (guided Q&A)  |
| 📡 Data Source   | `startSourceWizard()`    | `sources/` dir (legacy)     |
| 🗄️ RDBMS        | `startRdbmsSchemaWizard()` | DB introspection          |
| ☁️ Cloud Storage | `startCloudStorageWizard()` | `config.json` (guided Q&A)|
| 🗂️ Schema Mapping| `startMappingWizard()`   | `mappings/` dir             |
| 🎯 Jobs          | `startJobsManager()`     | Redirects to `/jobs`        |

### Inputs Wizard — API & Fields

**API Calls:**
- `GET /api/inputs_changes` — Load existing inputs
- `POST /api/inputs_changes` — Save full inputs document (array replace)
- (Delete: filters array and re-POSTs)

**Form Fields (Input Configuration):**

| Field           | HTML ID            | Required | Default     |
|-----------------|--------------------|----------|-------------|
| Input ID        | `inputsId`         | ✅        |             |
| Name            | `inputsName`       |          |             |
| Source Type     | `inputsSourceType` | ✅        | (dropdown)  |
| Host            | `inputsHost`       | ✅        |             |
| Database        | `inputsDatabase`   |          |             |
| Scope           | `inputsScope`      |          |             |
| Collection      | `inputsCollection` |          |             |
| Auth Method     | `inputsAuthMethod` |          | basic       |
| Username        | `inputsAuthUser`   |          |             |
| Password        | `inputsAuthPass`   |          |             |

**Pre-optimized defaults injected on save:**
```json
{
  "enabled": true,
  "changes_feed": {
    "feed_type": "longpoll",
    "poll_interval_seconds": 10,
    "active_only": true,
    "include_docs": false
  }
}
```

**Table columns shown:** Input ID, Name, Type, Host, Actions (Edit, Delete)

### Outputs Wizard — 4 Sub-Tabs

#### RDBMS Output Fields

| Field              | HTML ID                         | Required | Default |
|--------------------|---------------------------------|----------|---------|
| Output ID          | `outRdbmsId`                    | ✅        |         |
| Name               | `outRdbmsName`                  |          |         |
| Engine             | `outRdbmsEngine`                | ✅        | (dropdown: postgres/mysql/mssql/oracle) |
| Host               | `outRdbmsHost`                  | ✅        |         |
| Port               | `outRdbmsPort`                  |          | 5432    |
| Database           | `outRdbmsDatabase`              |          |         |
| Username           | `outRdbmsUser`                  |          |         |
| Password           | `outRdbmsPass`                  |          |         |
| Schema             | `outRdbmsSchema`                |          | public  |
| Pool Min           | `outRdbmsPoolMin`               |          | 2       |
| Pool Max           | `outRdbmsPoolMax`               |          | 10      |
| SSL                | `outRdbmsSsl`                   |          | false   |
| Enabled            | `outRdbmsEnabled`               |          | true    |
| Validation Enabled | `outRdbmsValidationEnabled`     |          | false   |
| Validation Strict  | `outRdbmsValidationStrict`      |          | false   |
| Track Originals    | `outRdbmsValidationTrackOriginals` |       | true    |
| DLQ on Error       | `outRdbmsValidationDlq`         |          | true    |

**API:** `GET/POST /api/outputs_rdbms`  
**Table columns:** ID, Name, Engine, Host:Port, Actions

#### HTTP Output Fields

| Field            | HTML ID           | Required | Default |
|------------------|-------------------|----------|---------|
| Output ID        | `outHttpId`       | ✅        |         |
| Name             | `outHttpName`     |          |         |
| Target URL       | `outHttpUrl`      | ✅        |         |
| HTTP Method      | `outHttpMethod`   |          | POST    |
| Timeout (sec)    | `outHttpTimeout`  |          | 30      |
| Retry Count      | `outHttpRetry`    |          | 3       |
| Enabled          | `outHttpEnabled`  |          | true    |

**API:** `GET/POST /api/outputs_http`  
**Table columns:** ID, Name, Target URL, Method, Actions

#### Cloud Output Fields

| Field         | HTML ID            | Required | Default |
|---------------|--------------------|----------|---------|
| Output ID     | `outCloudId`       | ✅        |         |
| Name          | `outCloudName`     |          |         |
| Provider      | `outCloudProvider` |          | s3      |
| Region        | `outCloudRegion`   |          |         |
| Bucket        | `outCloudBucket`   | ✅        |         |
| Prefix        | `outCloudPrefix`   |          |         |
| Enabled       | `outCloudEnabled`  |          | true    |

**API:** `GET/POST /api/outputs_cloud`  
**Table columns:** ID, Name, Provider, Bucket, Actions

#### Stdout Output Fields

| Field        | HTML ID            | Required | Default |
|--------------|--------------------|----------|---------|
| Output ID    | `outStdoutId`      | ✅        |         |
| Name         | `outStdoutName`    |          |         |
| Format       | `outStdoutFormat`  |          | json    |
| Enabled      | `outStdoutEnabled` |          | true    |

**API:** `GET/POST /api/outputs_stdout`  
**Table columns:** ID, Name, Format, Actions

### Data Source Wizard (Legacy)

**API Calls:**
- `GET /api/source/list`
- `POST /api/source/save`
- `POST /api/source/delete`
- `POST /api/source/clear`
- `POST /api/source/test`

### Settings Wizard (Guided Q&A)
- 8 guided questions about pipeline behavior
- Generates optimized `config.json` settings
- **API:** `GET /api/config`, `PUT /api/config`

### Cloud Storage Wizard (Guided Q&A)
- 6 guided questions about cloud storage strategy
- **API:** `GET /api/config`, `PUT /api/config`

### RDBMS Schema Wizard
- **API:** `POST /api/db/test`, `POST /api/db/introspect`, `GET /api/schema/rdbms`

### Schema Mapping Wizard
- Full mapping builder (overlaps with `/schema` page)
- **API:** Same as Schema Mapping page

---

## 6. Dead Letters `/dlq`

**File:** `web/templates/dlq.html` | **Handler:** `page_dlq`

### API Calls

| API Endpoint                   | Method | Purpose                         |
|--------------------------------|--------|---------------------------------|
| `/api/dlq/stats`               | GET    | DLQ statistics                  |
| `/api/dlq`                     | GET    | List DLQ entries (paginated)    |
| `/api/dlq/{id}`                | GET    | Get single DLQ entry detail     |
| `/api/dlq/{id}`                | DELETE | Delete single DLQ entry         |
| `/api/dlq`                     | DELETE | Clear all DLQ entries           |
| `/api/dlq/{id}/retry`          | POST   | Retry a single DLQ entry        |
| `/api/dlq/replay`              | POST   | Replay all DLQ entries          |
| `/api/jobs`                    | GET    | Load jobs for filter dropdown   |
| `/api/data_quality`            | GET    | Data quality issues list        |
| `/api/data_quality/{id}`       | GET    | Data quality detail             |
| `/api/data_quality/{id}`       | DELETE | Delete data quality entry       |
| `/api/audit_log`               | GET    | Audit log entries               |

---

## 7. Logs `/logs`

**File:** `web/templates/logs.html` | **Handler:** `page_logs`

### API Calls

| API Endpoint            | Method | Purpose                          |
|-------------------------|--------|----------------------------------|
| `/api/logs`             | GET    | Fetch parsed log entries         |
| `/api/log-files`        | GET    | List available log files         |
| `/api/jobs`             | GET    | Load jobs for filter dropdown    |
| `/api/{online\|offline}` | POST  | Toggle worker online/offline     |
| `/api/worker-status`    | GET    | Check worker online state        |

---

## 8. Settings `/settings`

**File:** `web/templates/settings.html` | **Handler:** `page_config`

### API Calls

| API Endpoint              | Method | Purpose                           |
|---------------------------|--------|-----------------------------------|
| `/api/config`             | GET    | Load full config.json             |
| `/api/config`             | PUT    | Save config.json                  |
| `/api/wizard/test-source` | POST   | Test source connectivity          |
| `/api/wizard/test-output` | POST   | Test HTTP output connectivity     |
| `/api/db/test`            | POST   | Test RDBMS connectivity           |
| `/api/cloud/test`         | POST   | Test cloud storage connectivity   |
| `/api/maintenance`        | POST   | Trigger maintenance operations    |

### Note
- Source/Gateway/Auth/Changes Feed/Output tabs are **HIDDEN** (Phase 7 migration)
- Info banner says: "Use the Wizard to create and manage jobs instead"
- Only **infrastructure** settings remain: Logging, Metrics, Admin UI, CBL Storage

---

## 9. Glossary `/glossary`

**File:** `web/templates/glossary.html` | **Handler:** `page_transforms`

- Static reference page, no API calls
- Lists field transform functions available in schema mappings

---

## 10. Help `/help`

**File:** `web/templates/help.html` | **Handler:** `page_help`

- Static reference page, no API calls

---

## 11. Full API Route Registry

**File:** `web/server.py` (lines 2175–2289)

### Page Routes
| Method | Path         | Handler            |
|--------|--------------|---------------------|
| GET    | `/`          | `page_index`        |
| GET    | `/settings`  | `page_config`       |
| GET    | `/jobs`      | `page_jobs`         |
| GET    | `/schema`    | `page_schema`       |
| GET    | `/glossary`  | `page_transforms`   |
| GET    | `/wizard`    | `page_wizard`       |
| GET    | `/help`      | `page_help`         |
| GET    | `/logs`      | `page_logs`         |
| GET    | `/dlq`       | `page_dlq`          |

### API v1 Routes
| Method | Path                                 | Handler                |
|--------|--------------------------------------|------------------------|
| GET    | `/api/logs`                          | `get_logs`             |
| GET    | `/api/log-files`                     | `get_log_files`        |
| GET    | `/api/config`                        | `get_config`           |
| PUT    | `/api/config`                        | `put_config`           |
| GET    | `/api/mappings`                      | `list_mappings`        |
| GET    | `/api/mappings/{name}`               | `get_mapping`          |
| PUT    | `/api/mappings/{name}`               | `put_mapping`          |
| PATCH  | `/api/mappings/{name}/active`        | `patch_mapping_active` |
| DELETE | `/api/mappings/{name}`               | `delete_mapping`       |
| POST   | `/api/mappings/validate`             | `validate_mapping`     |
| GET    | `/api/dlq`                           | `list_dlq`             |
| GET    | `/api/dlq/count`                     | `dlq_count`            |
| GET    | `/api/dlq/meta`                      | `dlq_meta`             |
| GET    | `/api/dlq/stats`                     | `dlq_stats`            |
| GET    | `/api/dlq/explain`                   | `dlq_explain`          |
| POST   | `/api/dlq/replay`                    | `replay_dlq`           |
| GET    | `/api/dlq/{id}`                      | `get_dlq_entry`        |
| POST   | `/api/dlq/{id}/retry`                | `retry_dlq_entry`      |
| DELETE | `/api/dlq/{id}`                      | `delete_dlq_entry`     |
| DELETE | `/api/dlq`                           | `clear_dlq`            |
| POST   | `/api/maintenance`                   | `post_maintenance`     |
| GET    | `/api/status`                        | `get_status`           |
| GET    | `/api/jobs`                          | `get_jobs`             |
| GET    | `/api/jobs/status`                   | `get_jobs_status`      |
| GET    | `/api/metrics`                       | `get_metrics`          |
| POST   | `/api/restart`                       | `post_restart`         |
| POST   | `/api/shutdown`                      | `post_shutdown`        |
| POST   | `/api/offline`                       | `post_offline`         |
| POST   | `/api/online`                        | `post_online`          |
| GET    | `/api/worker-status`                 | `get_worker_status`    |
| POST   | `/api/jobs/{job_id}/start`           | `post_job_start`       |
| POST   | `/api/jobs/{job_id}/stop`            | `post_job_stop`        |
| POST   | `/api/jobs/{job_id}/restart`         | `post_job_restart`     |
| POST   | `/api/jobs/{job_id}/kill`            | `post_job_kill`        |
| GET    | `/api/sample-doc`                    | `get_sample_doc`       |
| GET    | `/api/db/drivers`                    | `list_db_drivers`      |
| POST   | `/api/db/test`                       | `db_test_connection`   |
| POST   | `/api/db/introspect`                 | `db_introspect`        |
| POST   | `/api/db/parse-ddl`                  | `parse_ddl`            |
| POST   | `/api/auto-map`                      | `auto_map_columns`     |
| POST   | `/api/wizard/test-source`            | `wizard_test_source`   |
| POST   | `/api/wizard/test-output`            | `wizard_test_output`   |
| GET    | `/api/source/list`                   | `list_sources`         |
| POST   | `/api/source/save`                   | `save_source`          |
| POST   | `/api/source/delete`                 | `delete_source`        |
| POST   | `/api/source/clear`                  | `clear_all_sources`    |
| POST   | `/api/source/test`                   | `test_source`          |

### API v2 Routes (CBL-based)
| Method | Path                                        | Handler                        |
|--------|---------------------------------------------|--------------------------------|
| GET    | `/api/inputs_changes`                       | `api_get_inputs_changes`       |
| POST   | `/api/inputs_changes`                       | `api_post_inputs_changes`      |
| PUT    | `/api/inputs_changes/{id}`                  | `api_put_inputs_changes_entry` |
| DELETE | `/api/inputs_changes/{id}`                  | `api_delete_inputs_changes_entry` |
| GET    | `/api/outputs_{type}`                       | `api_get_outputs`              |
| POST   | `/api/outputs_{type}`                       | `api_post_outputs`             |
| PUT    | `/api/outputs_{type}/{id}`                  | `api_put_outputs_entry`        |
| DELETE | `/api/outputs_{type}/{id}`                  | `api_delete_outputs_entry`     |
| GET    | `/api/v2/jobs`                              | `api_get_jobs`                 |
| POST   | `/api/v2/jobs`                              | `api_post_jobs`                |
| GET    | `/api/v2/jobs/{id}`                         | `api_get_job`                  |
| PUT    | `/api/v2/jobs/{id}`                         | `api_put_job`                  |
| DELETE | `/api/v2/jobs/{id}`                         | `api_delete_job`               |
| POST   | `/api/v2/jobs/{id}/refresh-input`           | `api_refresh_job_input`        |
| POST   | `/api/v2/jobs/{id}/refresh-output`          | `api_refresh_job_output`       |
| GET    | `/api/v2/tables_rdbms`                      | `api_get_tables_rdbms`         |
| POST   | `/api/v2/tables_rdbms`                      | `api_post_tables_rdbms`        |
| GET    | `/api/v2/tables_rdbms/{id}`                 | `api_get_table_rdbms_entry`    |
| PUT    | `/api/v2/tables_rdbms/{id}`                 | `api_put_table_rdbms_entry`    |
| DELETE | `/api/v2/tables_rdbms/{id}`                 | `api_delete_table_rdbms_entry` |
| GET    | `/api/v2/tables_rdbms/{id}/used-by`         | `api_get_table_rdbms_used_by`  |

---

## 12. Data Shapes

### Input Entry (saved in `inputs_changes.src[]`)

```json
{
  "id": "sg-us-prices",
  "name": "US Prices Feed",
  "enabled": true,
  "source_type": "sync_gateway | app_services | edge_server | couchdb",
  "host": "http://localhost:4984",
  "database": "mydb",
  "scope": "_default",
  "collection": "_default",
  "auth": {
    "method": "basic | bearer | session | none",
    "username": "...",
    "password": "..."
  },
  "changes_feed": {
    "feed_type": "longpoll",
    "poll_interval_seconds": 10,
    "active_only": true,
    "include_docs": false
  }
}
```

### Table Definition Entry (saved in `tables_rdbms.tables[]`)

```json
{
  "id": "tbl-orders",
  "name": "orders",
  "engine_hint": "postgres",
  "sql": "CREATE TABLE IF NOT EXISTS orders (doc_id TEXT PRIMARY KEY, rev TEXT, status TEXT, total NUMERIC(10,2))",
  "columns": [
    { "name": "doc_id", "type": "TEXT", "primary_key": true, "nullable": false },
    { "name": "rev", "type": "TEXT", "primary_key": false, "nullable": true },
    { "name": "status", "type": "TEXT", "primary_key": false, "nullable": true },
    { "name": "total", "type": "NUMERIC(10,2)", "primary_key": false, "nullable": true }
  ],
  "parent_table": "",
  "foreign_key": {},
  "meta": {
    "created_at": "2026-04-20T10:00:00Z",
    "updated_at": "2026-04-22T14:30:00Z",
    "source": "ddl_upload | db_introspect | manual"
  }
}
```

### Output Entry — RDBMS (saved in `outputs_rdbms.src[]`)

```json
{
  "id": "pg-prod",
  "name": "Production Postgres",
  "enabled": true,
  "mode": "postgres",
  "engine": "postgres",
  "host": "localhost",
  "port": 5432,
  "database": "mydb",
  "username": "postgres",
  "password": "...",
  "schema": "public",
  "pool_min": 2,
  "pool_max": 10,
  "ssl": false,
  "tables": [],
  "validation": {
    "enabled": false,
    "strict": false,
    "track_originals": true,
    "dlq_on_error": true
  }
}
```

### Output Entry — HTTP (saved in `outputs_http.src[]`)

```json
{
  "id": "webhook-prod",
  "name": "Production Webhook",
  "enabled": true,
  "target_url": "https://api.example.com/webhook",
  "write_method": "POST",
  "timeout_seconds": 30,
  "retry_count": 3
}
```

### Output Entry — Cloud (saved in `outputs_cloud.src[]`)

```json
{
  "id": "s3-prod",
  "name": "Production S3",
  "enabled": true,
  "provider": "s3 | gcs | azure",
  "region": "us-east-1",
  "bucket": "my-bucket",
  "prefix": "changes/"
}
```

### Output Entry — Stdout (saved in `outputs_stdout.src[]`)

```json
{
  "id": "stdout-dev",
  "name": "Dev Console",
  "enabled": true,
  "pretty_print": true
}
```

### Job Document (saved as `job::{id}`)

```json
{
  "type": "job",
  "id": "uuid",
  "name": "My Sync Job",
  "inputs": [ { /* full copy of input entry */ } ],
  "outputs": [ { /* full copy of output entry */ } ],
  "output_type": "rdbms | http | cloud | stdout",
  "system": {
    "threads": 4,
    "attachments_enabled": false
  },
  "mapping": {},
  "state": {
    "status": "idle | running | error",
    "last_updated": null
  }
}
```

### Job Create Payload (POST /api/v2/jobs)

```json
{
  "name": "My Sync Job",
  "input_id": "sg-us-prices",
  "process_type": "D | DA",
  "output_id": "pg-prod",
  "output_type": "rdbms",
  "mapping_id": "mapping-name",
  "changes_feed": { /* overrides for input's changes_feed */ },
  "system": {
    "threads": 4,
    "attachments_enabled": false
  }
}
```

---

## APIs That Exist But Have NO UI

| API Endpoint                              | Purpose                                      |
|-------------------------------------------|----------------------------------------------|
| `PUT /api/inputs_changes/{id}`            | Update single input — **no UI calls this**   |
| `PUT /api/outputs_{type}/{id}`            | Update single output — **no UI calls this**  |
| `POST /api/v2/jobs/{id}/refresh-input`    | Re-copy input into job — **no UI calls this**|
| `POST /api/v2/jobs/{id}/refresh-output`   | Re-copy output into job — **no UI calls this**|
| `GET /api/db/drivers`                     | List available DB drivers — **no UI calls this** |
| `GET /api/dlq/explain`                    | Explain DLQ entry — **no UI calls this**     |
| `GET /api/v2/tables_rdbms`               | List all RDBMS table definitions — **no UI calls this yet** |
| `POST /api/v2/tables_rdbms`              | Save table definitions — **no UI calls this yet** |
| `GET /api/v2/tables_rdbms/{id}`          | Get single table definition — **no UI calls this yet** |
| `PUT /api/v2/tables_rdbms/{id}`          | Update single table — **no UI calls this yet** |
| `DELETE /api/v2/tables_rdbms/{id}`       | Delete single table — **no UI calls this yet** |
| `GET /api/v2/tables_rdbms/{id}/used-by`  | List jobs using a table — **no UI calls this yet** |
