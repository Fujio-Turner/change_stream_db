# JSON Schema Documentation

This directory contains comprehensive JSON Schema definitions for all collections in the `changes-worker` scope of the Couchbase Lite database.

## Overview

- **Scope**: `changes-worker`
- **Format**: [JSON Schema 2020-12](https://json-schema.org/)
- **Base Directory**: `json_schema/changes-worker/`

## Collections

### Core Pipeline Collections

#### 1. **inputs_changes** 
**Location**: `json_schema/changes-worker/inputs_changes/schema.json`

Defines input sources feeding into the pipeline. Manages data sources like:
- Couchbase Sync Gateway
- Couchbase App Services
- Couchbase Edge Server
- CouchDB

**Key Fields**:
- `id`: Unique source identifier
- `source_type`: Type of data source (sync_gateway, app_services, edge_server, couchdb)
- `host`: Source hostname
- `database`, `scope`, `collection`: Target location
- `username`, `password`: Authentication
- `ssl`: Enable TLS
- `continuous`: Enable change feeds

---

#### 2. **outputs_rdbms**
**Location**: `json_schema/changes-worker/outputs_rdbms/schema.json`

Defines relational database output destinations:
- MySQL
- PostgreSQL
- MSSQL
- Oracle

**Key Fields**:
- `id`: Output identifier
- `database_type`: RDBMS type
- `host`, `port`, `database`: Connection info
- `schema`: Database schema
- `username`, `password`: Credentials
- `pool_size`, `timeout_seconds`: Performance tuning
- `batch_size`: Batch insert size

---

#### 3. **outputs_http**
**Location**: `json_schema/changes-worker/outputs_http/schema.json`

Defines HTTP/REST endpoints for pushing data.

**Key Fields**:
- `id`: Endpoint identifier
- `url`: HTTP endpoint URL
- `method`: HTTP verb (POST, PUT, PATCH)
- `headers`: Custom HTTP headers
- `auth_type`: Authentication method (none, basic, bearer, api_key)
- `timeout_seconds`, `retry_count`: Reliability settings
- `batch_size`: Documents per request

---

#### 4. **outputs_cloud**
**Location**: `json_schema/changes-worker/outputs_cloud/schema.json`

Defines cloud storage destinations:
- AWS S3
- Google Cloud Storage (GCS)
- Azure Blob Storage
- Oracle Cloud Storage (OCS)

**Key Fields**:
- `id`: Storage identifier
- `cloud_provider`: Provider type
- `bucket`: Bucket/container name
- `region`: Cloud region
- `prefix`: Key/path prefix
- `access_key`, `secret_key`: Credentials
- `format`: Data format (json, jsonl, parquet, csv, avro)
- `compression`: Algorithm (none, gzip, snappy, brotli)
- `batch_size`: Documents per upload

---

#### 5. **tables_rdbms**
**Location**: `json_schema/changes-worker/tables_rdbms/schema.json`

Reusable library of RDBMS table definitions. Stores raw DDL and parsed column metadata. Tables are copied into jobs on selection — the job owns its copy.

**Key Fields**:
- `id`: Unique table definition ID (e.g., `tbl-orders`)
- `name`: SQL table name in the target database
- `engine_hint`: Which RDBMS engine the DDL targets (postgres, mysql, mssql, oracle)
- `sql`: Raw `CREATE TABLE` DDL statement
- `columns[]`: Parsed column definitions (name, type, primary_key, nullable)
- `parent_table`: ID of parent table for FK relationships
- `foreign_key`: FK column, referenced table, referenced column
- `meta.source`: How the definition was created (ddl_upload, db_introspect, manual, migration_v1)

---

#### 6. **jobs**
**Location**: `json_schema/changes-worker/jobs/schema.json`

Defines data pipeline jobs connecting inputs to outputs.

**Key Fields**:
- `id`: UUID job identifier
- `name`: Human-readable name
- `inputs`: Array of input source definitions
- `outputs`: Array of output destination definitions
- `output_type`: Destination type (rdbms, http, cloud)
- `mapping`: Schema transformation rules
- `state`: Job execution state (idle, running, paused, stopped, error)
- `system`: System-level configuration
- `enabled`: Whether job is active

---

### Runtime Collections

#### 7. **checkpoints**
**Location**: `json_schema/changes-worker/checkpoints/schema.json`

Tracks the last processed sequence number for resuming change feeds.

**Key Fields**:
- `client_id`: Job or client identifier
- `SGs_Seq`: Last processed sequence number
- `time`: Checkpoint timestamp
- `remote`: Remote counter

---

#### 8. **dlq** (Dead Letter Queue)
**Location**: `json_schema/changes-worker/dlq/schema.json`

Stores documents that failed processing for later retry or analysis.

**Key Fields**:
- `doc_id_original`: Original document ID
- `seq`: Source sequence number
- `method`: HTTP method attempted
- `status`: HTTP status code
- `error`: Error message
- `reason`: Failure category (timeout, validation_error, 4xx, 5xx)
- `time`: Failure timestamp
- `expires_at`: TTL expiration
- `retried`, `replay_attempts`: Retry tracking
- `doc_data`: JSON of original document

---

#### 9. **data_quality**
**Location**: `json_schema/changes-worker/data_quality/schema.json`

Tracks data quality metrics and anomalies.

**Key Fields**:
- `job_id`: Associated job
- `metrics`: Quality measurements (valid_records, invalid_records, duplicates, etc.)
- `anomalies`: Detected issues with severity
- `validation_rules_applied`: Rules used for validation

---

#### 10. **enrichments**
**Location**: `json_schema/changes-worker/enrichments/schema.json`

Stores enrichment rules and transformation metadata.

**Key Fields**:
- `id`: Enrichment identifier
- `name`: Enrichment name
- `enrichment_type`: Type (lookup_table, javascript_function, sql_query, api_call, groovy_script, semantic_mapping)
- `source_field`, `target_field`: Field mapping
- `rule_config`: Type-specific configuration
- `lookup_table`: Lookup mappings
- `enabled`: Whether active

---

### Infrastructure Collections

#### 11. **config**
**Location**: `json_schema/changes-worker/config/schema.json`

Global system configuration.

**Key Fields**:
- `logging`: Log level, format, output
- `database`: Connection pool settings
- `performance`: Batch size, timeouts, worker threads
- `security`: SSL/TLS, encryption keys
- `monitoring`: Metrics and health checks
- `dlq`: DLQ settings (TTL, max retries)

---

### Auth & Identity Collections (Future)

#### 12. **users**
**Location**: `json_schema/changes-worker/users/schema.json`

User accounts for authentication and access control.

**Key Fields**:
- `username`: Unique login name
- `email`: Contact email
- `password_hash`: Hashed password
- `roles`: Assigned roles (admin, operator, viewer, analyst)
- `permissions`: Explicit permissions
- `active`: Account status

---

#### 13. **sessions**
**Location**: `json_schema/changes-worker/sessions/schema.json`

User session tokens and authentication state.

**Key Fields**:
- `token`: JWT or opaque token
- `username`: Associated user
- `ip_address`, `user_agent`: Session context
- `created_at`, `expires_at`: Session lifetime
- `active`, `revoked`: Session status

---

### Observability Collections (Future)

#### 14. **audit_log**
**Location**: `json_schema/changes-worker/audit_log/schema.json`

Audit trail for tracking changes and operations.

**Key Fields**:
- `timestamp`: Event time
- `user`: Actor username
- `action`: Operation type (create, read, update, delete, start, stop, etc.)
- `resource_type`, `resource_id`: Affected resource
- `status`: Success/failure
- `details`: Additional context
- `ip_address`, `session_id`: Session info

---

#### 15. **notifications**
**Location**: `json_schema/changes-worker/notifications/schema.json`

System notifications and alerts.

**Key Fields**:
- `id`: Notification identifier
- `title`, `message`: Notification content
- `severity`: Level (info, warning, error, critical)
- `category`: Type (job_status, data_quality, system_health, security, user_action)
- `related_resource_type`, `related_resource_id`: Associated resource
- `read`: Read status
- `recipients`: Target users
- `delivery_status`: Per-channel status

---

### Legacy Collections

#### 16. **mappings** (Deprecated)
**Location**: `json_schema/changes-worker/mappings/schema.json`

Legacy schema mapping. **Deprecated in v2.0** — mappings now embedded in jobs.

---

## Usage

### Validation

You can use these schemas to validate documents using any JSON Schema validator:

```python
import jsonschema
import json

# Load schema
with open('json_schema/changes-worker/jobs/schema.json') as f:
    schema = json.load(f)

# Validate document
doc = {
    "type": "job",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "My Job",
    "inputs": [...],
    "outputs": [...],
    "output_type": "rdbms",
    "state": {"status": "running"}
}

jsonschema.validate(instance=doc, schema=schema)
```

### IDE Integration

Most modern IDEs support JSON Schema:
- **VS Code**: Use `"$schema"` in your JSON files or configure schema mappings
- **IntelliJ IDEA**: Automatically recognizes `$schema` field
- **PyCharm**: Supports JSON Schema validation

### Documentation Generation

These schemas can be used to auto-generate documentation:

```bash
# Example using json-schema-to-markdown or similar tools
json-schema-to-markdown json_schema/changes-worker/jobs/schema.json
```

---

## Schema Structure

Each collection has a schema file at:
```
json_schema/changes-worker/{collection_name}/schema.json
```

### Common Schema Properties

All schemas include:
- `$schema`: JSON Schema version (2020-12)
- `$id`: Unique schema identifier
- `title`: Human-readable title
- `description`: Detailed description
- `type`: Document type (object)
- `properties`: Field definitions
- `required`: Mandatory fields
- `examples`: Sample documents
- `additionalProperties`: Allow extra fields (true)

---

## Best Practices

1. **Always specify `type`**: Each document must have a `type` field matching its collection
2. **Validate on insert**: Validate documents before saving to CBL
3. **Use UUIDs for IDs**: Use UUID v4 for job and resource IDs
4. **Timestamps**: Use ISO 8601 format for dates, Unix timestamps for performance
5. **Sensitive data**: Encrypt passwords and tokens in storage
6. **Document versioning**: Version your schemas as they evolve

---

## Future Enhancements

- Add conditional schemas for different deployment types
- Add performance profiles (dev, staging, production)
- Add data lifecycle policies
- Add field-level encryption specifications
- Add audit trail for schema changes

---

## Support

For questions about schema structure or field definitions, refer to:
- `cbl_store.py`: Core CBL storage implementation
- `rest/api_v2.py`: REST API handlers for document validation
- Python code for actual field usage patterns

