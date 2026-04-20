# JSON Schema Format Guide

**Project**: Change Stream DB  
**Scope**: changes-worker  
**Date**: April 20, 2024  
**Status**: Active

---

## Overview

This document establishes naming conventions, field formats, and data type standards for all JSON documents in the `changes-worker` scope of the Couchbase Lite database.

---

## Field Naming Convention

### Primary Rule: Snake Case with Underscores

All field names MUST use **snake_case** (lowercase with underscores separating words).

✅ **Correct**:
```json
{
  "job_id": "550e8400-...",
  "source_type": "sync_gateway",
  "client_id": "my-client",
  "last_updated": "2024-01-15T10:30:00Z",
  "batch_size": 100,
  "is_enabled": true
}
```

❌ **Incorrect**:
```json
{
  "jobId": "...",          // camelCase - NO
  "SourceType": "...",     // PascalCase - NO
  "clientID": "...",       // Mixed - NO
  "lastUpdated": "..."     // camelCase - NO
}
```

### Special Case: Top-Level Underscore Prefix

**RESERVED FIELDS** — Do NOT use leading underscore `_` at the top level unless required by Couchbase/CouchDB.

#### System Reserved Fields (Forbidden)

These are managed by the database and will cause errors if you use them:

| Field | System | Purpose | Status |
|-------|--------|---------|--------|
| `_id` | CBL, SG, CouchDB | Document ID | **DO NOT SET** |
| `_rev` | CBL, SG, CouchDB | Revision ID | **DO NOT SET** |
| `_deleted` | CBL, SG, CouchDB | Tombstone marker | **DO NOT SET** |
| `_attachments` | CBL, SG, CouchDB | File attachments | **DO NOT SET** (handled by API) |
| `_revisions` | CBL, SG, CouchDB | Revision history | **DO NOT SET** |
| `_removed` | CBL, SG | Access removed | **DO NOT SET** |
| `_exp` | Sync Gateway | Expiration/TTL | **DO NOT SET** |
| `_purged` | Sync Gateway | Purged marker | **DO NOT SET** |
| `_sync` | Sync Gateway | Internal metadata | **DO NOT SET** |
| `_sequence` | Couchbase Lite | Internal sequence | **DO NOT SET** |

#### Allowed Exception: Metadata Container

If you need internal metadata, **nest it** instead of using a top-level underscore:

✅ **Correct**:
```json
{
  "type": "job",
  "id": "550e8400-...",
  "name": "My Job",
  "_meta": {
    "internal_flag": true,
    "processing_status": "pending"
  }
}
```

❌ **Wrong**:
```json
{
  "type": "job",
  "_internal_flag": true,  // NO - reserved prefix at top level
  "_processing_status": "pending"  // NO
}
```

**Note**: The `_meta` field itself is allowed as a container for application-level metadata that doesn't conflict with database reserved fields.

---

## DateTime Format

### Two Allowed Formats

All timestamp fields MUST use ONE of these formats (not both in same field):

#### 1. Unix Epoch Timestamp (Recommended for Performance)

**Format**: Integer (seconds since 1970-01-01T00:00:00Z)

✅ **Use When**:
- Performance is critical
- Storage efficiency matters
- High-frequency queries
- Checkpoints, DLQ entries, audit logs

**Example**:
```json
{
  "type": "checkpoint",
  "time": 1705324200,           // ← Unix epoch (integer)
  "created_at": 1705324200,
  "last_activity": 1705323900
}
```

**Conversion**:
```python
import time
from datetime import datetime

# Create Unix timestamp
now = int(time.time())  # 1705324200

# Convert to ISO-8601 for display
iso_string = datetime.utcfromtimestamp(1705324200).isoformat() + 'Z'
# Result: "2024-01-15T10:30:00Z"
```

#### 2. ISO-8601 Format (Recommended for Readability)

**Format**: String in RFC 3339 format: `YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS±HH:MM`

✅ **Use When**:
- Human readability matters
- API responses
- Configuration documents
- Audit trails, notifications

**Example**:
```json
{
  "type": "job",
  "created_at": "2024-01-15T10:30:00Z",      // ← ISO-8601 UTC
  "updated_at": "2024-01-15T11:45:30Z",
  "last_sync": "2024-01-15T10:30:00+05:00"   // ← With timezone
}
```

**Valid ISO-8601 Formats**:
```
2024-01-15T10:30:00Z              ✅ UTC (preferred)
2024-01-15T10:30:00+00:00         ✅ UTC with offset
2024-01-15T10:30:00.123Z          ✅ With milliseconds
2024-01-15T10:30:00+05:30         ✅ With timezone offset
2024-01-15T10:30:00                ⚠️ No timezone (avoid)
```

#### Field-Specific Recommendations

| Field Name | Type | Reason | Example |
|-----------|------|--------|---------|
| `time`, `timestamp`, `created_at` (runtime) | Unix | Performance | `1705324200` |
| `created_at`, `updated_at` (config) | ISO-8601 | Readability | `"2024-01-15T10:30:00Z"` |
| `expires_at` | Unix | TTL calculation | `1705410600` |
| `last_activity` | Unix | Frequent queries | `1705324200` |
| `checkpoint_time` | Unix | Tracking | `1705324200` |
| `scheduled_at` | ISO-8601 | Human scheduling | `"2024-01-15T10:30:00Z"` |

---

## Data Type Standards

### String Fields

✅ **Correct**:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Hotel Sync Job",
  "status": "running",
  "source_type": "sync_gateway",
  "database": "travel-sample",
  "host": "192.168.1.100",
  "error_message": "Connection timeout"
}
```

### Integer Fields

✅ **Correct**:
```json
{
  "port": 5432,
  "batch_size": 100,
  "timeout_seconds": 30,
  "pool_size": 10,
  "retry_count": 3,
  "status_code": 500,
  "replay_attempts": 0
}
```

### Boolean Fields

✅ **Correct**:
```json
{
  "enabled": true,
  "active": false,
  "ssl": true,
  "continuous": true,
  "retried": false,
  "is_active": true
}
```

❌ **Incorrect**:
```json
{
  "enabled": "true",      // String, not boolean - NO
  "active": 1,            // Integer - NO
  "ssl": "yes"            // String - NO
}
```

### Array Fields

✅ **Correct**:
```json
{
  "roles": ["admin", "operator", "viewer"],
  "recipients": ["john@example.com", "jane@example.com"],
  "sources": [
    {"id": "source-1", "type": "sync_gateway"},
    {"id": "source-2", "type": "couchdb"}
  ]
}
```

### Object/Map Fields

✅ **Correct**:
```json
{
  "properties": {
    "name": {"type": "string"},
    "age": {"type": "integer"}
  },
  "headers": {
    "Authorization": "Bearer token",
    "Content-Type": "application/json"
  },
  "config": {
    "batch_size": 100,
    "timeout_seconds": 30
  }
}
```

### Null Values

✅ **Use null when**:
- Field is optional and not set
- Value is explicitly empty/cleared

```json
{
  "error_message": null,
  "last_activity": null,
  "user_id": null
}
```

---

## Field Organization Standard

### Document Structure

All documents MUST follow this standard structure:

```json
{
  "type": "job",                    // ← 1. Type (first, always required)
  "id": "550e8400-...",             // ← 2. ID (second, when applicable)
  "name": "Hotel Sync",             // ← 3. Core fields
  "description": "...",
  "status": "running",
  "created_at": "2024-01-15T10:30:00Z",   // ← 4. Timestamps (grouped)
  "updated_at": "2024-01-15T10:30:00Z",
  "config": { },                    // ← 5. Complex objects
  "metadata": { }                   // ← 6. Metadata/internal
}
```

### Field Order Priority

1. **Type** (`type`) — Always first
2. **ID** (`id`, `doc_id`, etc.) — Always second
3. **Core** (name, status, config) — Main fields
4. **Timestamps** (created_at, updated_at) — Grouped together
5. **Complex Objects** (nested objects, arrays)
6. **Metadata** (_meta, internal flags)

---

## Enum Values

### Format for Enum Fields

All enum values MUST be:
- lowercase
- snake_case if multi-word
- Listed in schema's `enum` array

✅ **Correct**:
```json
{
  "output_type": "rdbms",            // ✅ lowercase, const
  "database_type": "postgres",       // ✅ lowercase
  "status": "running",               // ✅ lowercase
  "severity": "critical",            // ✅ lowercase
  "auth_type": "bearer"              // ✅ lowercase, snake_case
}
```

❌ **Incorrect**:
```json
{
  "output_type": "RDBMS",            // NO - uppercase
  "database_type": "PostgreSQL",     // NO - capitalized
  "status": "Running",               // NO - capitalized
  "auth_type": "OAuth2",             // NO - mixed case
}
```

### Allowed Enum Values by Collection

| Field | Allowed Values |
|-------|----------------|
| `type` | Constant per collection (job, checkpoint, dlq, etc) |
| `output_type` | rdbms, http, cloud, stdout |
| `source_type` | sync_gateway, app_services, edge_server, couchdb |
| `database_type` | mysql, postgres, mssql, oracle |
| `cloud_provider` | aws_s3, gcs, azure_blob, oracle_ocs |
| `status` | idle, running, paused, stopped, error |
| `method` | POST, PUT, PATCH, DELETE |
| `auth_type` | none, basic, bearer, api_key |
| `format` | json, jsonl, parquet, csv, avro |
| `compression` | none, gzip, snappy, brotli |
| `severity` | info, warning, error, critical |
| `enrichment_type` | lookup_table, javascript_function, sql_query, api_call, groovy_script, semantic_mapping |

---

## UUID Format

### UUID v4 Standard

All UUID fields MUST be UUID v4 format (random).

✅ **Correct**:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "job_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "correlation_id": "a1b2c3d4-e5f6-4789-0123-456789abcdef"
}
```

❌ **Incorrect**:
```json
{
  "id": "job-123",                   // NO - not UUID
  "id": "550e8400e29b41d4a716446655440000",  // NO - missing hyphens
  "id": "550e8400-e29b-41d4-a716"    // NO - incomplete
}
```

**Python Generation**:
```python
import uuid

# Generate UUID v4
new_id = str(uuid.uuid4())
# Result: "550e8400-e29b-41d4-a716-446655440000"
```

---

## Document ID (Metadata) vs ID Field

### Document ID (_id) — System Field

The `_id` field is managed by Couchbase Lite/CouchDB and determined by:
- Document ID pattern (see Collections Summary)
- Database behavior

**DO NOT set `_id` manually.**

### ID Field — Application Field

Many documents have an `id` field for application logic:

```json
{
  "type": "job",
  "id": "550e8400-...",           // ← Application ID (you set this)
  "_id": "550e8400-..."           // ← Document ID (database sets this)
}
```

---

## Optional vs Required Fields

### Required Fields (Must Always Be Present)

```json
{
  "type": "job",                    // ← Always required
  "id": "550e8400-...",             // ← Always required for jobs
  "name": "Job Name",               // ← Always required for jobs
  "inputs": [],                     // ← Always required for jobs
  "outputs": [],                    // ← Always required for jobs
  "output_type": "rdbms",           // ← Always required for jobs
  "state": {}                       // ← Always required for jobs
}
```

### Optional Fields (May Be Absent)

```json
{
  "description": null,              // ← Can be absent or null
  "enabled": true,                  // ← May have default
  "error_message": null             // ← Only present if error
}
```

**Schema Declaration**:
```json
{
  "required": ["type", "id", "name", "inputs", "outputs", "output_type", "state"],
  "properties": {
    "description": {"type": ["string", "null"]},
    "enabled": {"type": ["boolean", "null"], "default": true}
  }
}
```

---

## Comments & Metadata

### Use _meta for Application Metadata

For application-level metadata that doesn't fit standard fields:

```json
{
  "type": "job",
  "id": "550e8400-...",
  "name": "Hotel Sync",
  "_meta": {
    "source_file": "import_v1.5.json",
    "import_date": "2024-01-15T10:30:00Z",
    "import_version": "2.0",
    "notes": "Migrated from v1.x system"
  }
}
```

### Do NOT Use Comments in JSON

JSON doesn't support comments. Use `_meta` or description fields instead.

❌ **Incorrect**:
```json
{
  // This is a comment - WILL NOT WORK IN JSON
  "type": "job",
  "id": "550e8400-..."
}
```

---

## Nesting & Composition

### Keep Top Level Flat (Unless Grouped)

✅ **Correct** (flat structure):
```json
{
  "type": "job",
  "id": "550e8400-...",
  "name": "Hotel Sync",
  "enabled": true,
  "batch_size": 100,
  "timeout_seconds": 30
}
```

❌ **Avoid** (unnecessary nesting):
```json
{
  "type": "job",
  "properties": {
    "id": "550e8400-...",
    "name": "Hotel Sync",
    "config": {
      "enabled": true,
      "batch_size": 100,
      "timeout_seconds": 30
    }
  }
}
```

### Nest When Logically Related

✅ **Correct** (grouped logically):
```json
{
  "type": "config",
  "logging": {
    "level": "info",
    "format": "json",
    "output": "stdout"
  },
  "database": {
    "max_connections": 50,
    "timeout_seconds": 30
  },
  "security": {
    "ssl_enabled": true,
    "verify_certificate": true
  }
}
```

---

## Validation & Constraints

### Required Field Presence

All documents MUST include the `required` array in schema:

```json
{
  "required": [
    "type",
    "id",
    "name"
  ]
}
```

### Enum Constraints

Enum fields MUST have an explicit `enum` array:

```json
{
  "status": {
    "type": "string",
    "enum": ["idle", "running", "paused", "stopped", "error"],
    "description": "Job execution status"
  }
}
```

### Format Constraints

Use `format` for specific data types:

```json
{
  "id": {
    "type": "string",
    "format": "uuid",
    "description": "Unique identifier (UUID v4)"
  },
  "email": {
    "type": "string",
    "format": "email"
  },
  "created_at": {
    "type": "string",
    "format": "date-time"
  }
}
```

### Min/Max Constraints

Use constraints for numeric ranges:

```json
{
  "batch_size": {
    "type": "integer",
    "minimum": 1,
    "maximum": 10000,
    "description": "Batch size (1-10000)"
  },
  "port": {
    "type": "integer",
    "minimum": 1,
    "maximum": 65535
  }
}
```

---

## Example: Complete Document

```json
{
  "type": "job",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Hotel Inventory Sync",
  "description": "Syncs hotel data from Couchbase to PostgreSQL",
  "inputs": [
    {
      "id": "source-1",
      "source_type": "sync_gateway",
      "host": "192.168.1.100",
      "database": "travel-sample",
      "scope": "inventory",
      "collection": "hotels"
    }
  ],
  "outputs": [
    {
      "id": "postgres-prod",
      "database_type": "postgres",
      "host": "db.example.com",
      "port": 5432,
      "database": "production",
      "schema": "public",
      "username": "app_user"
    }
  ],
  "output_type": "rdbms",
  "enabled": true,
  "batch_size": 500,
  "timeout_seconds": 60,
  "mapping": {
    "rules": [
      {
        "source": "type",
        "destination": "doc_type"
      }
    ]
  },
  "state": {
    "status": "running",
    "last_updated": "2024-01-15T10:30:00Z"
  },
  "created_at": "2024-01-10T08:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z",
  "_meta": {
    "version": "2.0",
    "migrated_from": "v1.5"
  }
}
```

---

## Checklist for Document Authors

Before saving any document:

- [ ] All field names are snake_case (lowercase, underscores)
- [ ] No top-level fields start with `_` (except `_meta` container)
- [ ] Timestamps use either Unix epoch or ISO-8601 (consistent per field)
- [ ] Boolean values are true/false (not "true"/"false" or 1/0)
- [ ] Enum values are lowercase and match schema
- [ ] All required fields are present
- [ ] IDs are UUID v4 where applicable
- [ ] No trailing whitespace
- [ ] No comments (use _meta instead)
- [ ] Structure is logically organized (type → id → core → timestamps → objects)

---

## Common Mistakes & Fixes

| Mistake | Problem | Fix |
|---------|---------|-----|
| `jobId`, `JobName` | camelCase/PascalCase | Use `job_id`, `job_name` |
| `_internal_flag` | Reserved prefix at top level | Use `_meta.internal_flag` |
| `"enabled": "true"` | String instead of boolean | Use `"enabled": true` |
| `"status": "Running"` | Capitalized enum | Use `"status": "running"` |
| `1705324200000` | Milliseconds instead of seconds | Use `1705324200` (seconds) |
| `2024-01-15` | Date only, no time | Use ISO-8601: `"2024-01-15T10:30:00Z"` |
| `port: "5432"` | String instead of integer | Use `port: 5432` |

---

## References

- **JSON Schema**: https://json-schema.org/
- **RFC 3339 (Date/Time)**: https://tools.ietf.org/html/rfc3339
- **UUID v4**: https://tools.ietf.org/html/rfc4122
- **Couchbase Lite Reserved Fields**: https://docs.couchbase.com/couchbase-lite/3.0/
- **Sync Gateway Reserved Fields**: https://docs.couchbase.com/sync-gateway/3.0/

---

## Document Info

- **Document**: JSON_SCHEMA.md
- **Location**: guides/
- **Version**: 1.0
- **Last Updated**: April 20, 2024
- **Status**: Active
- **Maintainer**: Project Team

