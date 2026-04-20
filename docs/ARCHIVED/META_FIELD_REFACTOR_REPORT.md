# Migration Report: `_meta` → `meta`

## Summary
Convert JSON element from `_meta` to `meta` throughout the codebase. **31 locations** need updates across Python, JavaScript, JSON schemas, and mapping files.

---

## 1. PYTHON FILES (6 files, 18 changes)

### 🔴 [cbl_store.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/cbl_store.py)
**Lines**: 733, 1036-1037, 1052-1053, 1114, 1139-1140

| Line | Current | Change To |
|------|---------|-----------|
| 733 | `"_meta": {` | `"meta": {` |
| 1036 | `if "_meta" in doc:` | `if "meta" in doc:` |
| 1037 | `schema["_meta"] = dict(doc["_meta"])` | `schema["meta"] = dict(doc["meta"])` |
| 1052 | `if "_meta" in schema:` | `if "meta" in schema:` |
| 1053 | `doc["_meta"] = schema["_meta"]` | `doc["meta"] = schema["meta"]` |
| 1114 | `"_meta": dict(doc.get("_meta", {}))...` | `"meta": dict(doc.get("meta", {}))...` |
| 1139 | `if "_meta" in source_doc:` | `if "meta" in source_doc:` |
| 1140 | `doc["_meta"] = source_doc["_meta"]` | `doc["meta"] = source_doc["meta"]` |

### 🔴 [db/db_base.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/db/db_base.py)
**Line**: 309

| Line | Current | Change To |
|------|---------|-----------|
| 309 | `meta = raw.get("_meta", {})` | `meta = raw.get("meta", {})` |

### 🔴 [web/server.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/web/server.py)
**Lines**: 374, 409 (comment), 412, 416, 447, 452, 1969-1970

| Line | Current | Change To | Type |
|------|---------|-----------|------|
| 374 | `m = parsed.get("_meta", {})` | `m = parsed.get("meta", {})` | Code |
| 409 | `# Inject _meta into JSON content...` | `# Inject meta into JSON content...` | Comment |
| 412 | `meta = parsed.get("_meta", {})` | `meta = parsed.get("meta", {})` | Code |
| 416 | `parsed["_meta"] = meta` | `parsed["meta"] = meta` | Code |
| 447 | `meta = parsed.get("_meta", {})` | `meta = parsed.get("meta", {})` | Code |
| 452 | `parsed["_meta"] = meta` | `parsed["meta"] = meta` | Code |
| 1969 | `"_meta": {` | `"meta": {` | Code |
| 1970 | `**body.get("_meta", {}),` | `**body.get("meta", {}),` | Code |

### 🟡 [rest/output_http.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/output_http.py)
**Lines**: 1130, 1133, 1135, 1138

| Line | Context | Notes |
|------|---------|-------|
| 1130 | `def flush_insert_meta(self, job_id: str = "") -> None:` | Method name - **NO CHANGE** (refers to DLQ metadata concept, not JSON field) |
| 1133 | `self._store.update_dlq_meta("last_inserted_at", job_id)` | Method call - **NO CHANGE** |
| 1135 | `def flush_drain_meta(self, job_id: str = "") -> None:` | Method name - **NO CHANGE** |
| 1138 | `self._store.update_dlq_meta("last_drained_at", job_id)` | Method call - **NO CHANGE** |

✅ **These are method names, not JSON field references** - keep as-is.

### 🟡 [rest/changes_http.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py)
**Lines**: 1195, 1970

| Line | Context | Notes |
|------|---------|-------|
| 1195 | `dlq.flush_insert_meta(_job)` | Method call - **NO CHANGE** |
| 1970 | `dlq.flush_drain_meta()` | Method call - **NO CHANGE** |

✅ **Method calls to DLQ methods** - keep as-is.

### 🟡 [cloud/cloud_s3.py](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/cloud/cloud_s3.py)
**Lines**: 81, 175

| Line | Context | Notes |
|------|---------|-------|
| 81 | `self._custom_metadata = cfg.get("metadata", {})` | AWS S3 metadata field - **NO CHANGE** |
| 175 | `merged = {**self._custom_metadata, **(metadata or {})}` | S3 custom metadata - **NO CHANGE** |

✅ **AWS-specific metadata, not related to `_meta` field** - keep as-is.

---

## 2. JAVASCRIPT / HTML FILES (2 files, 5 changes)

### 🔴 [web/templates/wizard.html](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/web/templates/wizard.html)
**Lines**: 2152, 2358, 2411

| Line | Current | Change To |
|------|---------|-----------|
| 2152 | `var savedAt = src._meta && src._meta.saved_at ? ...` | `var savedAt = src.meta && src.meta.saved_at ? ...` |
| 2358 | `_meta: { saved_at: new Date().toISOString() }` | `meta: { saved_at: new Date().toISOString() }` |
| 2411 | `_meta: { saved_at: new Date().toISOString() }` | `meta: { saved_at: new Date().toISOString() }` |

### 🟡 [web/templates/settings.html](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/web/templates/settings.html)
**Lines**: 923, 1337, 2126, 2232, 2348, 2477

| Line | Context | Notes |
|------|---------|-------|
| 923 | `<textarea id="output_s3_metadata"...` | S3 metadata config - **NO CHANGE** |
| 1337 | `<textarea id="attach_dest_s3_metadata"...` | S3 metadata config - **NO CHANGE** |
| 2126 | `document.getElementById('output_s3_metadata')...` | S3 metadata - **NO CHANGE** |
| 2232 | `document.getElementById('attach_dest_s3_metadata')...` | S3 metadata - **NO CHANGE** |
| 2348 | `metadata: (function() {...` | AWS S3 metadata parsing - **NO CHANGE** |
| 2477 | `metadata: (function() {...` | AWS S3 metadata parsing - **NO CHANGE** |

✅ **All are S3 metadata, not `_meta` field** - keep as-is.

---

## 3. JSON SCHEMA FILES (7 files, 7 changes)

All schema files need the `_meta` property renamed to `meta`:

### 🔴 Schema Updates Needed:

| File | Line Range | Change |
|------|-----------|--------|
| [json_schema/changes-worker/inputs_changes/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/inputs_changes/schema.json) | 76-79 | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/outputs_http/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/outputs_http/schema.json) | 80-83 | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/outputs_stdout/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/outputs_stdout/schema.json) | 60-63 | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/outputs_cloud/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/outputs_cloud/schema.json) | 86-89 | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/outputs_rdbms/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/outputs_rdbms/schema.json) | 81-84 | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/mappings/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/mappings/schema.json) | TBD | `"_meta":` → `"meta":` |
| [json_schema/changes-worker/jobs/schema.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/json_schema/changes-worker/jobs/schema.json) | TBD | `"_meta":` → `"meta":` |

---

## 4. DATA FILES / MAPPINGS (2 files, 2 changes)

### 🔴 [mappings/orders.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/mappings/orders.json)
**Line**: 48

```json
// Before:
"_meta": {
  "updated_at": "2026-04-18T22:14:33.421024+00:00",
  "active": true
}

// After:
"meta": {
  "updated_at": "2026-04-18T22:14:33.421024+00:00",
  "active": true
}
```

### 🔴 [mappings/orders.meta.json](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/mappings/orders.meta.json)
**Expected to have `_meta`** - check and update if present.

---

## 5. DOCUMENTATION FILES (DO NOT CHANGE)

These are documentation only - mention the change in release notes but don't edit schema/guide docs directly:

- `guides/JSON_SCHEMA.md` - Document the change (update examples to use `meta`)
- `guides/SCHEMA_AUDIT_REPORT.md` - Update to reflect `meta` field
- `docs/DLQ.md` - References to methods are OK, just verify field names

---

## Implementation Checklist

### Phase 1: Code Changes (Ready to Execute)
- [ ] `cbl_store.py` - 8 changes (lines 733, 1036-37, 1052-53, 1114, 1139-40)
- [ ] `db/db_base.py` - 1 change (line 309)
- [ ] `web/server.py` - 8 changes (lines 374, 409, 412, 416, 447, 452, 1969-70)
- [ ] `web/templates/wizard.html` - 3 changes (lines 2152, 2358, 2411)

### Phase 2: Schema Updates
- [ ] 5 JSON schema files - rename `"_meta"` to `"meta"` in property definitions
- [ ] 2 mapping data files - update `"_meta"` to `"meta"` in instances

### Phase 3: Verification
- [ ] Run schema validation on all JSON files
- [ ] Test wizard source creation/loading
- [ ] Test mapping save/load operations
- [ ] Verify DLQ operations (methods unchanged, only data refs)

### Phase 4: Documentation
- [ ] Update `JSON_SCHEMA.md` examples
- [ ] Update `SCHEMA_AUDIT_REPORT.md` to reflect `meta` field
- [ ] Create migration guide for users with existing mappings

---

## Migration Impact Analysis

### ✅ Safe Changes
- JSON field rename: `_meta` → `meta`
- All access patterns are consistent (`.get()`, dict access)
- No database migrations needed (handled on read/write)

### 🔄 Requires Testing
1. **Wizard mapping creation** - Tests source save with `meta` field
2. **Mapping load operations** - Verify `meta.saved_at` and `meta.active` work
3. **Schema validation** - Ensure JSON schemas accept new field name
4. **Existing mappings** - Handle backward compatibility on first load

### ⚠️ Backward Compatibility
- Old mappings with `_meta` will need migration on first read
- Consider adding migration logic in `cbl_store.py` to handle both field names during transition

---

## SQL++ Queries & Indexes

✅ **No SQL++ changes needed** - The code doesn't use explicit SQL++ queries on `_meta` field. All access is through Python dict operations or Couchbase Lite SDK.

If SQL++ queries exist, they would use parameterized paths:
```n1ql
SELECT * FROM mappings WHERE meta.active = true
```

---

## Total Change Summary

| Category | Files | Changes | Status |
|----------|-------|---------|--------|
| Python | 3 | 17 | 🔴 Ready |
| JavaScript/HTML | 1 | 3 | 🔴 Ready |
| JSON Schema | 5+ | 5+ | 🔴 Ready |
| Data Files | 2 | 2 | 🔴 Ready |
| **TOTAL** | **11** | **27+** | **Ready** |

---

## Files NOT Requiring Changes

✅ No changes needed for:
- `rest/output_http.py` (method names only)
- `rest/changes_http.py` (method calls only)
- `cloud/cloud_s3.py` (AWS metadata, different context)
- `web/templates/settings.html` (S3 metadata fields)
- `web/static/js/highlight.min.js` (external library)
