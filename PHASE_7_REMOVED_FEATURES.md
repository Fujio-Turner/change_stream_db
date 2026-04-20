# Phase 7: Settings Cleanup – Removed Features

**Date**: 2026-04-19  
**Phase**: 7 (Settings Cleanup)  
**Category**: Feature Removal & Consolidation  

---

## Summary

Phase 7 removes job configuration from the Settings page and API. Job configuration is now managed exclusively through the Wizard (see Phase 6).

This document catalogues what was removed and why.

---

## Removed UI Tabs & Sections

### 1. Source Tab (Completely Hidden)

**Why**: All source/gateway configuration is now per-job in the Wizard.

**Removed sections**:
- **Gateway Configuration**
  - Source Type (sync_gateway, app_services, edge_server, couchdb)
  - URL (e.g., `http://localhost:4984`)
  - Database name
  - Scope & Collection
  - Self-signed cert acceptance
  - Test Connection button

- **Auth Configuration**
  - Auth Method (basic, session, bearer, none)
  - Username & Password
  - Session Cookie
  - Bearer Token
  - Auth field switching logic

- **Changes Feed Configuration**
  - Feed Type (longpoll, continuous, websocket, sse, eventsource)
  - Poll Interval
  - Active Only checkbox
  - Include Docs checkbox
  - Channels filter
  - Since (starting sequence)
  - Limit & Throttle
  - Flood Threshold
  - Heartbeat, Timeout, HTTP Timeout

- **Initial Sync Configuration**
  - Optimize Initial Sync checkbox
  - Catchup Limit
  - Get Batch Number

### 2. Process Tab (Completely Hidden)

**Why**: Processing is now configured per-job; applies to job data handling.

**Removed sections**:
- **Threads**
  - Worker Threads count

- **Processing**
  - Ignore Delete checkbox
  - Ignore Remove checkbox
  - Sequential checkbox
  - Max Concurrent
  - Dry Run checkbox

### 3. Output Tab (Completely Hidden)

**Why**: All output configuration is per-job in the Wizard.

**Removed sections**:
- **Output Mode Selection**
  - Mode: stdout, http, db, s3

- **HTTP Output Fields**
  - Target URL (for webhooks/APIs)
  - URL Template
  - Output Format (json, xml, form, msgpack, cbor, bson, yaml)
  - Write Method (PUT, POST, PATCH)
  - Delete Method
  - Send Body on DELETE
  - Request Timeout
  - Accept Self-Signed Certs
  - Follow Redirects
  - Target Authentication (basic, session, bearer)
  - Headers configuration
  - Test Connection button

- **Database Output Fields**
  - Connection URL
  - Connection Options
  - Schema Mappings (enabled, path, default mode, strict)
  - Edit Schema Mappings link

- **S3 Output Fields**
  - Bucket name
  - Region
  - Endpoint URL
  - Access Key ID & Secret
  - Session Token
  - Key Prefix & Template
  - Sanitize Keys checkbox
  - Content Type & Storage Class
  - Server-Side Encryption settings
  - KMS Key ID
  - Custom Metadata
  - On Delete handling (delete, tombstone, ignore)
  - Batching configuration (max docs, bytes, seconds)
  - Retry configuration (max retries, backoff)
  - Test Connection button

- **Output General Settings**
  - Halt on Failure toggle
  - Data Error Action (dlq, skip)

---

## Removed API Endpoints Behavior

### PUT /api/config

**No longer accepts**:
```json
{
  "gateway": { ... },           // ❌ NOW REJECTED (400)
  "auth": { ... },              // ❌ NOW REJECTED (400)
  "changes_feed": { ... },      // ❌ NOW REJECTED (400)
  "output": { ... },            // ❌ NOW REJECTED (400)
  "inputs": [ ... ],            // ❌ NOW REJECTED (400)
  "source_config": { ... }      // ❌ NOW REJECTED (400)
}
```

**Still accepts**:
```json
{
  "logging": { ... },           // ✅ STILL WORKS
  "metrics": { ... },           // ✅ STILL WORKS
  "checkpoint": { ... },        // ✅ STILL WORKS
  "couchbase_lite": { ... },    // ✅ STILL WORKS
  "shutdown": { ... },          // ✅ STILL WORKS
  "threads": <number>,          // ✅ STILL WORKS
  "retry": { ... },             // ✅ STILL WORKS
  "processing": { ... },        // ✅ STILL WORKS
  "admin_ui": { ... },          // ✅ STILL WORKS
  "attachments": { ... }        // ⚠️ LEGACY (for jobs)
}
```

---

## Deprecated UI Elements

### Attachments Tab

**Status**: ⚠️ Deprecated (Hidden by Phase 8)

**Reason**: Attachment processing is now per-job configuration.

**Current behavior**: 
- Tab still visible
- Marked as "Legacy only" with warning badge
- Fields ignored by runtime
- Will be completely removed in Phase 8

**Fields in Attachments tab** (all deprecated):
- General: Enabled, Dry Run, Mode, Halt on Failure, On Missing Attachment, Partial Success, Skip on Edge
- Filter: Content Types (allow/reject), Size limits, Name Pattern, Ignore Revpos
- Fetch: Bulk Get, Max Concurrent, Timeout, Temp Directory, Stream Threshold, Verify Digest/Length
- Destination: Type (S3/HTTP/Filesystem), Key Template/Prefix, S3 credentials, HTTP URL/headers, FS paths
- Post-Process: Action (none, update_doc, delete_attachments, delete_doc, set_ttl, purge), Update Field, TTL, Conflict Retries, Cleanup Orphaned
- Retry: Max Retries, Backoff, Retry on Status

---

## Removed Configuration Fields (Detailed)

| Field | Parent | Type | Removed in | Reason |
|-------|--------|------|-----------|--------|
| `gateway.src` | gateway | enum | 7 | Job config → Wizard |
| `gateway.url` | gateway | string | 7 | Job config → Wizard |
| `gateway.database` | gateway | string | 7 | Job config → Wizard |
| `auth.method` | auth | enum | 7 | Job config → Wizard |
| `auth.username` | auth | string | 7 | Job config → Wizard |
| `changes_feed.feed_type` | changes_feed | enum | 7 | Job config → Wizard |
| `changes_feed.poll_interval_seconds` | changes_feed | number | 7 | Job config → Wizard |
| `output.mode` | output | enum | 7 | Job config → Wizard |
| `output.target_url` | output | string | 7 | Job config → Wizard |
| ... | (70+ more) | ... | 7 | Job config → Wizard |

---

## Impact on Users

### What Users Can No Longer Do in Settings
❌ Configure gateway connection  
❌ Set authentication credentials  
❌ Choose changes feed type  
❌ Configure output destination  
❌ Set processing parameters  

### What Users Must Do Instead
✅ Use **Wizard** to create/edit jobs (recommended)  
✅ Use **API v2** to create/edit jobs programmatically  
✅ Use **Database UI** to inspect job documents  

### What Still Works in Settings
✅ Configure logging level  
✅ Configure metrics collection  
✅ Configure checkpoints  
✅ Configure CBL database  
✅ Configure shutdown behavior  

---

## Migration Path for Affected Users

### Existing Settings with Job Config

**Before Phase 7**:
```json
{
  "gateway": { "url": "http://localhost:4984", "database": "db" },
  "auth": { "method": "basic", "username": "user", "password": "pass" },
  "changes_feed": { "feed_type": "longpoll" },
  "output": { "mode": "http", "target_url": "https://api.example.com" },
  "logging": { "level": "INFO" }
}
```

**On First Startup with Phase 7**:
1. Migration detects job config fields
2. Creates job: `_migration_legacy_settings_{timestamp}`
3. Moves gateway, auth, changes_feed, output to job
4. Cleans settings to infrastructure only
5. Logs migration event

**Result**:
```json
{
  "logging": { "level": "INFO" }
}
```

**User Action**:
1. Review job in Wizard: `/wizard` → `_migration_legacy_settings_{timestamp}`
2. Adjust if needed
3. Activate/test job
4. Delete if not needed

---

## Error Messages for Removed Features

### When User Tries to Set Job Config via API

```bash
curl -X PUT /api/config \
  -H "Content-Type: application/json" \
  -d '{"gateway": {"url": "..."}}'

# Response:
HTTP 400 Bad Request
{
  "error": "Job configuration ('gateway') cannot be edited in Settings. Use the Wizard to create and manage jobs instead."
}
```

### When User Tries to Access Hidden UI Tabs

- Tabs are `display: none` in CSS
- Not rendered to DOM
- URL hash navigation still works (for any SPA)
- Graceful degradation

---

## Backward Compatibility

### API Clients

- ✅ **Reading** settings (infrastructure only): No change
- ❌ **Writing** job config: Now returns 400 error
- ✅ **Job management**: Use Wizard API instead

### UI Clients

- ✅ **Old browsers**: Hidden tabs don't render (CSS)
- ✅ **Mobile apps**: Settings still accessible
- ✅ **Infrastructure settings**: Full functionality

### Data Storage

- ✅ **Existing settings**: Auto-migrated to jobs
- ✅ **Existing jobs**: Unaffected by Phase 7
- ✅ **No data loss**: Migration preserves all config

---

## Cleanup Strategy

### For Deployments

1. **Phase 7 Release**: Settings validation + migration
2. **Grace Period** (optional): Allow both settings & Wizard (not done)
3. **Phase 8** (future): Remove Attachments tab UI
4. **Phase 9** (future): Remove legacy field references entirely

### For End-Users

1. Deploy Phase 7
2. Review migrated jobs in Wizard
3. Delete settings migration jobs if not needed
4. Continue using Wizard for all new jobs

---

## What's NOT Removed

✅ Wizard (for job management)  
✅ Job API (for programmatic job management)  
✅ Infrastructure settings (logging, metrics, etc.)  
✅ Migration capability (detects & converts legacy configs)  
✅ Backward compatibility (old configs still work)  

---

## Related Documentation

- **What's New**: See `PHASE_7_QUICK_REFERENCE.md`
- **How to Migrate**: See `PHASE_7_SUMMARY.md`
- **Status & Tests**: See `PHASE_7_STATUS.md`
- **Wizard Guide**: See `PHASE_6_SUMMARY.md`

---

## FAQ

**Q: Can I still use Settings?**  
A: Yes, but only for infrastructure settings (logging, metrics, checkpoints, etc.). Job configuration must use the Wizard.

**Q: Will my old settings break?**  
A: No. On first startup, Phase 7 auto-migrates job config to jobs and cleans settings.

**Q: How do I manage jobs now?**  
A: Use the Wizard (`/wizard`) or the Jobs API.

**Q: Can I revert Phase 7?**  
A: Yes, but it's not recommended. Re-add settings before reverting to restore old config.

**Q: When will Attachments tab be removed?**  
A: Phase 8 (future). Currently marked as legacy/informational.

---

**Status**: ✅ Complete  
**Date**: 2026-04-19  
**Impact**: Medium (UI only; Jobs via Wizard)  
**Breaking**: Yes for Settings API + UI (non-breaking for infrastructure)
