# Phase 6: Job-Based Startup Implementation

**Status:** 🚀 **IN PROGRESS**  
**Scope:** Refactor `main.py` to load and run jobs  
**Effort:** 200-300 lines  
**Timeline:** 1-2 hours

---

## Objectives

Refactor `main.py` to:
1. Load enabled jobs from `jobs` collection on startup
2. Build pipeline config from job documents
3. Refactor `poll_changes()` to accept job config
4. Update checkpoint reads/writes to use job-specific docs
5. Maintain backward compatibility with v1.x `config.json`

---

## Current State

### ✅ What Exists
- Phase 5 Jobs API complete (`rest/jobs_api.py`)
- Phase 5B UI complete (`web/templates/wizard.html`)
- Job document schema defined
- Checkpoint collection ready
- All infrastructure in place

### ❌ What Needs Refactoring
- `main.py` — still uses monolithic `config.json`
- `poll_changes()` — expects fixed config shape
- Checkpoint management — uses hardcoded collection paths
- `validate_config()` — expects v1.x schema

---

## Implementation Plan

### Step 1: Add Job-Loading Utility
**File:** `rest/jobs_api.py` (add utility function)

```python
def load_enabled_jobs(db):
    """Load all enabled jobs from jobs collection."""
    jobs = db.get_collection("jobs")
    results = []
    for doc in jobs.find({}, "enabled", "=", True):
        results.append(doc)
    return results
```

### Step 2: Build Pipeline Config from Job
**File:** `main.py` (add function)

```python
def build_pipeline_config_from_job(job_doc):
    """Convert job document to pipeline config format."""
    return {
        "job_id": job_doc["_id"],
        "inputs": job_doc.get("inputs", []),
        "outputs": job_doc.get("outputs", []),
        "system": job_doc.get("system", {}),
        "mapping": job_doc.get("mapping", None),
    }
```

### Step 3: Update poll_changes() Signature
**File:** `main.py`

**Before:**
```python
async def poll_changes(config, db, stop_event):
    """Poll for changes using global config."""
```

**After:**
```python
async def poll_changes(job_config, db, stop_event):
    """Poll for changes using job-specific config."""
```

### Step 4: Update Checkpoint Management
**File:** `cbl_store.py` or `main.py`

**Before:**
```python
checkpoint_doc_id = "checkpoint::default"
```

**After:**
```python
checkpoint_doc_id = f"checkpoint::{job_config['job_id']}"
```

### Step 5: Update main() Startup Flow
**File:** `main.py`

```python
async def main():
    # ... init DB, start metrics, start UI ...
    
    # Phase 6: Load jobs instead of config
    enabled_jobs = load_enabled_jobs(db)
    
    if not enabled_jobs and config_json_exists():
        # Backward compat: migrate v1.x config to a job
        migrate_legacy_config(db, config)
        enabled_jobs = load_enabled_jobs(db)
    
    if not enabled_jobs:
        logger.warning("No jobs configured. Visit UI to add jobs.")
        # Keep running for UI management
        return
    
    # Start pipeline for each job
    for job_doc in enabled_jobs:
        job_config = build_pipeline_config_from_job(job_doc)
        asyncio.create_task(poll_changes(job_config, db, stop_event))
```

### Step 6: Update validate_config() CLI
**File:** `main.py`

```python
if args.validate:
    enabled_jobs = load_enabled_jobs(db)
    for job_doc in enabled_jobs:
        validate_job_config(job_doc)
    logger.info("✅ All jobs valid")
```

### Step 7: Update Helper Functions
**File:** `main.py`

Update these to accept `job_config` instead of `config`:
- `build_base_url()` → use `job_config["inputs"][0]`
- `build_auth_headers()` → use `job_config["inputs"][0]`
- `build_basic_auth()` → use `job_config["inputs"][0]`
- `MetricsCollector.init()` → add `job_id` label from `job_config["job_id"]`

---

## Backward Compatibility

If user has old v1.x `config.json` and no jobs:
1. On startup, detect this condition
2. Auto-create a job document from v1.x config
3. Load and run that job
4. Log: "Auto-migrated legacy config to job format"

**Migration function:**
```python
def migrate_legacy_config_to_job(db, config):
    """Create job from v1.x config.json"""
    job = {
        "name": "legacy_auto_migrated",
        "enabled": True,
        "inputs": [config.get("gateway")],
        "outputs": [config.get("output")],
        "system": config.get("system"),
        "mapping": None,
    }
    jobs_collection = db.get_collection("jobs")
    jobs_collection.save(job)
```

---

## Testing Strategy

### Unit Tests
1. `test_build_pipeline_config_from_job()` — config building
2. `test_load_enabled_jobs()` — loading logic
3. `test_migrate_legacy_config()` — backward compat
4. `test_checkpoint_isolation()` — per-job checkpoints

### Integration Tests
1. Start with empty jobs → should wait for UI
2. Create job via UI → should auto-start pipeline
3. Disable job → should stop pipeline
4. Enable job → should restart pipeline
5. Migrate legacy config → should work transparently

### Manual Tests
1. `python main.py --validate` (no jobs)
2. Create job via web UI
3. Verify metrics labeled with job_id
4. Check checkpoint::job_id in DB
5. Verify graceful shutdown

---

## Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| `main.py` | Job loading, config building, startup refactor | 100-150 |
| `rest/jobs_api.py` | Add `load_enabled_jobs()` utility | 10-15 |
| `cbl_store.py` | Update checkpoint path to job-specific | 5-10 |
| `tests/test_main.py` | Add job-based startup tests | 50-100 |

**Total:** ~200-250 lines of new/modified code

---

## Success Criteria

✅ **Must achieve:**
1. No changes to job documents from Phase 5
2. Jobs load automatically on startup
3. Per-job checkpoints work correctly
4. Backward compat with v1.x config.json
5. All existing tests still pass
6. Metrics include job_id label
7. Graceful shutdown waits for all jobs
8. Logging includes job_id context

✅ **Documentation must cover:**
1. How jobs are loaded at startup
2. Checkpoint isolation by job
3. Migration from v1.x config
4. Per-job metrics tracking
5. Job lifecycle (enable/disable)
6. Troubleshooting common issues

---

## Deliverables

📄 **Phase 6 Files:**
1. `PHASE_6_IMPLEMENTATION.md` ← (this file)
2. `PHASE_6_QUICK_REFERENCE.md` (user guide)
3. `PHASE_6_VERIFIED.md` (verification report)
4. `PHASE_6_STATUS.md` (final status)

📝 **Code Changes:**
1. `main.py` (refactored)
2. `rest/jobs_api.py` (utility function)
3. `cbl_store.py` (checkpoint paths)
4. `tests/test_main.py` (new tests)

---

## Next Steps

1. ✅ Backup current `main.py`
2. ✅ Implement job loading utility
3. ✅ Refactor `poll_changes()` signature
4. ✅ Update `main()` startup flow
5. ✅ Add backward compatibility layer
6. ✅ Add tests
7. ✅ Verify all tests pass
8. ✅ Update documentation
9. ✅ Final verification

---

## Phase Roadmap

| Phase | Name | Status | Duration |
|-------|------|--------|----------|
| 3 | Inputs System | ✅ Complete | 2h |
| 4 | Outputs System | ✅ Complete | 2h |
| 5 | Jobs API | ✅ Complete | 2h |
| 5B | Jobs UI | ✅ Complete | 2h |
| **6** | **Job-Based Startup** | 🚀 **Active** | **1-2h** |
| 7 | Settings Cleanup | ⏳ Queued | 1h |
| 8 | Dashboard Updates | ⏳ Queued | 1h |
| 9 | Schema Migration | ⏳ Queued | 2h |
| 10 | Multi-Job Threading | ⏳ Queued | 2h |

**Total to v2.0:** ~15-17 hours

---

**Ready to begin!** 🚀
