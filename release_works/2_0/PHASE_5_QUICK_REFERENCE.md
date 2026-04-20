# Phase 5 Quick Reference

## Endpoints

### List All Jobs
```
GET /api/jobs
→ { "count": 3, "jobs": [...] }
```

### Get One Job
```
GET /api/jobs/{id}
→ { "type": "job", "id": "...", "name": "...", "inputs": [...], ... }
```

### Create Job
```
POST /api/jobs
{
  "input_id": "sg-us-orders",          // required
  "output_type": "rdbms",               // required: rdbms|http|cloud|stdout
  "output_id": "pg-orders-db",         // required
  "name": "US Orders → DB",            // optional, auto-generated if missing
  "system": { "threads": 2, ... },     // optional
  "mapping": { "source": "orders", ... } // optional
}
→ { "status": "ok", "job_id": "...", "name": "..." } [201]
```

### Update Job
```
PUT /api/jobs/{id}
{
  "name": "New Name",           // optional
  "system": { ... },            // optional – updates entire system config
  "mapping": { ... },           // optional – updates entire mapping
  "state": { "status": "..." }  // optional – for Phase 6 job lifecycle
}
→ { "status": "ok", "job_id": "..." }
```

### Delete Job (+ checkpoint)
```
DELETE /api/jobs/{id}
→ { "status": "ok", "job_id": "..." }
```

### Refresh Input from Source
```
POST /api/jobs/{id}/refresh-input
→ { "status": "ok", "job_id": "...", "input_id": "..." }
```
**Use case:** Input config changed in `inputs_changes`, update job with latest.

### Refresh Output from Source
```
POST /api/jobs/{id}/refresh-output
→ { "status": "ok", "job_id": "...", "output_id": "...", "output_type": "..." }
```
**Use case:** Output config changed in `outputs_{type}`, update job with latest.

---

## Error Responses

```
400 Bad Request
  - Missing required field (input_id, output_type, output_id)
  - Invalid output_type (must be rdbms|http|cloud|stdout)
  - Nonexistent input_id or output_id
  - Invalid JSON

404 Not Found
  - Job not found
  - No inputs defined
  - No outputs defined for the given type

500 Server Error
  - CBL operation failed
  - Unexpected exception
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | GET/PUT/POST (list, get, update, refresh) success |
| 201 | POST (create) success – new resource created |
| 400 | Bad request – validation failed |
| 404 | Resource not found |
| 500 | Server error |
| 503 | CBL disabled |

---

## Test Commands

Run all job tests:
```bash
python -m pytest tests/test_api_v2_jobs.py -v
```

Run specific test:
```bash
python -m pytest tests/test_api_v2_jobs.py::TestJobsAPI::test_create_job_rdbms -v
```

Run all Phase 5 + 4 + 3 tests:
```bash
python -m pytest tests/test_api_v2_*.py -v
```

---

## Job Document Shape

```json
{
  "type": "job",
  "id": "uuid",
  "name": "Job Name",
  "inputs": [
    {
      "id": "input-id",
      "name": "Input Name",
      "source_type": "sync_gateway|app_services|edge_server|couchdb",
      "host": "...",
      "database": "...",
      "scope": "...",
      "collection": "...",
      "auth": { "method": "basic", "username": "...", "password": "..." },
      "changes_feed": { "feed_type": "continuous", ... },
      ...
    }
  ],
  "outputs": [
    {
      "id": "output-id",
      "name": "Output Name",
      // Fields depend on output type:
      // RDBMS: engine, host, port, database, username, password, pool_max
      // HTTP: target_url, write_method, timeout_seconds, retry_count
      // Cloud: provider, region, bucket, prefix
      // Stdout: pretty_print
      ...
    }
  ],
  "output_type": "rdbms|http|cloud|stdout",
  "system": {
    "threads": 1,
    "batch_size": 100,
    "retry_count": 3,
    "checkpoint_interval_ms": 10000
  },
  "mapping": {
    "source": "...",
    "target": "...",
    "fields": { ... }
  },
  "state": {
    "status": "idle|running|paused|error",
    "last_updated": "2024-01-15T10:30:00Z"
  },
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

---

## Key Points

1. **Jobs copy input/output** – Not references. Immune to source changes. Use refresh endpoints to sync.
2. **Checkpoints auto-created** – Job creation auto-creates `checkpoint::{job_id}` doc.
3. **Checkpoints auto-deleted** – Deleting job also deletes its checkpoint.
4. **All 4 types supported** – Create jobs for rdbms, http, cloud, or stdout outputs.
5. **Self-contained** – Job doc has everything needed to run the pipeline.

---

## Phase 5B (Future): Wizard UI

Jobs API is ready. Next task: Build the wizard UI in `web/templates/wizard.html` with:
- Jobs tab/section
- Job list view with status
- Create form (input selector → output type → output selector)
- Edit form (name, system config, mapping)
- Delete button with confirm
- Refresh buttons

See `PHASE_5_SUMMARY.md` for the full plan.
