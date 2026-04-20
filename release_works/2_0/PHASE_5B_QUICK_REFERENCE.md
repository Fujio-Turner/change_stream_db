# Phase 5B: Jobs Wizard UI — QUICK REFERENCE

## Status: ✅ COMPLETE

**Phase 5B** adds the web UI for managing jobs created in Phase 5.

---

## Quick Start

1. Navigate to the wizard: `/wizard`
2. Click **"Jobs"** card (🎯 icon)
3. Click **"+ Create Job"** button
4. Fill the form:
   - **Job Name:** Give your job a name (e.g., "Orders Export")
   - **Select Input:** Choose a source system
   - **Output Type:** Pick RDBMS, HTTP, Cloud, or Stdout
   - **Select Output:** Choose destination (list filters by type)
   - **Field Mapping:** Select schema mapping (optional)
   - **System:** Leave as "default" or customize
5. Click **"Save Job"**
6. Job appears in the list immediately

---

## Jobs List View

**Columns:**
- Job ID (abbreviated)
- Name
- System config
- Input source
- Output type
- Number of fields mapped
- Actions: Edit / Delete

**Buttons:**
- **+ Create Job** — Open create form
- **Refresh** — Reload jobs list
- **Edit** — Modify job
- **Delete** — Remove job (with confirmation)

---

## Create / Edit Form

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Job ID | Text | Yes | Auto-generated, read-only when editing |
| Job Name | Text | Yes | e.g., "Orders Export" |
| Select Input | Dropdown | Yes | Lists all inputs from Phase 3 |
| Output Type | Select | Yes | RDBMS, HTTP, Cloud, Stdout |
| Select Output | Dropdown | Yes | Filtered by output type |
| Field Mapping | Dropdown | No | Leave blank for no mapping |
| System | Text | Yes | Default: "default" |

**Actions:**
- **Save Job** — Create (POST) or Update (PUT)
- **Cancel** — Return to list without saving

---

## Delete Job

1. Click **Delete** button on job row
2. Confirm dialog appears
3. Click **OK** to confirm
4. Job + checkpoint deleted atomically
5. Toast confirms: "Job deleted"

---

## API Endpoints Called

**From Phase 5 (Jobs):**
- `GET /api/jobs` — List jobs
- `POST /api/jobs` — Create job
- `PUT /api/jobs/{id}` — Update job
- `DELETE /api/jobs/{id}` — Delete job + checkpoint

**From Phase 3 (Inputs):**
- `GET /api/inputs` — Populate input dropdown

**From Phase 4 (Outputs):**
- `GET /api/outputs/{type}` — Populate output dropdown

**From Mappings:**
- `GET /api/mappings` — Populate mapping dropdown

---

## Common Tasks

### Create a Job
```
1. Click "Create Job"
2. Fill form (name, input, output type, output)
3. Click "Save Job"
→ Job created in database, appears in list
```

### Edit a Job
```
1. Click "Edit" on job row
2. Form loads with current values
3. Change fields (name, mapping, etc.)
4. Click "Save Job"
→ Job updated in database
```

### Delete a Job
```
1. Click "Delete" on job row
2. Confirm dialog appears
3. Click "OK"
→ Job and checkpoint deleted
```

### Change Output Type
```
1. On form, change "Output Type" dropdown
2. Output dropdown refreshes automatically
3. Select new output
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Error loading jobs" | Check network, restart browser |
| "Output required" | Select an output type first, then choose output |
| "Error saving job" | Check browser console, verify API running |
| Form doesn't appear | Refresh page, try again |
| Job doesn't appear after save | Refresh list or wait a moment |

---

## Data Model

Each job document contains:
```json
{
  "job_id": "job_1234567890",
  "name": "Orders Export",
  "input_id": "sg-production",
  "output_type": "rdbms",
  "output_id": "postgres-main",
  "mapping_id": "orders_mapping",
  "system": "default",
  "enabled": true
}
```

Plus a corresponding checkpoint document:
```json
{
  "type": "checkpoint",
  "job_id": "job_1234567890",
  "seq": 0,
  "...": "..."
}
```

---

## File Changed

- **web/templates/wizard.html** (+350 lines)
  - 120 lines HTML (Jobs Manager section)
  - 230 lines JavaScript (manager functions + state)

---

## Integration

✅ **Works with:**
- Phase 3 (Inputs)
- Phase 4 (Outputs)
- Phase 5 (Jobs REST API)
- Existing wizard infrastructure

✅ **Ready for:**
- Phase 6 (Job-Based Startup)
- Phase 10 (Multi-Job Threading)

---

## What's Next?

**Phase 6:** Load jobs at startup and run them  
**Phase 10:** Run multiple jobs concurrently

For now, Phase 5B provides the **UI layer** for managing jobs. The actual job execution happens in Phase 6 and beyond.

---

## Support

For issues or questions:
1. Check browser console for error messages
2. Verify Phase 5 API is running (`GET /api/jobs` should work)
3. Check Phase 3/4 APIs are working (inputs, outputs lists)
4. Review PHASE_5B_IMPLEMENTATION.md for detailed docs
