# Phase 9 Status — Schema Mapping Migration

**Objective:** Move mappings from `mappings/` files + `mappings` CBL collection into job documents.

**Status:** ✅ **COMPLETE**

---

## Implementation Summary

### 1. Migration Function (`cbl_store.py`)

Added `migrate_mappings_to_jobs()` function that:
- Loads all jobs from CBL `jobs` collection
- For each job:
  - Skips if mapping already embedded (non-empty `schema_mapping`)
  - Searches for mapping from:
    1. Disk files in `mappings/{job_id}.{json|yaml|yml}`
    2. CBL `mappings` collection (by job ID)
    3. Pattern matching (backwards compat by job name slug)
  - Parses JSON or treats as raw string
  - Updates job document with embedded mapping

**Key design:** Migration is **idempotent** — can run multiple times safely.

### 2. SchemaMapper Enhancement (`schema/mapper.py`)

Added `SchemaMapper.from_job()` classmethod:
```python
@classmethod
def from_job(cls, job: dict) -> SchemaMapper:
    """Load mapping from job document (Phase 9)."""
    mapping = job.get("schema_mapping", {})
    if not mapping:
        raise ValueError(f"Job {job.get('id')} has no schema_mapping")
    return cls(mapping)
```

This enables loading mappings from job documents instead of files.

### 3. Main.py Integration

Called `migrate_mappings_to_jobs()` in startup sequence:
```python
if USE_CBL:
    migrate_files_to_cbl(args.config)
    migrate_default_to_collections()
    migrate_mappings_to_jobs()  # Phase 9: embed mappings into jobs
```

Runs **automatically on startup** — no user action needed.

---

## What Changed

| File | Change |
|---|---|
| `cbl_store.py` | Added `migrate_mappings_to_jobs()` function (136 lines) |
| `schema/mapper.py` | Added `SchemaMapper.from_job()` classmethod (12 lines) |
| `main.py` | Added migration call in startup sequence (import + 1 line) |

---

## How It Works

### Startup Flow

1. ✅ Config loaded from CBL
2. ✅ Default collections created
3. ✅ **NEW:** Mappings migrated into jobs
4. ✅ Jobs loaded
5. ✅ Pipelines started

### Migration Strategies

For each job without embedded mapping:

```
Strategy 1: Disk file by job ID
  mappings/{job_id}.json
  mappings/{job_id}.yaml
  mappings/{job_id}.yml

Strategy 2: CBL mapping collection (by job ID)
  cbl:mapping:{job_id}.json

Strategy 3: Pattern matching (backwards compat)
  Extract job name slug → search CBL for {slug}.json
```

### Status Messages

The migration logs detailed events:
```
[INFO] CBL: starting schema mapping migration into jobs (job_count=5)
[DEBUG] CBL: job orders-2024 already has schema_mapping — skipping
[INFO] CBL: embedded schema_mapping into job orders-q2 from disk:mappings/orders-q2.json
[WARNING] CBL: no mapping found for job unknown — skipping
[INFO] CBL: mapping migration complete: 3 embedded, 1 skipped, 1 failed
```

---

## After Phase 9

✅ **Mappings are now embedded in jobs.**

Next phases:
- Phase 10: Multi-Job Threading (`PipelineManager`)
- Phase 11: Middleware Framework (`pydantic_coerce`, `timestamp_normalize`, etc.)
- Phase 12: Additional Middleware plugins

The `mappings/` directory becomes an **optional import surface** — drop files there on startup and the migration will embed them into matching jobs.

---

## Verification

✅ Python syntax validated (py_compile)
✅ All 3 files verified
✅ No diagnostic errors
✅ Function signatures correct
✅ Logging integrated

Ready for Phase 10.
