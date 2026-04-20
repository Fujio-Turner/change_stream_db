# API v2.x Endpoints Fix - Summary

## Problem Found
After the 2.x update, **API v2 endpoints were not registered in the web server**. The handlers were implemented in `rest/api_v2.py` but never connected to the aiohttp application router.

## Root Cause
The `web/server.py` file's `create_app()` function was missing route registrations for:
- Inputs API (`/api/inputs_changes`)
- Outputs API (`/api/outputs_{type}`)
- Jobs API (`/api/v2/jobs`)

## Changes Made

### 1. Added Imports in `web/server.py`
Added imports from `rest.api_v2`:
```python
from rest.api_v2 import (
    api_get_inputs_changes,
    api_post_inputs_changes,
    api_put_inputs_changes_entry,
    api_delete_inputs_changes_entry,
    api_get_outputs,
    api_post_outputs,
    api_put_outputs_entry,
    api_delete_outputs_entry,
    api_get_jobs,
    api_get_job,
    api_post_jobs,
    api_put_job,
    api_delete_job,
    api_refresh_job_input,
    api_refresh_job_output,
)
```

### 2. Registered Routes in `create_app()`

#### Inputs API
```python
# API v2.0 - Inputs (changes)
app.router.add_get("/api/inputs_changes", api_get_inputs_changes)
app.router.add_post("/api/inputs_changes", api_post_inputs_changes)
app.router.add_put("/api/inputs_changes/{id}", api_put_inputs_changes_entry)
app.router.add_delete("/api/inputs_changes/{id}", api_delete_inputs_changes_entry)
```

#### Outputs API  
```python
# API v2.0 - Outputs (dynamic type: rdbms, http, cloud, stdout)
app.router.add_get(r"/api/outputs_{type:rdbms|http|cloud|stdout}", api_get_outputs)
app.router.add_post(r"/api/outputs_{type:rdbms|http|cloud|stdout}", api_post_outputs)
app.router.add_put(r"/api/outputs_{type:rdbms|http|cloud|stdout}/{id}", api_put_outputs_entry)
app.router.add_delete(r"/api/outputs_{type:rdbms|http|cloud|stdout}/{id}", api_delete_outputs_entry)
```

#### Jobs API
```python
# API v2.0 - Jobs
app.router.add_get("/api/v2/jobs", api_get_jobs)
app.router.add_post("/api/v2/jobs", api_post_jobs)
app.router.add_get("/api/v2/jobs/{id}", api_get_job)
app.router.add_put("/api/v2/jobs/{id}", api_put_job)
app.router.add_delete("/api/v2/jobs/{id}", api_delete_job)
app.router.add_post("/api/v2/jobs/{id}/refresh-input", api_refresh_job_input)
app.router.add_post("/api/v2/jobs/{id}/refresh-output", api_refresh_job_output)
```

## Endpoints Now Available

### Inputs (Changes) - `/api/inputs_changes`
- `GET` - Get all inputs
- `POST` - Save inputs configuration  
- `PUT /{id}` - Update input entry
- `DELETE /{id}` - Delete input entry

### Outputs - `/api/outputs_{type}` (type: rdbms, http, cloud, stdout)
- `GET` - Get outputs for type
- `POST` - Save outputs for type
- `PUT /{id}` - Update output entry
- `DELETE /{id}` - Delete output entry

### Jobs - `/api/v2/jobs`
- `GET` - List all jobs
- `POST` - Create new job
- `GET /{id}` - Get specific job
- `PUT /{id}` - Update job
- `DELETE /{id}` - Delete job
- `POST /{id}/refresh-input` - Refresh input from inputs_changes
- `POST /{id}/refresh-output` - Refresh output from outputs_{type}

## Testing
✅ All input API tests pass (10/10)
✅ All output API tests pass (12/12)  
✅ Job API tests are skipped (require CBL enabled - expected behavior)

## Note on Route Pattern
The outputs routes use aiohttp's regex constraints:
```python
r"/api/outputs_{type:rdbms|http|cloud|stdout}"
```
This restricts the `{type}` parameter to valid output types and ensures proper routing.
