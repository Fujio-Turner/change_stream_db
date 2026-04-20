# Phase 4 Quick Reference

## What Was Built

**Phase 4: Outputs Wizard UI** — Manage 4 output types in a tabbed interface.

### 4 Output Types
1. **RDBMS** — PostgreSQL, MySQL, MSSQL, Oracle
2. **HTTP** — REST webhooks
3. **Cloud** — S3, GCS, Azure
4. **Stdout** — Console/log output

---

## Files

| File | Change | Size |
|------|--------|------|
| `tests/test_api_v2_outputs.py` | **Created** | 260 lines, 12 tests |
| `web/templates/wizard.html` | **Modified** | +377 HTML, +332 JS |
| `docs/DESIGN_2_0.md` | **Updated** | Status: Phase 4 ✅ |
| `rest/api_v2.py` | **Already done** (Phase 3) | 8 endpoints |

---

## JavaScript Functions

### Entry & Navigation
- `startOutputsWizard()` — Open wizard
- `switchOutputTab(type)` — Switch between tabs

### Load & Render
- `outputsLoadExisting(type)` — Fetch & display table

### Add New
- `outputsRdbmsAddNew()` → `outputsRdbmsSave()`
- `outputsHttpAddNew()` → `outputsHttpSave()`
- `outputsCloudAddNew()` → `outputsCloudSave()`
- `outputsStdoutAddNew()` → `outputsStdoutSave()`

### Edit & Delete
- `outputsEdit(outId, type)` — Populate form for edit
- `outputsDelete(outId, type)` — Delete with confirm
- `outputsClearAll(type)` — Clear all of type

### Save Handler
- `outputsSaveEntry(type, entry)` — Generic POST handler

### Cancel
- `outputsCancel(type)` — Hide form

---

## API Endpoints

All endpoints are in `rest/api_v2.py` and ready:

```
GET    /api/outputs_{type}           → Load all outputs of type
POST   /api/outputs_{type}           → Save (POST entire array)
PUT    /api/outputs_{type}/{id}      → Update one output
DELETE /api/outputs_{type}/{id}      → Delete one output
```

Where `{type}` ∈ {`rdbms`, `http`, `cloud`, `stdout`}

---

## HTML Structure

```
<div id="outputsWizard">
  <!-- Tab bar: RDBMS | HTTP | Cloud | Stdout -->
  
  <div id="outPanelRdbms">
    <!-- Table of existing RDBMS outputs -->
    <!-- Form for add/edit RDBMS output -->
  </div>
  
  <div id="outPanelHttp"> ... </div>
  <div id="outPanelCloud"> ... </div>
  <div id="outPanelStdout"> ... </div>
</div>
```

---

## Testing

### Run Outputs Tests
```bash
pytest tests/test_api_v2_outputs.py -v
```

### Run Inputs + Outputs Tests
```bash
pytest tests/test_api_v2_inputs.py tests/test_api_v2_outputs.py -v
```

### Run All
```bash
pytest tests/ -v
```

**Current Status:** ✅ 724 tests passing

---

## Field Schemas

### RDBMS Output
```json
{
  "id": "pg-prod",
  "name": "Production PostgreSQL",
  "enabled": true,
  "engine": "postgres",
  "host": "db.example.com",
  "port": 5432,
  "database": "mydb",
  "username": "user",
  "password": "pass",
  "pool_max": 10
}
```

### HTTP Output
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

### Cloud Output
```json
{
  "id": "s3-prod",
  "name": "Production S3",
  "enabled": true,
  "provider": "s3",
  "region": "us-east-1",
  "bucket": "my-bucket",
  "prefix": "changes/"
}
```

### Stdout Output
```json
{
  "id": "stdout-dev",
  "name": "Development Stdout",
  "enabled": true,
  "pretty_print": true
}
```

---

## Validation

Each output type validates required fields:

| Type | Required Fields |
|------|-----------------|
| RDBMS | `id`, `engine`, `host` |
| HTTP | `id`, `target_url` |
| Cloud | `id`, `bucket` |
| Stdout | `id` |

All validations happen client-side on form save and server-side on API POST/PUT.

---

## Next Steps: Phase 5

Phase 5 will add **Jobs Wizard** to connect inputs → outputs:

```
Input (source)
    ↓
Job (ties input + output + mapping)
    ↓
Output (destination)
```

Phases 1-4 lay the groundwork. Phase 5 brings it all together.

