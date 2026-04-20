# Multi-Job UI Management — Design v2.0+

> **Status:** Design Phase  
> **Scope:** Dashboard, job control, filtering, audit trail for multiple concurrent jobs  
> **Related:** `DESIGN_2_0.md` Phase 8 (Dashboard Updates), Phase 10 (Multi-Job Threading), Phase 11+ (Data Quality, Audit)

---

## Overview

Currently, the admin UI (index.html, logs.html, dlq.html) assumes **one job is running**. With Phase 10 (PipelineManager) and v2.0+, we need:

1. **Job-aware dashboard** — Show multiple jobs, their status, uptime, error counts
2. **Job lifecycle control** — Start, stop, restart, kill (graceful vs. non-graceful) individual jobs
3. **Job filtering** across all pages — logs, DLQ, data quality
4. **Prevent deletion** of running jobs
5. **Audit trail** — Track who started/stopped/killed/restarted jobs
6. **Data quality review** — New page for fixed documents; side-by-side bad→good comparison
7. **Users beta page** — Foundation for future RBAC

---

## Page-by-Page Changes

### 1. `index.html` — Multi-Job Dashboard

**Current state:** Single monolithic job; status bar + architecture diagram + config summary  
**Changes:**

#### 1a. Job Selector & Multi-Job Status Table

At the top (after status bar, before architecture), add:

```html
<!-- Job Selector & Per-Job Status -->
<div class="card bg-base-100 shadow rounded-2xl">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <label for="jobSelector" class="text-sm font-semibold">Active Job:</label>
      <select id="jobSelector" class="select select-bordered select-sm w-64" onchange="handleJobChange(event)">
        <option value="">All Jobs (Aggregate)</option>
      </select>
      <button class="btn btn-xs btn-ghost" onclick="openJobManager()">⚙ Manage</button>
    </div>
    
    <!-- Per-Job Status Table -->
    <div class="overflow-x-auto">
      <table class="table table-sm table-zebra">
        <thead>
          <tr class="bg-base-200">
            <th>Job</th>
            <th>Status</th>
            <th>Uptime</th>
            <th>Docs In</th>
            <th>Docs Out</th>
            <th>Errors</th>
            <th>Last Seq</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="jobStatusTable">
          <tr><td colspan="8" class="text-center opacity-50 py-4">Loading jobs...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
```

**Job Status Object (per row):**
```json
{
  "id": "job::12345678-1234-5678-1234-567812345678",
  "name": "SG-US → PostgreSQL",
  "enabled": true,
  "status": "running|stopped|error|starting|restarting",
  "uptime_seconds": 3600,
  "last_sync_at": "2026-04-20T15:30:00Z",
  "docs_processed": 145230,
  "docs_output_ok": 145220,
  "docs_output_err": 10,
  "error_count": 3,
  "last_error": "connection timeout",
  "checkpoint_seq": "12345-g1AAAAG...",
  "input_name": "SG-US",
  "output_name": "PostgreSQL-Prod",
  "system": {
    "middleware_threads": 2,
    "max_threads": 5,
    "enabled": true
  }
}
```

**Actions Column (per job):**
```html
<div class="join join-horizontal join-xs">
  <button class="btn btn-xs join-item" id="job-start-{id}" onclick="startJob('{id}')" title="Start">▶</button>
  <button class="btn btn-xs join-item" id="job-stop-{id}" onclick="stopJob('{id}')" title="Stop (graceful)">⏸</button>
  <button class="btn btn-xs join-item" id="job-restart-{id}" onclick="restartJob('{id}')" title="Restart">⟳</button>
  <button class="btn btn-xs join-item btn-error" id="job-kill-{id}" onclick="killJob('{id}')" title="Kill (non-graceful)">⏹</button>
  <button class="btn btn-xs join-item" id="job-edit-{id}" onclick="editJob('{id}')" title="Edit">✎</button>
</div>
```

**Status color coding:**
- `running` → green dot ●
- `stopped` → gray dot ●
- `error` → red dot ●
- `starting` → yellow dot ● (spinning)
- `restarting` → yellow dot ● (spinning)

#### 1b. Architecture Diagram — Job Selection Aware

When a job is selected from the dropdown:
- Update the architecture visualization to show that job's metrics only
- Update config summary to show that job's input/output/mapping
- If "All Jobs" selected, aggregate metrics across all running jobs

```javascript
function handleJobChange(event) {
  selectedJobId = event.target.value || null;
  updateDashboard(selectedJobId);  // Refresh all visualizations
}

function updateDashboard(jobId) {
  fetchJobMetrics(jobId).then(metrics => {
    updateArchData(metrics);
    updateConfigSummary(metrics);
    updateJobStatusTable();
  });
}
```

#### 1c. Config Summary — Show Selected Job

If a job is selected, show:
- **Source:** Job's input name, host, database, collection, auth method
- **Process:** Job's system config (max_retries, batch_size, middleware enabled)
- **Output:** Job's output destination, type, target connection

If "All Jobs" is selected, show aggregate counts:
- **Source:** N inputs across all jobs
- **Process:** Aggregate docs in/out/err
- **Output:** N outputs across all jobs

---

### 2. `logs.html` — Job Filtering

**Current state:** Global logs, pipeline stage filters (source, process, dlq, output), level filters  
**New feature:** Add **Job ID filter dropdown**

```html
<!-- Row 2: Pipeline + Level + Job Filter -->
<div class="flex flex-wrap items-center gap-1.5">
  <span class="text-xs font-semibold opacity-60">Job:</span>
  <select id="jobFilter" class="select select-xs select-bordered w-48" onchange="handleJobFilterChange(event)">
    <option value="">All Jobs</option>
    <!-- Populated from job list -->
  </select>
  
  <span class="text-xs font-semibold opacity-60 ml-4">Pipeline:</span>
  <button class="btn btn-xs stage-btn" data-stage="source" ...>● Source</button>
  <!-- etc -->
</div>
```

**Filtering logic:**
- Default: "All Jobs"
- When a job is selected, filter logs by `job_id` field
- Each log entry must have a `job_id` tag (added by Pipeline logger)
- Counts badge adjust per job

**Backend requirement:**
- Log entries must include `job_id` metadata
- Example: `[2026-04-20 15:30:45.123] job_id=job::abc123 [SOURCE] Received doc: ...`

---

### 3. `dlq.html` — Job Filtering + Data Quality Tab

#### 3a. Job Filter Dropdown

Add after "Entries" heading:

```html
<div class="flex items-center justify-between mb-3">
  <h2 class="card-title text-sm">Entries</h2>
  <div class="flex items-center gap-2">
    <select id="jobFilter" class="select select-bordered select-sm w-48" onchange="filterByJob(this.value)">
      <option value="">All Jobs</option>
      <!-- Populated from job list -->
    </select>
    <select id="reasonFilter" class="select select-bordered select-sm w-48" onchange="filterByReason(this.value)">
      <option value="">All Reasons</option>
      <!-- etc -->
    </select>
  </div>
</div>
```

**DLQ Entry Schema (updated):**
```json
{
  "id": "dlq::doc123::1713619445123",
  "doc_id_original": "doc123",
  "job_id": "job::abc123",
  "seq": "12345-g1AAAAG...",
  "method": "POST",
  "target_url": "http://api.example.com/insert",
  "status": 500,
  "error": "connection timeout",
  "reason": "connection|data_error|client_error|server_error|shutdown|redirect|unknown",
  "doc_data": { /* original doc */ },
  "time": "2026-04-20T15:30:45.123Z",
  "expires_at": "2026-04-27T15:30:45.123Z",
  "replay_attempts": 2,
  "retried": false
}
```

#### 3b. Data Quality & Fixed Documents Tab

**Current:** DLQ is for failed delivery  
**New:** Track docs that were **successfully delivered but with coerced values**

Add a **tabbed interface** to DLQ page:

```html
<div class="tabs" id="dlqTabs">
  <input type="radio" name="dlq_tab" class="tab" label="DLQ (Failed)" id="tab-dlq" checked />
  <input type="radio" name="dlq_tab" class="tab" label="Data Quality (Fixed)" id="tab-dq" />
  <input type="radio" name="dlq_tab" class="tab" label="Audit Log (Beta)" id="tab-audit" />
</div>

<!-- Tab content: DLQ (existing table) -->
<div class="tab-content" id="content-dlq">
  <!-- Existing DLQ table -->
</div>

<!-- Tab content: Data Quality -->
<div class="tab-content hidden" id="content-dq">
  <div class="card bg-base-100 shadow rounded-2xl">
    <div class="card-body p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold">Fixed Documents</h3>
        <div class="flex items-center gap-2">
          <select id="dqJobFilter" class="select select-bordered select-sm w-48" onchange="filterDQByJob(this.value)">
            <option value="">All Jobs</option>
          </select>
          <select id="dqTypeFilter" class="select select-bordered select-sm w-48" onchange="filterDQByType(this.value)">
            <option value="">All Coercion Types</option>
            <option value="type_coerce">Type Coercion</option>
            <option value="truncate">String Truncation</option>
            <option value="overflow">Numeric Overflow</option>
            <option value="parse_error">Parse Error</option>
          </select>
        </div>
      </div>
      
      <!-- Data Quality Table -->
      <div class="overflow-x-auto">
        <table class="table table-sm table-zebra" id="dqTable">
          <thead>
            <tr>
              <th>Doc ID</th>
              <th>Job</th>
              <th>Field</th>
              <th>Type</th>
              <th>Original</th>
              <th>Fixed</th>
              <th>Time</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="dqTableBody">
            <tr><td colspan="8" class="text-center opacity-50 py-4">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- Tab content: Audit Log (Beta) -->
<div class="tab-content hidden" id="content-audit">
  <!-- See section below -->
</div>
```

**Data Quality Entry Schema:**
```json
{
  "id": "dq::job::abc123::doc456::1713619445123",
  "job_id": "job::abc123",
  "doc_id": "doc456",
  "field": "user_age",
  "type": "type_coerce|truncate|overflow|parse_error",
  "original_value": "999999999999",
  "coerced_value": 2147483647,
  "reason": "exceeded INT32 max, clamped",
  "table_name": "users",
  "column_name": "age",
  "time": "2026-04-20T15:30:45.123Z",
  "ttl_seconds": 604800  // 7 days default
}
```

**Data Quality Detail Modal:**

When a row is clicked, show side-by-side comparison:

```html
<div id="dqDetailModal" class="modal">
  <div class="modal-box max-w-2xl">
    <h3 class="font-bold text-lg">Data Coercion Detail</h3>
    
    <div class="grid grid-cols-2 gap-4 mt-4">
      <!-- Left: Original -->
      <div>
        <h4 class="font-semibold text-sm mb-2">Original Value</h4>
        <div class="bg-base-300 p-3 rounded font-mono text-sm">
          <!-- original value (possibly invalid) -->
        </div>
        <p class="text-xs opacity-60 mt-2">Source type: string</p>
      </div>
      
      <!-- Right: Fixed -->
      <div>
        <h4 class="font-semibold text-sm mb-2">Delivered Value</h4>
        <div class="bg-base-300 p-3 rounded font-mono text-sm">
          <!-- coerced value -->
        </div>
        <p class="text-xs opacity-60 mt-2">Target type: int32</p>
      </div>
    </div>
    
    <!-- Reason & Recommendation -->
    <div class="alert alert-info mt-4 py-2">
      <strong>Reason:</strong> <span id="dqReason"></span>
    </div>
    <div class="alert alert-warning mt-2 py-2">
      <strong>Review Required?</strong> <span id="dqReview"></span>
    </div>
  </div>
</div>
```

---

### 4. New: `data_quality.html` — Dedicated Data Quality Page (Optional)

Alternative to tabbed DLQ: a dedicated page with more detail.

**Features:**
- Grouped by job, table, column
- Time-series chart of coercions
- Downloadable CSV export
- Alerting rules: "notify if >1000 coercions in 1 hour on critical_table.price"

(Out of scope for v2.0, consider for v2.1)

---

### 5. New: `job_manager.html` — Job Control Modal / Page

**Purpose:** Modal or dedicated page to manage job lifecycle

**Layout:**

```html
<div id="jobManagerModal" class="modal">
  <div class="modal-box max-w-3xl">
    <h2 class="font-bold text-lg mb-4">Job Manager</h2>
    
    <!-- List all jobs with controls -->
    <div class="overflow-x-auto">
      <table class="table table-sm">
        <thead>
          <tr>
            <th>Job</th>
            <th>Status</th>
            <th>Uptime</th>
            <th>Errors</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="jobManagerTable">
          <!-- Populated dynamically -->
        </tbody>
      </table>
    </div>
    
    <!-- Controls at bottom -->
    <div class="modal-action mt-6">
      <button class="btn btn-sm btn-secondary" onclick="restartAllJobs()">Restart All</button>
      <button class="btn btn-sm btn-secondary" onclick="goOffline()">Stop All (Graceful)</button>
      <button class="btn btn-sm btn-error" onclick="killAllJobs()">Kill All (Force)</button>
      <button class="btn btn-sm btn-ghost" onclick="closeJobManager()">Close</button>
    </div>
  </div>
</div>
```

**Action Buttons:**

Per job:
- **Start** — Only visible if status is "stopped"
- **Stop** — Graceful shutdown, waits for in-flight docs
- **Restart** — Stop + start
- **Kill** — Non-graceful, immediate termination, checkpoint may be lost
- **Edit** — Open wizard to modify job config

**Prevent Delete While Running:**
- If status is "running" or "starting", disable/hide delete button
- Show tooltip: "Stop this job before deleting"
- Only allow delete after "stopped" state

---

### 6. New: `audit_log.html` / Tab — Audit Trail (Beta)

**Purpose:** Track admin actions (job start/stop/kill, DLQ replay, config edits)

**Tab in DLQ or dedicated page:**

```html
<div class="tab-content hidden" id="content-audit">
  <div class="card bg-base-100 shadow rounded-2xl">
    <div class="card-body p-4">
      <h3 class="text-sm font-semibold mb-3">Audit Log (Beta)</h3>
      
      <div class="flex items-center gap-2 mb-3">
        <select id="auditActionFilter" class="select select-bordered select-sm" onchange="filterAuditByAction(this.value)">
          <option value="">All Actions</option>
          <option value="job_start">Job Started</option>
          <option value="job_stop">Job Stopped</option>
          <option value="job_restart">Job Restarted</option>
          <option value="job_kill">Job Killed</option>
          <option value="dlq_replay">DLQ Replay</option>
          <option value="config_edit">Config Edit</option>
          <option value="job_delete">Job Deleted</option>
        </select>
        <select id="auditUserFilter" class="select select-bordered select-sm" onchange="filterAuditByUser(this.value)">
          <option value="">All Users</option>
          <!-- Populated -->
        </select>
        <input type="date" id="auditDateFilter" class="input input-bordered input-sm" onchange="filterAuditByDate(this.value)" />
      </div>
      
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>User</th>
              <th>Action</th>
              <th>Resource</th>
              <th>Details</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="auditTableBody">
            <tr><td colspan="6" class="text-center opacity-50 py-4">Loading audit log...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
```

**Audit Log Entry Schema:**
```json
{
  "id": "audit::2026-04-20T15:30:45.123Z::uuid",
  "timestamp": "2026-04-20T15:30:45.123Z",
  "user": "admin@example.com",  // From session or API key
  "action": "job_start|job_stop|job_kill|dlq_replay|config_edit|job_delete|restart_all",
  "resource_type": "job|dlq|config",
  "resource_id": "job::abc123 or dlq::doc456 or config",
  "details": {
    "job_name": "SG-US → PostgreSQL",
    "reason": "user initiated",
    "prev_state": "stopped",
    "new_state": "running"
  },
  "status": "success|failure",
  "error_message": null
}
```

---

### 7. New: `users.html` — Users & RBAC (Beta)

**Purpose:** Foundation for future role-based access control

**Current state:** No authentication in v2.0; admin UI is open  
**v2.0 scope:** UI skeleton only  
**v2.2+ scope:** Implement actual RBAC

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Changes Worker — Users (Beta)</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
  <link href="/static/css/daisyui.css" rel="stylesheet" type="text/css" />
  <link href="/static/css/themes.css" rel="stylesheet" type="text/css" />
  <link href="/static/css/sidebar.css" rel="stylesheet" type="text/css" />
  <script src="/static/js/tailwind.js"></script>
</head>
<body class="min-h-screen bg-base-200">
  <div id="sidebar-root"></div>
  <div class="sidebar-main">
    <main class="w-full mx-auto p-6 space-y-4">
      
      <!-- Header -->
      <div class="card bg-base-100 shadow rounded-2xl">
        <div class="card-body p-4">
          <div class="flex items-center justify-between">
            <h1 class="text-lg font-bold">Users & Access Control (Beta)</h1>
            <button class="btn btn-sm btn-primary" onclick="openAddUserModal()">+ Add User</button>
          </div>
          <p class="opacity-60 text-sm mt-2">
            User management and role-based access control (RBAC) coming in v2.2. 
            This page is a preview.
          </p>
        </div>
      </div>
      
      <!-- Users table -->
      <div class="card bg-base-100 shadow rounded-2xl">
        <div class="card-body p-4">
          <h2 class="card-title text-sm mb-3">Users</h2>
          <div class="overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Created</th>
                  <th>Last Login</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="usersTableBody">
                <tr>
                  <td colspan="7" class="text-center opacity-50 py-8">
                    No users yet. Coming soon.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      
      <!-- Roles & Permissions (info only) -->
      <div class="card bg-base-100 shadow rounded-2xl">
        <div class="card-body p-4">
          <h2 class="card-title text-sm mb-3">Roles (v2.2+)</h2>
          <div class="space-y-2">
            <div>
              <h3 class="font-semibold text-sm">Admin</h3>
              <p class="text-xs opacity-60">Full access: job control, config edit, audit log, user management</p>
            </div>
            <div>
              <h3 class="font-semibold text-sm">Operator</h3>
              <p class="text-xs opacity-60">Limited: start/stop jobs, view logs/DLQ, replay DLQ (no config edit, no delete)</p>
            </div>
            <div>
              <h3 class="font-semibold text-sm">Viewer</h3>
              <p class="text-xs opacity-60">Read-only: view dashboard, logs, DLQ, audit log</p>
            </div>
          </div>
        </div>
      </div>
      
    </main>
  </div>

  <script src="/static/js/sidebar.js"></script>
</body>
</html>
```

---

## REST API Endpoints (Updated)

### Jobs

```
GET    /api/jobs                    — List all jobs with state
GET    /api/jobs/{id}               — Get single job details
GET    /api/jobs/{id}/state         — Get job runtime state (uptime, error count, etc)
POST   /api/jobs/{id}/start         — Start a job
POST   /api/jobs/{id}/stop          — Stop a job gracefully
POST   /api/jobs/{id}/restart       — Restart a job
POST   /api/jobs/{id}/kill          — Kill a job non-gracefully
POST   /api/_restart_all            — Restart all jobs
POST   /api/_offline                — Stop all jobs
POST   /api/_online                 — Start all jobs (restore from _offline)
```

### Logs (Job-Aware)

```
GET    /api/logs                    — Get logs (optional query params: job_id, level, stage, since)
GET    /api/logs/{job_id}           — Get logs for a specific job
```

### DLQ (Job-Aware)

```
GET    /api/dlq                     — List DLQ entries (query: job_id, reason, limit, offset)
GET    /api/dlq/{id}                — Get single DLQ entry
DELETE /api/dlq/{id}                — Delete single DLQ entry
DELETE /api/dlq                     — Clear all DLQ entries
POST   /api/dlq/{id}/replay         — Replay a single DLQ entry
POST   /api/dlq?job_id={id}/replay  — Replay all entries for a job
```

### Data Quality

```
GET    /api/data_quality            — List data quality entries (query: job_id, type, field)
GET    /api/data_quality/{id}       — Get detail
```

### Audit Log

```
GET    /api/audit_log               — List audit entries (query: action, user, date_from, date_to)
```

### Users (v2.2+)

```
GET    /api/users                   — List users
POST   /api/users                   — Create user
PUT    /api/users/{id}              — Update user
DELETE /api/users/{id}              — Delete user
```

---

## Frontend State Management

### Global Job Cache

```javascript
// Cache of all jobs with current state
var jobsCache = {
  "job::abc123": {
    id: "job::abc123",
    name: "SG-US → PostgreSQL",
    status: "running",
    uptime_seconds: 3600,
    // ... etc
  },
  "job::def456": {
    id: "job::def456",
    name: "SG-EU → HTTP",
    status: "stopped",
    // ... etc
  }
};

var selectedJobId = null;  // Currently selected job (null = all)

function loadJobs() {
  return fetch('/api/jobs')
    .then(r => r.json())
    .then(jobs => {
      jobsCache = {};
      jobs.forEach(j => jobsCache[j.id] = j);
      populateJobSelectors();
      updateJobStatusTable();
    });
}

function populateJobSelectors() {
  // Populate all <select id="jobSelector"> dropdowns
  var selects = document.querySelectorAll('[id*="jobFilter"], #jobSelector');
  var opts = '<option value="">All Jobs</option>';
  Object.values(jobsCache).forEach(j => {
    opts += '<option value="' + escHtml(j.id) + '">' + escHtml(j.name) + '</option>';
  });
  selects.forEach(s => s.innerHTML = opts);
}
```

### Job Control Functions

```javascript
function startJob(jobId) {
  fetch('/api/jobs/' + jobId + '/start', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast(jobsCache[jobId].name + ' started', 'success');
      loadJobs();  // Refresh status
    })
    .catch(err => showToast('Failed to start job: ' + err.message, 'error'));
}

function stopJob(jobId) {
  if (!confirm('Stop ' + jobsCache[jobId].name + ' gracefully?')) return;
  fetch('/api/jobs/' + jobId + '/stop', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast(jobsCache[jobId].name + ' stopping...', 'success');
      loadJobs();
    })
    .catch(err => showToast('Failed to stop job', 'error'));
}

function killJob(jobId) {
  if (!confirm('KILL ' + jobsCache[jobId].name + ' (non-graceful)? This may lose checkpoint.')) return;
  fetch('/api/jobs/' + jobId + '/kill', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast(jobsCache[jobId].name + ' killed', 'warning');
      loadJobs();
    })
    .catch(err => showToast('Failed to kill job', 'error'));
}

function restartJob(jobId) {
  fetch('/api/jobs/' + jobId + '/restart', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast(jobsCache[jobId].name + ' restarting...', 'success');
      loadJobs();
    })
    .catch(err => showToast('Failed to restart job', 'error'));
}
```

### Filtering Helper

```javascript
function handleJobFilterChange(event) {
  var jobId = event.target.value || null;
  selectedJobId = jobId;
  
  // Fetch filtered logs
  var url = '/api/logs';
  if (jobId) url += '?job_id=' + jobId;
  
  fetch(url)
    .then(r => r.json())
    .then(logs => {
      currentLogs = logs;
      renderLogs();
      updateCharts();
    });
}
```

---

## Database Schema Updates

### Collections

**New fields in existing collections:**

#### `jobs` collection
```json
{
  "_id": "job::12345678-1234-5678-1234-567812345678",
  "type": "job",
  "name": "SG-US → PostgreSQL",
  "enabled": true,
  "input_id": "sg-us",
  "output_id": "postgres-prod",
  "mapping_id": "default",
  "system": {
    "middleware_threads": 2,
    "max_retries": 5,
    "batch_size": 100
  },
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-04-20T15:30:00Z"
}
```

**New collections:**

#### `data_quality` collection
```json
{
  "_id": "dq::job::abc123::doc456::1713619445123",
  "type": "data_quality",
  "job_id": "job::abc123",
  "doc_id": "doc456",
  "field": "user_age",
  "coerce_type": "type_coerce|truncate|overflow|parse_error",
  "original_value": "999999999999",
  "coerced_value": 2147483647,
  "table_name": "users",
  "column_name": "age",
  "timestamp": "2026-04-20T15:30:45.123Z",
  "ttl": 604800
}
```

#### `audit_log` collection
```json
{
  "_id": "audit::2026-04-20T15:30:45.123Z::uuid",
  "type": "audit_log",
  "timestamp": "2026-04-20T15:30:45.123Z",
  "user": "admin@example.com",
  "action": "job_start|job_stop|...",
  "resource_type": "job|dlq|config",
  "resource_id": "job::abc123",
  "details": { /* action-specific */ },
  "status": "success|failure",
  "error_message": null
}
```

#### `users` collection (v2.2+)
```json
{
  "_id": "user::admin@example.com",
  "type": "user",
  "username": "admin",
  "email": "admin@example.com",
  "password_hash": "...",
  "role": "admin|operator|viewer",
  "created_at": "2026-01-01T00:00:00Z",
  "last_login": "2026-04-20T15:30:00Z",
  "enabled": true
}
```

---

## Implementation Checklist

### Phase 8b: Dashboard Updates (Multi-Job)

- [ ] Add job status table to `index.html`
- [ ] Implement job selector dropdown (all pages)
- [ ] Fetch `/api/jobs` on page load
- [ ] Update architecture diagram to be job-aware
- [ ] Add job action buttons (start, stop, restart, kill)
- [ ] Create `job_manager.html` modal or page
- [ ] Add "Prevent delete while running" validation

### Phase 8c: Logs & DLQ Job Filtering

- [ ] Add job filter dropdown to `logs.html`
- [ ] Add job filter dropdown to `dlq.html`
- [ ] Update backend logging to include `job_id` field
- [ ] Update `/api/logs` to support `?job_id=` query param
- [ ] Update `/api/dlq` to support `?job_id=` query param

### Phase 11a: Data Quality Tab

- [ ] Add `data_quality` collection to CBL schema
- [ ] Create data quality middleware (coerce, truncate, overflow handlers)
- [ ] Implement `/api/data_quality` endpoint
- [ ] Add "Data Quality (Fixed)" tab to `dlq.html`
- [ ] Create data quality detail modal with side-by-side comparison

### Phase 11b: Audit Log

- [ ] Add `audit_log` collection to CBL schema
- [ ] Log all job lifecycle events (start, stop, kill, restart)
- [ ] Log all DLQ replays, config edits, job deletes
- [ ] Implement `/api/audit_log` endpoint
- [ ] Add "Audit Log (Beta)" tab to `dlq.html` or create `audit_log.html`
- [ ] Add filters (action, user, date)

### v2.2: Users & RBAC (Beta)

- [ ] Create `users.html` skeleton
- [ ] Add `users` collection to CBL schema
- [ ] Implement `/api/users` CRUD endpoints
- [ ] Add session/token management (optional in v2.0)
- [ ] Add role checks to admin UI (hide/disable buttons based on role)

---

## Security Considerations

1. **Delete protection:** Only allow delete of stopped jobs
2. **Kill confirmation:** Show confirmation dialog before kill
3. **Audit trail:** Every action logged with user + timestamp
4. **RBAC foundation:** Prepare for role-based restrictions (v2.2)
5. **API validation:** Server-side checks on all job state transitions

---

## Testing Strategy

1. **Unit:** Job state transitions (start → running, stop → stopped, etc)
2. **Integration:** 3 jobs concurrently; verify filtering works per job
3. **E2E:** Full workflow: start job, view logs filtered by job, stop job, verify audit log
4. **Load:** 10 concurrent job managers open; verify no race conditions

---

## Future Considerations (v2.1+)

- **Job scheduling:** Cron-like "run only between 2am–6am"
- **Alerting:** "Notify if job crashes >3 times in 1 hour"
- **Job templates:** "PostgreSQL CDC standard" pre-filled wizard
- **Secrets:** Encrypted credential store (don't store passwords in config)
- **Metrics export:** Prometheus-compatible metrics per job
