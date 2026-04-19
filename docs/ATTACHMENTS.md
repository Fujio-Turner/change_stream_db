# Attachments Processing – Design Plan

A common use of consuming the `_changes` feed is to process Couchbase and CouchDB attachments. The typical workflow is: listen to the `_changes` feed, detect documents with attachments, download the attachment binaries, and copy them to another system (cloud blob storage, file system, CDN, etc.).

After processing the attachments, a secondary operation is usually performed on the source document:

1. **Update the document** — `PUT /{keyspace}/{docId}` with the external URL/location of the attachment added to the document body
2. **Set TTL (Couchbase only)** — `PUT /{keyspace}/{docId}?_exp=<ttl>` to expire the document after a time-to-live period
3. **Delete the whole document** — `DELETE /{keyspace}/{docId}?rev=<rev>` to remove the document that held the attachment
4. **Delete only the attachment(s)** — `DELETE /{keyspace}/{docId}/{attachment_name}?rev=<rev>` to remove individual attachments while keeping the document

**Related docs:**
- [`DESIGN.md`](DESIGN.md) – Overall pipeline architecture, three-stage pipeline, failure modes
- [`CLOUD_BLOB_PLAN.md`](CLOUD_BLOB_PLAN.md) – Cloud blob storage output (S3, GCS, Azure) — natural destination for extracted attachments
- [`CHANGES_PROCESSING.md`](CHANGES_PROCESSING.md) – `_changes` feed processing, checkpoint strategy
- [`DLQ.md`](DLQ.md) – Dead letter queue for failed attachment operations

---

## Source Platform Support

| Platform | Attachment API | Notes |
|---|---|---|
| **Sync Gateway / App Services** | ✅ Full API | GET/PUT/DELETE individual attachments, `_bulk_get` with attachments, `_bulk_docs` with inline attachments |
| **CouchDB** | ✅ Full API | GET/PUT/DELETE individual attachments, `_bulk_get` with attachments, `_bulk_docs` with inline attachments |
| **Edge Server** | ❌ **No attachment API** | Edge Server has no attachment endpoints — attachments are not available. See [Edge Server Workarounds](#edge-server-workarounds) for alternatives. |

---

## API Reference

### Couchbase Sync Gateway / App Services (Port 4984 Public, Port 4985 Admin)

| Operation | Method | Endpoint | Reference |
|---|---|---|---|
| Get attachment | `GET` | `/{keyspace}/{docid}/{attach}` | [REST API — Get an attachment](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document-Attachment) |
| Put attachment | `PUT` | `/{keyspace}/{docid}/{attach}?rev=<rev>` | [REST API — Put an attachment](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document-Attachment) |
| Head/ping attachment | `HEAD` | `/{keyspace}/{docid}/{attach}` | [REST API — Check if attachment exists](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document-Attachment/operation/head_keyspace-docid-attach) |
| Delete attachment | `DELETE` | `/{keyspace}/{docid}/{attach}?rev=<rev>` | [REST API — Delete an attachment](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document-Attachment) |
| Get document (with attachments) | `GET` | `/{keyspace}/{docid}?attachments=true` | [REST API — Get a document](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document/operation/get_keyspace-docid) |
| Bulk get (with attachments) | `POST` | `/{keyspace}/_bulk_get?attachments=true` | [REST API — Bulk get](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document/operation/post_keyspace-_bulk_get) |
| Bulk docs (inline attachments) | `POST` | `/{keyspace}/_bulk_docs` | [REST API — Bulk docs](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document/operation/post_keyspace-_bulk_docs) |
| Delete document | `DELETE` | `/{keyspace}/{docid}?rev=<rev>` | [REST API — Delete a document](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_public.html#tag/Document/operation/delete_keyspace-docid) |
| Purge document (Admin only, port 4985) | `POST` | `/{keyspace}/_purge` | [Admin REST API — Purge](https://docs.couchbase.com/sync-gateway/current/rest-api/rest_api_admin.html#tag/Document/operation/post_keyspace-_purge) |

### CouchDB

| Operation | Method | Endpoint | Reference |
|---|---|---|---|
| Get attachment | `GET` | `/{db}/{docid}/{attach}` | [CouchDB API — Attachments](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Put attachment | `PUT` | `/{db}/{docid}/{attach}?rev=<rev>` | [CouchDB API — Attachments](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Head/ping attachment | `HEAD` | `/{db}/{docid}/{attach}` | [CouchDB API — Attachments](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Delete attachment | `DELETE` | `/{db}/{docid}/{attach}?rev=<rev>` | [CouchDB API — Attachments](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Get document (with attachments) | `GET` | `/{db}/{docid}?attachments=true` | [CouchDB API — Documents](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Bulk get (with attachments) | `POST` | `/{db}/_bulk_get` | [CouchDB API — Bulk get](https://docs.couchdb.org/en/stable/api/database/bulk-api.html#db-bulk-get) |
| Bulk docs (inline attachments) | `POST` | `/{db}/_bulk_docs` | [CouchDB API — Bulk docs](https://docs.couchdb.org/en/stable/api/database/bulk-api.html#db-bulk-docs) |
| Delete document | `DELETE` | `/{db}/{docid}?rev=<rev>` | [CouchDB API — Documents](https://docs.couchdb.org/en/stable/api/document/common.html#attachments) |
| Purge document | `POST` | `/{db}/_purge` | [CouchDB API — Purge](https://docs.couchdb.org/en/stable/api/database/misc.html) |

---

## Attachment Detection

When a document arrives via `_changes`, attachment metadata appears in the `_attachments` field as stubs:

```json
{
  "_id": "photo_001",
  "_rev": "3-abc123",
  "_attachments": {
    "photo.jpg": {
      "content_type": "image/jpeg",
      "digest": "md5-abc123==",
      "length": 2048576,
      "revpos": 2,
      "stub": true
    },
    "thumbnail.png": {
      "content_type": "image/png",
      "digest": "md5-def456==",
      "length": 8192,
      "revpos": 3,
      "stub": true
    }
  },
  "title": "Vacation Photo"
}
```

The worker detects documents with attachments by checking for a non-empty `_attachments` field. When `stub: true`, the attachment body is not included — it must be fetched separately.

---

## Design: Attachment Processing Pipeline

The attachment processor extends the existing three-stage pipeline (LEFT → MIDDLE → RIGHT) by adding an **ATTACHMENT stage** between MIDDLE and RIGHT:

```
┌─────────────┐     ┌────────────┐     ┌──────────────────┐     ┌─────────────┐
│   LEFT      │     │  MIDDLE    │     │  ATTACHMENT      │     │  RIGHT      │
│  _changes   │────►│  filter /  │────►│  detect / fetch  │────►│  output     │
│  feed       │     │  fetch doc │     │  / upload / post │     │  (original) │
└─────────────┘     └────────────┘     └──────────────────┘     └─────────────┘
```

### ATTACHMENT Stage Flow

```
Document arrives with _attachments stubs
    │
    ▼
┌──────────────────────────────────┐
│ 1. DETECT                        │
│    Does doc have _attachments?   │
│    Does it match attachment       │
│    filter (content_type, size)?  │
└──────────────┬───────────────────┘
               │ yes
               ▼
┌──────────────────────────────────┐
│ 2. FETCH                         │
│    GET /{keyspace}/{docid}/{att} │
│    (or _bulk_get?attachments=true│
│    if fetching all at once)      │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 3. UPLOAD                        │
│    Upload binary to destination  │
│    (cloud blob, filesystem, CDN, │
│    REST endpoint)                │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 4. POST-PROCESS (optional)       │
│    One of:                       │
│    a) Update doc with ext URL    │
│    b) Set TTL (_exp)             │
│    c) Delete the document        │
│    d) Delete the attachment(s)   │
│    e) Purge the document (admin) │
│    f) Do nothing                 │
└──────────────────────────────────┘
```

---

## Config Changes

Attachment processing is configured via a new `attachments` block in the config:

```jsonc
{
  "attachments": {
    "enabled": false,                         // master switch (default: disabled)
    "dry_run": false,                         // detect + fetch + upload but skip post-processing
                                              // and do NOT advance checkpoint (safe for testing)
    "mode": "individual",                     // "individual" | "bulk" | "multipart" — fetch strategy

    // --- Filtering ---
    "filter": {
      "content_types": [],                    // e.g., ["image/*", "application/pdf"] — empty = all
      "reject_content_types": [],             // e.g., ["application/x-msdownload", "application/x-executable"]
                                              // reject dangerous/unwanted MIME types before download
      "min_size_bytes": 0,                    // skip attachments smaller than this
      "max_size_bytes": 0,                    // skip attachments larger than this (0 = no limit)
      "max_total_bytes_per_doc": 0,           // skip doc if sum of all attachments exceeds this (0 = no limit)
                                              // prevents one monster doc from OOMing or filling temp volume
      "name_pattern": "",                     // regex pattern to match attachment names (empty = all)
      "ignore_revpos": false                  // if true, re-process attachments even if revpos hasn't changed
                                              // useful when you want to re-extract on every doc update
    },

    // --- Fetch ---
    "fetch": {
      "use_bulk_get": false,                  // true = fetch all attachments via _bulk_get?attachments=true
      "max_concurrent_downloads": 5,          // parallel attachment downloads PER DOCUMENT
      "max_concurrent_downloads_global": 20,  // parallel attachment downloads across ALL docs in a batch
                                              // prevents SG thread pool saturation when multiple workers run
      "request_timeout_seconds": 120,         // timeout for large attachment downloads
      "temp_dir": "/tmp/attachments",         // temporary directory for streaming large files
      "verify_digest": true,                  // check digest after download (default: true)
      "verify_length": true                   // check Content-Length vs stub length (default: true)
    },

    // --- Upload Destination ---
    "destination": {
      "type": "s3",                           // "s3" | "gcs" | "azure" | "http" | "filesystem"
      "key_template": "{prefix}/{doc_id}/{attachment_name}",
      "key_prefix": "attachments",

      // Reuses the existing output.s3 / output.gcs / output.azure config blocks
      // OR a destination-specific override:
      "s3": {
        "bucket": "my-attachments-bucket",
        "region": "us-east-1"
        // ... same fields as output.s3
      },
      "http": {
        "url_template": "https://cdn.example.com/upload/{doc_id}/{attachment_name}",
        "method": "PUT",
        "headers": {}
      },
      "filesystem": {
        "base_path": "/mnt/attachments",
        "dir_template": "{doc_id}",
        "preserve_filename": true
      },

      // --- Pre-signed URLs (S3/GCS only) ---
      "presigned_urls": {
        "enabled": false,                     // generate pre-signed URLs instead of direct bucket URLs
        "expiry_seconds": 604800              // pre-signed URL expiry (default: 7 days)
                                              // use when downstream consumers (mobile/web) lack IAM permissions
      }
    },

    // --- Post-Processing ---
    "post_process": {
      "action": "none",                       // "none" | "update_doc" | "set_ttl" | "delete_doc"
                                              // | "delete_attachments" | "purge"
                                              // Can also be an ARRAY to chain actions:
                                              // ["update_doc", "set_ttl"] — update doc with URL, then set expiry

      // action = "update_doc": add external URL to the document
      "update_field": "_attachments_external", // field name to add/update with external URLs
      "remove_attachments_after_upload": false, // if true, remove _attachments from the doc body on update

      // action = "set_ttl": set document expiry (Couchbase only)
      "ttl_seconds": 86400,                   // time-to-live in seconds (e.g., 24 hours)

      // action = "purge": requires admin port access
      // ⚠️ SECURITY: Use environment variables or a secret manager for admin credentials
      //    in production. Never commit admin_auth credentials to source control.
      "admin_url": "",                        // e.g., "http://localhost:4985" (admin port)
      "admin_auth": {                         // separate auth for admin port
        "method": "basic",
        "username": "",                       // prefer env var: ATTACHMENTS_ADMIN_USERNAME
        "password": ""                        // prefer env var: ATTACHMENTS_ADMIN_PASSWORD
      }
    },

    // --- Retry & Error Handling ---
    "retry": {
      "max_retries": 3,
      "backoff_base_seconds": 1,
      "backoff_max_seconds": 30
    },
    "halt_on_failure": true,                  // stop processing if attachment ops fail
    "skip_on_edge_server": true               // auto-skip when src=edge_server (no attachment API)
  }
}
```

### Key Template Placeholders

| Placeholder | Description | Example Value |
|---|---|---|
| `{doc_id}` | Document ID (`_id` field) | `photo_001` |
| `{attachment_name}` | Attachment filename from `_attachments` key | `photo.jpg` |
| `{content_type}` | MIME type of the attachment | `image/jpeg` |
| `{rev}` | Document revision at time of fetch | `3-abc123` |
| `{revpos}` | Revision number when attachment was added | `2` |
| `{prefix}` | The `key_prefix` config value | `attachments` |
| `{scope}` | Couchbase scope name | `us` |
| `{collection}` | Couchbase collection name | `prices` |
| `{database}` | Couchbase database name | `db` |
| `{digest}` | Attachment digest from stub | `md5-abc123==` |
| `{length}` | Attachment size in bytes | `2048576` |
| `{timestamp}` | Unix epoch seconds at upload time | `1768521600` |
| `{iso_date}` | ISO 8601 date-time | `2026-04-18T12:00:00Z` |
| `{year}` / `{month}` / `{day}` | Date components | `2026` / `04` / `18` |

> **⚠️ Crash safety:** Use only `{doc_id}` and `{attachment_name}` for idempotent keys. Adding `{timestamp}` or `{rev}` creates different keys on retry, resulting in duplicate blobs. See [Corner Case 6](#corner-case-6-worker-crashes-mid-attachment-processing).

---

## Fetch Strategies

### Individual Fetch (`mode: "individual"`)

Fetches each attachment separately. Best for selective processing (only certain content types or names):

```
GET /{keyspace}/{docId}/photo.jpg
GET /{keyspace}/{docId}/thumbnail.png
```

- Allows parallel downloads per document (`max_concurrent_downloads`)
- Respects content_type / size / name filters — skips non-matching attachments without downloading
- Can HEAD-check attachment existence before downloading (`HEAD /{keyspace}/{docId}/{attach}`)

### Bulk Fetch (`mode: "bulk"`)

Fetches the full document with all attachment bodies inline via `_bulk_get`:

```
POST /{keyspace}/_bulk_get?attachments=true
Body: {"docs": [{"id": "photo_001"}]}
```

- Fewer HTTP round-trips for documents with many attachments
- Attachments arrive base64-encoded in the JSON response
- **Trade-off:** Downloads ALL attachments even if filters would skip some
- **Trade-off:** Higher memory usage — all attachment data is in memory as base64
- **Trade-off:** Base64 encoding increases data size by **~33%** and spikes CPU during decode

### Multipart/Related Fetch (`mode: "multipart"`)

Fetches the document with all attachments in a single HTTP request using `multipart/related` format:

```
GET /{keyspace}/{docId}?attachments=true
Accept: multipart/related
```

- **No Base64 overhead** — attachment binaries arrive as raw MIME parts, not base64-encoded
- Single HTTP round-trip per document (like bulk, but without the 33% encoding tax)
- The response is a multipart MIME stream: first part is the JSON document, subsequent parts are raw attachment binaries
- **Trade-off:** More complex response parsing (MIME boundary splitting)
- **Trade-off:** Downloads ALL attachments (like bulk — no per-attachment filtering)
- **Best for:** Documents with many large attachments where you need all of them and want to avoid the Base64 CPU/memory overhead

```
HTTP/1.1 200 OK
Content-Type: multipart/related; boundary="abc123"

--abc123
Content-Type: application/json

{"_id":"photo_001","_rev":"3-abc","_attachments":{"photo.jpg":{...}}}
--abc123
Content-Type: image/jpeg
Content-Disposition: attachment; filename="photo.jpg"

<raw binary bytes>
--abc123--
```

### Which to use?

| Scenario | Recommendation |
|---|---|
| Documents have 1–3 small attachments | `individual` — simple, low overhead |
| Documents have many attachments, need all of them | `multipart` — single request, no Base64 tax |
| Only need specific attachments (by type/name) | `individual` — skip unwanted ones |
| Large attachments (>10 MB) | `individual` — stream to disk, avoid memory pressure |
| High-volume feed with mixed attachment sizes | `individual` + `max_concurrent_downloads` |
| Many small attachments, all needed, CPU-constrained | `multipart` — avoids Base64 decode overhead |
| Need to minimize HTTP round-trips | `bulk` or `multipart` — one request per doc |

---

## Post-Processing Operations

After the attachment binary has been uploaded to the destination, the worker performs the configured post-processing action on the source document.

### `update_doc` — Update Document with External URL

```
PUT /{keyspace}/{docId}?rev=<current_rev>
Body: {
  ...original doc fields...,
  "_attachments_external": {
    "photo.jpg": {
      "url": "https://my-bucket.s3.amazonaws.com/attachments/photo_001/photo.jpg",
      "content_type": "image/jpeg",
      "length": 2048576,
      "uploaded_at": "2026-04-18T12:00:00Z"
    }
  }
}
```

This allows downstream consumers to find the attachment at its new external location.

**Upsert-safe behavior:** On crash recovery (worker restarts and re-processes the same batch), the attachment may already exist in S3 from the pre-crash upload. The `update_doc` logic must be **idempotent** — if the binary already exists at the destination (same key), the worker proceeds to update the document metadata regardless. It does NOT skip the update because "the upload already happened." The goal is: **binary in destination + document updated = success**, regardless of how many times the pipeline runs.

### `set_ttl` — Set Document Expiry (Couchbase Only)

```
PUT /{keyspace}/{docId}?rev=<current_rev>
Body: { ...doc..., "_exp": 1713571200 }
```

Sets a time-to-live so the document (and its attachments) automatically expire after the attachment data has been safely extracted. Only works on Couchbase Sync Gateway / App Services — CouchDB does not have TTL.

### `delete_doc` — Delete the Entire Document

```
DELETE /{keyspace}/{docId}?rev=<current_rev>
```

Removes the source document after attachments have been extracted. Creates a tombstone — the deletion will replicate to other peers.

### `delete_attachments` — Delete Only the Attachment(s)

```
DELETE /{keyspace}/{docId}/photo.jpg?rev=<current_rev>
DELETE /{keyspace}/{docId}/thumbnail.png?rev=<next_rev>
```

Removes individual attachments from the document while keeping the document itself. Each delete creates a new revision, so deletes must be sequenced (each uses the rev returned by the previous delete).

### `purge` — Purge the Document (Admin Port Only)

```
POST /{keyspace}/_purge   (admin port 4985)
Body: { "photo_001": ["*"] }
```

Permanently removes the document from the database with no tombstone. **This is irreversible and does NOT replicate.** Requires admin port access (4985 for Sync Gateway / App Services). CouchDB also supports `_purge` but on the standard port.

> **⚠️ Design Warning — `purge` in sync ecosystems:** If this worker is part of a sync ecosystem with mobile clients (Couchbase Lite on iOS/Android), using `action: "purge"` will **leave the attachments on mobile devices forever** because they never receive a deletion tombstone. Mobile clients only learn about deletes through replication of tombstones — purge skips that entirely. **Use `delete_doc` or `delete_attachments` for standard workflows.** Reserve `purge` strictly for GDPR/compliance "Right to be Forgotten" scenarios where you need the data physically wiped from the server disk and accept that mobile copies remain until the next full sync or app reinstall.

### `none` — Do Nothing

The attachment is uploaded but no post-processing is performed on the source document. The document remains as-is with the original attachment stubs.

---

## Metrics

New Prometheus metrics for attachment processing (extending `MetricsCollector`):

| Metric (prefixed `changes_worker_`) | Type | Description |
|---|---|---|
| `attachments_detected_total` | counter | Documents with `_attachments` seen |
| `attachments_downloaded_total` | counter | Individual attachment downloads completed |
| `attachments_download_errors_total` | counter | Failed attachment downloads |
| `attachments_uploaded_total` | counter | Attachments uploaded to destination |
| `attachments_upload_errors_total` | counter | Failed attachment uploads |
| `attachments_bytes_downloaded_total` | counter | Total bytes downloaded from source |
| `attachments_bytes_uploaded_total` | counter | Total bytes uploaded to destination |
| `attachments_post_process_total` | counter | Post-processing operations completed |
| `attachments_post_process_errors_total` | counter | Failed post-processing operations |
| `attachments_skipped_total` | counter | Attachments skipped by filter |
| `attachments_download_time_seconds` | summary | Attachment download latency |
| `attachments_upload_time_seconds` | summary | Attachment upload latency |

---

## Corner Cases & Error States

Attachment processing has a uniquely wide surface for failure because it spans **four sequential operations** (detect → fetch → upload → post-process) across **two or three different systems** (source DB, destination store, and optionally the source DB again for post-processing). A failure at any step leaves the system in a partially-completed state that must be handled correctly.

---

### Corner Case 1: `_changes` says attachment exists, but GET returns 404

**Scenario:** The `_changes` feed delivers a document with `_attachments` stubs (e.g., `"photo.jpg": {"stub": true}`), but when the worker calls `GET /{keyspace}/{docId}/photo.jpg`, the source returns 404.

**Why this happens:**
- The attachment was deleted between the `_changes` response and the fetch (another client or replication removed it)
- The document was updated to a newer revision that no longer has that attachment
- Replication lag — the attachment hasn't arrived on this Sync Gateway node yet (multi-node cluster)
- Corrupt or orphaned attachment metadata (rare, but possible after failed compaction)

**How we handle it:**

1. **HEAD-first check (optional, recommended):** Before downloading, `HEAD /{keyspace}/{docId}/{attach}` to confirm existence. If 404, skip this attachment without counting it as an error.
2. **On GET 404:** Log at WARN level with `log_key=ATTACHMENT`:
   ```
   WARN  ATTACHMENT  attachment not found: doc=photo_001 attach=photo.jpg rev=3-abc — skipping
   ```
3. **Increment** `attachments_missing_total` metric (new counter).
4. **Continue** processing remaining attachments on this document — a missing attachment does NOT fail the whole document.
5. **Post-processing decision:** If *all* attachments are missing → skip post-processing entirely (nothing was uploaded). If *some* were fetched → post-process with only the successful ones (the `_attachments_external` field only includes attachments that were actually uploaded).

**Config:**

```jsonc
"attachments": {
  "on_missing_attachment": "skip"  // "skip" (default) | "fail" | "retry"
  // "skip"  — log warning, skip the missing attachment, continue
  // "fail"  — treat as a hard failure (halt or DLQ the whole doc)
  // "retry" — retry with backoff (in case of replication lag)
}
```

---

### Corner Case 2: Upload to external destination times out

**Scenario:** The worker downloads the attachment binary successfully, but the `PUT` to S3 / HTTP endpoint / GCS times out or returns 5xx.

**Why this happens:**
- Network partition or destination outage
- Large attachment (100 MB+) exceeds upload timeout
- Rate limiting (S3 throttling, HTTP 429)
- DNS resolution failure on the destination

**How we handle it:**

1. **Retry with exponential backoff** using `attachments.retry` config:
   ```
   attempt 1 → fail → wait 1s
   attempt 2 → fail → wait 2s
   attempt 3 → fail → wait 4s (capped at backoff_max_seconds)
   ```
2. **Classify the error:**
   - **Transient** (timeout, 429, 500, 502, 503, 504) → retry
   - **Permanent** (400, 403, 404 on bucket) → do NOT retry, fail immediately
3. **After `max_retries` exhausted:**
   - `halt_on_failure=true` → **stop processing**, checkpoint does NOT advance, the entire batch will be retried on next cycle
   - `halt_on_failure=false` → **write to DLQ**, skip this document, continue with next document
4. **DLQ entry includes** the local temp file path (if streaming to disk) so the attachment doesn't need to be re-downloaded from source on DLQ replay:
   ```json
   {
     "doc_id": "photo_001",
     "attachment": "photo.jpg",
     "stage": "upload",
     "temp_file": "/tmp/attachments/photo_001_photo.jpg",
     "destination": "s3://my-bucket/attachments/photo_001/photo.jpg",
     "error": "TimeoutError: upload timed out after 120s",
     "attempts": 3,
     "timestamp": "2026-04-18T12:05:00Z"
   }
   ```
5. **Cleanup on skip:** If the document is sent to DLQ and we're continuing, the temp file is NOT cleaned up (needed for DLQ replay). Temp files are cleaned up by a background sweep after `temp_file_ttl_seconds` (default: 3600).

**Retry config:**

```jsonc
"attachments": {
  "retry": {
    "max_retries": 3,           // total attempts = max_retries (not max_retries + 1)
    "backoff_base_seconds": 1,
    "backoff_max_seconds": 30,
    "retry_on_status": [408, 429, 500, 502, 503, 504]
  },
  "fetch": {
    "request_timeout_seconds": 120  // per-attachment download timeout
  },
  "destination": {
    "upload_timeout_seconds": 300   // per-attachment upload timeout (large files need more)
  }
}
```

---

### Corner Case 3: Post-processing update, but document no longer exists (404)

**Scenario:** Attachments were downloaded and uploaded successfully. Now the worker tries to `PUT /{keyspace}/{docId}` to update the document with the external URL / `_exp` / etc., but the source returns **404** — the document was deleted by another client or by replication while the worker was processing attachments.

**Why this happens:**
- Another client deleted the document between `_changes` delivery and post-processing
- Replication brought a deletion tombstone from another peer
- The document was purged (admin operation)
- Race condition in high-write environments where documents are short-lived

**How we handle it:**

1. **The attachment upload already succeeded** — the binary is safely in S3/GCS/HTTP. This is the important part. The post-processing is a "nice-to-have" follow-up.
2. **On 404 from the PUT/DELETE post-process call:**
   ```
   WARN  ATTACHMENT  post-process target gone: doc=photo_001 action=update_doc — document deleted before post-processing
   ```
3. **Do NOT treat this as a failure.** The attachment was successfully extracted — the primary goal is met. Increment `attachments_post_process_skipped_total` (not the error counter).
4. **Log the orphaned upload** so operators can decide if cleanup is needed:
   ```json
   {
     "event": "post_process_target_gone",
     "doc_id": "photo_001",
     "uploaded_to": "s3://my-bucket/attachments/photo_001/photo.jpg",
     "action": "update_doc",
     "reason": "document_deleted"
   }
   ```
5. **Continue** processing — checkpoint advances normally.

**Config:**

```jsonc
"attachments": {
  "post_process": {
    "on_doc_missing": "skip"  // "skip" (default) | "fail"
    // "skip" — log warning, consider the doc done (attachment was uploaded)
    // "fail" — treat as error (halt or DLQ)
  }
}
```

---

### Corner Case 4: Post-processing update, but document is on a newer revision with no attachments

**Scenario:** The worker fetched attachment `photo.jpg` from rev `3-abc`, uploaded it to S3, and now tries to `PUT /{keyspace}/{docId}?rev=3-abc` to add the external URL. The PUT returns **409 Conflict** because the document is now at rev `5-xyz` — and this newer revision has no `_attachments` at all (they were removed by another client).

**Why this happens:**
- Another client updated the document and removed the attachments (intentionally or via a full-document PUT that didn't include `_attachments`)
- A mobile device synced a new version without attachments
- An admin manually cleaned the document

**How we handle it:**

1. **On 409 Conflict**, re-fetch the document to get the latest revision:
   ```
   GET /{keyspace}/{docId}
   ```
2. **Check the re-fetched document for `_attachments`:**
   - **If the attachment we processed is still present** → retry the PUT with the new `_rev` (normal conflict resolution)
   - **If the attachment we processed is gone** → the attachment was intentionally removed. **Do NOT update the document.**
3. **Log the stale-attachment event:**
   ```
   WARN  ATTACHMENT  stale attachment: doc=photo_001 attach=photo.jpg was rev=3-abc, now rev=5-xyz with no attachments — skipping post-process
   ```
4. **Increment** `attachments_stale_total` metric.
5. **The uploaded blob in S3 is now "orphaned"** — it refers to an attachment that no longer exists on the source document. Options:
   - **Default:** Leave it (the upload is still valid data, and the operator may want it for archival)
   - **`cleanup_orphaned_uploads: true`:** Delete the uploaded blob from the destination (best-effort, non-blocking)
6. **Continue** processing — this is not a failure.

**Conflict resolution flow:**

```
PUT /{docId}?rev=3-abc  →  409 Conflict
    │
    ▼
GET /{docId}  →  rev=5-xyz
    │
    ▼
Does rev 5-xyz still have "photo.jpg" in _attachments?
    │
    ├─ YES → retry PUT with rev=5-xyz (up to max_conflict_retries)
    │
    └─ NO  → skip post-process, log stale attachment
             optionally cleanup orphaned upload
```

**Config:**

```jsonc
"attachments": {
  "post_process": {
    "max_conflict_retries": 3,          // max times to re-fetch + retry on 409
    "cleanup_orphaned_uploads": false   // delete uploaded blob if source attachment is gone
  }
}
```

---

### Corner Case 5: Document claims 5 attachments, but only 4 are fetchable

**Scenario:** The `_attachments` stub lists 5 attachments (`a.jpg`, `b.jpg`, `c.jpg`, `d.jpg`, `e.pdf`), but when the worker fetches them individually, `e.pdf` returns 404 or an error. The other 4 download successfully.

**Why this happens:**
- Attachment `e.pdf` was added in a revision that got pruned (revision tree trimming)
- Replication conflict — the attachment exists on one peer but not this one
- Storage corruption (very rare)
- Attachment was deleted between the `_changes` response and the individual GET

**How we handle it:**

This is a **partial success** scenario. The key question: does the worker treat this as success or failure?

**Approach: Configurable via `partial_success` policy:**

```jsonc
"attachments": {
  "partial_success": "continue"  // "continue" (default) | "fail_doc" | "require_all"
}
```

| Policy | Behavior |
|---|---|
| `continue` | Upload the 4 that succeeded. Post-process with only those 4. Log the missing 1 at WARN. Increment `attachments_missing_total`. Document is considered **successful**. |
| `fail_doc` | If ANY attachment fails to download, treat the **entire document** as failed (halt or DLQ). No partial uploads — but the 4 already uploaded to the destination are NOT rolled back (eventual consistency). |
| `require_all` | Like `fail_doc`, but also **deletes the 4 already-uploaded blobs** from the destination before failing the document. Transactional "all or nothing" semantics. Slower, but clean. **⚠️ Rollback deletes can be expensive and may hit rate limits on S3/GCS** — use `continue` unless you have a strong reason for all-or-nothing. |

**For `continue` (default), the post-processing only includes successful attachments:**

```json
{
  "_attachments_external": {
    "a.jpg": { "url": "s3://bucket/photo_001/a.jpg", "status": "uploaded" },
    "b.jpg": { "url": "s3://bucket/photo_001/b.jpg", "status": "uploaded" },
    "c.jpg": { "url": "s3://bucket/photo_001/c.jpg", "status": "uploaded" },
    "d.jpg": { "url": "s3://bucket/photo_001/d.jpg", "status": "uploaded" }
  },
  "_attachments_failed": {
    "e.pdf": { "error": "404 Not Found", "timestamp": "2026-04-18T12:05:00Z" }
  }
}
```

This gives downstream consumers visibility into what succeeded and what didn't.

---

### Corner Case 6: Worker crashes mid-attachment-processing

**Scenario:** The worker has downloaded 3 of 5 attachments, uploaded 2 to S3, and crashes (OOM, SIGKILL, power failure, container eviction).

**What's left behind:**
- 2 blobs uploaded to S3 (orphaned — no post-processing happened)
- 1 temp file on disk (partially downloaded attachment)
- Checkpoint has NOT advanced (it only advances after the full pipeline completes)

**On restart, what happens:**

1. **Checkpoint recovery:** The worker loads the last saved checkpoint and re-fetches the same `_changes` batch. The crashed document will appear again.
2. **Re-download all attachments:** The worker doesn't know which attachments were already uploaded. It fetches all of them again.
3. **Re-upload to destination:** The worker uploads all attachments again. Because object keys are **deterministic** (based on `key_template` using `{doc_id}/{attachment_name}`), the re-upload **overwrites** the same objects in S3. This is safe and idempotent — the data is identical.
4. **Post-processing runs:** This time it completes (assuming no new failure).
5. **Checkpoint advances:** Only after all stages succeed.

**Key design principle: deterministic object keys make crash recovery idempotent.**

```
key_template: "{prefix}/{doc_id}/{attachment_name}"

Crash upload:   s3://bucket/attachments/photo_001/photo.jpg  (orphaned)
Restart upload: s3://bucket/attachments/photo_001/photo.jpg  (overwrites with same data)
```

**What about temp files?**

- On startup, the worker scans `temp_dir` and deletes any temp files older than `temp_file_ttl_seconds` (default: 3600)
- This handles abandoned temp files from crashes without requiring a separate cleanup daemon

**What if the key_template includes `{timestamp}` or `{seq}`?**

- **`{timestamp}`:** Each upload creates a DIFFERENT key → crash recovery creates **duplicate blobs** in the destination. The worker logs a WARN about the orphaned previous upload, but does NOT delete it (it doesn't know the old key).
- **`{seq}`:** Same problem — the seq might differ between the original `_changes` response and the replayed one.
- **Recommendation:** For crash-safe idempotent behavior, use only `{doc_id}` and `{attachment_name}` in the key template. Avoid `{timestamp}` and `{seq}` unless you accept potential duplicates.

**Config note:**

```jsonc
"attachments": {
  "destination": {
    // ✅ Crash-safe (deterministic):
    "key_template": "{prefix}/{doc_id}/{attachment_name}",

    // ⚠️ NOT crash-safe (creates duplicates on retry):
    "key_template": "{prefix}/{doc_id}/{attachment_name}_{timestamp}"
  },
  "fetch": {
    "temp_dir": "/tmp/attachments",
    "temp_file_ttl_seconds": 3600    // cleanup orphaned temp files on startup
  }
}
```

---

### Corner Case 7: Rapid successive revisions with changing attachments

**Scenario:** The `_changes` feed delivers three revisions of the same document in one batch:
- Rev 2: has `photo.jpg` (added)
- Rev 3: has `photo.jpg` + `thumb.png` (added)
- Rev 4: has `thumb.png` only (`photo.jpg` removed)

The worker processes changes sequentially. By the time it fetches `photo.jpg` from rev 2, the document is already at rev 4 which doesn't have `photo.jpg`.

**How we handle it:**

1. **The `_changes` feed with `include_docs=true` includes the document body at the time of the response** — NOT at the time each change happened. So all three rows will show the doc at rev 4.
2. **Without `include_docs`**, the worker fetches the doc separately. By then it's at rev 4.
3. **In both cases**, the worker sees the *current* state: `thumb.png` exists, `photo.jpg` does not.
4. **Result:** Only `thumb.png` is processed. `photo.jpg` is never fetched because it doesn't exist at the current revision.
5. **This is correct behavior** — the worker processes the latest state, not historical states. If the user needs historical attachment versions, they should enable bucket versioning on the destination.

**However**, if `style=all_docs` is used on the `_changes` feed (returning all leaf revisions including conflicts), the worker might see conflicting revisions with different attachments. In this case:
- **The worker MUST process attachments from the winning revision only** (the one without `_conflicts` marker). Non-winning leaf revisions are ignored for attachment processing even if they contain different `_attachments` stubs.
- Log conflicting revisions at DEBUG level

---

### Corner Case 8: Attachment binary is corrupted or truncated

**Scenario:** The `GET /{keyspace}/{docId}/photo.jpg` returns 200 but the response body is truncated (connection dropped mid-transfer) or the bytes don't match the `digest` in the attachment stub.

**How we handle it:**

1. **Content-Length check:** Compare the downloaded bytes against `length` from the attachment stub. If they don't match → treat as a transient error and retry.
2. **Digest verification:** The attachment stub includes `digest` (e.g., `"md5-abc123=="`). After download, compute the digest and compare. If mismatch → retry. Increment `attachments_digest_mismatch_total`.
3. **On retry exhaustion:** Same halt/DLQ behavior as download failures.

Both checks are enabled by default via `fetch.verify_digest` and `fetch.verify_length` in the main config block.

---

### Summary: Error State Decision Matrix

| Corner Case | Default Behavior | Checkpoint Advances? | Metric Incremented |
|---|---|---|---|
| Attachment 404 on GET | Skip attachment, continue | Yes (if other stages succeed) | `attachments_missing_total` |
| Upload timeout / 5xx | Retry → halt or DLQ | No (halt) / Yes (DLQ skip) | `attachments_upload_errors_total` |
| Post-process doc 404 | Skip post-process, log orphan | Yes | `attachments_post_process_skipped_total` |
| Post-process 409 + no attachments on new rev | Skip post-process, log stale | Yes | `attachments_stale_total` |
| 5 of 5 attachments but 4 returned | Upload 4, log missing 1 | Yes | `attachments_missing_total` |
| Worker crash mid-processing | Restart from checkpoint, re-process idempotently | No (not saved yet) | — |
| Rapid revisions, attachment gone | Process latest state only | Yes | — |
| Corrupted/truncated download | Retry with digest check | No (until retry succeeds) | `attachments_download_errors_total` |
| Edge Server (no API) | Skip attachment stage entirely | Yes | `attachments_skipped_total` |
| Upload permanent error (403, bucket not found) | Fail immediately (no retry) | No (halt) / Yes (DLQ skip) | `attachments_upload_errors_total` |

---

### Additional Metrics (from corner cases)

| Metric (prefixed `changes_worker_`) | Type | Description |
|---|---|---|
| `attachments_missing_total` | counter | Attachments listed in `_attachments` but returned 404 on fetch |
| `attachments_stale_total` | counter | Post-process skipped because source doc no longer has the attachment |
| `attachments_post_process_skipped_total` | counter | Post-process skipped (doc deleted, stale rev, etc.) |
| `attachments_conflict_retries_total` | counter | 409 conflict retries during post-processing |
| `attachments_orphaned_uploads_total` | counter | Uploads that completed but post-process target is gone |
| `attachments_digest_mismatch_total` | counter | Downloads where digest didn't match (re-downloaded) |
| `attachments_partial_success_total` | counter | Documents where some (not all) attachments succeeded |
| `attachments_temp_files_cleaned_total` | counter | Orphaned temp files removed on startup |

---

## Couchbase Lite & Attachment Storage Considerations

Couchbase Lite **does support attachments** (called "blobs") — the binary data is stored on the **filesystem** inside the CBL database directory (e.g., `/app/data/changes_worker_db.cblite2/Attachments/`), while the metadata (digest, content_type, length) is stored as JSON properties in the CBL document.

However, using CBL as an intermediate cache for large attachments (3 GB – 50 GB) during processing creates a **filesystem capacity risk**:

| Risk | What happens | Mitigation |
|---|---|---|
| **Disk fills up during download** | CBL writes blob to filesystem → `ENOSPC` → CBL write fails → worker crashes | Monitor `system_disk_percent` and `cbl_db_size_bytes`. Set `max_size_bytes` filter to reject very large attachments. |
| **Many concurrent large attachments** | 5 × 10 GB attachments processing in parallel = 50 GB temp disk usage | Limit `max_concurrent_downloads` and use `sequential: true` for large-attachment workloads. |
| **Abandoned blobs after crash** | Worker crashes mid-processing, orphaned blobs remain in CBL data dir | CBL maintenance (`compact` + `optimize`) on startup cleans orphaned blobs. |
| **Temp files + CBL blobs double the space** | If streaming to `temp_dir` AND storing in CBL, disk usage is 2× attachment size | Choose one: stream to temp_dir (recommended for large files) OR store in CBL (fine for small files). |

**Recommendation for large attachments (>100 MB):** Stream directly from source to destination using `temp_dir` on a volume with sufficient space. Do NOT store in CBL — the CBL data directory is typically sized for metadata (KB–MB), not multi-GB blobs.

**Config:**

```jsonc
"attachments": {
  "fetch": {
    "temp_dir": "/mnt/attachments-temp",   // Use a volume with enough space
    "temp_file_ttl_seconds": 3600,         // Cleanup orphans after 1 hour
    "stream_to_disk_threshold_bytes": 10485760  // Stream to disk if > 10MB (else hold in memory)
  },
  "filter": {
    "max_size_bytes": 5368709120           // Skip attachments > 5GB (0 = no limit)
  }
}
```

**Monitoring:**

```promql
# Disk filling up from attachment processing
changes_worker_system_disk_percent > 85

# CBL database growing unexpectedly large
changes_worker_cbl_db_size_bytes > 2147483648   # > 2GB

# Temp directory usage (if tracked)
changes_worker_attachments_bytes_downloaded_total - changes_worker_attachments_bytes_uploaded_total > 1073741824
```

---

## Sequence Diagrams

### Happy Path — Single Attachment (download → upload → post-process)

```mermaid
sequenceDiagram
    autonumber
    participant CF as _changes Feed
    participant W as Changes Worker
    participant SG as Sync Gateway / CouchDB
    participant S3 as Destination (S3/GCS/HTTP)
    participant CBL as Couchbase Lite

    Note over CF,CBL: Happy Path — Single Attachment

    CF->>W: change row {_id: "photo_001", _attachments: {photo.jpg: {stub:true}}}
    W->>W: Detect _attachments, apply filter (content_type, size, name)

    W->>SG: HEAD /{keyspace}/photo_001/photo.jpg
    SG-->>W: 200 OK (Content-Length: 2MB)

    W->>SG: GET /{keyspace}/photo_001/photo.jpg
    SG-->>W: 200 OK (binary stream → temp file)

    W->>W: Verify length + digest vs stub metadata

    W->>S3: PUT attachments/photo_001/photo.jpg (binary)
    S3-->>W: 200 OK (ETag, location)

    W->>W: Cleanup temp file

    Note over W,SG: Post-Process: update_doc

    W->>SG: GET /{keyspace}/photo_001 (get latest _rev)
    SG-->>W: 200 {_rev: "3-abc", ...}

    W->>SG: PUT /{keyspace}/photo_001?rev=3-abc {_attachments_external: {photo.jpg: {url: "s3://..."}}}
    SG-->>W: 201 {rev: "4-def"}

    W->>W: Continue to RIGHT stage (output doc)
    W->>CBL: Save checkpoint (seq advances)
```

### Happy Path — Multiple Attachments with TTL

```mermaid
sequenceDiagram
    autonumber
    participant CF as _changes Feed
    participant W as Changes Worker
    participant SG as Sync Gateway / CouchDB
    participant S3 as Destination (S3/GCS/HTTP)
    participant CBL as Couchbase Lite

    Note over CF,CBL: Happy Path — Multiple Attachments (3 of 3 succeed)

    CF->>W: change {_id: "report_42", _attachments: {a.pdf, b.png, c.csv}}
    W->>W: Detect 3 attachments, all pass filter

    par Parallel downloads (max_concurrent_downloads)
        W->>SG: GET /{keyspace}/report_42/a.pdf
        SG-->>W: 200 OK (500KB)
        W->>SG: GET /{keyspace}/report_42/b.png
        SG-->>W: 200 OK (80KB)
        W->>SG: GET /{keyspace}/report_42/c.csv
        SG-->>W: 200 OK (12KB)
    end

    W->>W: Verify all 3 digests ✓

    par Parallel uploads
        W->>S3: PUT attachments/report_42/a.pdf
        S3-->>W: 200 OK
        W->>S3: PUT attachments/report_42/b.png
        S3-->>W: 200 OK
        W->>S3: PUT attachments/report_42/c.csv
        S3-->>W: 200 OK
    end

    Note over W,SG: Post-Process: set_ttl (24h expiry)

    W->>SG: GET /{keyspace}/report_42
    SG-->>W: 200 {_rev: "2-aaa", ...}
    W->>SG: PUT /{keyspace}/report_42?rev=2-aaa {_exp: 1713571200, _attachments_external: {...}}
    SG-->>W: 201 {rev: "3-bbb"}

    W->>CBL: Save checkpoint
```

### Error: Attachment 404 (missing on source)

```mermaid
sequenceDiagram
    autonumber
    participant CF as _changes Feed
    participant W as Changes Worker
    participant SG as Sync Gateway / CouchDB
    participant DLQ as DLQ (CBL)

    Note over CF,DLQ: Error Case 1 — Attachment 404 (missing on source)

    CF->>W: change {_id: "photo_001", _attachments: {photo.jpg: {stub:true}}}
    W->>SG: HEAD /{keyspace}/photo_001/photo.jpg
    SG-->>W: 404 Not Found

    alt on_missing_attachment = "skip" (default)
        W->>W: WARN log: attachment not found, skipping
        W->>W: Inc attachments_missing_total
        W->>W: Skip post-process (nothing uploaded)
        W->>W: Continue to RIGHT stage
        W->>W: Checkpoint advances ✓
    else on_missing_attachment = "retry"
        loop Retry with backoff (max_retries)
            W->>SG: HEAD /{keyspace}/photo_001/photo.jpg
            SG-->>W: 404
        end
        W->>W: All retries exhausted
        W->>W: WARN log: attachment missing after retries
        W->>W: Skip this attachment
    else on_missing_attachment = "fail"
        alt halt_on_failure = true
            W->>W: STOP batch, checkpoint does NOT advance
        else halt_on_failure = false
            W->>DLQ: Write DLQ entry {stage: "fetch", error: "404"}
            W->>W: Skip doc, continue batch
        end
    end
```

### Error: Upload timeout / 5xx

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)
    participant DLQ as DLQ (CBL)

    Note over W,DLQ: Error Case 2 — Upload timeout / 5xx

    W->>SG: GET /{keyspace}/photo_001/photo.jpg
    SG-->>W: 200 OK (binary saved to temp file)

    W->>S3: PUT attachments/photo_001/photo.jpg
    S3-->>W: ⏱ Timeout / 503 Service Unavailable

    loop Retry with exponential backoff
        Note over W: wait 1s → 2s → 4s (capped at backoff_max)
        W->>S3: PUT attachments/photo_001/photo.jpg (retry)
        S3-->>W: 503 Service Unavailable
    end

    W->>W: All retries exhausted

    alt halt_on_failure = true
        W->>W: STOP batch
        W->>W: Checkpoint does NOT advance
        Note over W: Temp file remains for next attempt
        Note over W: Next poll cycle re-fetches same batch
    else halt_on_failure = false
        W->>DLQ: Write entry {stage: "upload", temp_file: "/tmp/...", attempts: 3}
        Note over DLQ: Temp file preserved for DLQ replay
        W->>W: Inc attachments_upload_errors_total
        W->>W: Skip doc, continue with next doc in batch
        W->>W: Checkpoint advances (doc skipped)
    end
```

### Error: Post-process but document deleted (404)

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)

    Note over W,S3: Error Case 3 — Post-process but doc deleted (404)

    W->>SG: GET /{keyspace}/photo_001/photo.jpg
    SG-->>W: 200 OK (binary)
    W->>S3: PUT attachments/photo_001/photo.jpg
    S3-->>W: 200 OK ✓ (attachment safely stored)

    Note over W,SG: Post-process: update_doc with external URL

    W->>SG: GET /{keyspace}/photo_001
    SG-->>W: 404 Not Found (doc was deleted by another client)

    alt on_doc_missing = "skip" (default)
        W->>W: WARN: post-process target gone
        W->>W: Log orphaned upload: s3://bucket/.../photo.jpg
        W->>W: Inc attachments_post_process_skipped_total
        W->>W: Continue — attachment IS safely in S3
        W->>W: Checkpoint advances ✓
    else on_doc_missing = "fail"
        W->>W: Treat as error (halt or DLQ)
        Note over W: Attachment already in S3 — can't roll back
    end
```

### Error: 409 Conflict, newer revision has no attachments

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)

    Note over W,S3: Error Case 4 — 409 Conflict, newer rev has no attachments

    W->>SG: GET /{keyspace}/photo_001/photo.jpg (rev 3-abc)
    SG-->>W: 200 OK (binary)
    W->>S3: PUT attachments/photo_001/photo.jpg
    S3-->>W: 200 OK ✓

    W->>SG: PUT /{keyspace}/photo_001?rev=3-abc {_attachments_external: {...}}
    SG-->>W: 409 Conflict (doc is now rev 5-xyz)

    W->>SG: GET /{keyspace}/photo_001
    SG-->>W: 200 {_rev: "5-xyz", NO _attachments}

    W->>W: Check: does rev 5-xyz have photo.jpg?
    W->>W: NO — attachment was removed by another client

    W->>W: WARN: stale attachment, skip post-process
    W->>W: Inc attachments_stale_total

    alt cleanup_orphaned_uploads = true
        W->>S3: DELETE attachments/photo_001/photo.jpg
        S3-->>W: 204 No Content
        W->>W: Orphaned blob cleaned up
    else cleanup_orphaned_uploads = false (default)
        W->>W: Leave blob in S3 (archival value)
    end

    W->>W: Checkpoint advances ✓
```

### Error: Partial fetch (4 of 5 attachments)

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)
    participant DLQ as DLQ (CBL)

    Note over W,DLQ: Error Case 5 — 5 attachments claimed, only 4 fetchable

    W->>W: Doc has _attachments: {a.jpg, b.jpg, c.jpg, d.jpg, e.pdf}

    par Fetch all 5
        W->>SG: GET .../a.jpg
        SG-->>W: 200 OK ✓
        W->>SG: GET .../b.jpg
        SG-->>W: 200 OK ✓
        W->>SG: GET .../c.jpg
        SG-->>W: 200 OK ✓
        W->>SG: GET .../d.jpg
        SG-->>W: 200 OK ✓
        W->>SG: GET .../e.pdf
        SG-->>W: 404 Not Found ✗
    end

    W->>W: Result: 4 downloaded, 1 missing

    alt partial_success = "continue" (default)
        par Upload the 4 that succeeded
            W->>S3: PUT .../a.jpg
            S3-->>W: 200 ✓
            W->>S3: PUT .../b.jpg
            S3-->>W: 200 ✓
            W->>S3: PUT .../c.jpg
            S3-->>W: 200 ✓
            W->>S3: PUT .../d.jpg
            S3-->>W: 200 ✓
        end
        W->>SG: PUT doc with _attachments_external (4) + _attachments_failed (1)
        SG-->>W: 201 ✓
        W->>W: Checkpoint advances ✓
    else partial_success = "require_all"
        W->>S3: DELETE .../a.jpg (rollback)
        W->>S3: DELETE .../b.jpg (rollback)
        W->>S3: DELETE .../c.jpg (rollback)
        W->>S3: DELETE .../d.jpg (rollback)
        W->>DLQ: Write DLQ entry {error: "1 of 5 attachments missing"}
    end
```

### Error: Worker crash mid-processing (idempotent recovery)

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)
    participant CBL as Couchbase Lite
    participant FS as Filesystem (temp_dir)

    Note over W,FS: Error Case 6 — Worker crash mid-processing

    W->>SG: GET .../photo.jpg
    SG-->>W: 200 OK (saved to /tmp/attachments/...)
    W->>S3: PUT attachments/photo_001/photo.jpg
    S3-->>W: 200 OK ✓ (blob exists in S3)

    W->>SG: GET .../thumb.png
    SG-->>W: 200 OK (saved to /tmp/attachments/...)

    Note over W: 💥 CRASH (OOM / SIGKILL / power loss)
    Note over S3: photo.jpg orphaned in S3 (no post-process ran)
    Note over FS: thumb.png temp file abandoned on disk
    Note over CBL: Checkpoint NOT advanced (never saved)

    Note over W,FS: ═══ Worker Restarts ═══

    W->>CBL: Load checkpoint → same seq as before crash
    W->>FS: Scan temp_dir, delete files older than TTL

    W->>SG: GET _changes?since=<last_checkpoint>
    SG-->>W: Same batch including photo_001

    W->>SG: GET .../photo.jpg (re-download)
    SG-->>W: 200 OK

    W->>S3: PUT attachments/photo_001/photo.jpg (idempotent overwrite)
    S3-->>W: 200 OK ✓ (same key, same data)

    W->>SG: GET .../thumb.png (re-download)
    SG-->>W: 200 OK
    W->>S3: PUT attachments/photo_001/thumb.png
    S3-->>W: 200 OK ✓

    W->>SG: PUT photo_001 with _attachments_external (post-process)
    SG-->>W: 201 ✓

    W->>CBL: Save checkpoint ✓ (now advances)
```

### DLQ Replay for failed attachment uploads

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination (S3)
    participant DLQ as DLQ (CBL)

    Note over W,DLQ: DLQ Replay — failed attachment upload from previous run

    Note over W,DLQ: ═══ Worker Restart ═══

    W->>DLQ: Load pending DLQ entries
    DLQ-->>W: 1 entry: {doc_id: photo_001, stage: upload, temp_file: /tmp/.../photo.jpg}

    W->>W: Check temp file exists?

    alt Temp file still exists
        W->>S3: PUT attachments/photo_001/photo.jpg (from temp file)
        S3-->>W: 200 OK ✓
        W->>DLQ: Purge DLQ entry ✓
        W->>W: Cleanup temp file
    else Temp file gone (TTL cleanup or disk wipe)
        W->>SG: GET /{keyspace}/photo_001/photo.jpg (re-download)
        alt Attachment still exists
            SG-->>W: 200 OK (binary)
            W->>S3: PUT attachments/photo_001/photo.jpg
            S3-->>W: 200 OK ✓
            W->>DLQ: Purge DLQ entry ✓
        else Attachment gone (doc changed/deleted)
            SG-->>W: 404 Not Found
            W->>W: WARN: DLQ source attachment gone
            W->>DLQ: Inc replay_attempts
            Note over DLQ: Entry stays until max_replay_attempts or TTL
        end
    end

    W->>W: Continue to normal _changes processing
```

### CBL Storage & Filesystem Capacity Risk

```mermaid
sequenceDiagram
    autonumber
    participant W as Changes Worker
    participant SG as Sync Gateway
    participant S3 as Destination
    participant CBL as Couchbase Lite
    participant FS as Filesystem (CBL data dir)

    Note over W,FS: CBL storage risk with large attachments

    W->>SG: GET /{keyspace}/photo_001/photo.jpg (3GB file)
    SG-->>W: 200 OK (streaming to temp_dir)

    Note over FS: ⚠ 3GB written to /mnt/attachments-temp/
    Note over FS: If temp_dir is on same volume as CBL data dir,<br/>disk usage compounds

    W->>S3: PUT attachments/photo_001/photo.jpg (3GB upload)
    S3-->>W: 200 OK ✓

    W->>W: Cleanup temp file (3GB freed)

    alt Disk space OK throughout
        W->>W: Continue normally ✓
    else Filesystem full during download
        W->>W: ERROR: OSError ENOSPC writing temp file
        W->>W: Download fails → retry or DLQ
        Note over W: Monitor: system_disk_percent > 85%
        Note over W: Mitigation: separate volume for temp_dir,<br/>max_size_bytes filter, sequential processing
    else Filesystem full during CBL write
        W->>W: ERROR: CBL write fails (no space for WAL/compact)
        Note over W: CBL maintenance on restart will compact + reclaim
        Note over W: Monitor: cbl_db_size_bytes
    end
```

---

## Data Flow Diagram

```
_changes ──► has _attachments? ──► NO ──► normal pipeline (skip attachment stage)
                    │
                    YES
                    │
                    ▼
              filter match? ──► NO ──► normal pipeline (skip attachment stage)
                    │
                    YES
                    │
                    ▼
         ┌──────────────────────────────────────────────────────────────┐
         │  ATTACHMENT PROCESSING                                       │
         │                                                              │
         │  1. HEAD /{keyspace}/{docId}/{attach}  (optional, check size)│
         │  2. GET  /{keyspace}/{docId}/{attach}  (download binary)     │
         │  3. PUT  → destination (S3, GCS, HTTP, filesystem)           │
         │  4. Post-process source doc (update/delete/ttl/purge/none)   │
         │                                                              │
         │  On failure:                                                 │
         │    halt_on_failure=true  → stop, no checkpoint advance       │
         │    halt_on_failure=false → DLQ, skip, continue               │
         └──────────────────────────────────────────────────────────────┘
                    │
                    ▼
              normal RIGHT stage (output the document)
                    │
                    ▼
              checkpoint advances
```

---

## Edge Server Workarounds

Edge Server has **no attachment API** — there are no endpoints for GET/PUT/DELETE of attachments. If your pipeline consumes `_changes` from an Edge Server and the source documents have attachments, here are the workarounds:

### Option 1: Upstream Proxy — Fetch from Sync Gateway / App Services

If the Edge Server is replicating from a Sync Gateway or App Services instance, the attachments **do exist** on the upstream server. The worker can:

1. Detect documents with `_attachments` stubs from the Edge Server `_changes` feed
2. Fetch the actual attachment binaries from the **upstream Sync Gateway** (not the Edge Server)
3. This requires a second `gateway` config pointing to the upstream SG with its own auth

```jsonc
"attachments": {
  "edge_server_upstream": {
    "url": "https://sync-gateway.example.com:4984",
    "database": "db",
    "auth": {
      "method": "basic",
      "username": "attachment_reader",
      "password": ""                  // prefer env var
    }
  }
}
```

**Trade-off:** Adds a dependency on the upstream SG being reachable. If the point of Edge Server is offline/edge deployment, this may not be viable.

### Option 2: Embedded Attachments via `include_docs`

If the upstream application writes attachments inline (base64-encoded in the document body as a custom field, not using the `_attachments` API), the Edge Server `_changes` feed with `include_docs=true` will include the base64 data in the document body. The worker can:

1. Detect the custom attachment field (e.g., `"embedded_files"`)
2. Decode the base64 data
3. Upload to destination

This is an application-level workaround — not using the standard `_attachments` system at all.

### Option 3: Skip and Log

The default behavior (`skip_on_edge_server: true`) silently skips attachment processing. This is correct when the downstream consumer doesn't need the attachments, or when another pipeline handles attachments from a non-Edge-Server source.

---

## Security & Operational Guidance

### Credential Management

| Credential | Location | Recommendation |
|---|---|---|
| `destination.s3.access_key_id` / `secret_access_key` | Config or env | Use IAM roles (EC2/ECS/Lambda) or env vars. Never hardcode in `config.json` committed to git. |
| `post_process.admin_auth` (port 4985) | Config or env | **Always use env vars** (`ATTACHMENTS_ADMIN_USERNAME`, `ATTACHMENTS_ADMIN_PASSWORD`) or a secret manager. Admin port credentials grant full database access. |
| `destination.presigned_urls` | Runtime-generated | Pre-signed URLs contain the signature in the URL itself. Set `expiry_seconds` as short as practical for your use case. |

### Content Type Validation

The `reject_content_types` filter provides a safety net against uploading dangerous file types to your destination. Common patterns:

```jsonc
"filter": {
  "reject_content_types": [
    "application/x-msdownload",      // .exe files
    "application/x-executable",      // Unix executables
    "application/x-msdos-program",   // .com files
    "application/x-sh",              // shell scripts
    "application/javascript"         // JS files (if unexpected)
  ]
}
```

### Multi-Tenant Isolation

If this worker runs in a multi-tenant environment, use `key_prefix` or `key_template` to isolate tenants:

```jsonc
"destination": {
  "key_template": "{prefix}/{tenant_id}/{doc_id}/{attachment_name}",
  "key_prefix": "tenant-attachments"
}
```

Where `{tenant_id}` could be derived from the document body, the Couchbase channel, or a custom transform.

---

## Implementation Order

### Phase 0: Detect-Only Mode (noop)

0. ⬜ **Noop attachment detector** — only counts documents with `_attachments` and logs stats. No download, no upload, no post-processing. Run this first to measure volume, tune filters, and validate that attachment detection works before turning on real processing. Enabled via `dry_run: true` with no destination configured.

### Phase 1: Core Attachment Detection & Download

1. ⬜ Attachment detection in MIDDLE stage — check `_attachments` field
2. ⬜ Individual attachment fetch — `GET /{keyspace}/{docId}/{attach}`
3. ⬜ Attachment filter logic — content_type, size, name pattern
4. ⬜ Config schema — add `attachments` block to `config.json`
5. ⬜ Metrics — attachment-specific counters

### Phase 2: Upload Destinations

6. ⬜ S3 upload — reuse `cloud/cloud_s3.py` base with attachment key templating
7. ⬜ HTTP upload — POST/PUT binary to a REST endpoint
8. ⬜ Filesystem write — save to local/mounted path

### Phase 3: Post-Processing

9. ⬜ `update_doc` — PUT updated doc with external URLs
10. ⬜ `delete_attachments` — sequential DELETE of individual attachments
11. ⬜ `delete_doc` — DELETE source document
12. ⬜ `set_ttl` — PUT with `_exp` field (Couchbase only)
13. ⬜ `purge` — POST to admin `_purge` endpoint

### Phase 4: Advanced

14. ⬜ Bulk fetch via `_bulk_get?attachments=true`
15. ⬜ Multipart/related fetch — `GET /{docId}?attachments=true` with `Accept: multipart/related`
16. ⬜ Streaming large attachments — pipe GET response directly to destination PUT without touching disk
17. ⬜ Pre-signed URL generation for `update_doc` post-processing (S3/GCS)
18. ⬜ Admin UI — attachment config editor, attachment processing status panel
19. ⬜ CouchDB-specific multipart/related response parsing
20. ⬜ Edge Server upstream proxy — fetch attachments from upstream SG when source is Edge Server

---

## File Changelist Summary

### New Files

| File | Description |
|---|---|
| `rest/attachments.py` | Attachment processor — detect, fetch, upload, post-process |
| `tests/test_attachments.py` | Unit tests for attachment processing |

### Modified Files

| File | Change |
|---|---|
| `main.py` | Wire attachment stage into the processing pipeline |
| `config.json` | Add `attachments` config block |
| `rest/changes_http.py` | Pass attachment metadata through the processing pipeline |
| `cloud/cloud_base.py` | Support binary (non-JSON) uploads for attachment blobs |
| `cloud/cloud_s3.py` | Binary upload support + attachment key templating |
| `web/server.py` | Admin UI — attachment config fields |
| `docs/DESIGN.md` | Update pipeline diagram to include ATTACHMENT stage |
