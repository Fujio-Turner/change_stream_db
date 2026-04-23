# Couchbase Lite Collections Summary

## Database Structure

```
Database: changes_worker_db
└── Scope: changes-worker
    ├── inputs_changes          (1 doc) — Input source definitions
    ├── outputs_rdbms           (n docs) — RDBMS destination configs
    ├── outputs_http            (n docs) — HTTP endpoint configs
    ├── outputs_cloud           (n docs) — Cloud storage configs
    ├── tables_rdbms            (1 doc) — Reusable RDBMS table definitions
    ├── jobs                    (n docs) — Data pipeline jobs
    ├── checkpoints             (n docs) — Change feed progress tracking
    ├── dlq                     (n docs) — Failed documents
    ├── data_quality            (n docs) — Quality metrics
    ├── enrichments             (n docs) — Transformation rules
    ├── config                  (1 doc) — System configuration
    ├── users                   (n docs) — User accounts (future)
    ├── sessions                (n docs) — Session tokens (future)
    ├── audit_log               (n docs) — Audit trail (future)
    ├── notifications           (n docs) — System alerts (future)
    └── mappings                (n docs) — Legacy schema mappings (deprecated)
```

## Collection Details

### Production Collections (v2.0+)

| Collection | Doc Count | Purpose | Indexed By | TTL |
|-----------|-----------|---------|-----------|-----|
| inputs_changes | 1 | Define data sources | — | — |
| outputs_rdbms | 1-n | RDBMS destinations | id | — |
| outputs_http | 1-n | HTTP endpoints | id | — |
| outputs_cloud | 1-n | Cloud storage | id | — |
| tables_rdbms | 1 | RDBMS table definitions library | id | — |
| jobs | 1-n | Pipeline jobs | id, enabled | — |
| checkpoints | 1-n | Progress tracking | client_id | — |
| dlq | 1-n | Failed docs | doc_id_original, time | Default 7d |
| config | 1 | System settings | — | — |

### Future Collections

| Collection | Purpose | Status |
|-----------|---------|--------|
| users | Authentication | Planned |
| sessions | Session management | Planned |
| audit_log | Compliance auditing | Planned |
| notifications | System alerts | Planned |

### Runtime Collections

| Collection | Purpose | Auto-managed |
|-----------|---------|--------------|
| data_quality | Quality metrics | Yes (by processor) |
| enrichments | Transformation rules | Manual |

### Deprecated

| Collection | Reason | Migration |
|-----------|--------|-----------|
| mappings | v2.0 refactoring | Embed in jobs.mapping |

---

## Key Relationships

### Input → Job → Output Flow

```
inputs_changes
└── contains: [InputSource]
    └── referenced by: job.inputs[*].id
        └── job: {
            inputs: [InputSource],
            outputs: [OutputDestination],
            output_type: "rdbms|http|cloud",
            mapping: {
              rules: [TransformRule]
            }
          }
          └── writes to: outputs_rdbms|http|cloud[*]
              └── on failure: dlq[*]
              └── progress: checkpoints[job_id]
```

### Document ID Patterns

```
inputs_changes           → "inputs_changes" (singleton)
outputs_*               → "outputs_{type}" (singleton per type)
jobs                    → UUID (550e8400-e29b-41d4-a716-446655440000)
checkpoints             → "checkpoint:{job_id}"
dlq                     → "dlq:{original_id}:{timestamp}"
config                  → "config" (singleton)
users                   → "{username}"
sessions                → "session:{token_hash}"
audit_log               → "audit:{action}:{timestamp}:{uuid}"
notifications           → "{notification_id}"
enrichments             → "{enrichment_id}"
data_quality            → "dq:{job_id}:{timestamp}"
mappings                → "{mapping_name}"
```

---

## Data Flow Example

### Example: Hotel Sync Job

1. **Define Input Source** (inputs_changes)
   ```json
   {
     "type": "inputs_changes",
     "src": [{
       "id": "couchbase-hotels",
       "source_type": "sync_gateway",
       "host": "cb.example.com",
       "database": "travel-sample",
       "scope": "inventory",
       "collection": "hotels"
     }]
   }
   ```

2. **Define Output** (outputs_rdbms)
   ```json
   {
     "type": "outputs_rdbms",
     "src": [{
       "id": "postgres-prod",
       "database_type": "postgres",
       "host": "db.example.com",
       "database": "production"
     }]
   }
   ```

3. **Create Job** (jobs)
   ```json
   {
     "type": "job",
     "id": "550e8400-...",
     "name": "Hotel Sync",
     "inputs": [{"id": "couchbase-hotels", ...}],
     "outputs": [{"id": "postgres-prod", ...}],
     "output_type": "rdbms",
     "mapping": {
       "rules": [{"source": "type", "destination": "doc_type"}]
     },
     "state": {"status": "running"}
   }
   ```

4. **Track Progress** (checkpoints)
   ```json
   {
     "type": "checkpoint",
     "client_id": "550e8400-...",
     "SGs_Seq": "12345",
     "time": 1705324200,
     "remote": 0
   }
   ```

5. **Log Failures** (dlq)
   ```json
   {
     "type": "dlq",
     "doc_id_original": "hotel:123",
     "seq": "12345",
     "method": "INSERT",
     "status": 500,
     "error": "Connection timeout",
     "reason": "timeout",
     "time": 1705324200,
     "doc_data": "{...}"
   }
   ```

---

## Schema Validation

All documents validate against JSON Schema 2020-12.

### Validate in Python

```python
from cbl_store import CBLStore
import jsonschema

store = CBLStore()
job = store.load_job(job_id)

# Load schema
with open('json_schema/changes-worker/jobs/schema.json') as f:
    schema = json.load(f)

# Validate
try:
    jsonschema.validate(instance=job, schema=schema)
    print("✓ Valid")
except jsonschema.ValidationError as e:
    print(f"✗ Invalid: {e.message}")
```

### Validate in REST API

All POST/PUT endpoints validate before saving:
- `POST /api/jobs` — validates against jobs schema
- `POST /api/inputs_changes` — validates against inputs_changes schema
- `POST /api/outputs/{type}` — validates against outputs_{type} schema

---

## Querying Examples

### N1QL Queries

```sql
-- List all jobs
SELECT * FROM `changes-worker`.jobs WHERE type = 'job'

-- Find job by name
SELECT * FROM `changes-worker`.jobs 
WHERE type = 'job' AND name = 'Hotel Sync'

-- Get last checkpoint
SELECT * FROM `changes-worker`.checkpoints 
WHERE client_id = '550e8400-...'

-- List DLQ entries by status
SELECT * FROM `changes-worker`.dlq 
WHERE type = 'dlq' AND status = 500

-- Count quality metrics
SELECT COUNT(*) as count FROM `changes-worker`.data_quality
WHERE job_id = '550e8400-...'
```

### Python API

```python
from cbl_store import CBLStore

store = CBLStore()

# List jobs
jobs = store.list_jobs()

# Load job
job = store.load_job(job_id)

# Save job
store.save_job(job_id, job_doc)

# Load checkpoint
checkpoint = store.load_checkpoint(job_id)

# List DLQ
dlq_entries = store.list_dlq()
```

---

## Performance Tuning

### Indexes

Create indexes for common queries:

```sql
-- Job queries
CREATE INDEX idx_jobs_enabled 
ON `changes-worker`.jobs(enabled)

-- Checkpoint queries
CREATE INDEX idx_checkpoint_client 
ON `changes-worker`.checkpoints(client_id)

-- DLQ queries
CREATE INDEX idx_dlq_time 
ON `changes-worker`.dlq(time DESC)
```

### Batch Operations

- Insert/update jobs: 10-100 per batch
- Insert checkpoints: 1 per batch (single job)
- Insert DLQ: 100-1000 per batch
- Insert config: 1 per batch (singleton)

### Retention

- Config: Keep indefinitely
- Jobs: Keep indefinitely (unless deleted via API)
- Checkpoints: Keep last 30 days
- DLQ: Keep 7 days (TTL-expired automatically)
- Audit logs: Keep 90 days (when enabled)

---

## Migration from v1.x

### Mappings (Deprecated)

Old: Standalone mapping documents in `mappings` collection

New: Embedded in `job.mapping` field

```python
# v1.x
mapping_doc = store.load_mapping("hotel_mapping")

# v2.x
job_doc = store.load_job(job_id)
mapping = job_doc.get("mapping", {})
```

### Settings Migration

Old: Global settings document

New: Split into:
- `config` — system settings
- `jobs` — job definitions
- `inputs_changes` — input sources
- `outputs_*` — output configs

---

## Security Considerations

1. **Credentials**: Encrypt username/password fields in transit and at rest
2. **Tokens**: Hash session tokens, use short TTL
3. **Audit**: Enable audit logging for production
4. **Validation**: Always validate documents against schema
5. **Access Control**: Use role-based access (future: users/sessions)

---

## References

- **Schema Files**: `json_schema/changes-worker/{collection}/schema.json`
- **README**: `json_schema/README.md`
- **Implementation**: `cbl_store.py`
- **API**: `rest/api_v2.py`

