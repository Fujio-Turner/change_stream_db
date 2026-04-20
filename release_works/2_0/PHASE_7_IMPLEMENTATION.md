# Phase 7: Settings Cleanup – Implementation Plan

**Status:** 🚀 In Progress  
**Date:** 2026-04-19  
**Objective:** Remove job configuration from settings page. Keep only infrastructure settings.

---

## Current State

### What Settings Currently Have
The settings page in the UI (Phase 5B) allows editing:
- Gateway settings
- Output settings
- Input settings (mapping sources)
- **Job-specific configuration**

### Problem
Now that Phase 6 loads jobs from the database and the job management UI exists in the wizard, **job configuration should NOT be in settings**. Settings should only contain:
- Infrastructure settings (DB, API ports, etc.)
- Application-level settings (logging, metrics, etc.)
- System configuration (timeouts, retries, etc.)

---

## Implementation Plan

### Step 1: Audit Current Settings
**Files to examine:**
- `web/src/components/Settings.svelte` (or similar)
- `rest/settings.py` (or API endpoint)
- `db/` (schema for settings collection)

**Tasks:**
- [ ] Identify all job-related settings fields
- [ ] Identify all infrastructure-only fields
- [ ] Map which fields can be removed
- [ ] Map which fields to keep

### Step 2: Update Settings Schema
**If schema exists in DB:**
- Remove `gateway`, `outputs`, `inputs` from settings schema
- Keep infrastructure fields only
- Add migration to clean existing settings

**If no formal schema:**
- Document which fields are valid in settings
- Update validation logic

### Step 3: Update Settings API
**File:** `rest/settings.py` (or similar)

**Changes:**
- Remove endpoints that allow editing job config via settings
- Keep infrastructure settings endpoints
- Update validation to reject job config fields
- Add logging for removed fields

### Step 4: Update Settings UI
**File:** `web/src/components/Settings.svelte` (or similar)

**Changes:**
- Remove gateway config form sections
- Remove input mapping config sections
- Remove output config sections
- Keep infrastructure config sections
- Add help text: "Edit jobs in the Wizard instead"

### Step 5: Migration & Cleanup
**For existing deployments:**
- Detect settings with job config
- Migrate to job documents
- Clean up settings collection
- Log migration info

### Step 6: Testing
- [ ] Settings can't save job config (rejected)
- [ ] Settings can still save infrastructure config
- [ ] UI no longer shows job config fields
- [ ] Wizard still works for job editing
- [ ] Backward compat: old settings don't break

### Step 7: Documentation
- [ ] Create `PHASE_7_QUICK_REFERENCE.md`
- [ ] Create `PHASE_7_VERIFIED.md`
- [ ] Update `PHASE_7_STATUS.md`
- [ ] Create `PHASE_7_SUMMARY.md`

---

## Expected Changes

### Removed Fields (from settings)
```json
{
  "gateway": { ... },       // ❌ Remove
  "outputs": { ... },       // ❌ Remove
  "inputs": { ... },        // ❌ Remove
  "source_config": { ... }  // ❌ Remove
}
```

### Kept Fields (infrastructure only)
```json
{
  "db_url": "...",          // ✅ Keep
  "db_name": "...",         // ✅ Keep
  "api_port": 8080,         // ✅ Keep
  "metrics_port": 9090,     // ✅ Keep
  "log_level": "INFO",      // ✅ Keep
  "timeout_seconds": 30,    // ✅ Keep
  ...
}
```

---

## Risks & Mitigation

| Risk | Mitigation |
|------|-----------|
| Settings doc schema changes | Backward compat validation |
| Existing settings broken | Auto-migration script |
| UI breaks | Feature flag to hide removed fields |
| Users confused | Clear error messages & docs |

---

## Success Criteria

- [ ] No job config can be set via settings API
- [ ] Settings UI only shows infrastructure fields
- [ ] Existing job configs in settings migrated to jobs
- [ ] All tests pass
- [ ] Documentation complete
- [ ] Zero breaking changes

---

## Timeline

- **Step 1 (Audit):** 10 min
- **Step 2 (Schema):** 15 min
- **Step 3 (API):** 15 min
- **Step 4 (UI):** 20 min
- **Step 5 (Migration):** 10 min
- **Step 6 (Testing):** 20 min
- **Step 7 (Docs):** 20 min

**Total:** ~110 minutes

---

## Next: Audit Settings

Starting with Step 1...
