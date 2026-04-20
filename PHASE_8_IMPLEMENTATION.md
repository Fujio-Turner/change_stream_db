# Phase 8: Dashboard Updates – Implementation Plan

**Status:** 🚀 In Progress  
**Date:** 2026-04-19  
**Objective:** Add job selector dropdown and per-job status to dashboard.

---

## Current State

### Dashboard Today
The main dashboard displays:
- Overall pipeline metrics (documents processed, errors, throughput)
- System health (uptime, memory, CPU)
- Last N changes
- Single job status

### Problem
Now that Phase 6 supports **multiple jobs**, the dashboard needs to:
1. Show a **job selector dropdown** to filter metrics by job
2. Display **per-job status** (enabled/disabled, last sync, throughput)
3. Allow switching between jobs without page reload
4. Show **aggregate metrics** when no job selected

---

## Implementation Plan

### Step 1: Identify Dashboard Files
**Files to audit:**
- Frontend: Dashboard page (HTML/JS or framework)
- Backend: Metrics endpoint(s)
- Backend: Job status endpoint(s)

**Tasks:**
- [ ] Find dashboard template/component
- [ ] Identify metrics endpoints
- [ ] Identify current status data structure
- [ ] Check if per-job metrics already collected

### Step 2: Add Job Selector UI
**Frontend changes:**
- Add dropdown above metrics showing all enabled jobs
- Default: "All Jobs" (aggregate view)
- Option for each job by name
- Dropdown populates from job list

**Updates needed:**
- Dashboard HTML/component
- CSS for dropdown styling (match existing theme)
- JavaScript to handle selection change

### Step 3: Add Per-Job Status Display
**New section on dashboard:**
- Table or cards showing:
  - Job name
  - Enabled status (toggle)
  - Last sync time
  - Current status (idle/running/error)
  - Documents processed (job-specific)
  - Error count (job-specific)

**Data from:**
- Job documents
- Job-specific checkpoint
- Metrics by job

### Step 4: Update Metrics Endpoint
**If needed (may already exist):**
- `GET /api/metrics/jobs` – List all jobs with status
- `GET /api/metrics/job/{job_id}` – Metrics for one job
- `GET /api/metrics?job_id=abc` – Filter by job

**Considerations:**
- Should be fast (< 100ms)
- Include job name, enabled status, last sync
- Include job-specific counters

### Step 5: Frontend Logic
**JavaScript changes:**
- On dropdown change: reload metrics for selected job
- Format metrics appropriately:
  - If "All Jobs": show aggregated counters
  - If specific job: show job-specific counters
- Update charts/tables dynamically

### Step 6: Styling & UX
- Match existing DaisyUI/Tailwind theme
- Responsive on mobile
- Loading states while fetching
- Error states for missing data

### Step 7: Testing
- [ ] Dropdown renders with all jobs
- [ ] Clicking a job loads correct metrics
- [ ] "All Jobs" shows aggregates
- [ ] Job enable/disable toggles work
- [ ] Responsive on mobile
- [ ] Error handling for missing jobs

### Step 8: Documentation
- [ ] Create `PHASE_8_QUICK_REFERENCE.md`
- [ ] Create `PHASE_8_VERIFIED.md`
- [ ] Update `PHASE_8_STATUS.md`
- [ ] Create `PHASE_8_SUMMARY.md`

---

## Files to Modify

### Frontend
- `web/templates/dashboard.html` (or equivalent)
- `web/static/js/dashboard.js` (or equivalent)
- CSS updates in Tailwind/DaisyUI

### Backend
- `web/server.py` – May add/update endpoints
- `cbl_store.py` – May need job list method
- `main.py` – May need to expose per-job metrics

---

## Success Criteria

- [ ] Job selector dropdown visible on dashboard
- [ ] Metrics update when job selected
- [ ] Per-job status table displays correctly
- [ ] All jobs view works (aggregates)
- [ ] Mobile responsive
- [ ] Tests all passing
- [ ] Documentation complete
- [ ] Zero breaking changes

---

## Timeline

- **Step 1 (Audit):** 10 min
- **Step 2 (Selector UI):** 15 min
- **Step 3 (Status Display):** 20 min
- **Step 4 (Metrics):** 15 min
- **Step 5 (JS Logic):** 20 min
- **Step 6 (Styling):** 10 min
- **Step 7 (Testing):** 15 min
- **Step 8 (Docs):** 15 min

**Total:** ~120 minutes

---

## Next: Audit Dashboard Files

Starting with Step 1...
