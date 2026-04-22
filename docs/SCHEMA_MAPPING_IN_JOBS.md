# Schema Mapping Integration in Job Builder

> **Status:** 📋 Design  
> **Depends on:** Phase 5 (Job Builder), Phase 9 (Schema Mapping Migration) from [`DESIGN_2_0.md`](DESIGN_2_0.md)  
> **Related docs:**
> - [`JOB_BUILDER_DESIGN.md`](JOB_BUILDER_DESIGN.md) — Job Builder layout & 3-column design
> - [`SCHEMA_MAPPING.md`](SCHEMA_MAPPING.md) — Mapping format, transforms, tables
> - [`ADMIN_UI.md`](ADMIN_UI.md) — Schema editor features

---

## The Problem

Today, schema mapping and job creation are **separate workflows on separate pages**:

1. User goes to `/jobs` → picks Input, Process, Output → saves job  
2. In the Process card, they pick a mapping from a dropdown **or** click "Create new mapping →" which opens `/schema` **in a new tab**  
3. After building the mapping on `/schema`, they save it, switch back to `/jobs`, refresh the dropdown, and select it  
4. If they need to **edit** a mapping for an existing job, they have to navigate to `/schema`, find the mapping, edit it, save it, go back to `/jobs`

This flow is broken because:

- **Context loss** — the user leaves the job builder to build the mapping, and has to remember which job they were working on when they come back
- **No pre-seeding** — the mapping editor on `/schema` doesn't know which input or output the user just selected, so they start from scratch (re-entering source fields, re-selecting output tables)
- **No validation loop** — there's no way to dry-run the mapping against the actual source data before saving the job
- **Edit friction** — changing a mapping on a running job requires navigating away, and there's no visual connection between "this mapping belongs to this job"

---

## Proposed Solution: Full-Screen Modal + Iframe

Embed the schema mapper inside the job builder using a **full-screen DaisyUI `<dialog>`** containing an **`<iframe>` to `/schema`** with query parameters that pre-seed context. The mapping editor stays a single source of truth (no code duplication), the user never leaves `/jobs`, and the mapping is passed back to the job builder via `postMessage`.

### Why iframe instead of copy-pasting the HTML?

| Approach | Pros | Cons |
|---|---|---|
| **Copy HTML/JS into jobs.html** | Single page, no message passing | ~2,400 lines of duplicated HTML+JS. Two copies to maintain. CSS conflicts. |
| **Extract to web component** | Clean separation | Major refactor of schema.html. DaisyUI doesn't play well with Shadow DOM. |
| **Iframe in modal** | Zero code duplication. schema.html works standalone AND embedded. Small JS bridge. | Iframe styling boundaries. Need `postMessage` contract. |

The iframe approach wins for now. It can be upgraded to a web component later.

---

## Detailed Step-by-Step Implementation

### Step 1: Add `job_mode` Query Parameter to `schema.html`

**Goal:** When `schema.html` is loaded with `?job_mode=true`, it hides standalone UI elements and enables parent communication.

**Changes to `schema.html`:**

```
URL: /schema?job_mode=true&input_id=sg-us-prices&output_type=rdbms&output_id=pg-prod&mapping_name=order.json
```

**Query parameters:**

| Param | Required | Purpose |
|---|---|---|
| `job_mode` | Yes | Switches schema.html into embedded mode |
| `input_id` | No | Pre-selects the input source for "Live Sample" fetch |
| `output_type` | No | Pre-selects output type (rdbms, http, cloud) |
| `output_id` | No | Pre-selects the specific output (for DB introspection) |
| `mapping_name` | No | Loads an existing mapping for editing |
| `job_id` | No | Associates this mapping with a specific job (for edit flows) |

**What changes in `schema.html` when `job_mode=true`:**

1. **Hide** the top action bar (export/import/sample templates/filename input/save button)
2. **Hide** the "Saved Mappings" table — the user isn't browsing mappings, they're building one for a specific job
3. **Show** a new footer bar with two buttons: **"Apply to Job"** (sends mapping JSON to parent) and **"Cancel"** (closes the modal)
4. **Auto-fetch** a live sample doc using `input_id` if provided (calls `/api/sample-doc?input_id={id}`)
5. **Auto-load** output tables if `output_type=rdbms` and `output_id` is provided (calls `/api/outputs_rdbms/{id}` to get table definitions)
6. **Auto-load** existing mapping if `mapping_name` is provided

**JS additions to `schema.html`:**

```javascript
// At the top of the <script> block:
var urlParams = new URLSearchParams(window.location.search);
var JOB_MODE = urlParams.get('job_mode') === 'true';

if (JOB_MODE) {
  document.addEventListener('DOMContentLoaded', function() {
    // Hide standalone UI elements
    document.getElementById('topActionBar').classList.add('hidden');
    document.getElementById('savedMappingsCard').classList.add('hidden');
    
    // Show job-mode footer
    document.getElementById('jobModeFooter').classList.remove('hidden');
    
    // Pre-seed from query params
    var inputId = urlParams.get('input_id');
    var outputType = urlParams.get('output_type');
    var outputId = urlParams.get('output_id');
    var mappingName = urlParams.get('mapping_name');
    
    if (mappingName) {
      loadExistingMapping(mappingName);
    }
    if (inputId) {
      autoFetchSampleForInput(inputId);
    }
    if (outputType === 'rdbms' && outputId) {
      autoLoadOutputTables(outputId);
    }
  });
}

// "Apply to Job" button handler:
function applyMappingToJob() {
  saveCurrentTableEdits();
  var mappingJson = buildMappingJson();  // existing function that builds the mapping object
  
  // Send to parent window (jobs.html)
  window.parent.postMessage({
    type: 'schema-mapping-result',
    action: 'apply',
    mapping: mappingJson,
    mappingName: document.getElementById('filenameInput').value || ''
  }, '*');
}

// "Cancel" button handler:
function cancelJobModeMapping() {
  window.parent.postMessage({
    type: 'schema-mapping-result',
    action: 'cancel'
  }, '*');
}
```

**New footer HTML (add to bottom of `<main>` in `schema.html`):**

```html
<div id="jobModeFooter" class="hidden sticky bottom-0 z-50 p-3 bg-base-100 border-t border-base-300 flex items-center justify-between">
  <span class="text-sm opacity-60">Mapping will be saved with the job</span>
  <div class="flex gap-2">
    <button class="btn btn-ghost btn-sm" onclick="cancelJobModeMapping()">Cancel</button>
    <button class="btn btn-success btn-sm" onclick="applyMappingToJob()">Apply to Job</button>
  </div>
</div>
```

---

### Step 2: Add the Full-Screen Modal to `jobs.html`

**Goal:** A `<dialog>` element that opens the schema mapper iframe when the user clicks "Edit Mapping" or "Create Mapping" in the job builder.

**HTML to add to `jobs.html` (before `</main>`):**

```html
<!-- Schema Mapping Modal (full-screen iframe) -->
<dialog id="schemaMappingModal" class="modal">
  <div class="modal-box w-full max-w-full h-full max-h-full rounded-none p-0 flex flex-col">
    <!-- Modal header -->
    <div class="flex items-center justify-between p-3 bg-base-200 border-b border-base-300">
      <h3 class="text-sm font-semibold">Schema Mapping — <span id="schemaMappingModalTitle">New Mapping</span></h3>
      <button class="btn btn-ghost btn-sm" onclick="closeSchemaMappingModal()">✕</button>
    </div>
    <!-- Iframe -->
    <iframe id="schemaMappingIframe" class="flex-1 w-full border-0" src="about:blank"></iframe>
  </div>
</dialog>
```

**JS to add to `jobs.html`:**

```javascript
// ===== Schema Mapping Modal =====

function openSchemaMappingModal(options) {
  // options: { mappingName, inputId, outputType, outputId, jobId, title }
  var params = new URLSearchParams();
  params.set('job_mode', 'true');
  if (options.mappingName) params.set('mapping_name', options.mappingName);
  if (options.inputId) params.set('input_id', options.inputId);
  if (options.outputType) params.set('output_type', options.outputType);
  if (options.outputId) params.set('output_id', options.outputId);
  if (options.jobId) params.set('job_id', options.jobId);
  
  document.getElementById('schemaMappingModalTitle').textContent = 
    options.title || (options.mappingName ? 'Edit: ' + options.mappingName : 'New Mapping');
  
  document.getElementById('schemaMappingIframe').src = '/schema?' + params.toString();
  document.getElementById('schemaMappingModal').showModal();
}

function closeSchemaMappingModal() {
  document.getElementById('schemaMappingModal').close();
  document.getElementById('schemaMappingIframe').src = 'about:blank';
}

// Listen for postMessage from the iframe
window.addEventListener('message', function(event) {
  if (!event.data || event.data.type !== 'schema-mapping-result') return;
  
  if (event.data.action === 'apply') {
    // Store the mapping in the current job builder state
    currentJobBuilder.mappingData = event.data.mapping;
    currentJobBuilder.mappingName = event.data.mappingName;
    
    // Update the UI to show mapping is configured
    updateMappingDisplay(event.data);
    
    // Update step indicators
    updateStepIndicators();
  }
  
  closeSchemaMappingModal();
});

function updateMappingDisplay(mappingData) {
  // Update the Process card to show the mapping summary
  var select = document.getElementById('jbMappingSelect');
  
  // Add or update the "Embedded mapping" option
  var embeddedOpt = select.querySelector('option[value="__embedded__"]');
  if (!embeddedOpt) {
    embeddedOpt = document.createElement('option');
    embeddedOpt.value = '__embedded__';
    select.insertBefore(embeddedOpt, select.options[1]);
  }
  
  var tableCount = (mappingData.mapping && mappingData.mapping.tables) 
    ? mappingData.mapping.tables.length : 0;
  embeddedOpt.textContent = '✓ Custom mapping (' + tableCount + ' tables)';
  select.value = '__embedded__';
}
```

---

### Step 3: Replace the Mapping Dropdown with Action Buttons

**Goal:** Change the Process card's "Schema Mapping" section from a simple dropdown to a richer UI that supports create, edit, and pick-existing flows.

**Current HTML (in jobs.html Process card, lines 193–202):**

```html
<div class="divider text-xs opacity-60">Schema Mapping</div>
<div>
  <label class="label label-text text-xs font-semibold">Mapping File</label>
  <select id="jbMappingSelect" class="select select-bordered select-sm w-full">
    <option value="">None (pass-through)</option>
  </select>
  <a href="/schema" target="_blank" class="text-xs text-primary mt-1 inline-block">Create new mapping →</a>
</div>
```

**Replace with:**

```html
<div class="divider text-xs opacity-60">Schema Mapping</div>
<div class="space-y-2">
  <label class="label label-text text-xs font-semibold">Mapping</label>
  
  <!-- Option 1: Pick existing mapping -->
  <select id="jbMappingSelect" class="select select-bordered select-sm w-full" onchange="onMappingSelectChange()">
    <option value="">None (pass-through)</option>
    <!-- populated from GET /api/mappings -->
  </select>
  
  <!-- Action buttons -->
  <div class="flex gap-2 flex-wrap">
    <button class="btn btn-primary btn-xs" onclick="openCreateMapping()">+ New Mapping</button>
    <button class="btn btn-ghost btn-xs" id="jbEditMappingBtn" onclick="openEditMapping()" disabled>Edit Selected</button>
    <button class="btn btn-ghost btn-xs" id="jbPreviewMappingBtn" onclick="previewMapping()" disabled>Preview</button>
  </div>
  
  <!-- Mapping summary (shown when mapping is configured) -->
  <div id="jbMappingSummary" class="hidden card bg-base-200 rounded-xl p-3">
    <div class="text-xs font-semibold mb-1">Mapping: <span id="jbMappingSummaryName">—</span></div>
    <div class="text-xs opacity-60" id="jbMappingSummaryDetail">—</div>
  </div>
</div>
```

**Supporting JS:**

```javascript
function openCreateMapping() {
  openSchemaMappingModal({
    inputId: currentJobBuilder.sourceId,
    outputType: currentJobBuilder.outputType,
    outputId: currentJobBuilder.outputId,
    title: 'New Mapping'
  });
}

function openEditMapping() {
  var selected = document.getElementById('jbMappingSelect').value;
  if (selected === '__embedded__') {
    // Re-open the modal with the embedded mapping data
    // Need to pass the mapping data back into the iframe somehow
    openSchemaMappingModal({
      inputId: currentJobBuilder.sourceId,
      outputType: currentJobBuilder.outputType,
      outputId: currentJobBuilder.outputId,
      title: 'Edit Mapping'
      // mapping data will be posted to iframe after load (see Step 4)
    });
  } else if (selected) {
    openSchemaMappingModal({
      mappingName: selected,
      inputId: currentJobBuilder.sourceId,
      outputType: currentJobBuilder.outputType,
      outputId: currentJobBuilder.outputId,
      title: 'Edit: ' + selected
    });
  }
}

function onMappingSelectChange() {
  var val = document.getElementById('jbMappingSelect').value;
  document.getElementById('jbEditMappingBtn').disabled = !val;
  document.getElementById('jbPreviewMappingBtn').disabled = !val;
  
  // Clear embedded data if switching to a saved mapping
  if (val !== '__embedded__') {
    currentJobBuilder.mappingData = null;
  }
}
```

---

### Step 4: Passing Embedded Mapping Data Into the Iframe

**Problem:** When the user has an embedded mapping (not saved as a file) and clicks "Edit", we need to pass the mapping JSON into the iframe's schema editor.

**Solution:** After the iframe loads, `jobs.html` sends a `postMessage` with the mapping data, and `schema.html` listens for it.

**In `jobs.html`:**

```javascript
function openSchemaMappingModal(options) {
  // ... existing code to build URL and open modal ...
  
  var iframe = document.getElementById('schemaMappingIframe');
  
  // If we have embedded mapping data, send it after iframe loads
  if (currentJobBuilder.mappingData && !options.mappingName) {
    iframe.onload = function() {
      iframe.contentWindow.postMessage({
        type: 'load-mapping',
        mapping: currentJobBuilder.mappingData,
        mappingName: currentJobBuilder.mappingName || ''
      }, '*');
      iframe.onload = null;
    };
  }
  
  iframe.src = '/schema?' + params.toString();
  document.getElementById('schemaMappingModal').showModal();
}
```

**In `schema.html`:**

```javascript
// Listen for mapping data from parent (job builder edit flow)
window.addEventListener('message', function(event) {
  if (!event.data || event.data.type !== 'load-mapping') return;
  if (!JOB_MODE) return;
  
  // Load the mapping into the editor
  loadMappingFromObject(event.data.mapping);
  if (event.data.mappingName) {
    document.getElementById('filenameInput').value = event.data.mappingName;
  }
});
```

---

### Step 5: Add `id` Attributes to `schema.html` Sections

**Goal:** The top action bar and saved mappings table need `id` attributes so `job_mode` JS can hide them.

**Changes to `schema.html`:**

1. Add `id="topActionBar"` to the action bar card (currently line ~81)
2. Add `id="savedMappingsCard"` to the saved mappings card (currently line ~129)

These are small, safe changes — just adding IDs to existing elements.

---

### Step 6: Update the Save Job Payload

**Goal:** When saving a job that has an embedded mapping, include the mapping data in the job document.

**Changes to `saveJob()` in `jobs.html`:**

```javascript
function saveJob() {
  // ... existing validation ...
  
  var payload = {
    name: jobName,
    input_id: currentJobBuilder.sourceId,
    process_type: currentJobBuilder.processType,
    output_id: currentJobBuilder.outputId,
    output_type: currentJobBuilder.outputType,
    changes_feed: changesFeed,
    system: {
      threads: threads,
      attachments_enabled: attachmentsEnabled
    }
  };
  
  // Schema mapping — either embedded or reference
  if (currentJobBuilder.mappingData) {
    // Embedded mapping (created/edited in the modal)
    payload.mapping = currentJobBuilder.mappingData;
    payload.mapping_id = null;
  } else if (mappingId && mappingId !== '__embedded__') {
    // Reference to a saved mapping file
    payload.mapping_id = mappingId;
    payload.mapping = null;
  }
  // else: no mapping (pass-through mode)
  
  // ... existing fetch POST/PUT ...
}
```

---

### Step 7: Update `editJob()` to Load Embedded Mappings

**Goal:** When editing a job that has an embedded mapping, populate `currentJobBuilder.mappingData` so the edit flow works.

**Changes to `editJob()` in `jobs.html`:**

```javascript
function editJob(jobId) {
  // ... existing code ...
  .then(function(job) {
    // ... existing population code ...
    
    // Handle embedded vs referenced mapping
    if (job.mapping && typeof job.mapping === 'object' && job.mapping.tables) {
      // Embedded mapping — store it and show summary
      currentJobBuilder.mappingData = job.mapping;
      currentJobBuilder.mappingName = job.mapping_id || '';
      updateMappingDisplay({ mapping: job.mapping, mappingName: job.mapping_id || '' });
    } else {
      // Reference mapping — select from dropdown
      currentJobBuilder.mappingData = null;
      // ... existing dropdown selection logic ...
    }
  });
}
```

---

### Step 8: Server-Side — Update Job API to Accept Embedded Mappings

**Goal:** The `POST /api/v2/jobs` and `PUT /api/v2/jobs/{id}` endpoints need to accept a `mapping` field containing the full mapping object.

**Changes to `web/server.py`:**

```python
# In the create_job / update_job handler:

async def create_job(request):
    data = await request.json()
    
    # Schema mapping — embedded or reference
    mapping_data = data.get('mapping')  # full mapping object, or None
    mapping_id = data.get('mapping_id')  # reference to saved mapping, or None
    
    job_doc = {
        "type": "job",
        "id": str(uuid.uuid4()),
        "name": data["name"],
        "enabled": True,
        "inputs": [resolved_input],
        "output_type": data["output_type"],
        "outputs": [resolved_output],
        "system": data.get("system", {}),
    }
    
    if mapping_data:
        # Validate the mapping before saving
        errors = validate_mapping_schema(mapping_data)
        if errors:
            return web.json_response({"error": "Invalid mapping", "details": errors}, status=400)
        job_doc["mapping"] = mapping_data
        job_doc["mapping_id"] = mapping_id  # optional name reference
    elif mapping_id:
        job_doc["mapping_id"] = mapping_id
        # Optionally resolve and embed the mapping at save time:
        # mapping = load_mapping_by_name(mapping_id)
        # job_doc["mapping"] = mapping
    
    # ... save job_doc to CBL ...
```

---

### Step 9: Server-Side — Add `/api/sample-doc` Input Scoping

**Goal:** The existing `/api/sample-doc` endpoint needs to accept an `input_id` query parameter so the schema mapper in job mode can fetch a sample doc from the *specific* source the user selected.

**Current behavior:** Fetches a random doc from whatever source is configured in `config.json`.

**New behavior:** `GET /api/sample-doc?input_id=sg-us-prices` fetches a doc from the specified input's source.

```python
async def get_sample_doc(request):
    input_id = request.query.get('input_id')
    
    if input_id:
        # Look up the input config by ID from inputs_changes
        input_config = cbl_store.get_input_by_id(input_id)
        if not input_config:
            return web.json_response({"error": "Input not found"}, status=404)
        # Build connection from this specific input
        sample = await fetch_sample_from_input(input_config)
    else:
        # Existing behavior — use default config
        sample = await fetch_sample_from_default()
    
    return web.json_response({"doc": sample, "pool_size": 1})
```

---

## Editing Mappings on Existing Jobs

This is the most common workflow — a job is already created and running, and the user needs to tweak the mapping (add a column, change a transform, add a child table).

### Three Entry Points for Editing

#### Entry Point 1: From the Jobs Table

Add a **"Mapping"** button to each job row's Actions column.

```javascript
// In the job table row rendering (loadJobs function):
var mappingBtn = '<button class="btn btn-ghost btn-xs" ' +
  'onclick="editJobMapping(\'' + escHtml(jobId) + '\')" ' +
  'title="Edit schema mapping">' +
  '📐 Mapping</button>';
```

```javascript
function editJobMapping(jobId) {
  var apiId = jobId.replace(/^job::/, '');
  
  // Fetch the job to get its current mapping + input/output context
  fetch('/api/v2/jobs/' + apiId)
    .then(function(r) { return r.json(); })
    .then(function(job) {
      var inp = (job.inputs && job.inputs[0]) || {};
      var out = (job.outputs && job.outputs[0]) || {};
      var mappingName = job.mapping_id || '';
      
      // Store context so we can save back to this job
      _editingMappingJobId = apiId;
      _editingMappingJobData = job;
      
      openSchemaMappingModal({
        mappingName: mappingName,
        inputId: inp.id,
        outputType: job.output_type,
        outputId: out.id,
        jobId: apiId,
        title: 'Mapping — ' + (job.name || apiId)
      });
      
      // If embedded mapping, send it after iframe loads
      if (job.mapping && typeof job.mapping === 'object' && job.mapping.tables) {
        var iframe = document.getElementById('schemaMappingIframe');
        iframe.onload = function() {
          iframe.contentWindow.postMessage({
            type: 'load-mapping',
            mapping: job.mapping,
            mappingName: mappingName
          }, '*');
          iframe.onload = null;
        };
      }
    });
}
```

**When the user clicks "Apply to Job" in the modal:**

```javascript
// In the postMessage listener, handle the case where we're editing
// a mapping on an existing job (not building a new one):
window.addEventListener('message', function(event) {
  if (!event.data || event.data.type !== 'schema-mapping-result') return;
  
  if (event.data.action === 'apply') {
    if (_editingMappingJobId) {
      // Direct save to existing job
      saveMappingToExistingJob(_editingMappingJobId, event.data.mapping);
    } else {
      // Building a new job — store in currentJobBuilder
      currentJobBuilder.mappingData = event.data.mapping;
      currentJobBuilder.mappingName = event.data.mappingName;
      updateMappingDisplay(event.data);
      updateStepIndicators();
    }
  }
  
  closeSchemaMappingModal();
  _editingMappingJobId = null;
  _editingMappingJobData = null;
});

function saveMappingToExistingJob(jobId, mappingData) {
  fetch('/api/v2/jobs/' + jobId + '/mapping', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mapping: mappingData })
  })
  .then(function(r) {
    if (r.ok) {
      showToast('✓ Mapping updated!', 'success');
      loadJobs();  // refresh table
    } else {
      return r.text().then(function(t) { alert('Failed to save mapping: ' + t); });
    }
  });
}
```

**New server endpoint needed:**

```
PUT /api/v2/jobs/{id}/mapping   — Update only the mapping on an existing job
```

This endpoint:
1. Validates the mapping JSON
2. Updates `job.mapping` in the CBL document
3. If the job is running, optionally hot-reloads the mapper (or warns the user to restart)
4. Returns 200 with the updated job

#### Entry Point 2: From Inside the Job Builder (Edit Mode)

When a user clicks "Edit" on a job in the table, the job builder opens with all fields populated. The Process card shows the current mapping. They can click **"Edit Selected"** to open the modal with the existing mapping pre-loaded.

This already works with the implementation in Steps 3–4 above.

#### Entry Point 3: From the Standalone Schema Page

The standalone `/schema` page continues to work as-is for users who prefer it. Mappings saved here are available in the job builder dropdown. If a mapping is referenced by a job (via `mapping_id`), editing it on `/schema` and saving updates the file — the job will pick up the changes on next restart.

**Future enhancement:** Show a warning on `/schema` when editing a mapping that's referenced by active jobs:

```
⚠ This mapping is used by 2 running jobs: "US Orders Sync", "EU Orders Sync"
   Changes will take effect after job restart.
```

---

## Handling the Two Mapping Storage Models

With this design, mappings can live in two places:

### Model A: Referenced Mapping (existing behavior)

```json
{
  "type": "job",
  "id": "abc-123",
  "mapping_id": "order.json",
  "mapping": null
}
```

- Mapping is stored in `mappings/` directory or CBL `mappings` collection
- Multiple jobs can share the same mapping
- Editing the mapping on `/schema` affects all jobs that reference it
- Good for: shared mappings, centralized management

### Model B: Embedded Mapping (new behavior)

```json
{
  "type": "job",
  "id": "abc-123",
  "mapping_id": null,
  "mapping": {
    "source": { "match": { "field": "type", "value": "order" } },
    "output_format": "tables",
    "tables": [ ... ]
  }
}
```

- Mapping is stored inside the job document itself
- Each job has its own independent copy
- Editing the mapping only affects this job
- Good for: job-specific mappings, isolation between jobs

### Resolution Order

When the pipeline starts a job, it resolves the mapping in this order:

1. If `job.mapping` exists and is a non-empty object → use it (embedded)
2. Else if `job.mapping_id` exists → load from `mappings/` or CBL `mappings` collection (referenced)
3. Else → no mapping, pass-through mode

### UI Behavior

| Scenario | Mapping Select Shows | Edit Button Does |
|---|---|---|
| No mapping | "None (pass-through)" selected | Disabled |
| Referenced mapping | `"order.json"` selected from dropdown | Opens modal with `mapping_name=order.json` |
| Embedded mapping | `"✓ Custom mapping (3 tables)"` synthetic option | Opens modal and posts mapping data via postMessage |
| User picks from dropdown after having embedded | Clears embedded data, switches to reference | Opens modal with `mapping_name={selection}` |

---

## Dry Run Feature

### Goal

After configuring Source + Output + Mapping, the user can run a **dry run** that:

1. Fetches 1–5 real documents from the source
2. Runs each through the schema mapper
3. Shows what would be written to each output table (or output JSON)
4. Highlights any errors, missing fields, type coercions, or transform failures
5. Does NOT write anything to the actual output

### UI: New Step in the Job Builder

Add a 4th step to the progress indicator:

```html
<ul class="steps steps-horizontal w-full text-sm">
  <li id="jbStep1" class="step step-error" data-content="✕">Source Selected</li>
  <li id="jbStep2" class="step step-error" data-content="✕">Process Configured</li>
  <li id="jbStep3" class="step step-error" data-content="✕">Output Selected</li>
  <li id="jbStep4" class="step step-error" data-content="✕">Dry Run</li>
</ul>
```

Add a "Dry Run" card below the 3-column builder (inside `jobBuilderContainer`):

```html
<!-- Dry Run Card -->
<div id="jbDryRunCard" class="card bg-base-100 shadow-sm rounded-2xl border-process">
  <div class="card-body p-4">
    <div class="flex items-center gap-2 mb-3">
      <h4 class="text-xs font-bold uppercase" style="color:var(--color-info)">Dry Run</h4>
      <div class="tooltip tooltip-right" data-tip="Test the mapping against real documents without writing to the output.">
        <span class="badge badge-ghost badge-sm cursor-help">?</span>
      </div>
      <div class="flex gap-2 ml-auto">
        <select id="jbDryRunCount" class="select select-bordered select-xs w-24">
          <option value="1">1 doc</option>
          <option value="3" selected>3 docs</option>
          <option value="5">5 docs</option>
          <option value="10">10 docs</option>
        </select>
        <button class="btn btn-info btn-sm" onclick="runDryRun()" id="jbDryRunBtn">
          ▶ Run Dry Run
        </button>
      </div>
    </div>
    
    <!-- Results area -->
    <div id="jbDryRunResults" class="hidden space-y-3">
      <!-- Per-doc results inserted here dynamically -->
    </div>
    
    <div id="jbDryRunEmpty" class="text-sm opacity-50 text-center py-4">
      Configure Source, Mapping, and Output, then click "Run Dry Run" to preview results.
    </div>
  </div>
</div>
```

### API: `POST /api/v2/jobs/dry-run`

**Request:**

```json
{
  "input_id": "sg-us-prices",
  "output_type": "rdbms",
  "output_id": "pg-prod",
  "mapping": { ... },
  "mapping_id": "order.json",
  "doc_count": 3
}
```

Either `mapping` (embedded object) or `mapping_id` (reference to saved mapping) is required. If both, `mapping` takes precedence.

**Response:**

```json
{
  "success": true,
  "docs_processed": 3,
  "docs_succeeded": 2,
  "docs_failed": 1,
  "results": [
    {
      "doc_id": "order::12345",
      "doc_rev": "3-abc123",
      "status": "ok",
      "tables": {
        "orders": {
          "operation": "UPSERT",
          "row": {
            "doc_id": "order::12345",
            "status": "shipped",
            "customer_name": "Alice",
            "ship_city": "Springfield"
          }
        },
        "order_items": {
          "operation": "DELETE_INSERT",
          "rows": [
            { "order_doc_id": "order::12345", "product_id": "p:100", "qty": 2, "price": "19.99" },
            { "order_doc_id": "order::12345", "product_id": "p:200", "qty": 1, "price": "49.50" }
          ]
        }
      },
      "warnings": [
        { "table": "orders", "column": "ship_zip", "message": "Field $.shipping_address.zip not found — NULL will be inserted" }
      ],
      "coercions": []
    },
    {
      "doc_id": "order::99999",
      "doc_rev": "1-xyz",
      "status": "error",
      "error": "Transform to_decimal() failed on $.price: value 'free' is not numeric",
      "tables": {}
    }
  ]
}
```

### Server-Side Implementation

```python
async def dry_run_job(request):
    data = await request.json()
    
    input_id = data['input_id']
    doc_count = min(data.get('doc_count', 3), 10)  # cap at 10
    
    # 1. Resolve the input
    input_config = cbl_store.get_input_by_id(input_id)
    if not input_config:
        return web.json_response({"error": "Input not found"}, status=404)
    
    # 2. Resolve the mapping
    mapping = data.get('mapping')
    if not mapping:
        mapping_id = data.get('mapping_id')
        if mapping_id:
            mapping = load_mapping_by_name(mapping_id)
    
    if not mapping:
        return web.json_response({"error": "No mapping provided"}, status=400)
    
    # 3. Fetch N sample docs from the source
    sample_docs = await fetch_sample_docs(input_config, count=doc_count)
    
    # 4. Run each doc through the mapper (dry run — no DB writes)
    mapper = SchemaMapper(mapping)
    results = []
    
    for doc in sample_docs:
        try:
            ops = mapper.map_document(doc)
            # Convert SqlOperations to a readable preview
            tables_preview = {}
            for op in ops:
                table_name = op.table
                if op.operation == 'UPSERT':
                    tables_preview[table_name] = {
                        "operation": "UPSERT",
                        "row": op.values
                    }
                elif op.operation == 'DELETE_INSERT':
                    if table_name not in tables_preview:
                        tables_preview[table_name] = {"operation": "DELETE_INSERT", "rows": []}
                    tables_preview[table_name]["rows"].append(op.values)
            
            results.append({
                "doc_id": doc.get("_id", "unknown"),
                "doc_rev": doc.get("_rev", ""),
                "status": "ok",
                "tables": tables_preview,
                "warnings": mapper.get_warnings(),
                "coercions": mapper.get_coercions()
            })
        except Exception as e:
            results.append({
                "doc_id": doc.get("_id", "unknown"),
                "doc_rev": doc.get("_rev", ""),
                "status": "error",
                "error": str(e),
                "tables": {}
            })
    
    succeeded = sum(1 for r in results if r["status"] == "ok")
    
    return web.json_response({
        "success": True,
        "docs_processed": len(results),
        "docs_succeeded": succeeded,
        "docs_failed": len(results) - succeeded,
        "results": results
    })
```

### Dry Run Results UI (JS)

```javascript
async function runDryRun() {
  var btn = document.getElementById('jbDryRunBtn');
  btn.classList.add('loading');
  
  var mapping = currentJobBuilder.mappingData || null;
  var mappingId = mapping ? null : document.getElementById('jbMappingSelect').value;
  
  if (!mapping && !mappingId) {
    alert('No mapping configured. Select a mapping or create a new one first.');
    btn.classList.remove('loading');
    return;
  }
  
  var payload = {
    input_id: currentJobBuilder.sourceId,
    output_type: currentJobBuilder.outputType,
    output_id: currentJobBuilder.outputId,
    doc_count: parseInt(document.getElementById('jbDryRunCount').value, 10)
  };
  
  if (mapping) {
    payload.mapping = mapping;
  } else {
    payload.mapping_id = mappingId;
  }
  
  try {
    var res = await fetch('/api/v2/jobs/dry-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    renderDryRunResults(data);
  } catch (e) {
    alert('Dry run failed: ' + e.message);
  }
  
  btn.classList.remove('loading');
}

function renderDryRunResults(data) {
  var container = document.getElementById('jbDryRunResults');
  document.getElementById('jbDryRunEmpty').classList.add('hidden');
  container.classList.remove('hidden');
  
  // Summary bar
  var summaryClass = data.docs_failed > 0 ? 'alert-warning' : 'alert-success';
  var html = '<div class="alert ' + summaryClass + ' text-sm p-3">' +
    '<span>' + data.docs_succeeded + '/' + data.docs_processed + ' docs mapped successfully</span>' +
    '</div>';
  
  // Per-doc accordion
  data.results.forEach(function(r, idx) {
    var statusBadge = r.status === 'ok'
      ? '<span class="badge badge-success badge-sm">OK</span>'
      : '<span class="badge badge-error badge-sm">Error</span>';
    
    html += '<div class="collapse collapse-arrow bg-base-200 rounded-xl">' +
      '<input type="checkbox"' + (idx === 0 ? ' checked' : '') + ' />' +
      '<div class="collapse-title text-sm font-semibold flex items-center gap-2">' +
        statusBadge + ' ' + escHtml(r.doc_id) +
        (r.warnings && r.warnings.length ? ' <span class="badge badge-warning badge-xs">' + r.warnings.length + ' warnings</span>' : '') +
      '</div>' +
      '<div class="collapse-content">';
    
    if (r.status === 'error') {
      html += '<div class="alert alert-error text-xs p-2">' + escHtml(r.error) + '</div>';
    } else {
      // Table results
      Object.keys(r.tables).forEach(function(tableName) {
        var table = r.tables[tableName];
        var rows = table.rows ? table.rows : [table.row];
        
        html += '<div class="mb-3">' +
          '<div class="text-xs font-semibold mb-1">' + escHtml(tableName) + 
          ' <span class="badge badge-ghost badge-xs">' + table.operation + '</span>' +
          ' <span class="badge badge-ghost badge-xs">' + rows.length + ' row(s)</span></div>' +
          '<div class="overflow-x-auto"><table class="table table-xs table-zebra">' +
          '<thead><tr class="bg-base-300">';
        
        var cols = Object.keys(rows[0] || {});
        cols.forEach(function(c) { html += '<th class="text-xs">' + escHtml(c) + '</th>'; });
        html += '</tr></thead><tbody>';
        
        rows.forEach(function(row) {
          html += '<tr>';
          cols.forEach(function(c) {
            var val = row[c];
            html += '<td class="text-xs font-mono">' + escHtml(String(val != null ? val : 'NULL')) + '</td>';
          });
          html += '</tr>';
        });
        
        html += '</tbody></table></div></div>';
      });
      
      // Warnings
      if (r.warnings && r.warnings.length) {
        html += '<div class="space-y-1 mt-2">';
        r.warnings.forEach(function(w) {
          html += '<div class="alert alert-warning text-xs p-2">⚠ [' + escHtml(w.table) + '.' + escHtml(w.column) + '] ' + escHtml(w.message) + '</div>';
        });
        html += '</div>';
      }
    }
    
    html += '</div></div>';
  });
  
  container.innerHTML = html;
  
  // Update step 4
  if (data.docs_succeeded > 0) {
    var step = document.getElementById('jbStep4');
    step.className = 'step step-success';
    step.setAttribute('data-content', '✓');
  }
}
```

---

## Complete User Flow: New Job Creation

```
1. User clicks "+ New Job" on /jobs
   └── Job builder panel appears (3-column + advanced)

2. User picks a Source (clicks SG → selects "sg-us-prices")
   └── Step 1 turns green ✓
   └── Source ID stored in currentJobBuilder.sourceId

3. User picks an Output type + instance (clicks RDBMS → selects "pg-prod")
   └── Step 3 turns green ✓
   └── Output ID stored in currentJobBuilder.outputId

4. User configures Process type (Data Only or Data & Attachments)
   └── Step 2 turns green ✓

5. User clicks "+ New Mapping" button in Process card
   └── Full-screen modal opens
   └── Iframe loads /schema?job_mode=true&input_id=sg-us-prices&output_type=rdbms&output_id=pg-prod
   └── schema.html hides standalone UI, shows job-mode footer
   └── Live Sample auto-fetches from the selected input
   └── (If RDBMS) output tables are auto-introspectable

6. User builds mapping in the schema editor
   └── Drag fields, configure tables, add transforms
   └── All existing schema.html features work (DDL import, AI assist, etc.)

7. User clicks "Apply to Job"
   └── Mapping JSON is postMessage'd back to jobs.html
   └── Modal closes
   └── Process card shows "✓ Custom mapping (3 tables)"
   └── Mapping data stored in currentJobBuilder.mappingData

8. User clicks "▶ Run Dry Run" (optional but recommended)
   └── 3 sample docs fetched from source
   └── Each run through mapper
   └── Results shown per-doc with table previews, warnings, coercions
   └── Step 4 turns green ✓ (if at least 1 doc succeeds)

9. User clicks "Save Job"
   └── Job document saved to CBL with embedded mapping
   └── Job appears in table with "Stopped" status
   └── User can Start/Stop/Restart from the table
```

---

## Complete User Flow: Editing a Mapping on an Existing Job

```
Flow A — From the Job Table (quick mapping edit):

1. User sees job "US Orders Sync" in the Jobs table
2. User clicks "📐 Mapping" button in the Actions column
   └── Job is fetched via GET /api/v2/jobs/{id}
   └── Full-screen modal opens
   └── Iframe loads schema.html with job_mode + context
   └── Existing mapping is loaded into the editor (via postMessage or mapping_name param)
3. User makes changes (adds a column, changes a transform)
4. User clicks "Apply to Job"
   └── PUT /api/v2/jobs/{id}/mapping sends the updated mapping
   └── Modal closes
   └── Toast: "✓ Mapping updated!"
   └── If job was running: toast also says "Restart job to apply changes"

Flow B — From inside the Job Builder (full edit mode):

1. User clicks "Edit" on a job in the Jobs table
   └── Job builder opens with all fields populated
   └── Process card shows current mapping in dropdown/summary
2. User clicks "Edit Selected" next to the mapping dropdown
   └── Same modal flow as above
3. User edits the mapping and clicks "Apply to Job"
   └── Mapping updated in currentJobBuilder.mappingData
   └── User can also change other fields (threads, source, output)
4. User clicks "Save Job"
   └── Entire job is saved via PUT /api/v2/jobs/{id}

Flow C — From the standalone Schema page (legacy):

1. User navigates to /schema
2. User loads "order.json" from the Saved Mappings table
3. User makes changes and clicks "Save Mapping"
   └── Mapping file updated in mappings/ or CBL
   └── Any job with mapping_id="order.json" picks up changes on next restart
   └── (Note: jobs with embedded mappings are NOT affected)
```

---

## Migration Path for Existing Mappings

When Phase 9 (Schema Mapping Migration) runs:

1. For each `mappings/*.json` file:
   a. Find jobs that reference it via `mapping_id`
   b. If exactly 1 job references it → embed the mapping into the job document (Model B)
   c. If N>1 jobs reference it → keep as referenced (Model A), all jobs share it
   d. If 0 jobs reference it → keep the file, no migration needed
2. The `mappings/` directory stays as an import surface — users can drop mapping files there and reference them

---

## Implementation Checklist

### `schema.html` Changes

- [x] Add `id="topActionBar"` to the top action bar card
- [x] Add `id="savedMappingsCard"` to the saved mappings table card
- [x] Add job-mode footer HTML (`id="jobModeFooter"`, hidden by default)
- [x] Add `JOB_MODE` detection from URL params at script top
- [x] Add `applyMappingToJob()` function (postMessage to parent)
- [x] Add `cancelJobModeMapping()` function (postMessage cancel)
- [x] Add `postMessage` listener for `load-mapping` type (for edit flows)
- [x] Add `loadMappingFromObject(mapping)` function (loads a mapping object into the editor state, counterpart to the existing `loadExistingMapping(name)` which loads from the API)
- [ ] Add `autoFetchSampleForInput(inputId)` function (calls `/api/sample-doc?input_id={id}`)
- [ ] Add `autoLoadOutputTables(outputId)` function (calls `/api/outputs_rdbms/{id}` and populates table definitions)
- [x] When `job_mode=true`: hide topActionBar, savedMappingsCard; show jobModeFooter
- [x] When `job_mode=true`: auto-trigger pre-seeding from query params after DOMContentLoaded

### `jobs.html` Changes

- [x] Add `<dialog id="schemaMappingModal">` with full-screen iframe
- [x] Replace Process card "Schema Mapping" section with action buttons (+ New, Edit, Preview)
- [x] Add mapping summary card (`jbMappingSummary`)
- [x] Add `openSchemaMappingModal(options)` function
- [x] Add `closeSchemaMappingModal()` function
- [x] Add `postMessage` listener for `schema-mapping-result`
- [x] Add `updateMappingDisplay()` function
- [x] Add `openCreateMapping()` and `openEditMapping()` functions
- [x] Add `editJobMapping(jobId)` function for direct table edit flow
- [x] Add `saveMappingToExistingJob(jobId, mappingData)` function
- [x] Add "📐 Mapping" button to job table rows
- [x] Update `saveJob()` to include `mapping` or `mapping_id` in payload
- [x] Update `editJob()` to handle embedded mapping data
- [x] Add `currentJobBuilder.mappingData` and `currentJobBuilder.mappingName` to state
- [x] Add Dry Run card HTML (`jbDryRunCard`)
- [x] Add 4th step to progress indicator ("Dry Run")
- [x] Add `runDryRun()` function
- [x] Add `renderDryRunResults()` function

### `web/server.py` Changes

- [x] Update `POST /api/v2/jobs` to accept `mapping` object in payload
- [x] Update `PUT /api/v2/jobs/{id}` to accept `mapping` object in payload
- [x] Add `PUT /api/v2/jobs/{id}/mapping` — update mapping only on existing job
- [ ] Add `POST /api/v2/jobs/dry-run` — dry run endpoint
- [ ] Update `GET /api/sample-doc` to accept `?input_id=` query param
- [x] Add route registrations for new endpoints

### `schema/mapper.py` Changes

- [ ] Add `get_warnings()` method to SchemaMapper (for dry run reporting)
- [ ] Add `get_coercions()` method to SchemaMapper (for dry run reporting)
- [ ] Ensure `map_document()` can be called standalone without a DB connection (for dry run)

### Job Document Schema Update

- [x] Add `mapping` field (object, nullable) to job document JSON schema
- [x] Document the resolution order: embedded → referenced → pass-through
- [x] Update validation to accept either `mapping` or `mapping_id` (or neither)

---

## RDBMS Table Definitions: New `tables_rdbms` Collection

### The Problem Today

Currently, RDBMS table DDL (`CREATE TABLE ...`) lives **inside** the output document at `outputs_rdbms.src[].tables[]`:

```json
{
  "type": "output_rdbms",
  "src": [
    {
      "id": "pg-local",
      "engine": "postgres",
      "host": "...",
      "tables": [
        { "active": true, "name": "orders", "sql": "CREATE TABLE IF NOT EXISTS orders (...)" },
        { "active": true, "name": "order_items", "sql": "CREATE TABLE IF NOT EXISTS order_items (...)" }
      ]
    }
  ]
}
```

This is wrong for several reasons:

1. **Tables belong to jobs, not outputs.** Two jobs can write to the same PostgreSQL instance (`pg-local`) but to completely different tables. Tying tables to the output connection means you can't have Job A write to `orders` and Job B write to `events` on the same `pg-local` — they'd share the same `tables[]` array.
2. **Tables are reusable across jobs.** The `orders` table definition might be used by a US Orders job AND an EU Orders job writing to different PostgreSQL instances. There's no way to define it once and reference it from both.
3. **No lifecycle management.** Users add/remove tables from jobs frequently. With tables buried inside outputs, there's no central place to browse "all my table definitions" or see "which jobs use this table."
4. **Schema mapping and table DDL are tightly coupled but stored separately.** The schema mapping defines column paths (`doc_id → $._id`), and the table DDL defines column types (`doc_id TEXT PRIMARY KEY`). These describe the same table but live in different places with no link between them.

### Solution: `tables_rdbms` Collection

Add a new CBL collection: **`tables_rdbms`** — a library of reusable RDBMS table definitions.

**Collection:** `tables_rdbms`  
**Doc ID:** `tables_rdbms`

```json
{
  "type": "tables_rdbms",
  "tables": [
    {
      "id": "tbl-orders",
      "name": "orders",
      "engine_hint": "postgres",
      "sql": "CREATE TABLE IF NOT EXISTS orders (\n  doc_id TEXT PRIMARY KEY,\n  rev TEXT,\n  status TEXT,\n  customer_id TEXT,\n  customer_name TEXT,\n  order_date TIMESTAMP,\n  total NUMERIC(10,2)\n)",
      "columns": [
        { "name": "doc_id", "type": "TEXT", "primary_key": true, "nullable": false },
        { "name": "rev", "type": "TEXT", "primary_key": false, "nullable": true },
        { "name": "status", "type": "TEXT", "primary_key": false, "nullable": true },
        { "name": "customer_id", "type": "TEXT", "primary_key": false, "nullable": true },
        { "name": "customer_name", "type": "TEXT", "primary_key": false, "nullable": true },
        { "name": "order_date", "type": "TIMESTAMP", "primary_key": false, "nullable": true },
        { "name": "total", "type": "NUMERIC(10,2)", "primary_key": false, "nullable": true }
      ],
      "meta": {
        "created_at": "2026-04-20T10:00:00Z",
        "updated_at": "2026-04-22T14:30:00Z",
        "source": "ddl_upload"
      }
    },
    {
      "id": "tbl-order-items",
      "name": "order_items",
      "engine_hint": "postgres",
      "parent_table": "tbl-orders",
      "foreign_key": { "column": "order_doc_id", "references_table": "orders", "references_column": "doc_id" },
      "sql": "CREATE TABLE IF NOT EXISTS order_items (\n  id SERIAL PRIMARY KEY,\n  order_doc_id TEXT REFERENCES orders(doc_id),\n  product_id TEXT,\n  qty INTEGER,\n  price NUMERIC(10,2)\n)",
      "columns": [
        { "name": "id", "type": "SERIAL", "primary_key": true, "nullable": false },
        { "name": "order_doc_id", "type": "TEXT", "primary_key": false, "nullable": false },
        { "name": "product_id", "type": "TEXT", "primary_key": false, "nullable": true },
        { "name": "qty", "type": "INTEGER", "primary_key": false, "nullable": true },
        { "name": "price", "type": "NUMERIC(10,2)", "primary_key": false, "nullable": true }
      ],
      "meta": {
        "created_at": "2026-04-20T10:00:00Z",
        "updated_at": "2026-04-22T14:30:00Z",
        "source": "ddl_upload"
      }
    },
    {
      "id": "tbl-events",
      "name": "events",
      "engine_hint": "mysql",
      "sql": "CREATE TABLE IF NOT EXISTS events (\n  id VARCHAR(255) PRIMARY KEY,\n  payload JSON,\n  created_at DATETIME\n)",
      "columns": [
        { "name": "id", "type": "VARCHAR(255)", "primary_key": true, "nullable": false },
        { "name": "payload", "type": "JSON", "primary_key": false, "nullable": true },
        { "name": "created_at", "type": "DATETIME", "primary_key": false, "nullable": true }
      ],
      "meta": {
        "created_at": "2026-04-21T09:00:00Z",
        "updated_at": "2026-04-21T09:00:00Z",
        "source": "db_introspect"
      }
    }
  ]
}
```

### Key Design Decisions

#### Why `columns[]` AND `sql`?

Both are stored because they serve different purposes:

| Field | Purpose |
|---|---|
| `sql` | The raw DDL statement. Executed against the target DB to create/alter the table. Preserves the user's exact SQL (constraints, indexes, comments). |
| `columns[]` | Parsed, structured representation. Used by the UI (column pickers, mapping editor, dry-run type validation). Generated automatically when DDL is uploaded or DB is introspected. |

When a user uploads DDL, the server parses it into `columns[]`. When a user edits columns in the UI, the server regenerates `sql` from `columns[]`. Either can be the source of truth depending on the user's workflow.

#### Why `engine_hint` and not strict engine locking?

A table defined with PostgreSQL syntax (`SERIAL`, `TEXT`) can often work on MySQL with minor tweaks. The `engine_hint` records which engine the DDL was written for, but doesn't prevent using the table with a different output engine. The pipeline's DB layer already handles dialect translation.

#### Why `parent_table` and `foreign_key`?

Tables have relationships. `order_items` references `orders`. Storing this in the table library means:
- The UI can show the relationship tree when browsing tables
- The job builder can auto-suggest related child tables when you add a parent table
- The schema mapping can auto-wire FK column paths

### How Tables Flow Into Jobs

When a job is created or edited:

1. User picks tables from the `tables_rdbms` library → **copies** of the selected table definitions are embedded into the job document at `job.outputs[].tables[]`
2. The job document owns its copy — editing the table definition in the job does NOT change the library version (and vice versa)
3. This is the same copy-on-select pattern used for inputs and outputs already

```json
{
  "type": "job",
  "id": "abc-123",
  "outputs": [
    {
      "id": "pg-local",
      "engine": "postgres",
      "host": "...",
      "tables": [
        {
          "id": "tbl-orders",
          "library_ref": "tbl-orders",
          "name": "orders",
          "sql": "CREATE TABLE IF NOT EXISTS orders (...)",
          "active": true
        },
        {
          "id": "tbl-order-items",
          "library_ref": "tbl-order-items",
          "name": "order_items",
          "sql": "CREATE TABLE IF NOT EXISTS order_items (...)",
          "active": true
        }
      ]
    }
  ]
}
```

**`library_ref`** — tracks which library table this was copied from. Used for:
- "Refresh from library" button (re-copy the latest version if the library definition was updated)
- "Which jobs use this library table?" query (scan jobs for matching `library_ref`)
- Showing a diff when the library version diverges from the job's embedded copy

### Tables Stay in Outputs Too (Backward Compat)

The `outputs_rdbms.src[].tables[]` field stays for backward compatibility and as a "default tables" concept — when you create a new job with output `pg-local`, the job builder can auto-suggest the tables already defined on that output. But the job's own `tables[]` is authoritative.

### Multi-Job Table Sharing Warnings

When the same table (by `name`, not `id`) is used by multiple jobs writing to the **same** database, there's a conflict risk. Two jobs writing to the same `orders` table on the same PostgreSQL instance will collide (competing UPSERTs, DELETE cascades, etc.).

#### When to Warn

| Scenario | Warning Level | Message |
|---|---|---|
| Same table name + same output `id` (same DB) | 🔴 **Error** | "Table `orders` is already used by job 'US Orders Sync' on the same database `pg-local`. Two jobs writing to the same table will conflict." |
| Same table name + different output `id` but same host:port/database | 🟡 **Warning** | "Table `orders` exists on another output (`pg-backup`) that points to the same database. Verify this is intentional." |
| Same table name + different database entirely | ✅ **OK** | No warning. Different databases, no conflict. |
| Same `library_ref` + different table name (user renamed) | ✅ **OK** | No warning. They diverged. |

#### How to Detect

When saving a job, the server:

1. For each table in `job.outputs[].tables[]`:
   a. Query all other jobs in the `jobs` collection
   b. For each other job, check if it has a table with the same `name`
   c. If yes, compare the output connection details (host, port, database)
   d. Return warnings in the save response

```python
def check_table_conflicts(new_job, all_jobs):
    warnings = []
    new_output = new_job['outputs'][0]
    new_tables = [t['name'] for t in new_output.get('tables', [])]
    new_db_key = f"{new_output['host']}:{new_output['port']}/{new_output['database']}"
    
    for other_job in all_jobs:
        if other_job['id'] == new_job['id']:
            continue
        other_output = other_job['outputs'][0]
        other_tables = [t['name'] for t in other_output.get('tables', [])]
        other_db_key = f"{other_output['host']}:{other_output['port']}/{other_output['database']}"
        
        overlap = set(new_tables) & set(other_tables)
        if overlap and new_db_key == other_db_key:
            for table_name in overlap:
                warnings.append({
                    "level": "error",
                    "table": table_name,
                    "conflicting_job": other_job['name'],
                    "conflicting_job_id": other_job['id'],
                    "message": f"Table '{table_name}' is already used by job '{other_job['name']}' on the same database."
                })
    
    return warnings
```

#### UI: Warning Display

In the job builder, after saving (or on dry-run), show conflict warnings:

```html
<!-- Table conflict warnings (shown below the tables panel) -->
<div id="jbTableConflicts" class="hidden space-y-1 mt-2">
  <!-- dynamically populated -->
</div>
```

```javascript
function renderTableConflicts(warnings) {
  var el = document.getElementById('jbTableConflicts');
  if (!warnings || !warnings.length) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML = warnings.map(function(w) {
    var alertClass = w.level === 'error' ? 'alert-error' : 'alert-warning';
    return '<div class="alert ' + alertClass + ' text-xs p-2">' +
      '<span>⚠ <strong>' + escHtml(w.table) + '</strong>: ' + escHtml(w.message) + '</span>' +
      '<a class="link link-primary text-xs" onclick="editJob(\'' + escHtml(w.conflicting_job_id) + '\')">' +
        'View ' + escHtml(w.conflicting_job) + ' →</a>' +
      '</div>';
  }).join('');
}
```

#### Library Page: "Used By" Column

On the table library UI (see Step 10 below), each table shows which jobs reference it:

```
Table Name    | Engine  | Columns | Used By              | Actions
──────────────|─────────|─────────|──────────────────────|──────────
orders        | postgres| 7       | US Orders, EU Orders | Edit · Delete
order_items   | postgres| 5       | US Orders, EU Orders | Edit · Delete
events        | mysql   | 3       | Analytics Sync       | Edit · Delete
products      | postgres| 4       | (none)               | Edit · Delete
```

Tables used by 1+ jobs show a green "used by" count. Tables used by 0 jobs show "(none)" in gray. Delete is blocked (with confirmation) for tables used by active jobs.

### REST API Endpoints

```
GET    /api/v2/tables_rdbms                    — List all table definitions
GET    /api/v2/tables_rdbms/{id}               — Get one table definition  
POST   /api/v2/tables_rdbms                    — Create a new table definition
PUT    /api/v2/tables_rdbms/{id}               — Update a table definition
DELETE /api/v2/tables_rdbms/{id}               — Delete a table definition
POST   /api/v2/tables_rdbms/import-ddl         — Parse DDL text, create table entries
POST   /api/v2/tables_rdbms/import-introspect  — Introspect a DB, create table entries
GET    /api/v2/tables_rdbms/{id}/used-by       — List jobs that reference this table
```

### CBL Store Changes

```python
# In cbl_store.py:
COLL_TABLES_RDBMS = "tables_rdbms"

# Add to _ensure_collections():
self._ensure_collection(SCOPE, COLL_TABLES_RDBMS)
```

### Step 10: Table Library UI in the Job Builder

The table library integrates into the job builder's Output card. When the user selects an RDBMS output, the Tables panel shows:

1. **Library tables** — all tables from `tables_rdbms`, with checkboxes to add/remove from the current job
2. **Job-specific tables** — tables already in this job (checked), with "Edit" to modify the job's copy
3. **Import options** — "Import DDL", "Fetch from DB", "+ New Table" (same as today, but saves to the library AND adds to the job)

```
┌─────────────────────────────────────────────────────────┐
│  Tables for this Job                                     │
│  ┌───────────────────────────────────────────────────┐   │
│  │ ☑ orders         7 cols  PK: doc_id    [Edit][✕]  │   │
│  │ ☑ order_items    5 cols  FK→orders     [Edit][✕]  │   │
│  │ ☐ events         3 cols  PK: id                   │   │
│  │ ☐ products       4 cols  PK: doc_id               │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  [Import DDL]  [Fetch from DB]  [+ New Table]            │
│                                                          │
│  ⚠ "orders" is also used by job "EU Orders Sync"         │
│    on the same database pg-local.                        │
└─────────────────────────────────────────────────────────┘
```

**Checkbox behavior:**
- ☑ Checking a table → copies the library definition into `currentJobBuilder.tables[]`
- ☐ Unchecking → removes it from `currentJobBuilder.tables[]` (doesn't touch the library)
- "Edit" → opens inline editor for the **job's copy** of the table (not the library version)
- Saving the job embeds the checked tables into the job document

### How This Connects to Schema Mapping

The schema mapping's `tables[].name` must match the RDBMS table names in the job's `outputs[].tables[].name`. The dry-run can now validate this:

```
Dry Run Validation:
  ✓ Mapping table "orders" → found in job output tables
  ✓ Mapping table "order_items" → found in job output tables  
  ✗ Mapping table "order_tags" → NOT found in job output tables
    → Either add an "order_tags" table definition or remove this mapping table
```

This closes the loop: **table definitions** (DDL) define the physical schema, **schema mapping** defines the JSON→column routing, and the **dry run** validates that both agree before the job runs.

### Migration: Tables from `outputs_rdbms` → `tables_rdbms`

On first v2.0 startup (or migration trigger):

1. Read `outputs_rdbms.src[].tables[]` for every output
2. For each table, check if it already exists in `tables_rdbms` by `name`
3. If not, create a new entry in `tables_rdbms` with:
   - `id`: auto-generated `tbl-{name}`
   - `engine_hint`: from parent output's `engine`
   - `sql`: from existing `sql` field
   - `columns`: parsed from DDL
   - `meta.source`: `"migration_v1"`
4. For each job that references this output, add `library_ref` to the job's embedded table copy

### Implementation Checklist for `tables_rdbms`

**`cbl_store.py`:**
- [x] Add `COLL_TABLES_RDBMS = "tables_rdbms"` constant
- [x] Add `load_tables_rdbms()` — return full document
- [x] Add `save_tables_rdbms(doc)` — save full document
- [x] Add `get_table_rdbms(table_id)` — find one entry in `tables[]`
- [x] Add `upsert_table_rdbms(table_entry)` — add or update one entry
- [x] Add `delete_table_rdbms(table_id)` — remove one entry
- [x] Add `get_tables_rdbms_used_by(table_id)` — scan jobs for `library_ref` matches

**`rest/api_v2.py` + `web/server.py`:**
- [x] Add `GET /api/v2/tables_rdbms` endpoint
- [x] Add `GET /api/v2/tables_rdbms/{id}` endpoint
- [x] Add `POST /api/v2/tables_rdbms` endpoint
- [x] Add `PUT /api/v2/tables_rdbms/{id}` endpoint
- [x] Add `DELETE /api/v2/tables_rdbms/{id}` endpoint
- [x] Add `GET /api/v2/tables_rdbms/{id}/used-by` endpoint
- [x] Add route registrations in `server.py`
- [ ] Add `POST /api/v2/tables_rdbms/import-ddl` endpoint
- [ ] Add `POST /api/v2/tables_rdbms/import-introspect` endpoint
- [ ] Add `check_table_conflicts()` to job save flow
- [ ] Return warnings in job save response

**Tests:**
- [x] `tests/test_cbl_store_tables_rdbms.py` — 12 unit tests (all passing)
- [x] `tests/test_api_v2_tables_rdbms.py` — 11 integration tests (all passing)

**`jobs.html` Changes:**
- [ ] Update Tables panel to show library tables with checkboxes
- [ ] Add "used by" indicators to table rows
- [ ] Add table conflict warnings display
- [ ] Update `saveJob()` to include `library_ref` on embedded tables
- [ ] Import DDL / Fetch from DB now saves to library AND adds to job

**`schema.html` Changes:**
- [ ] When in `job_mode`, auto-populate output table columns from job's table definitions
- [ ] Validate mapping table names against job's table names

**Migration:**
- [ ] Add migration step to `cbl_store.py` to move `outputs_rdbms.src[].tables[]` → `tables_rdbms`
- [ ] Add `library_ref` backfill to existing job documents

---

## Updated Collections Table

Adding `tables_rdbms` to the v2.0 collections from `DESIGN_2_0.md`:

| Collection | Documents | Purpose |
|---|---|---|
| `inputs_changes` | 1 document | Array of `_changes` feed source definitions |
| `outputs_rdbms` | 1 document | Array of RDBMS output configs (connection only — tables move to `tables_rdbms`) |
| `outputs_http` | 1 document | Array of HTTP/REST output configs |
| `outputs_cloud` | 1 document | Array of cloud blob output configs |
| **`tables_rdbms`** | **1 document** | **Array of reusable RDBMS table definitions (DDL + parsed columns)** |
| `jobs` | N documents | Each job connects input → output with tables + mapping |
| `checkpoints` | N documents | Per-job checkpoint state |
| `dlq` | N documents | Dead letter queue |
| `data_quality` | N documents | Data coercion log |
| `enrichments` | N documents | Async analysis results |
| `config` | 1 document | Global infrastructure config |

---

## Open Questions

1. **Hot reload?** When a mapping is updated on a running job, should the pipeline hot-reload the mapper without a full restart? Or always require restart? (Recommendation: require restart for safety — show a toast "Restart job to apply mapping changes")

2. **Mapping versioning?** Should embedded mappings track a version counter so users can see "this mapping was modified 3 times since job creation"? (Recommendation: defer to v2.1 — use `meta.updated_at` timestamp for now)

3. **Shared mapping warning?** When a user edits a referenced mapping on `/schema` that's used by multiple jobs, should we show a warning listing all affected jobs? (Recommendation: yes, low-effort — query jobs collection for `mapping_id` matches)

4. **Dry run on edit?** Should the "Edit Mapping" flow (for existing jobs) also include a dry-run option inside the modal? (Recommendation: yes — add a "Test" button to the job-mode footer in schema.html that calls the same dry-run API)

5. **Iframe vs. refactor?** Should we invest in extracting schema.html's mapping editor into a reusable JS module (no iframe) for v2.1? (Recommendation: yes for v2.1, iframe is the pragmatic v2.0 solution)

6. **Table conflict behavior?** When two jobs write to the same table on the same DB, should the save be blocked (hard error) or just warned (soft warning, allow save)? (Recommendation: soft warning — there are legitimate cases like partitioned writes where two jobs intentionally share a table)

7. **Auto-suggest child tables?** When a user adds `orders` to a job, should the UI auto-check `order_items` and `order_tags` if they have `parent_table: "tbl-orders"`? (Recommendation: yes, auto-check with a toast "Also added 2 child tables" — user can uncheck)

8. **Table DDL auto-create?** Should the pipeline auto-run `CREATE TABLE IF NOT EXISTS` on job start, or require the user to pre-create tables? (Recommendation: auto-create by default, with a `auto_create_tables: false` system setting to disable for locked-down prod environments)
