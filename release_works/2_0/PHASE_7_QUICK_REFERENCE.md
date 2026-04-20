# Phase 7: Settings Cleanup – Quick Reference

## What Changed

### 1. Settings API Validation (`web/server.py`)
- **PUT /api/config** now rejects job configuration fields:
  - ❌ `gateway` 
  - ❌ `auth`
  - ❌ `changes_feed`
  - ❌ `output`
  
- **Allowed infrastructure fields** (kept):
  - ✅ `couchbase_lite`
  - ✅ `logging`
  - ✅ `admin_ui`
  - ✅ `metrics`
  - ✅ `shutdown`
  - ✅ `threads`
  - ✅ `checkpoint`
  - ✅ `retry`
  - ✅ `processing`
  - ✅ `attachments`

### 2. Settings UI Changes (`web/templates/settings.html`)
- **Hidden tabs:**
  - Source (Gateway, Auth, Changes Feed)
  - Process
  - Output
  
- **Visible tabs:**
  - Attachments (marked as legacy/informational)
  - Reliability (default selected)
  - Observability

- **User guidance:**
  - Alert banner explaining job config moved to Wizard
  - Link to `/wizard` for job management

### 3. Migration Logic (`cbl_store.py`)
New method: `CBLStore.migrate_job_config_from_settings()`

```python
result = store.migrate_job_config_from_settings()
# Returns:
# {
#   "migrated": bool,
#   "job_config_found": dict | None,
#   "removed_fields": list[str],
#   "job_id": str | None,
#   "error": str | None
# }
```

### 4. Tests (`tests/test_phase_7_config_cleanup.py`)
- Validation tests for rejected fields
- Validation tests for allowed fields
- Migration tests
- Error message clarity tests

## API Behavior

### Before Phase 7
```bash
curl -X PUT http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "gateway": {"url": "http://localhost:4984"},
    "logging": {"level": "INFO"}
  }'
# ✅ 200 OK (both saved)
```

### After Phase 7
```bash
curl -X PUT http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "gateway": {"url": "http://localhost:4984"},
    "logging": {"level": "INFO"}
  }'
# ❌ 400 Bad Request
# {
#   "error": "Job configuration ('gateway') cannot be edited in Settings. Use the Wizard to create and manage jobs instead."
# }
```

```bash
curl -X PUT http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{"logging": {"level": "INFO"}}'
# ✅ 200 OK
```

## Migration Path

1. **Automatic detection**: On startup, call `migrate_job_config_from_settings()`
2. **If legacy config found**:
   - Creates job document: `_migration_legacy_settings_{timestamp}`
   - Removes job fields from settings
   - Keeps infrastructure fields
   - Logs migration event
3. **User action**: Review migrated job in Wizard, adjust if needed

## Error Messages

| Scenario | Status | Message |
|----------|--------|---------|
| Try to set `gateway` | 400 | Job configuration ('gateway') cannot be edited in Settings. Use the Wizard to create and manage jobs instead. |
| Try to set `gateway`, `auth`, `output` | 400 | Job configuration ('gateway', 'auth', 'output') cannot be edited in Settings... |
| Valid infrastructure field | 200 | `{"ok": true, "restart": "ok"}` |

## Backward Compatibility

- ✅ Existing infrastructure configs continue to work
- ✅ Migration auto-creates job from legacy settings
- ✅ Settings with job config don't break; validation prevents new ones
- ⚠️ Users must use Wizard for future job management

## Files Modified

| File | Changes |
|------|---------|
| `web/server.py` | `put_config()` validation logic |
| `web/templates/settings.html` | Hidden job tabs, alert banner |
| `cbl_store.py` | `migrate_job_config_from_settings()` method |
| `tests/test_phase_7_config_cleanup.py` | New test suite |

## Testing

```bash
pytest tests/test_phase_7_config_cleanup.py -v
```

### Test Coverage
- ✅ Reject `gateway`, `auth`, `changes_feed`, `output`
- ✅ Accept all 10 allowed infrastructure fields
- ✅ Accept mixed infrastructure fields
- ✅ Migration detection
- ✅ Error message clarity

## Next Steps

1. **Run migration on startup** (call `migrate_job_config_from_settings()` during app init)
2. **Notify users** via RELEASE_NOTES that Settings now requires Wizard
3. **Monitor logs** for migration events
4. **Deprecation timeline**: Plan removal of Attachments tab in Phase 8

---

**Status**: ✅ Complete  
**Date**: 2026-04-19  
**Phase**: 7 (Settings Cleanup)
