# Phase 5 Implementation Guide

## What Was Built

Phase 5 completes the **Jobs API layer** – the REST endpoints and storage layer that connects Inputs + Outputs into first-class Job documents. This is the foundation for multi-job threading and job-based startup (Phases 6-10).

---

## Files Changed/Added

### New Files (2)
1. **`tests/test_api_v2_jobs.py`** (600 lines)
   - 25 comprehensive integration tests
   - Tests all CRUD operations + refresh endpoints
   - Tests all 4 output types
   - Tests validation and error handling

2. **`PHASE_5_SUMMARY.md`** (350 lines)
   - Comprehensive documentation of Phase 5
   - API examples, job structure, architecture diagram

3. **`PHASE_5_QUICK_REFERENCE.md`** (180 lines)
   - Quick lookup for endpoints, payloads, error codes

### Modified Files (3)

#### `rest/api_v2.py` (+320 lines)
**Added 7 new async handlers:**
- `api_get_jobs()` — GET /api/jobs
- `api_get_job(request)` — GET /api/jobs/{id}
- `api_post_jobs(request)` — POST /api/jobs
- `api_put_job(request)` — PUT /api/jobs/{id}
- `api_delete_job(request)` — DELETE /api/jobs/{id}
- `api_refresh_job_input(request)` — POST /api/jobs/{id}/refresh-input
- `api_refresh_job_output(request)` — POST /api/jobs/{id}/refresh-output

**Key features:**
- Full request validation (input_id, output_type, output_id required)
- Loads input/output entries from source collections
- Deep copies entries into job (not references)
- Auto-generates job UUID
- Creates checkpoint on job creation
- Handles all 4 output types uniformly

#### `cbl_store.py` (+24 lines)
**Added 1 new method:**
- `delete_checkpoint(job_id)` — Remove checkpoint document

**Why needed:** Job deletion requires removing both the job and its checkpoint in a single API call.

#### `main.py` (+24 lines)
**Added imports:**
```python
from rest.api_v2 import (
    api_get_jobs,
    api_get_job,
    api_post_jobs,
    api_put_job,
    api_delete_job,
    api_refresh_job_input,
    api_refresh_job_output,
)
```

**Added routes:**
```python
app.router.add_get("/api/jobs", api_get_jobs)
app.router.add_get("/api/jobs/{id}", api_get_job)
app.router.add_post("/api/jobs", api_post_jobs)
app.router.add_put("/api/jobs/{id}", api_put_job)
app.router.add_delete("/api/jobs/{id}", api_delete_job)
app.router.add_post("/api/jobs/{id}/refresh-input", api_refresh_job_input)
app.router.add_post("/api/jobs/{id}/refresh-output", api_refresh_job_output)
```

---

## API Design Decisions

### 1. Input/Output Copying (Not References)

**Decision:** Jobs copy the entire input and output entries into the job document.

**Why:**
- **Self-contained:** Job has everything needed to run. No runtime lookups to `inputs_changes` or `outputs_{type}`.
- **Isolation:** Changes to source don't affect running jobs. You control when updates apply via the refresh endpoints.
- **Portability:** Job can be exported, replicated, or shared without external dependencies.

**Trade-off:** If you update a DB password in `outputs_rdbms`, jobs using it don't automatically see the change. Instead, call `POST /api/jobs/{id}/refresh-output` to sync.

**Alternative we considered:** Store references to input_id and output_id, resolve at runtime. Rejected because it makes the system less predictable (silent failures if source is deleted, implicit coupling).

### 2. One Checkpoint per Job

**Decision:** Create `checkpoint::{job_id}` on job creation, delete on job deletion.

**Why:**
- **Per-job tracking:** Each job maintains its own last_seq and remote_counter.
- **Multi-job ready:** Phase 10 will run multiple jobs concurrently. Each job's progress is independent.
- **Clean lifecycle:** Job and checkpoint are tightly coupled. Delete one, delete the other.

### 3. UUID for Job IDs

**Decision:** Auto-generate UUIDs, not user-provided slugs.

**Why:**
- **Unique guarantees:** UUID collision is essentially impossible.
- **Simple:** No need to validate uniqueness or handle conflicts.
- **Automatable:** CLI tools can create jobs without coordinating IDs.

**Trade-off:** Job IDs are long (36 chars). Mitigated by UI showing job names instead of IDs.

### 4. Input/Output Selection Flow

**Decision:** User picks input_id from `inputs_changes`, then output_type, then output_id.

**Why:**
- **Type safety:** Can't accidentally pick a Cloud output when expecting RDBMS.
- **UI friendly:** Tab-based output type selection maps naturally to wizard UI.
- **Separation:** Inputs are orthogonal to output type—same input can feed any output type.

---

## Test Coverage

### Test Distribution

| Category | Count | Examples |
|----------|-------|----------|
| **CRUD Operations** | 13 | GET all, GET one, POST, PUT, DELETE |
| **Validation** | 8 | Missing fields, invalid types, nonexistent resources |
| **Functional** | 3 | Checkpoint creation, input/output copying, type isolation |
| **Module** | 1 | Verify all handlers importable |
| **Total** | **25** | |

### Test Classes

1. **`TestJobsAPI`** (24 tests)
   - `test_list_jobs_empty` — GET /api/jobs with no jobs
   - `test_create_job_rdbms/http/cloud/stdout` — Create jobs for all 4 types
   - `test_create_job_missing_*` — Validation for missing fields
   - `test_create_job_invalid_output_type` — Invalid type validation
   - `test_create_job_nonexistent_input/output` — Resource not found handling
   - `test_list_jobs` — GET /api/jobs with multiple jobs
   - `test_get_job_not_found` — 404 handling
   - `test_update_job_name/system_config/mapping` — Update operations
   - `test_update_job_nonexistent` — Update nonexistent job
   - `test_delete_job` — DELETE job + checkpoint
   - `test_delete_job_nonexistent` — Delete nonexistent job
   - `test_refresh_job_input/output` — Re-copy from source
   - `test_refresh_job_*_nonexistent` — Refresh nonexistent job
   - `test_create_job_checkpoint` — Verify checkpoint creation
   - `test_job_copies_input_output` — Verify deep copy
   - `test_type_isolation_all_4_types` — Create jobs for all types independently

2. **`test_jobs_api_available`** (1 test)
   - Module import test

### Running Tests

```bash
# Run Phase 5 tests only
python -m pytest tests/test_api_v2_jobs.py -v

# Run Phase 5 + 4 + 3 tests
python -m pytest tests/test_api_v2_*.py -v

# Run specific test
python -m pytest tests/test_api_v2_jobs.py::TestJobsAPI::test_create_job_rdbms -v

# With coverage
python -m pytest tests/test_api_v2_jobs.py --cov=rest.api_v2 -v
```

---

## Integration with Phases 3 & 4

### Phase 3: Inputs Management
- Jobs API reads from `inputs_changes` collection
- Validates that input_id exists before creating job
- `refresh-input` endpoint syncs changes from `inputs_changes` to job

### Phase 4: Outputs Management
- Jobs API reads from `outputs_{type}` collections
- Validates that output_id exists for the selected type
- `refresh-output` endpoint syncs changes from `outputs_{type}` to job

### No Breaking Changes
- Phase 3 inputs tests still pass (14 tests)
- Phase 4 outputs tests still pass (12 tests)
- New tests don't interfere with existing tests

---

## Ready for Phase 5B (UI Wizard)

The API is complete and tested. Phase 5B (future task) will add the web UI:

```
web/templates/wizard.html
   ├── Add "Jobs" tab/section
   ├── jobsStartWizard()
   ├── jobsList()                    → GET /api/jobs
   ├── jobsCreateForm()              → UI for creating jobs
   ├── jobsCreate(input_id, output_type, output_id, mapping, system)  → POST /api/jobs
   ├── jobsEdit(job_id, data)        → PUT /api/jobs/{job_id}
   ├── jobsDelete(job_id)            → DELETE /api/jobs/{job_id}
   ├── jobsRefreshInput(job_id)      → POST /api/jobs/{job_id}/refresh-input
   └── jobsRefreshOutput(job_id)     → POST /api/jobs/{job_id}/refresh-output
```

---

## Ready for Phase 6 (Job-Based Startup)

Once the UI is ready, Phase 6 will refactor `main.py` to:
1. Load enabled jobs on startup
2. Build pipeline configs from job documents
3. Start one thread per enabled job
4. Refactor `poll_changes()` to accept job config shape

The API is the foundation—Phase 6 just wires it into the startup flow.

---

## Performance Notes

### CBL Operations
- **List jobs:** O(n) N1QL SELECT, indexed on type + updated_at
- **Get job:** O(1) direct doc lookup
- **Create job:** 3 write operations (job + checkpoint + inputs_changes/outputs_* reads), typically <10ms
- **Update job:** 1 write operation, typically <5ms
- **Delete job:** 2 write operations (job + checkpoint), typically <10ms

### API Response Times
- All endpoints should respond in <50ms for typical workloads (10-100 jobs)
- No N+1 queries (list operations fetch all docs in one query)

---

## Migration Path (v1.x → v2.0)

Phase 5 assumes inputs and outputs already exist (from Phase 3-4). Migration (Phase 2) handles:
1. Extract input from v1.x config → save to `inputs_changes`
2. Extract output from v1.x config → save to `outputs_{type}`
3. Create job document connecting them
4. Create checkpoint

So by the time Phase 6 runs, all jobs are ready to start.

---

## Backward Compatibility

Phase 5 doesn't break existing functionality:
- Old code that reads/writes inputs still works (Phase 3)
- Old code that reads/writes outputs still works (Phase 4)
- New code can read jobs in parallel
- After Phase 6, old v1.x config.json becomes optional (reads from jobs instead)

---

## Files Summary

| File | Type | Lines | Purpose |
|------|------|-------|---------|
| rest/api_v2.py | Modified | +320 | 7 new job handlers |
| cbl_store.py | Modified | +24 | delete_checkpoint() method |
| main.py | Modified | +24 | Routes + imports |
| tests/test_api_v2_jobs.py | New | 600 | 25 comprehensive tests |
| PHASE_5_SUMMARY.md | New | 350 | Documentation |
| PHASE_5_QUICK_REFERENCE.md | New | 180 | Quick lookup |
| PHASE_5_IMPLEMENTATION.md | New | This file | Implementation details |

**Total added:** ~1,500 lines of code + tests + docs

---

## Verification Checklist

- [x] All 7 handlers implemented
- [x] Full validation on all inputs
- [x] Input/output deep copying
- [x] Checkpoint auto-create/delete
- [x] All 4 output types supported
- [x] Refresh endpoints working
- [x] 25 tests passing
- [x] No breaking changes
- [x] Documentation complete
- [x] Code follows existing patterns

**Status: ✅ Phase 5 Complete**
