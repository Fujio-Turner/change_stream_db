# Backend: Adding job_id to Logging Calls

## Overview
The frontend is ready to filter logs by job_id. Now the **Pipeline** needs to pass `job_id` to all logging.

## Pattern

### Before (❌ No job_id)
```python
from pipeline_logging import log_event

logger = logging.getLogger(__name__)
log_event(logger, "info", "CHANGES", "Received change batch",
          batch_size=len(batch), doc_count=len(docs))
```

### After (✅ With job_id)
```python
from pipeline_logging import log_event

logger = logging.getLogger(__name__)
log_event(logger, "info", "CHANGES", "Received change batch",
          job_id=self.job_id,        # ← ADD THIS
          batch_size=len(batch), 
          doc_count=len(docs))
```

## Implementation in pipeline.py

### Pattern 1: Class method with self.job_id
```python
class Pipeline:
    def __init__(self, job_id, ...):
        self.job_id = job_id
        self.logger = logging.getLogger(f"pipeline.{job_id}")
    
    def run(self):
        log_event(self.logger, "info", "CHANGES", "Starting job",
                  job_id=self.job_id)
```

### Pattern 2: Context variable (recommended)
```python
import contextvars

# Module level
job_context = contextvars.ContextVar('job_id', default=None)

# In Pipeline.run()
token = job_context.set(self.job_id)
try:
    log_event(logger, "info", "CHANGES", "msg", 
              job_id=job_context.get())
finally:
    job_context.reset(token)
```

### Pattern 3: LoggerAdapter (best practice)
```python
import logging

class Pipeline:
    def run(self):
        # Wrap logger to auto-inject job_id
        adapter = logging.LoggerAdapter(
            logging.getLogger(__name__),
            {"job_id": self.job_id}
        )
        
        # Now job_id is auto-included in all log_event calls
        log_event(adapter.logger, "info", "CHANGES", "msg",
                  **adapter.extra)
```

## Search & Replace Guide

### Find all log_event calls in pipeline.py
```bash
grep -n "log_event" pipeline.py
```

### Pattern to update (in order)
```python
# FIND:
log_event(logger, "TYPE", "KEY", "message", ...)

# REPLACE:
log_event(logger, "TYPE", "KEY", "message", job_id=self.job_id, ...)
```

## Files to Update

### 1. pipeline.py
**Task:** Add `job_id=self.job_id` to all `log_event()` calls

Example locations:
```python
# Line ~XX: Poll changes feed
log_event(logger, "info", "CHANGES", "Polling feed...",
          job_id=self.job_id)  # ← ADD

# Line ~YY: Process batch
log_event(logger, "info", "PROCESSING", "Processing batch",
          job_id=self.job_id,  # ← ADD
          batch_size=len(batch))

# Line ~ZZ: Output success
log_event(logger, "info", "OUTPUT", "Sent to output",
          job_id=self.job_id,  # ← ADD
          status=200)
```

### 2. schema/mapper.py
**Task:** Add `job_id` parameter to functions that log

```python
def map_doc(doc, mapping, job_id=None):
    """Map a document according to schema mapping."""
    
    log_event(logger, "debug", "MAPPING", "Mapping doc",
              job_id=job_id,  # ← ADD
              doc_id=doc.get("_id"))
```

### 3. pipeline_manager.py
**Task:** Pass `job_id` when calling Pipeline methods

```python
class PipelineManager:
    def start_job(self, job_id):
        pipeline = Pipeline(job_id=job_id, ...)
        
        # Pipeline will log with job_id automatically
        pipeline.run()
```

## Checklist

- [ ] Update `pipeline.py` — add `job_id=self.job_id` to all log_event calls
  - [ ] Feed polling logs
  - [ ] Batch processing logs
  - [ ] Output sending logs
  - [ ] Error handling logs
  - [ ] Checkpoint logs
  - [ ] Retry logs

- [ ] Update `schema/mapper.py` — accept & pass `job_id` parameter
  - [ ] `map_doc()` function
  - [ ] `validate_doc()` function
  - [ ] Transform application logs

- [ ] Update `rest/api_v2_jobs_control.py` — job lifecycle logging
  - [ ] Start job log
  - [ ] Stop job log
  - [ ] Restart job log
  - [ ] Kill job log

- [ ] Verify all `logger.info()` direct calls
  - [ ] Convert to `log_event()` with job_id
  - [ ] Or add to safe list (config validation, startup, etc.)

- [ ] Test
  - [ ] Create 2 jobs
  - [ ] Run both
  - [ ] Verify logs show job_id field
  - [ ] Test logs.html filtering

## Testing Query
```bash
# After implementation, grep for job_id in logs
grep "job_id=" logs/changes_worker.log | head -5

# Expected output:
2026-04-20 07:07:21.980 [INFO] changes_worker: Received changes job_id=job::2162fb33 [CHANGES]
2026-04-20 07:07:21.981 [INFO] changes_worker: Processing batch job_id=job::2162fb33 [PROCESSING] batch_size=50
```

## Quick Template

Use this template for each Pipeline method:

```python
def method_name(self):
    """Docstring."""
    try:
        log_event(self.logger, "info", "LOG_KEY", "Starting operation",
                  job_id=self.job_id)
        
        # Do work
        
        log_event(self.logger, "info", "LOG_KEY", "Operation complete",
                  job_id=self.job_id, result="success")
    except Exception as e:
        log_event(self.logger, "error", "LOG_KEY", f"Operation failed: {e}",
                  job_id=self.job_id)
        raise
```

---

## Status
- Frontend: ✅ Ready (logs.html parsing & filtering implemented)
- Backend: 🔧 In progress (needs job_id in log calls)

Once backend is updated, run tests to verify end-to-end functionality.
