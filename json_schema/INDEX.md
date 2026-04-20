# JSON Schema Index

Complete reference guide for all JSON schema files in the `changes-worker` scope.

## Quick Navigation

### 📖 Documentation
- **[README.md](README.md)** — Complete schema documentation and usage guide
- **[COLLECTIONS_SUMMARY.md](COLLECTIONS_SUMMARY.md)** — Overview of collections, relationships, and examples

### 🔌 Schema Files

#### Input & Output Definitions

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [inputs_changes/schema.json](changes-worker/inputs_changes/schema.json) | inputs_changes | Data source definitions | `inputs_changes` |
| [outputs_rdbms/schema.json](changes-worker/outputs_rdbms/schema.json) | outputs_rdbms | Relational database destinations | `outputs_rdbms` |
| [outputs_http/schema.json](changes-worker/outputs_http/schema.json) | outputs_http | HTTP/REST endpoints | `outputs_http` |
| [outputs_cloud/schema.json](changes-worker/outputs_cloud/schema.json) | outputs_cloud | Cloud storage (S3, GCS, Azure) | `outputs_cloud` |
| [outputs_stdout/schema.json](changes-worker/outputs_stdout/schema.json) | outputs_stdout | Console/log output | `outputs_stdout` |

#### Pipeline & Jobs

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [jobs/schema.json](changes-worker/jobs/schema.json) | jobs | Data pipeline job definitions | UUID v4 |

#### Runtime & Progress Tracking

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [checkpoints/schema.json](changes-worker/checkpoints/schema.json) | checkpoints | Change feed progress | `checkpoint:{job_id}` |
| [dlq/schema.json](changes-worker/dlq/schema.json) | dlq | Dead letter queue (failures) | `dlq:{doc_id}:{timestamp}` |

#### Data Quality & Enrichment

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [data_quality/schema.json](changes-worker/data_quality/schema.json) | data_quality | Quality metrics and anomalies | `dq:{job_id}:{timestamp}` |
| [enrichments/schema.json](changes-worker/enrichments/schema.json) | enrichments | Data transformation rules | Custom ID |

#### System Configuration

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [config/schema.json](changes-worker/config/schema.json) | config | System-wide settings | `config` |

#### Future (Auth & Observability)

| File | Collection | Purpose | Doc ID |
|------|-----------|---------|--------|
| [users/schema.json](changes-worker/users/schema.json) | users | User accounts | `{username}` |
| [sessions/schema.json](changes-worker/sessions/schema.json) | sessions | Session tokens | `session:{token_hash}` |
| [audit_log/schema.json](changes-worker/audit_log/schema.json) | audit_log | Compliance audit trail | `audit:{timestamp}:{uuid}` |
| [notifications/schema.json](changes-worker/notifications/schema.json) | notifications | System alerts & events | `{notification_id}` |

#### Legacy (Deprecated)

| File | Collection | Purpose | Status |
|------|-----------|---------|--------|
| [mappings/schema.json](changes-worker/mappings/schema.json) | mappings | Legacy schema mappings | Deprecated in v2.0 |

---

## Collection Matrix

### By Category

**Core Pipeline** (Production)
- `inputs_changes` — Where data comes from
- `outputs_*` — Where data goes (4 types)
- `jobs` — How data flows (transform)

**Runtime** (Production)
- `checkpoints` — Progress tracking
- `dlq` — Error handling

**Infrastructure** (Production)
- `config` — System settings

**Quality** (Production)
- `data_quality` — Metrics
- `enrichments` — Transformation

**Auth & Audit** (Future)
- `users`, `sessions` — Access control
- `audit_log` — Compliance

**Legacy** (Deprecated)
- `mappings` → Moved to `jobs.mapping`

---

## Usage Examples

### Validate a Document

```python
import json
import jsonschema

# Load schema
with open('json_schema/changes-worker/jobs/schema.json') as f:
    schema = json.load(f)

# Load document
with open('my_job.json') as f:
    job = json.load(f)

# Validate
try:
    jsonschema.validate(instance=job, schema=schema)
    print("✓ Document is valid")
except jsonschema.ValidationError as e:
    print(f"✗ Validation error: {e.message}")
```

### Find Required Fields

```python
schema = json.load(open('changes-worker/jobs/schema.json'))
required = schema.get('required', [])
print(f"Required fields: {required}")
# Output: ['type', 'id', 'name', 'inputs', 'outputs', 'output_type', 'state']
```

### List Schema Properties

```python
schema = json.load(open('changes-worker/jobs/schema.json'))
for field, definition in schema['properties'].items():
    print(f"{field}: {definition.get('description', 'No description')}")
```

---

## Schema Reference by Field

### Common Fields (All Collections)

- `type` — Document type identifier (required, const value)
- `id` — Unique identifier (UUID for most)
- `created_at` — ISO 8601 creation timestamp
- `updated_at` — ISO 8601 update timestamp

### Input/Output Fields

- `id` — Output identifier
- `host` — Server hostname
- `port` — Server port
- `username` — Auth username
- `password` — Auth password (encrypted)
- `ssl` — Enable TLS
- `timeout_seconds` — Request timeout
- `batch_size` — Documents per batch

### Job Fields

- `inputs` — Array of input sources
- `outputs` — Array of output destinations
- `output_type` — Destination type (rdbms|http|cloud|stdout)
- `mapping` — Data transformation rules
- `state` — Job execution state
- `enabled` — Active flag

### Checkpoint Fields

- `client_id` — Job identifier
- `SGs_Seq` — Last sequence number
- `time` — Checkpoint timestamp
- `remote` — Remote counter

### DLQ Fields

- `doc_id_original` — Original document ID
- `seq` — Sequence number
- `status` — HTTP status code
- `error` — Error message
- `reason` — Failure category
- `retried` — Retry flag
- `replay_attempts` — Retry count

---

## JSON Schema Specifications

All schemas conform to:
- **Standard**: JSON Schema 2020-12
- **Documentation**: https://json-schema.org/
- **Draft**: Latest stable draft

### Schema Properties Used

- `$schema` — Schema version
- `$id` — Schema identifier
- `title` — Human-readable title
- `description` — Detailed description
- `type` — Data type (always "object")
- `properties` — Field definitions
- `required` — Mandatory fields
- `additionalProperties` — Allow extra fields (true)
- `examples` — Sample valid documents
- `enum` — Allowed values
- `format` — Data format (e.g., uuid, date-time, email)

---

## Validation Checklist

When creating/updating documents:

- [ ] Document has `type` field matching collection
- [ ] All required fields are present
- [ ] Enum fields have allowed values
- [ ] Timestamps are ISO 8601 or Unix
- [ ] IDs are UUIDs (where required)
- [ ] Nested objects match their schemas
- [ ] Array items match item schema

---

## Integration Points

### REST API Validation
- **Endpoint**: `rest/api_v2.py`
- **Pattern**: POST/PUT validate before save
- **Location**: Check examples in endpoint handlers

### Python Library
- **Module**: `cbl_store.py`
- **Methods**: `load_*()`, `save_*()` functions
- **Location**: Search for `MutableDocument` assignments

### Documentation Generation
- **Tool**: Any JSON Schema to documentation converter
- **Input**: Individual schema files
- **Output**: HTML, Markdown, or PDF

---

## File Statistics

```
Total Collections: 16
- Production: 9 (inputs, outputs×4, jobs, checkpoints, dlq, config)
- Runtime: 2 (data_quality, enrichments)
- Future: 4 (users, sessions, audit_log, notifications)
- Deprecated: 1 (mappings)

Total Schema Files: 16
All Valid JSON: ✓
```

---

## Getting Help

1. **Understanding a field?** → Check the schema description
2. **Need an example?** → Look at schema's `examples` key
3. **Validation error?** → Check `required` and `enum` fields
4. **Integration question?** → See integration points above
5. **Schema evolution?** → Check COLLECTIONS_SUMMARY.md

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-01-15 | Initial schema documentation for v2.0 |

---

## Related Documentation

- [Python Code](../cbl_store.py) — Implementation details
- [REST API](../rest/api_v2.py) — Endpoint validation
- [Database Schema](../schema/) — Legacy schema files
- [README](../README.md) — Project overview

