# Job Builder — Design Concept

> Style reference: `guide/STYLE_HTML_CSS.md` — DaisyUI 5.x, theme tokens only, cards for all content, pipeline border accents, buttons inside cards.

## Problem

The current sidebar under **TOOLS** only has "Schema Mapping" and "Wizards." There's no dedicated page to **construct a Job** — the thing that ties together a Source (Input), Process type, Schema Mapping, and Output into a runnable pipeline. The Wizard page is overloaded with configuration that belongs in a Job Builder context (Inputs, Outputs, RDBMS, Cloud, Data Source, Schema Mapping wizard, Jobs manager — all crammed into one wizard landing page).

## Goal

A new **Job Builder** page (`/jobs`) added to the sidebar under **TOOLS** that lets users:

1. See all existing Jobs in a table (with status, controls)
2. Click **"+ New Job"** to open a visual, step-by-step Job construction flow
3. Configure Source → Process → Output in a linear left-to-right visual (matching the architecture diagram the user drew)
4. Save the assembled Job

The **Wizard page remains** but is slimmed down to only contain actual "wizard" workflows (Settings Q&A, guided setup helpers).

---

## Proposed Sidebar Change

```
OVERVIEW
  ▪ Dashboard

TOOLS
  ▪ Job Builder       ← NEW (the main construction page)
  ▪ Schema Mapping    (stays — used as sub-step inside Job Builder too)
  ▪ Wizards           (stays — slimmed to Settings/guided helpers only)

SYSTEM
  ▪ Dead Letters
  ▪ Logs
  ▪ Settings

REFERENCE
  ▪ Glossary
  ▪ Help
```

---

## Page Structure (follows `guide/STYLE_HTML_CSS.md`)

```html
<!-- Standard boilerplate: daisyui.css, themes.css, sidebar.css, tailwind.js -->
<body class="min-h-screen bg-base-200">
  <div id="sidebar-root"></div>
  <div class="sidebar-main">
    <main class="w-full mx-auto p-6 space-y-6">
      <!-- Card 1: Jobs Table -->
      <!-- Card 2: Job Builder (3-step progress + 3-column builder) -->
    </main>
  </div>
  <script src="/static/js/sidebar.js"></script>
</body>
```

---

## Card 1 — Jobs Table

Standard card (`card bg-base-100 shadow rounded-2xl`) with compact body (`p-4`).

```html
<div class="card bg-base-100 shadow rounded-2xl">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <h3 class="text-sm font-semibold">Jobs</h3>
      <span id="jobCount" class="badge badge-ghost badge-sm">0</span>
      <div class="tooltip tooltip-right" data-tip="All configured pipeline jobs.">
        <span class="badge badge-ghost badge-sm cursor-help">?</span>
      </div>
      <!-- "+ New Job" stays inside the card body, not pushed to edge -->
      <button class="btn btn-primary btn-sm ml-auto" onclick="showJobBuilder()">+ New Job</button>
    </div>
    <div class="overflow-x-auto">
      <table class="table table-sm table-zebra w-full">
        <thead>
          <tr class="bg-base-200">
            <th class="text-xs font-semibold">Status</th>
            <th class="text-xs font-semibold">Job Name</th>
            <th class="text-xs font-semibold">Source Name / Type</th>
            <th class="text-xs font-semibold">Process</th>
            <th class="text-xs font-semibold">Output Name / Type</th>
            <th class="text-xs font-semibold">Last Run Date</th>
            <th class="text-xs font-semibold">Threads</th>
            <th class="text-xs font-semibold text-right">Actions</th>
          </tr>
        </thead>
        <tbody id="jobsTableBody">
          <!-- dynamic rows -->
        </tbody>
      </table>
    </div>
  </div>
</div>
```

### Table Row Spec

| Column | Content | Notes |
|--------|---------|-------|
| Status | `<span class="badge badge-success badge-sm">Online</span>` or `badge-error` Offline | Uses theme semantic colors |
| Job Name | Text, clickable to edit | |
| Source Name / Type | `sg-us-prices` / Sync Gateway | Two-line: name bold, type `opacity-60` |
| Process | `D` or `DA` | Badge: `badge-info badge-sm` |
| Output Name / Type | `pg-prod` / RDBMS | Same two-line pattern as Source |
| Last Run Date | ISO date or `--` | `opacity-60` when never run |
| Threads | Number | `font-mono` |
| Actions | `btn-ghost btn-xs` buttons | Start · Stop · Restart · Edit · Delete — all same size |

---

## Card 2 — Job Builder Panel

Shown when "+ New Job" is clicked or when editing. Hidden by default (`class="hidden"`).

### Progress Steps (top of builder card)

Uses DaisyUI steps (same pattern as `schema.html`):

```html
<div class="card bg-base-100 shadow-sm rounded-2xl border-process">
  <div class="card-body p-3">
    <ul class="steps steps-horizontal w-full text-sm">
      <li id="jbStep1" class="step step-error" data-content="✕">Source Selected</li>
      <li id="jbStep2" class="step step-error" data-content="✕">Process Configured</li>
      <li id="jbStep3" class="step step-error" data-content="✕">Output Selected</li>
    </ul>
  </div>
</div>
```

Steps toggle `step-success` + `data-content="✓"` as each section is completed.

### Job Name + Save (action bar card)

```html
<div class="card bg-base-100 shadow-sm rounded-2xl">
  <div class="card-body p-4 flex-row items-center gap-3 flex-wrap">
    <div class="flex items-center gap-2">
      <label class="label label-text text-xs font-semibold">Job Name</label>
      <input id="jobName" type="text" class="input input-bordered input-sm w-64" placeholder="My Sync Job" />
    </div>
    <div class="flex items-center gap-2 ml-auto">
      <button class="btn btn-ghost btn-sm" onclick="cancelJobBuilder()">Cancel</button>
      <button class="btn btn-success btn-sm" onclick="saveJob()">Save Job</button>
      <span id="jobSaveStatus" class="text-sm opacity-60"></span>
    </div>
  </div>
</div>
```

> **Note:** Buttons are same size (`btn-sm`), inside card padding, per style guide rules.

### 3-Column Builder

```html
<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
  <!-- SOURCE card (border-source = green left border) -->
  <div class="card bg-base-100 shadow-sm rounded-2xl border-source">...</div>

  <!-- PROCESS card (border-process = blue left border) -->
  <div class="card bg-base-100 shadow-sm rounded-2xl border-process">...</div>

  <!-- OUTPUT card (border-output = amber left border) -->
  <div class="card bg-base-100 shadow-sm rounded-2xl border-output">...</div>
</div>
```

Uses the existing pipeline accent classes: `border-source`, `border-process`, `border-output`.

---

## Source Card (Left — `border-source`)

Each source type shows a **count badge** — 🔴 red with `0` = none configured, 🟢 green with count = configured. Clicking a type drills down into a table of saved instances for that type.

### Source Type List (default view)

```html
<div class="card bg-base-100 shadow-sm rounded-2xl border-source">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <h4 class="text-xs font-bold uppercase" style="color:var(--color-success)">Source</h4>
      <div class="tooltip tooltip-right" data-tip="Choose which saved Input to read _changes from.">
        <span class="badge badge-ghost badge-sm cursor-help">?</span>
      </div>
    </div>

    <!-- Source type list with count badges -->
    <div id="jbSourceTypeList" class="space-y-2">
      <!-- Each row: clickable, shows type name + count badge -->
      <!-- count=0 → badge-error (red), count>=1 → badge-success (green) -->
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowSourceType('sync_gateway')">
        <span class="badge badge-error badge-sm font-bold" id="jbSrcCountSG">0</span>
        <span class="text-sm font-semibold">Couchbase Sync Gateway</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowSourceType('app_services')">
        <span class="badge badge-error badge-sm font-bold" id="jbSrcCountAS">0</span>
        <span class="text-sm font-semibold">Couchbase App Services</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowSourceType('edge_server')">
        <span class="badge badge-error badge-sm font-bold" id="jbSrcCountES">0</span>
        <span class="text-sm font-semibold">Couchbase Edge Server</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowSourceType('couchdb')">
        <span class="badge badge-error badge-sm font-bold" id="jbSrcCountCDB">0</span>
        <span class="text-sm font-semibold">CouchDB</span>
      </div>
    </div>
  </div>
</div>
```

**Badge logic (JS):**
```js
// count = number of saved inputs for this type
badge.className = count > 0
  ? 'badge badge-success badge-sm font-bold'   // 🟢 green
  : 'badge badge-error badge-sm font-bold';     // 🔴 red
badge.textContent = count;
```

### Source Drill-Down Table (shown when a type is clicked)

Clicking e.g. "Couchbase Sync Gateway" replaces the type list with a table of configured SG inputs:

```html
<div id="jbSourceDrillDown" class="hidden">
  <div class="flex items-center justify-between mb-3">
    <button class="btn btn-ghost btn-xs" onclick="jbBackToSourceTypes()">← Back</button>
    <span class="text-sm font-semibold" id="jbSrcDrillTitle">Sync Gateway Inputs</span>
    <button class="btn btn-primary btn-xs" onclick="jbAddNewSource()">+</button>
  </div>

  <div class="overflow-x-auto">
    <table class="table table-xs table-zebra w-full">
      <thead>
        <tr class="bg-base-200">
          <th class="text-xs font-semibold">Name</th>
          <th class="text-xs font-semibold">URL / db.scope.collection</th>
          <th class="text-xs font-semibold">Channels</th>
          <th class="text-xs font-semibold">Auth Type</th>
          <th class="text-xs font-semibold">In Job</th>
          <th class="text-xs font-semibold text-right">Actions</th>
        </tr>
      </thead>
      <tbody id="jbSrcDrillBody">
        <!-- Example row with channels: -->
        <tr>
          <td class="font-semibold">sg-us-prices</td>
          <td class="font-mono text-xs">
            http://localhost:4984<br/>
            <span class="opacity-60">db.us.prices</span>
          </td>
          <td>
            <!-- Channel tags — shown as small badges, empty = "all" -->
            <span class="badge badge-primary badge-xs">retail</span>
            <span class="badge badge-primary badge-xs">wholesale</span>
          </td>
          <td><span class="badge badge-ghost badge-xs">Basic</span></td>
          <td>
            <span class="badge badge-success badge-xs">Job-A</span>
          </td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectSource('sg-us-prices')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestSourceConn('sg-us-prices')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditSource('sg-us-prices')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteSource('sg-us-prices')" disabled>Delete</button>
            </div>
          </td>
        </tr>
        <!-- Example row with no channels: -->
        <tr>
          <td class="font-semibold">sg-all-data</td>
          <td class="font-mono text-xs">
            http://localhost:4984<br/>
            <span class="opacity-60">db._default._default</span>
          </td>
          <td>
            <span class="text-xs opacity-40 italic">all</span>
          </td>
          <td><span class="badge badge-ghost badge-xs">Bearer</span></td>
          <td><span class="opacity-60">--</span></td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectSource('sg-all-data')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestSourceConn('sg-all-data')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditSource('sg-all-data')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteSource('sg-all-data')">Delete</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- Inline create form (toggled by "+" button) -->
  <div id="jbSourceForm" class="hidden mt-4">
    <!-- Same fields as wizard Inputs form, embedded here -->
  </div>
</div>
```

**Key behaviors:**
- **"In Job" column** — shows which job(s) use this input; if in an active job, the Delete button is `disabled`
- **"Test" button** — calls `POST /api/test-connection` with the input's config
- **"Select" button** — selects this input for the current job being built, marks step 1 ✓
- **URL column** — formatted as `{url}` + `{db}.{scope}.{collection}` on second line
- **Channels column** — shows channel filter tags as `badge-primary badge-xs`; if empty shows italic "all"

#### Channels Filter — Tagify Input (in Create/Edit form)

Channels are an **optional server-side filter** for the `_changes` feed. Supported by Sync Gateway, App Services, and Edge Server — **NOT CouchDB** (hidden when source type = CouchDB).

Uses [Tagify](https://yaireo.github.io/tagify/) for a tag-based input (same library already used in `settings.html`):

```html
<!-- Inside the Source create/edit form -->
<div id="jbChannelsWrap">
  <label class="label label-text text-xs font-semibold">
    Channels
    <div class="tooltip tooltip-bottom inline" data-tip="Server-side filter for the _changes feed. Only documents in listed channels are returned. Leave empty for all changes. Not supported by CouchDB.">
      <span class="opacity-40 cursor-help">(?)</span>
    </div>
    <span class="text-xs opacity-40 ml-1">(optional)</span>
  </label>
  <input type="text" id="jbChannelsInput" class="input input-bordered input-sm w-full"
         placeholder="Type a channel and press Enter" />
</div>
```

```js
// Initialize Tagify on the channels input (same config as settings.html)
var jbChannelsTagify = new Tagify(document.getElementById('jbChannelsInput'), {
  delimiters: ',| |Enter',
  trim: true,
  duplicates: false,
  placeholder: 'Type a channel and press Enter'
});

// Hide channels field for CouchDB
function jbOnSourceTypeChange(type) {
  var wrap = document.getElementById('jbChannelsWrap');
  wrap.classList.toggle('hidden', type === 'couchdb');
}

// Read channels when saving
function jbGetChannels() {
  return jbChannelsTagify.value.map(function(t) { return t.value; });
}

// Populate channels when editing an existing input
function jbSetChannels(channels) {
  jbChannelsTagify.removeAllTags();
  jbChannelsTagify.addTags(channels || []);
}
```

**Channel filter applies to:**
| Source Type | Channels Supported |
|---|---|
| Sync Gateway | ✅ |
| App Services | ✅ |
| Edge Server | ✅ |
| CouchDB | ❌ (field hidden) |

---

## Process Card (Center — `border-process`)

```html
<div class="card bg-base-100 shadow-sm rounded-2xl border-process">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <h4 class="text-xs font-bold uppercase" style="color:var(--color-info)">Process</h4>
      <div class="tooltip tooltip-right" data-tip="Choose processing mode and schema mapping.">
        <span class="badge badge-ghost badge-sm cursor-help">?</span>
      </div>
    </div>

    <!-- Process Type (radio cards) -->
    <div class="space-y-2 mb-4">
      <label class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors">
        <input type="radio" name="processType" value="data" class="radio radio-sm radio-info" checked />
        <div>
          <div class="text-sm font-semibold">Data Only</div>
          <div class="text-xs opacity-60">Documents only, no attachments</div>
        </div>
      </label>
      <label class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors">
        <input type="radio" name="processType" value="data_attachments" class="radio radio-sm radio-info" />
        <div>
          <div class="text-sm font-semibold">Data &amp; Attachments</div>
          <div class="text-xs opacity-60">Documents + binary attachments</div>
        </div>
      </label>
    </div>

    <div class="divider text-xs opacity-60">Schema Mapping</div>

    <div>
      <label class="label label-text text-xs font-semibold">Mapping File</label>
      <select id="jbMappingSelect" class="select select-bordered select-sm w-full">
        <option value="">None (pass-through)</option>
        <!-- populated from GET /api/mappings -->
      </select>
      <a href="/schema" target="_blank" class="text-xs text-primary mt-1 inline-block">Create new mapping →</a>
    </div>

    <div class="divider text-xs opacity-60">Settings</div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="label label-text text-xs font-semibold">Threads</label>
        <input id="jbThreads" type="number" class="input input-bordered input-sm w-full" value="4" min="1" />
      </div>
    </div>

    <!-- Attachment config (shown only when Data & Attachments selected) -->
    <div id="jbAttachConfig" class="hidden mt-4">
      <div class="divider text-xs opacity-60">Attachment Settings</div>
      <!-- key attachment destination fields -->
    </div>
  </div>
</div>
```

---

## Output Card (Right — `border-output`)

Same pattern as Source — each output type shows a **count badge** (🔴 red `0` / 🟢 green with count). Clicking a type drills into a table of configured instances.

### Output Type List (default view)

```html
<div class="card bg-base-100 shadow-sm rounded-2xl border-output">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <h4 class="text-xs font-bold uppercase" style="color:var(--color-warning)">Output</h4>
      <div class="tooltip tooltip-right" data-tip="Choose where processed documents are sent.">
        <span class="badge badge-ghost badge-sm cursor-help">?</span>
      </div>
    </div>

    <!-- Output type list with count badges -->
    <div id="jbOutputTypeList" class="space-y-2">
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowOutputType('rdbms')">
        <span class="badge badge-error badge-sm font-bold" id="jbOutCountRDBMS">0</span>
        <span class="text-sm font-semibold">🗄️ RDBMS</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowOutputType('http')">
        <span class="badge badge-error badge-sm font-bold" id="jbOutCountHTTP">0</span>
        <span class="text-sm font-semibold">📡 HTTP</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowOutputType('cloud')">
        <span class="badge badge-error badge-sm font-bold" id="jbOutCountCloud">0</span>
        <span class="text-sm font-semibold">☁️ Cloud Storage</span>
      </div>
      <div class="flex items-center gap-3 p-3 rounded-xl cursor-pointer bg-base-200 hover:bg-base-300 transition-colors"
           onclick="jbShowOutputType('stdout')">
        <span class="badge badge-success badge-sm font-bold" id="jbOutCountStdout">✓</span>
        <span class="text-sm font-semibold">📺 Stdout</span>
        <span class="text-xs opacity-60">(always available)</span>
      </div>
    </div>
  </div>
</div>
```

### Output Drill-Down: RDBMS

Clicking "🗄️ RDBMS" shows configured RDBMS outputs:

```html
<div id="jbOutDrillRdbms" class="hidden">
  <div class="flex items-center justify-between mb-3">
    <button class="btn btn-ghost btn-xs" onclick="jbBackToOutputTypes()">← Back</button>
    <span class="text-sm font-semibold">RDBMS Outputs</span>
    <button class="btn btn-primary btn-xs" onclick="jbAddNewRdbms()">+</button>
  </div>

  <div class="overflow-x-auto">
    <table class="table table-xs table-zebra w-full">
      <thead>
        <tr class="bg-base-200">
          <th class="text-xs font-semibold">Name</th>
          <th class="text-xs font-semibold">URL / IP</th>
          <th class="text-xs font-semibold">Tables</th>
          <th class="text-xs font-semibold">Auth Type</th>
          <th class="text-xs font-semibold">In Job</th>
          <th class="text-xs font-semibold text-right">Actions</th>
        </tr>
      </thead>
      <tbody id="jbOutRdbmsBody">
        <tr>
          <td class="font-semibold">pg-prod</td>
          <td class="font-mono text-xs">
            localhost:5432<br/>
            <span class="opacity-60">mydb / public</span>
          </td>
          <td>
            <!-- Clickable button: opens table detail panel below -->
            <button class="btn btn-info btn-xs" onclick="jbShowTables('pg-prod')">Tables(3)</button>
          </td>
          <td><span class="badge badge-ghost badge-xs">Basic</span></td>
          <td><span class="badge badge-success badge-xs">Job-A</span></td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectOutput('pg-prod')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestOutputConn('pg-prod')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditOutput('pg-prod')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteOutput('pg-prod')" disabled>Delete</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

#### RDBMS Tables Detail Panel

Clicking **"Tables(3)"** expands a panel below the row showing the CREATE TABLE schemas for that RDBMS output. Tables in active jobs are **locked** (cannot be removed).

```html
<!-- Appears below the clicked RDBMS row -->
<div id="jbTablesPanel" class="hidden card bg-base-200 rounded-xl mt-2">
  <div class="card-body p-3">
    <div class="flex items-center justify-between mb-2">
      <h4 class="text-sm font-semibold">Tables — pg-prod</h4>
      <div class="flex gap-1">
        <button class="btn btn-ghost btn-xs" onclick="jbImportDDL('pg-prod')">Import DDL</button>
        <button class="btn btn-ghost btn-xs" onclick="jbIntrospectDb('pg-prod')">Fetch from DB</button>
        <button class="btn btn-primary btn-xs" onclick="jbAddTable('pg-prod')">+ Table</button>
      </div>
    </div>

    <div class="overflow-x-auto">
      <table class="table table-xs w-full">
        <thead>
          <tr class="bg-base-300">
            <th class="text-xs font-semibold">Table Name</th>
            <th class="text-xs font-semibold">Columns</th>
            <th class="text-xs font-semibold">Primary Key</th>
            <th class="text-xs font-semibold">In Job</th>
            <th class="text-xs font-semibold text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td class="font-mono text-xs font-semibold">orders</td>
            <td class="text-xs">
              <span class="badge badge-ghost badge-xs">5 cols</span>
            </td>
            <td class="font-mono text-xs opacity-60">doc_id</td>
            <td>
              <!-- 🔒 locked = in active job, cannot remove -->
              <span class="badge badge-success badge-xs">🔒 Job-A</span>
            </td>
            <td class="text-right">
              <div class="flex gap-1 justify-end">
                <button class="btn btn-ghost btn-xs" onclick="jbViewDDL('orders')">View DDL</button>
                <button class="btn btn-ghost btn-xs" onclick="jbEditTable('orders')">Edit</button>
                <!-- disabled if locked to active job -->
                <button class="btn btn-ghost btn-xs text-error" disabled>Remove</button>
              </div>
            </td>
          </tr>
          <tr>
            <td class="font-mono text-xs font-semibold">order_items</td>
            <td class="text-xs">
              <span class="badge badge-ghost badge-xs">4 cols</span>
            </td>
            <td class="font-mono text-xs opacity-60">id</td>
            <td>
              <span class="badge badge-success badge-xs">🔒 Job-A</span>
            </td>
            <td class="text-right">
              <div class="flex gap-1 justify-end">
                <button class="btn btn-ghost btn-xs" onclick="jbViewDDL('order_items')">View DDL</button>
                <button class="btn btn-ghost btn-xs" onclick="jbEditTable('order_items')">Edit</button>
                <button class="btn btn-ghost btn-xs text-error" disabled>Remove</button>
              </div>
            </td>
          </tr>
          <tr>
            <td class="font-mono text-xs font-semibold">audit_log</td>
            <td class="text-xs">
              <span class="badge badge-ghost badge-xs">3 cols</span>
            </td>
            <td class="font-mono text-xs opacity-60">id</td>
            <td><span class="opacity-60">--</span></td>
            <td class="text-right">
              <div class="flex gap-1 justify-end">
                <button class="btn btn-ghost btn-xs" onclick="jbViewDDL('audit_log')">View DDL</button>
                <button class="btn btn-ghost btn-xs" onclick="jbEditTable('audit_log')">Edit</button>
                <button class="btn btn-ghost btn-xs text-error" onclick="jbRemoveTable('audit_log')">Remove</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- DDL viewer (shown when "View DDL" clicked) -->
    <div id="jbDdlViewer" class="hidden mt-3">
      <pre class="bg-base-200 p-4 rounded-xl font-mono text-xs overflow-x-auto whitespace-pre-wrap border border-base-300"></pre>
    </div>
  </div>
</div>
```

**Tables panel behaviors:**
- **"Tables(N)"** button — `badge-info` styled, count of tables; `badge-error` with `0` if none
- **"Import DDL"** — opens the DDL paste modal (same as current `schema.html` DDL modal)
- **"Fetch from DB"** — introspects the live RDBMS connection, same as current wizard
- **"+ Table"** — add a blank table definition manually
- **"🔒" badge** — table is referenced in an active job's mapping; Remove button `disabled`
- **"View DDL"** — expands a `<pre>` block showing the CREATE TABLE statement
- **"Edit"** — opens inline editor for columns (same as schema.html table editor)
- **"Remove"** — only enabled when table is NOT in any active job

---

### Output Drill-Down: HTTP

Clicking "📡 HTTP" shows configured HTTP endpoint outputs. HTTP has a richer config because it involves URL templates, methods for write/update/delete, headers, auth, format, and health checks.

```html
<div id="jbOutDrillHttp" class="hidden">
  <div class="flex items-center justify-between mb-3">
    <button class="btn btn-ghost btn-xs" onclick="jbBackToOutputTypes()">← Back</button>
    <span class="text-sm font-semibold">HTTP Outputs</span>
    <button class="btn btn-primary btn-xs" onclick="jbAddNewHttp()">+</button>
  </div>

  <div class="overflow-x-auto">
    <table class="table table-xs table-zebra w-full">
      <thead>
        <tr class="bg-base-200">
          <th class="text-xs font-semibold">Name</th>
          <th class="text-xs font-semibold">Target URL</th>
          <th class="text-xs font-semibold">Methods</th>
          <th class="text-xs font-semibold">Format</th>
          <th class="text-xs font-semibold">Auth</th>
          <th class="text-xs font-semibold">In Job</th>
          <th class="text-xs font-semibold text-right">Actions</th>
        </tr>
      </thead>
      <tbody id="jbOutHttpBody">
        <tr>
          <td class="font-semibold">webhook-prod</td>
          <td class="font-mono text-xs">
            https://api.example.com<br/>
            <span class="opacity-60">{target_url}/{doc_id}</span>
          </td>
          <td>
            <!-- Show write + delete methods as badges -->
            <span class="badge badge-info badge-xs">PUT</span>
            <span class="badge badge-error badge-xs">DELETE</span>
          </td>
          <td><span class="badge badge-ghost badge-xs">JSON</span></td>
          <td><span class="badge badge-ghost badge-xs">Bearer</span></td>
          <td><span class="badge badge-success badge-xs">Job-B</span></td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectOutput('webhook-prod')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestHttpEndpoint('webhook-prod')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditHttp('webhook-prod')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteOutput('webhook-prod')" disabled>Delete</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

#### HTTP Output — Create / Edit Form

When clicking **"+"** or **"Edit"**, an inline form expands with these sections:

```html
<div id="jbHttpForm" class="hidden card bg-base-200 rounded-xl mt-3">
  <div class="card-body p-4">
    <h4 class="text-sm font-semibold mb-3">HTTP Output Configuration</h4>

    <!-- Basic info -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Output ID</label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="webhook-prod" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Name</label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="Production Webhook" />
      </div>
    </div>

    <!-- URL & Template -->
    <div class="space-y-3 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">
          Target URL
          <div class="tooltip tooltip-bottom inline" data-tip="Base URL for the endpoint. Used in URL template below.">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="https://api.example.com/docs" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">
          URL Template
          <div class="tooltip tooltip-bottom inline" data-tip="Template for per-document URL. Variables: {target_url}, {doc_id}. Example: {target_url}/{doc_id}">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" value="{target_url}/{doc_id}" />
      </div>
    </div>

    <div class="divider text-xs opacity-60">HTTP Methods</div>

    <!-- Methods -->
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Write Method</label>
        <select class="select select-bordered select-sm w-full">
          <option value="PUT">PUT</option>
          <option value="POST">POST</option>
          <option value="PATCH">PATCH</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Delete Method</label>
        <select class="select select-bordered select-sm w-full">
          <option value="DELETE">DELETE</option>
          <option value="POST">POST</option>
          <option value="none">Don't send deletes</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Output Format</label>
        <select class="select select-bordered select-sm w-full">
          <option value="json">JSON</option>
          <option value="xml">XML</option>
          <option value="form">Form-encoded</option>
          <option value="msgpack">MessagePack</option>
          <option value="csv">CSV</option>
        </select>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Send body on DELETE</span>
          <input type="checkbox" class="checkbox checkbox-sm" />
        </label>
      </div>
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Follow redirects</span>
          <input type="checkbox" class="checkbox checkbox-sm" />
        </label>
      </div>
    </div>

    <div class="divider text-xs opacity-60">Authentication</div>

    <!-- Auth -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Auth Method</label>
        <select class="select select-bordered select-sm w-full" onchange="jbHttpAuthChange(this)">
          <option value="none">None</option>
          <option value="basic">Basic</option>
          <option value="bearer">Bearer Token</option>
          <option value="session">Session Cookie</option>
        </select>
      </div>
      <!-- Auth fields shown/hidden based on method selection -->
      <div id="jbHttpAuthFields">
        <!-- Basic: username + password -->
        <!-- Bearer: token input -->
        <!-- Session: cookie input -->
      </div>
    </div>

    <div class="divider text-xs opacity-60">Timeouts & Retry</div>

    <!-- Timeouts & Retry -->
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Timeout (sec)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="30" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Max Retries</label>
        <input type="number" class="input input-bordered input-sm w-full" value="3" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">
          Retry On Status
          <div class="tooltip tooltip-bottom inline" data-tip="Comma-separated HTTP status codes to retry on.">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" value="500,502,503,504" />
      </div>
    </div>

    <div class="divider text-xs opacity-60">Custom Headers</div>

    <!-- Custom Headers (key-value pairs) -->
    <div id="jbHttpHeaders" class="space-y-1 mb-4">
      <div class="flex gap-2 items-center">
        <input type="text" class="input input-bordered input-xs w-1/3 font-mono" placeholder="Header-Name" />
        <input type="text" class="input input-bordered input-xs w-2/3 font-mono" placeholder="value" />
        <button class="btn btn-ghost btn-xs text-error" onclick="this.parentElement.remove()">✕</button>
      </div>
    </div>
    <button class="btn btn-ghost btn-xs" onclick="jbAddHttpHeader()">+ Header</button>

    <div class="divider text-xs opacity-60">Health Check</div>

    <!-- Health Check -->
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Enabled</span>
          <input type="checkbox" class="checkbox checkbox-sm" checked />
        </label>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Interval (sec)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="30" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Health URL</label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" placeholder="(defaults to target_url)" />
      </div>
    </div>

    <div class="divider text-xs opacity-60">Options</div>

    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Accept Self-Signed Certs</span>
          <input type="checkbox" class="checkbox checkbox-sm" />
        </label>
      </div>
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Log Response Times</span>
          <input type="checkbox" class="checkbox checkbox-sm" checked />
        </label>
      </div>
    </div>

    <!-- Save / Cancel -->
    <div class="flex items-center gap-3 mt-4">
      <button class="btn btn-success btn-sm" onclick="jbSaveHttp()">Save HTTP Output</button>
      <button class="btn btn-ghost btn-sm" onclick="jbCancelHttp()">Cancel</button>
      <span id="jbHttpSaveStatus" class="text-sm opacity-60"></span>
    </div>
  </div>
</div>
```

**HTTP "Test" button behavior:**
1. Sends a `GET` (or configured health check method) to the health check URL
2. If no health check URL, sends to `target_url` with `GET`
3. Shows response status code + latency in a toast
4. Badge on the row turns 🟢 on success, 🔴 on failure

---

### Output Drill-Down: Cloud Storage

Clicking "☁️ Cloud Storage" — simpler than HTTP since cloud storage has a narrower set of config options (provider, bucket, region, key template, auth credentials).

```html
<div id="jbOutDrillCloud" class="hidden">
  <div class="flex items-center justify-between mb-3">
    <button class="btn btn-ghost btn-xs" onclick="jbBackToOutputTypes()">← Back</button>
    <span class="text-sm font-semibold">Cloud Storage Outputs</span>
    <button class="btn btn-primary btn-xs" onclick="jbAddNewCloud()">+</button>
  </div>

  <div class="overflow-x-auto">
    <table class="table table-xs table-zebra w-full">
      <thead>
        <tr class="bg-base-200">
          <th class="text-xs font-semibold">Name</th>
          <th class="text-xs font-semibold">Provider</th>
          <th class="text-xs font-semibold">Bucket / Region</th>
          <th class="text-xs font-semibold">Key Template</th>
          <th class="text-xs font-semibold">Auth</th>
          <th class="text-xs font-semibold">In Job</th>
          <th class="text-xs font-semibold text-right">Actions</th>
        </tr>
      </thead>
      <tbody id="jbOutCloudBody">
        <tr>
          <td class="font-semibold">s3-prod</td>
          <td>
            <span class="badge badge-ghost badge-xs">S3</span>
          </td>
          <td class="font-mono text-xs">
            my-changes-bucket<br/>
            <span class="opacity-60">us-east-1</span>
          </td>
          <td class="font-mono text-xs opacity-60">{prefix}/{doc_id}.json</td>
          <td><span class="badge badge-ghost badge-xs">IAM Key</span></td>
          <td><span class="badge badge-success badge-xs">Job-C</span></td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectOutput('s3-prod')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestCloudConn('s3-prod')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditCloud('s3-prod')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteOutput('s3-prod')" disabled>Delete</button>
            </div>
          </td>
        </tr>
        <tr>
          <td class="font-semibold">minio-dev</td>
          <td>
            <span class="badge badge-ghost badge-xs">S3</span>
            <span class="text-xs opacity-40">(MinIO)</span>
          </td>
          <td class="font-mono text-xs">
            dev-bucket<br/>
            <span class="opacity-60">us-east-1</span>
          </td>
          <td class="font-mono text-xs opacity-60">{prefix}/{doc_id}.json</td>
          <td><span class="badge badge-ghost badge-xs">IAM Key</span></td>
          <td><span class="opacity-60">--</span></td>
          <td class="text-right">
            <div class="flex gap-1 justify-end">
              <button class="btn btn-ghost btn-xs" onclick="jbSelectOutput('minio-dev')">Select</button>
              <button class="btn btn-ghost btn-xs" onclick="jbTestCloudConn('minio-dev')">Test</button>
              <button class="btn btn-ghost btn-xs" onclick="jbEditCloud('minio-dev')">Edit</button>
              <button class="btn btn-ghost btn-xs text-error" onclick="jbDeleteOutput('minio-dev')">Delete</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

#### Cloud Storage — Create / Edit Form

```html
<div id="jbCloudForm" class="hidden card bg-base-200 rounded-xl mt-3">
  <div class="card-body p-4">
    <h4 class="text-sm font-semibold mb-3">Cloud Storage Output Configuration</h4>

    <!-- Basic info -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Output ID</label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="s3-prod" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Name</label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="Production S3" />
      </div>
    </div>

    <!-- Provider & Region -->
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Provider</label>
        <select class="select select-bordered select-sm w-full" onchange="jbCloudProviderChange(this)">
          <option value="s3">Amazon S3</option>
          <option value="gcs">Google Cloud Storage</option>
          <option value="azure">Azure Blob</option>
          <option value="minio">MinIO (S3-compatible)</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Region</label>
        <input type="text" class="input input-bordered input-sm w-full" value="us-east-1" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Bucket</label>
        <input type="text" class="input input-bordered input-sm w-full" placeholder="my-bucket" />
      </div>
    </div>

    <div class="divider text-xs opacity-60">Key Configuration</div>

    <!-- Key template & prefix -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">
          Key Prefix
          <div class="tooltip tooltip-bottom inline" data-tip="Path prefix added to all object keys. e.g. 'couchdb-changes'">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" value="couchdb-changes" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">
          Key Template
          <div class="tooltip tooltip-bottom inline" data-tip="Template for object key. Variables: {prefix}, {doc_id}">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" value="{prefix}/{doc_id}.json" />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Content Type</label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" value="application/json" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">
          On Delete
          <div class="tooltip tooltip-bottom inline" data-tip="What happens when a deleted document arrives: 'delete' removes the S3 object, 'ignore' does nothing.">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
        </label>
        <select class="select select-bordered select-sm w-full">
          <option value="delete">Delete object</option>
          <option value="ignore">Ignore</option>
        </select>
      </div>
    </div>

    <div class="divider text-xs opacity-60">Authentication</div>

    <!-- Credentials -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Access Key ID</label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" placeholder="AKIA..." />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Secret Access Key</label>
        <input type="password" class="input input-bordered input-sm w-full" />
      </div>
    </div>
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">
          Session Token
          <span class="text-xs opacity-40 ml-1">(optional)</span>
        </label>
        <input type="password" class="input input-bordered input-sm w-full" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">
          Endpoint URL
          <div class="tooltip tooltip-bottom inline" data-tip="Custom endpoint for S3-compatible services like MinIO. Leave blank for AWS S3.">
            <span class="opacity-40 cursor-help">(?)</span>
          </div>
          <span class="text-xs opacity-40 ml-1">(MinIO / custom)</span>
        </label>
        <input type="text" class="input input-bordered input-sm w-full font-mono" placeholder="http://localhost:9000" />
      </div>
    </div>

    <div class="divider text-xs opacity-60">Advanced</div>

    <div class="grid grid-cols-3 gap-4 mb-4">
      <div>
        <label class="label label-text text-xs font-semibold">Storage Class</label>
        <select class="select select-bordered select-sm w-full">
          <option value="">(default)</option>
          <option value="STANDARD">STANDARD</option>
          <option value="INTELLIGENT_TIERING">INTELLIGENT_TIERING</option>
          <option value="STANDARD_IA">STANDARD_IA</option>
          <option value="GLACIER">GLACIER</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Server-Side Encryption</label>
        <select class="select select-bordered select-sm w-full">
          <option value="">(none)</option>
          <option value="AES256">AES256</option>
          <option value="aws:kms">aws:kms</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Max Retries</label>
        <input type="number" class="input input-bordered input-sm w-full" value="3" />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Key Sanitize</span>
          <input type="checkbox" class="checkbox checkbox-sm" checked />
        </label>
      </div>
      <div>
        <label class="label cursor-pointer gap-2">
          <span class="label-text text-xs font-semibold">Batch Upload</span>
          <input type="checkbox" class="checkbox checkbox-sm" onchange="jbToggleBatch(this)" />
        </label>
      </div>
    </div>

    <!-- Batch settings (hidden unless batch enabled) -->
    <div id="jbCloudBatch" class="hidden">
      <div class="grid grid-cols-3 gap-4 mb-4">
        <div>
          <label class="label label-text text-xs font-semibold">Max Docs</label>
          <input type="number" class="input input-bordered input-sm w-full" value="100" />
        </div>
        <div>
          <label class="label label-text text-xs font-semibold">Max Bytes</label>
          <input type="number" class="input input-bordered input-sm w-full" value="1048576" />
        </div>
        <div>
          <label class="label label-text text-xs font-semibold">Max Seconds</label>
          <input type="number" class="input input-bordered input-sm w-full" value="5" />
        </div>
      </div>
    </div>

    <!-- Save / Cancel -->
    <div class="flex items-center gap-3 mt-4">
      <button class="btn btn-success btn-sm" onclick="jbSaveCloud()">Save Cloud Output</button>
      <button class="btn btn-ghost btn-sm" onclick="jbCancelCloud()">Cancel</button>
      <span id="jbCloudSaveStatus" class="text-sm opacity-60"></span>
    </div>
  </div>
</div>
```

**Cloud "Test" button behavior:**
1. Attempts a `HeadBucket` (S3) or equivalent API call with the configured credentials
2. Shows success/failure toast with bucket access status
3. For MinIO/custom endpoint — tests against the custom `endpoint_url`

---

### Output Drill-Down — Column Summary by Type

| Column | RDBMS | HTTP | Cloud Storage |
|--------|-------|------|---------------|
| Name | Output name | Output name | Output name |
| URL / target | `host:port` + `db/schema` | Target URL + URL template | Bucket + Region |
| Detail | **Tables(N)** clickable | Methods: `PUT` `DELETE` badges | Key template |
| Auth | Basic / None | None / Basic / Bearer / Session | IAM Key / Session |
| In Job | Job name badge or `--` | Job name badge or `--` | Job name badge or `--` |
| Actions | Select · Test · Edit · Delete | Select · Test · Edit · Delete | Select · Test · Edit · Delete |

**Shared behaviors across all output types:**
- **"In Job" column** — shows which job(s) use this output; if in an active job, Delete button is `disabled`
- **"Test" button** — validates connectivity to the target
- **"Select" button** — selects this output for the current job being built, marks step 3 ✓
- **Stdout** — always available, no drill-down needed, just "Select"

---

## What Moves OUT of Wizards

| Current Wizard Card | New Home |
|---------------------|----------|
| 📥 Inputs | Job Builder → Source card |
| 📤 Outputs | Job Builder → Output card |
| 📡 Data Source | Job Builder → Source card (combined with Input) |
| 🗄️ RDBMS | Job Builder → Output card (RDBMS tab) |
| ☁️ Cloud Storage | Job Builder → Output card (Cloud tab) |
| 🗂️ Schema Mapping | Job Builder → Process card (mapping selector) |
| 🎯 Jobs | Replaced entirely by Job Builder page |

**What STAYS in Wizards:**
- ⚙️ Settings — guided Q&A wizard for tuning pipeline settings (this is a genuine "wizard" flow)
- Future guided workflows that don't map to Job construction

---

## Save Flow

When the user clicks **"Save Job"** (`btn btn-success btn-sm`, inside card):

1. Validate all 3 sections are configured (Source selected, Process type chosen, Output selected)
2. `POST /api/jobs` with:
   ```json
   {
     "name": "My Price Sync Job",
     "input_id": "sg-us-prices",
     "output_type": "rdbms",
     "output_id": "pg-prod",
     "mapping": { ... },
     "system": {
       "threads": 4,
       "attachments_enabled": false
     }
   }
   ```
3. Show success toast + job appears in the Jobs table above
4. Job starts in "idle" status — user can Start/Stop/Restart from the table

---

## Data Flow Diagram

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│  SAVED      │         │  SAVED      │         │  SAVED      │
│  INPUTS     │────────▶│  MAPPINGS   │────────▶│  OUTPUTS    │
│  (CBL)      │         │  (/mappings)│         │  (CBL)      │
└─────────────┘         └─────────────┘         └─────────────┘
      │                       │                       │
      ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────┐
│                      JOB DOCUMENT (CBL)                     │
│  { input, process_type, mapping, output, threads, state }   │
└─────────────────────────────────────────────────────────────┘
```

---

## Advanced Section Pattern (all forms)

Every create/edit form uses a DaisyUI **collapse** at the bottom to hide optimization and tuning fields that most users won't need on first setup. This keeps the main form clean while giving power users full control.

### HTML Pattern

```html
<!-- After the main form fields, before Save/Cancel -->
<div class="collapse collapse-arrow bg-base-300 rounded-xl mt-4">
  <input type="checkbox" />
  <div class="collapse-title text-xs font-semibold opacity-70">Advanced</div>
  <div class="collapse-content">
    <!-- advanced fields go here -->
  </div>
</div>
```

### What Goes in Advanced — by Form Type

#### Source (Sync Gateway / App Services / Edge Server)

| Basic (always visible) | Advanced (collapsed) |
|---|---|
| Input ID, Name | Channels filter (Tagify) |
| Source Type | Feed type (longpoll / continuous) |
| Host URL | Poll interval (sec) |
| Database, Scope, Collection | Heartbeat (ms) |
| Auth method + credentials | Timeout (ms) |
| Accept self-signed certs | HTTP timeout (sec) |
| | Throttle feed (ms) |
| | Active only (toggle) |
| | Include docs (toggle) |
| | Limit |
| | Flood threshold |
| | Optimize initial sync (toggle) |

> **CouchDB sources** — Channels, active_only, and scope/collection are hidden entirely (not just in Advanced).

#### Source form with Advanced:

```html
<!-- Main form fields: ID, Name, Type, Host, DB/Scope/Collection, Auth -->
<!-- ... (existing basic fields) ... -->

<div class="collapse collapse-arrow bg-base-300 rounded-xl mt-4">
  <input type="checkbox" />
  <div class="collapse-title text-xs font-semibold opacity-70">Advanced</div>
  <div class="collapse-content">

    <!-- Channels (not CouchDB) -->
    <div id="jbChannelsWrap">
      <label class="label label-text text-xs font-semibold">
        Channels
        <div class="tooltip tooltip-bottom inline" data-tip="Server-side filter for _changes. Leave empty for all.">
          <span class="opacity-40 cursor-help">(?)</span>
        </div>
        <span class="text-xs opacity-40 ml-1">(optional)</span>
      </label>
      <input type="text" id="jbChannelsInput" class="input input-bordered input-sm w-full"
             placeholder="Type a channel and press Enter" />
    </div>

    <div class="divider text-xs opacity-60">Feed Tuning</div>

    <div class="grid grid-cols-3 gap-4">
      <div>
        <label class="label label-text text-xs font-semibold">Feed Type</label>
        <select class="select select-bordered select-sm w-full">
          <option value="longpoll">Long Poll</option>
          <option value="continuous">Continuous</option>
        </select>
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Poll Interval (sec)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="10" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Heartbeat (ms)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="30000" />
      </div>
    </div>
    <div class="grid grid-cols-3 gap-4 mt-3">
      <div>
        <label class="label label-text text-xs font-semibold">Timeout (ms)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="60000" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">HTTP Timeout (sec)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="300" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Throttle Feed (ms)</label>
        <input type="number" class="input input-bordered input-sm w-full" value="5000" />
      </div>
    </div>
    <div class="grid grid-cols-3 gap-4 mt-3">
      <div>
        <label class="label label-text text-xs font-semibold">Limit</label>
        <input type="number" class="input input-bordered input-sm w-full" value="0" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Flood Threshold</label>
        <input type="number" class="input input-bordered input-sm w-full" value="10000" />
      </div>
      <div>
        <label class="label label-text text-xs font-semibold">Catchup Limit</label>
        <input type="number" class="input input-bordered input-sm w-full" value="5000" />
      </div>
    </div>
    <div class="flex gap-6 mt-3">
      <label class="label cursor-pointer gap-2">
        <span class="label-text text-xs font-semibold">Active Only</span>
        <input type="checkbox" class="checkbox checkbox-sm" checked />
      </label>
      <label class="label cursor-pointer gap-2">
        <span class="label-text text-xs font-semibold">Include Docs</span>
        <input type="checkbox" class="checkbox checkbox-sm" />
      </label>
      <label class="label cursor-pointer gap-2">
        <span class="label-text text-xs font-semibold">Optimize Initial Sync</span>
        <input type="checkbox" class="checkbox checkbox-sm" />
      </label>
    </div>

  </div>
</div>

<!-- Save / Cancel -->
<div class="flex items-center gap-3 mt-4">
  <button class="btn btn-success btn-sm">Save Input</button>
  <button class="btn btn-ghost btn-sm">Cancel</button>
</div>
```

#### HTTP Output

| Basic (always visible) | Advanced (collapsed) |
|---|---|
| Output ID, Name | Send body on DELETE |
| Target URL | Follow redirects |
| URL Template | Accept self-signed certs |
| Write method, Delete method | Log response times |
| Output format | Custom headers (key-value) |
| Auth method + credentials | Retry on status codes |
| | Backoff base / max seconds |
| | Health check (enable, URL, interval, method) |

#### Cloud Storage Output

| Basic (always visible) | Advanced (collapsed) |
|---|---|
| Output ID, Name | Storage class |
| Provider | Server-side encryption |
| Region, Bucket | KMS Key ID |
| Key Prefix, Key Template | Key sanitize (toggle) |
| Access Key ID, Secret Key | Batch upload (toggle + max docs/bytes/seconds) |
| | Content type |
| | On Delete behavior |
| | Max retries, Backoff settings |
| | Session token |
| | Endpoint URL (MinIO/custom) |

#### RDBMS Output

| Basic (always visible) | Advanced (collapsed) |
|---|---|
| Output ID, Name | Pool min |
| Engine (Postgres/MySQL/MSSQL) | Pool max |
| Host, Port, Database | Schema (default: public) |
| Username, Password | SSL toggle |
| | Connection timeout |

---

## Style Checklist (per `guide/STYLE_HTML_CSS.md`)

- [x] All content inside `card bg-base-100 shadow rounded-2xl`
- [x] Pipeline accents: `border-source` (green), `border-process` (blue), `border-output` (amber)
- [x] Section headings: `text-xs font-bold uppercase` with `color:var(--color-success|info|warning)`
- [x] Help tooltips: `badge badge-ghost badge-sm cursor-help` pattern
- [x] Tables: `table table-sm table-zebra` with `bg-base-200` header rows
- [x] Buttons: all `btn-sm`, inside cards, same size — color distinguishes primary vs ghost
- [x] No hard-coded colors — only DaisyUI theme tokens
- [x] Nested cards: `bg-base-200 rounded-xl` with `p-3`
- [x] Forms: `input input-bordered input-sm w-full`, `select select-bordered select-sm`
- [x] Dividers: `divider text-xs opacity-60`
- [x] Steps: `steps steps-horizontal` with `step-success`/`step-error`
- [x] Save button not pushed to screen edge — inside card body with padding
- [x] Advanced sections: `collapse collapse-arrow bg-base-300 rounded-xl` — collapsed by default

---

## Implementation Plan (for later)

1. **Create `jobs.html`** template with the layout above
2. **Add `/jobs` route** in the web server
3. **Update `sidebar.js`** — add "Job Builder" nav item under TOOLS
4. **Slim down `wizard.html`** — remove cards that moved to Job Builder
5. **Wire up API calls** — reuse existing `/api/jobs`, `/api/inputs`, `/api/outputs` endpoints
6. **Add validation** — ensure Source + Process + Output are all configured before saving

---

## Open Questions

1. Should the Job Builder support **multiple inputs** per job? (Current API supports `inputs[]` array but UI could start with single-input)
2. Should we allow **inline creation** of Inputs/Outputs from the Job Builder, or always link out to the Wizards/dedicated pages?
3. ~~Should the Schema Mapping selector open in a **modal**, a **new tab**, or an **inline panel**?~~ → **Resolved:** Full-screen modal with iframe to `/schema?job_mode=true`. See [`SCHEMA_MAPPING_IN_JOBS.md`](SCHEMA_MAPPING_IN_JOBS.md).
4. ~~Should we add a **"Test Job"** button that does a dry-run before saving?~~ → **Resolved:** Yes, `POST /api/v2/jobs/dry-run`. See [`SCHEMA_MAPPING_IN_JOBS.md`](SCHEMA_MAPPING_IN_JOBS.md#dry-run-feature).
5. How should we handle editing an existing job that's currently **running**? (Stop first? Warn?)
6. ~~Where do RDBMS table definitions live?~~ → **Resolved:** Standalone `tables_rdbms` CBL collection (reusable library). Tables are copied into jobs on selection. See [`SCHEMA_MAPPING_IN_JOBS.md`](SCHEMA_MAPPING_IN_JOBS.md#rdbms-table-definitions-new-tables_rdbms-collection). **Implemented:** `cbl_store.py` + `rest/api_v2.py` + 23 tests passing.
