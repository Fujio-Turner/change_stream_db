# Logs.html Job ID Filtering — Quick Fix Summary

## Problem Identified
The job filter dropdown in logs.html wasn't working. Root causes:
1. ❌ Log entries had no `job_id` field in output
2. ❌ JavaScript wasn't parsing `job_id` from logs
3. ❌ Filter logic wasn't checking job_id

## Solution Applied

### ✅ Fix 1: Enable job_id in Logging (`pipeline_logging.py`)
**File:** `/pipeline_logging.py` (line ~175)

Added to `_EXTRA_FIELDS`:
```python
_EXTRA_FIELDS = (
    "log_key",
    "job_id",  # ← NEW
    "operation",
    ...
)
```

**Effect:** Now logs can include `job_id=job::uuid` in structured output.

---

### ✅ Fix 2: Parse & Filter job_id (`web/templates/logs.html`)

**Change 2a: Filter logic** (line ~354)
```javascript
var selectedJobId = document.getElementById('jobFilter').value;

// ... in loop ...
if (selectedJobId) {
  var logJobId = e.fields && e.fields.job_id ? e.fields.job_id : '';
  if (logJobId !== selectedJobId) continue;
}
```

**Change 2b: Display job_id badge** (line ~410)
```javascript
if (e.fields && e.fields.job_id) {
  html += '<span class="badge badge-secondary badge-xs font-mono">🔗 ' 
        + esc(e.fields.job_id).substring(0, 12) + '</span>';
}
// Skip job_id from generic fields display
if (keys[j] === 'doc_id' || keys[j] === 'job_id') continue;
```

**Change 2c: Handler callback** (line ~1080)
```javascript
function handleJobFilterChange(event) {
  selectedJobId = event.target.value || null;
  applyFilters();  // Re-apply with new job selection
}
```

---

## What This Does

### For Users
1. Open logs.html
2. "Job:" dropdown populates from `/api/jobs`
3. Select a job → logs instantly filter to show only that job
4. Each log line displays `🔗 job::abcd` badge for visibility

### For Developers
The parsing already worked! The fix:
- Ensures `job_id` is available in log structured fields
- Implements the filtering comparison
- Displays job ID visually in UI

---

## Next Steps (Backend)

### For Phase 10 Implementation
All `Pipeline` instances must pass `job_id` to logging calls:

```python
# In pipeline.py
from pipeline_logging import log_event

class Pipeline:
    def run(self):
        log_event(logger, "info", "CHANGES", "received batch",
                  job_id=self.job_id,  # ← Must add this
                  batch_size=len(batch))
```

### Checklist
- [ ] Update `pipeline.py` to pass `job_id` to all `log_event()` calls
- [ ] Update `schema/mapper.py` schema mapping logs
- [ ] Audit all direct `logger.info()` calls → convert to `log_event()`
- [ ] Test: Create 2 jobs, verify logs filter correctly
- [ ] Test: Verify `🔗 job::xxxx` badge appears in UI

---

## Files Changed
1. **pipeline_logging.py** — +1 line (added `job_id` field)
2. **web/templates/logs.html** — +3 edits (filter, display, handler)

## Testing
```bash
# Manual test
1. Create 2 jobs via Wizard
2. Run both concurrently
3. Go to Logs & Debugging
4. Select each job from dropdown
5. Verify logs filter correctly
```

---

## Status
✅ Frontend ready for job_id filtering
⏳ Backend needs job_id passed to logging calls
