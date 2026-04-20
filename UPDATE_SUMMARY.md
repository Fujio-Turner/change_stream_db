# HTML Updates for v2.0 Multi-Job UI Management

## Summary
Updated admin UI to support multi-job management as per `UI_JOBS_MANAGEMENT.md` design document.

## Files Modified

### 1. `web/templates/index.html` — Dashboard Updates

**Changes:**
- Updated job status table header columns:
  - Changed from: `Job Name | Enabled | Status | Last Sync | Docs Processed | Errors`
  - Changed to: `Job | Status | Uptime | Docs In | Docs Out | Errors | Last Seq | Actions`
  
- Added job management button (⚙ Manage) next to job selector dropdown

- Redesigned job status table to show:
  - Status indicator dot (green/yellow/red/gray) with status text
  - Uptime display (e.g., "2h 45m 30s")
  - Docs in/out separated columns
  - Checkpoint sequence (truncated preview)
  - **Action buttons per job:** Start (▶), Stop (⏸), Restart (⟳), Kill (⏹), Edit (✎)

- **Added JavaScript functions:**
  - `openJobManager()` - Placeholder for job manager modal (v2.1+)
  - `startJob(jobId)` - POST `/api/jobs/{jobId}/start`
  - `stopJob(jobId)` - POST `/api/jobs/{jobId}/stop` (graceful, with confirmation)
  - `killJob(jobId)` - POST `/api/jobs/{jobId}/kill` (non-graceful, with strong confirmation)
  - `restartJob(jobId)` - POST `/api/jobs/{jobId}/restart`
  - `editJob(jobId)` - Placeholder for job editor (v2.1+)
  - `escapeJs(str)` - Helper to escape job IDs in HTML event handlers

---

### 2. `web/templates/logs.html` — Job Filtering

**Changes:**
- Added Job filter dropdown in Row 2 filters
  - Populated dynamically from `/api/jobs` endpoint
  - "All Jobs" option by default
  - Positioned before Pipeline stage filters

- **Added JavaScript functions:**
  - `handleJobFilterChange(event)` - Refreshes logs/charts when job is selected
  - `populateJobSelectors()` - Loads job list and populates dropdown on page load
  - `escapeHtml(text)` - Helper to safely encode job names in HTML

---

### 3. `web/templates/dlq.html` — Job Filtering + Reason Filter

**Changes:**
- Added Job filter dropdown alongside Reason filter in the entries section
  - Positioned before the "Reason" filter dropdown
  - Populated dynamically from `/api/jobs` endpoint
  - "All Jobs" option by default

- **Added JavaScript functions:**
  - `currentJobFilter` - State variable to track selected job
  - `filterByJob(jobId)` - Updates `currentJobFilter` and reloads page
  - `populateJobSelectors()` - Loads job list and populates dropdown on page init
  - `escHtml(text)` - Helper to safely encode job names

---

## Backend API Requirements

The following endpoints are expected by the updated UI:

### `/api/jobs` (GET)
Returns array of job objects:
```json
[
  {
    "id": "job::12345678-1234-5678-1234-567812345678",
    "job_id": "job::12345678-1234-5678-1234-567812345678",
    "name": "SG-US → PostgreSQL",
    "status": "running|stopped|error|starting|restarting",
    "uptime_seconds": 3600,
    "docs_processed": 145230,
    "docs_output_ok": 145220,
    "docs_output_err": 10,
    "error_count": 3,
    "checkpoint_seq": "12345-g1AAAAG...",
    ...
  }
]
```

### `/api/jobs/{jobId}/start` (POST)
Starts a stopped job. Returns success/error response.

### `/api/jobs/{jobId}/stop` (POST)
Gracefully stops a running job. Returns success/error response.

### `/api/jobs/{jobId}/kill` (POST)
Non-gracefully kills a running job (may lose checkpoint). Returns success/error response.

### `/api/jobs/{jobId}/restart` (POST)
Restarts a job (stop + start). Returns success/error response.

### `/api/logs?job_id={jobId}` (GET)
(Optional) Backend should support job_id filtering in logs endpoint.

### `/api/dlq?job_id={jobId}` (GET)
(Optional) Backend should support job_id filtering in DLQ endpoint.

---

## Status Indicators

The UI uses color-coded status dots:
- 🟢 **Green** - Running
- 🔴 **Red** - Error
- 🟡 **Yellow** (spinning) - Starting/Restarting
- ⚪ **Gray** - Stopped

---

## TODO / Future Work

- [ ] Backend API endpoints for job control (`/api/jobs/{id}/start|stop|kill|restart`)
- [ ] Job manager modal (`openJobManager()`)
- [ ] Job editor modal (`editJob()`)
- [ ] Backend support for `?job_id=` filtering on logs and DLQ endpoints
- [ ] Data Quality tab in DLQ page (v2.1+)
- [ ] Audit log tracking for job lifecycle events (v2.1+)
- [ ] User management / RBAC foundation (v2.2+)

---

## Testing Checklist

- [ ] Index dashboard loads with job status table
- [ ] Job selector dropdown populates correctly
- [ ] Start/stop/restart/kill buttons call correct endpoints
- [ ] Confirmation dialogs appear before stop/kill
- [ ] Toast notifications show success/error
- [ ] Logs page job filter dropdown populates
- [ ] Logs refresh when job is selected
- [ ] DLQ page job filter dropdown populates
- [ ] DLQ refreshes when job is selected
- [ ] All HTML is valid and loads without errors

---

## Files Changed Summary

```
web/templates/
├── index.html       ← Multi-job dashboard, job actions
├── logs.html        ← Job filter dropdown
└── dlq.html         ← Job filter dropdown
```
