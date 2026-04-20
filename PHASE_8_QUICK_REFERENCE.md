# Phase 8: Dashboard Updates – Quick Reference

**Status:** ✅ Complete  
**Date:** 2026-04-20  
**Changes:** Job selector dropdown + per-job status table

## What's New

### 1. New API Endpoint
- **`GET /api/jobs/status`** – Returns list of all jobs with status

### 2. New UI Components
- **Job Selector Dropdown** – Filter metrics by job
  - Default: "All Jobs" (aggregate view)
  - Options: One per configured job
- **Per-Job Status Table** – Shows:
  - Job Name
  - Enabled Status (✓/✗)
  - Current Status (idle/running/error)
  - Last Sync Time
  - Docs Processed
  - Error Count

### 3. JavaScript Functions
- `loadJobStatus()` – Fetch and display job status
- `handleJobChange(event)` – Handle job selector changes

## Files Changed

### Backend
- **`web/server.py`**
  - Added `get_jobs_status()` async function
  - Added `GET /api/jobs/status` route
  - Added logger import

### Frontend
- **`web/templates/index.html`**
  - Added job selector dropdown section
  - Added per-job status table (HTML + CSS)
  - Added `loadJobStatus()` JavaScript function
  - Added `handleJobChange()` JavaScript function
  - Added interval refresh for job status (every 10s)

### Tests
- **`tests/test_phase_8_dashboard.py`**
  - Tests for `/api/jobs/status` endpoint
  - Response structure validation
  - Integration tests (with CBL enabled)

## Quick Test

```bash
# Run dashboard tests
pytest tests/test_phase_8_dashboard.py -v

# Test endpoint manually
curl http://localhost:8080/api/jobs/status | jq

# Check dashboard
open http://localhost:8080
# Look for "Filter by Job:" dropdown and job status table
```

## UI Features

### Job Selector
- Located below status bar
- Responsive dropdown with all job names
- "All Jobs (Aggregate)" option shows total metrics
- Auto-refreshes every 10 seconds

### Status Table
- Shows all configured jobs
- Displays enabled/disabled status with badges
- Shows last sync timestamp
- Displays document count from checkpoint
- Displays error count
- Responsive overflow-x on mobile

### Styling
- DaisyUI components (select, table, badge)
- Tailwind responsive grid
- Mobile-friendly (vertical on small screens, horizontal on large)
- Matches existing dashboard theme

## Metrics Filtering

When a job is selected:
1. Metrics are reloaded via `loadMetrics()`
2. Charts update to show job-specific data
3. "All Jobs" shows aggregated metrics across all jobs
4. Specific job selection filters to that job only

## Data Structure

Response from `/api/jobs/status`:
```json
{
  "jobs": [
    {
      "job_id": "abc-123",
      "name": "Sync Job 1",
      "enabled": true,
      "status": "idle",
      "last_sync_time": "2024-01-01T10:00:00Z",
      "docs_processed": 1234,
      "errors": 0
    }
  ],
  "count": 1
}
```

## Integration Notes

- No breaking changes to existing APIs
- Backward compatible (works without CBL)
- Returns empty list when no jobs configured
- Graceful error handling with fallback UI

## Mobile Responsive

- Dropdown responsive at all sizes
- Table has horizontal scroll on mobile
- Badge formatting optimized for small screens
- DaisyUI handles dark/light theme switching

## Next Steps (Phase 9)

- Add real-time job metrics per dashboard
- Implement per-job error rate visualization
- Add job enable/disable toggle in dashboard
- Real-time status update via WebSocket
