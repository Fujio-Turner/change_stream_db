# Cloud Blob Storage Output – Design Plan

This document outlines the design for forwarding Couchbase `_changes` feed documents into cloud blob/object stores (AWS S3, Google Cloud Storage, Azure Blob Storage) as an alternative output mode alongside the existing REST/HTTP, stdout, and RDBMS outputs.

**Related docs:**
- [`DESIGN.md`](DESIGN.md) – Overall pipeline architecture, failure modes, DLQ
- [`RDBMS_PLAN.md`](RDBMS_PLAN.md) – RDBMS output architecture (same pattern we follow here)
- [`RDBMS_IMPLEMENTATION.md`](RDBMS_IMPLEMENTATION.md) – Implementation guide for DB forwarders
- [`ADMIN_UI.md`](ADMIN_UI.md) – Dashboard, config editor UI

---

## Goal

The changes_worker already consumes the `_changes` feed and forwards each document to a REST endpoint (`rest/` module), stdout, or an RDBMS (`db/` module). The goal is to add **cloud blob storage output** so the same feed can write each document (or batch of documents) as objects in a cloud object store — no intermediate REST service required.

```
┌──────────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│  Sync Gateway /      │         │                  │         │  REST endpoint      │
│  App Services /      │ ──GET── │  changes_worker  │ ──PUT── │  (rest/ module)     │
│  Edge Server         │ _changes│                  │         └─────────────────────┘
│                      │ ◄─JSON─ │                  │
│  /{db}.{scope}.      │         │                  │         ┌─────────────────────┐
│   {collection}/      │         │                  │ ──SQL── │  RDBMS              │
│   _changes           │         │                  │         │  (db/ module)       │
└──────────────────────┘         │                  │         └─────────────────────┘
                                 │                  │
                                 │                  │         ┌─────────────────────┐
                                 │                  │ ──PUT── │  Cloud Blob Storage │
                                 │                  │         │  (cloud/ module)    │
                                 └──────────────────┘         │  AWS S3         ✅  │
                                                              │  GCS            ⬜  │
                                                              │  Azure Blob     ⬜  │
                                                              └─────────────────────┘
```

### Why cloud blob storage?

| Use Case | Description |
|---|---|
| **Data lake ingestion** | Land every change as a JSON object in S3/GCS/Azure for downstream Spark, Athena, BigQuery, or Synapse queries. |
| **Archival / compliance** | Write an immutable copy of every document version for audit trails. |
| **Cross-region replication** | Use cloud-native replication (S3 CRR, GCS dual-region) for disaster recovery. |
| **Decoupled consumers** | Multiple downstream systems read from the bucket at their own pace — no backpressure on the changes_worker. |
| **Cost-effective storage** | Object stores are cheap at scale — pennies per GB/month with lifecycle policies for tiering. |

---

## Architecture

### Module Layout

```
cloud/
├── __init__.py              # Factory function (create_cloud_output)
├── cloud_base.py            # Abstract base class (BaseCloudForwarder)
├── cloud_s3.py              # AWS S3 output (boto3)        ✅ implement first
├── cloud_gcs.py             # Google Cloud Storage output   ⬜ planned
└── cloud_azure.py           # Azure Blob Storage output     ⬜ planned
```

Each `cloud_*.py` module implements a common interface so the changes_worker can swap cloud targets via config without changing the core loop — same pattern as `db/db_base.py` → `db/db_postgres.py`.

### Common Interface

The base class (`BaseCloudForwarder` in `cloud/cloud_base.py`) defines the interface that all cloud modules must follow. It mirrors the `BaseOutputForwarder` from `db/db_base.py`:

```python
class BaseCloudForwarder(abc.ABC):
    """Abstract async cloud blob storage output forwarder."""

    def __init__(self, out_cfg: dict, dry_run: bool = False, metrics=None):
        """
        Args:
            out_cfg:  The full output config dict (contains 'cloud' key).
            dry_run:  If True, log operations but don't execute.
            metrics:  MetricsCollector instance (optional).
        """

    async def connect(self) -> None:
        """Initialize the cloud client and validate credentials/bucket access."""

    async def send(self, doc: dict, method: str = "PUT") -> dict:
        """
        Write a single document to the cloud store as a JSON object.

        - method="PUT"    → Upload object (create or overwrite)
        - method="DELETE" → Delete the object from the bucket

        Returns: {"ok": bool, "doc_id": str, "key": str, ...}
        """

    async def test_reachable(self) -> bool:
        """Verify the bucket is accessible (used by --test)."""

    async def close(self) -> None:
        """Close/cleanup the cloud client."""

    def log_stats(self) -> None:
        """Log accumulated write statistics."""
```

---

## Object Key Strategy

Each document uploaded to the cloud store needs a **unique, deterministic object key**. The key template is user-configurable via `key_template` in the config:

### Key Template Syntax

```
{prefix}/{doc_id}.json
{prefix}/{scope}/{collection}/{doc_id}/{rev}.json
{prefix}/{doc_id}_{timestamp}.json
{prefix}/{year}/{month}/{day}/{doc_id}.json
```

#### Available Template Variables

| Variable | Description | Example |
|---|---|---|
| `{doc_id}` | Document ID (`_id` field) | `p:12345` |
| `{rev}` | Document revision | `4-abc123` |
| `{seq}` | Sequence number from the `_changes` feed | `42` |
| `{timestamp}` | Unix epoch seconds | `1768521600` |
| `{iso_date}` | ISO 8601 date-time | `2026-04-17T12:30:00Z` |
| `{year}` | 4-digit year | `2026` |
| `{month}` | 2-digit month | `04` |
| `{day}` | 2-digit day | `17` |
| `{scope}` | Couchbase scope name (from gateway config) | `us` |
| `{collection}` | Couchbase collection name (from gateway config) | `prices` |
| `{database}` | Couchbase database name (from gateway config) | `db` |
| `{prefix}` | The `key_prefix` config value | `couchdb-changes` |

### Default Key Template

```
{prefix}/{doc_id}.json
```

This produces keys like `couchdb-changes/p:12345.json`. Simple, deterministic, and easy to query.

### Key Sanitization

Document IDs may contain characters that are problematic for object keys (`:`, `/`, spaces). The forwarder sanitizes by:
- Replacing `:` with `_` (configurable)
- URL-encoding other special characters
- Configurable via `key_sanitize: true|false`

---

## Config Changes

Cloud storage gets its own `output.mode` value (`"s3"`, `"gcs"`, `"azure"`) with a corresponding config block under the `output` key. This follows the same pattern as RDBMS engines (`output.postgres`, `output.mysql`, etc.).

### AWS S3

```jsonc
{
  "output": {
    "mode": "s3",                           // "stdout" | "http" | "postgres" | "s3" | "gcs" | "azure"
    "s3": {
      "bucket": "my-changes-bucket",
      "region": "us-east-1",               // AWS region
      "key_prefix": "couchdb-changes",     // prefix prepended to all object keys
      "key_template": "{prefix}/{doc_id}.json",  // object key template
      "key_sanitize": true,                // sanitize doc_id for object key safety
      "content_type": "application/json",  // Content-Type header on uploaded objects
      "storage_class": "",                 // e.g., "STANDARD", "INTELLIGENT_TIERING", "GLACIER" (empty = bucket default)
      "server_side_encryption": "",        // e.g., "AES256", "aws:kms" (empty = none)
      "kms_key_id": "",                    // KMS key ARN (only when server_side_encryption = "aws:kms")
      "metadata": {},                      // custom x-amz-meta-* headers (key-value pairs)
      "endpoint_url": "",                  // custom S3 endpoint (for MinIO, LocalStack, etc.)
      "access_key_id": "",                 // explicit credentials (empty = use default chain)
      "secret_access_key": "",             // explicit credentials (empty = use default chain)
      "session_token": "",                 // for temporary credentials / STS
      "max_retries": 3,                    // client-level retry count
      "backoff_base_seconds": 0.5,
      "backoff_max_seconds": 10
    },
    "halt_on_failure": true,
    "dead_letter_path": "failed_docs.jsonl"
  }
}
```

#### Authentication Priority (AWS S3)

1. **Explicit credentials** in config (`access_key_id` / `secret_access_key`) — useful for Docker, non-AWS environments, MinIO
2. **Environment variables** (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) — standard in CI/CD
3. **IAM instance profile / task role** — the recommended approach for EC2, ECS, Lambda
4. **Default credential chain** (boto3 automatically searches `~/.aws/credentials`, env vars, instance metadata)

The forwarder uses boto3's built-in credential resolution. If explicit credentials are provided in config, they're passed to `boto3.client()`. Otherwise, boto3 discovers credentials automatically.

### Google Cloud Storage (GCS)

```jsonc
{
  "output": {
    "mode": "gcs",
    "gcs": {
      "bucket": "my-changes-bucket",
      "project": "",                       // GCP project ID (empty = infer from credentials)
      "key_prefix": "couchdb-changes",
      "key_template": "{prefix}/{doc_id}.json",
      "key_sanitize": true,
      "content_type": "application/json",
      "storage_class": "",                 // e.g., "STANDARD", "NEARLINE", "COLDLINE"
      "credentials_json": "",              // path to service account JSON (empty = use default)
      "max_retries": 3,
      "backoff_base_seconds": 0.5,
      "backoff_max_seconds": 10
    },
    "halt_on_failure": true
  }
}
```

#### Authentication Priority (GCS)

1. **Service account JSON** path in config (`credentials_json`)
2. **`GOOGLE_APPLICATION_CREDENTIALS`** environment variable
3. **Application Default Credentials** (ADC) — workload identity on GKE, attached service accounts on GCE/Cloud Run

### Azure Blob Storage

```jsonc
{
  "output": {
    "mode": "azure",
    "azure": {
      "container": "my-changes-container",  // Azure calls them "containers" not "buckets"
      "account_name": "",                    // storage account name
      "account_url": "",                     // full URL: https://<account>.blob.core.windows.net
      "key_prefix": "couchdb-changes",
      "key_template": "{prefix}/{doc_id}.json",
      "key_sanitize": true,
      "content_type": "application/json",
      "access_tier": "",                     // e.g., "Hot", "Cool", "Archive" (empty = account default)
      "connection_string": "",               // full connection string (takes precedence)
      "account_key": "",                     // storage account key
      "sas_token": "",                       // shared access signature token
      "max_retries": 3,
      "backoff_base_seconds": 0.5,
      "backoff_max_seconds": 10
    },
    "halt_on_failure": true
  }
}
```

#### Authentication Priority (Azure)

1. **Connection string** in config (`connection_string`) — contains everything needed
2. **Account key** (`account_name` + `account_key`) — simple, works everywhere
3. **SAS token** (`account_url` + `sas_token`) — time-limited, scoped permissions
4. **DefaultAzureCredential** (falls back to managed identity, az cli, env vars)

---

## How It Plugs Into changes_worker

### Factory Function

`cloud/__init__.py` exposes a factory for creating the right cloud forwarder based on the configured mode:

```python
def create_cloud_output(out_cfg: dict, dry_run: bool = False, metrics=None):
    mode = out_cfg.get("mode", "")
    if mode == "s3":
        from .cloud_s3 import S3OutputForwarder
        return S3OutputForwarder(out_cfg, dry_run, metrics=metrics)
    elif mode == "gcs":
        from .cloud_gcs import GCSOutputForwarder
        return GCSOutputForwarder(out_cfg, dry_run, metrics=metrics)
    elif mode == "azure":
        from .cloud_azure import AzureOutputForwarder
        return AzureOutputForwarder(out_cfg, dry_run, metrics=metrics)
    else:
        raise ValueError(f"Unknown cloud output mode: {mode}")
```

### Integration in `main.py`

The `poll_changes()` function in `main.py` currently routes output based on `output.mode`. Cloud modes are added to the same routing block:

```python
# In poll_changes():
output_mode = out_cfg.get("mode", "stdout")

if output_mode == "db":
    # existing RDBMS routing ...
elif output_mode in ("s3", "gcs", "azure"):
    from cloud import create_cloud_output
    output = create_cloud_output(out_cfg, dry_run, metrics=metrics)
    await output.connect()
    cloud_output = output
    log_event(logger, "info", "OUTPUT", f"cloud output ready (provider={output_mode})")
else:
    # existing HTTP/stdout routing ...
    output = OutputForwarder(session, out_cfg, dry_run, ...)
```

The rest of the loop (`process_one()`, checkpoint logic, DLQ, `halt_on_failure`) stays **completely unchanged** — it just calls `output.send(doc, method)` regardless of the output type.

---

## Engine-Specific Notes

### AWS S3 (`cloud_s3.py`) — ✅ Implement First

- **Library:** `boto3` (AWS SDK for Python)
- **Core call:** `s3.put_object(Bucket=..., Key=..., Body=..., ContentType=...)`
- **Delete call:** `s3.delete_object(Bucket=..., Key=...)`
- **Connection test:** `s3.head_bucket(Bucket=...)` — verifies bucket exists and credentials work
- **Async strategy:** boto3 is synchronous. Use `asyncio.get_event_loop().run_in_executor(None, ...)` to avoid blocking the event loop. (Alternative: `aioboto3` wraps boto3 with native async — evaluate if it's worth the extra dependency.)
- **S3-compatible stores:** Works with MinIO, LocalStack, Ceph, etc. by setting `endpoint_url` in config. No code changes needed.
- **Default port:** N/A (HTTPS)

#### Why boto3 Instead of Raw HTTP

While S3 is a REST API and we could use `aiohttp` directly with AWS Signature V4 signing, boto3 provides:
- Automatic credential discovery (IAM roles, env vars, config files)
- Request signing (SigV4)
- Automatic retries with exponential backoff
- Multipart upload for large objects
- Consistent error handling

The added dependency is justified — boto3 is the de-facto standard for any Python↔AWS interaction.

### Google Cloud Storage (`cloud_gcs.py`) — ⬜ Planned

- **Library:** `google-cloud-storage` (official GCS client)
- **Core call:** `bucket.blob(key).upload_from_string(data, content_type=...)`
- **Delete call:** `bucket.blob(key).delete()`
- **Connection test:** `bucket.exists()`
- **Async strategy:** The `google-cloud-storage` library is synchronous. Use `run_in_executor()`, or evaluate the async-native `gcloud-aio-storage` package.
- **Note:** GCS has an S3-compatible API (interoperability mode), so users can also use the S3 forwarder with `endpoint_url: "https://storage.googleapis.com"` and HMAC keys. However, the native GCS client provides better integration with IAM, workload identity, and GCS-specific features.

### Azure Blob Storage (`cloud_azure.py`) — ⬜ Planned

- **Library:** `azure-storage-blob` (official Azure SDK)
- **Core call:** `container_client.upload_blob(name=key, data=..., content_type=..., overwrite=True)`
- **Delete call:** `container_client.delete_blob(name=key)`
- **Connection test:** `container_client.get_container_properties()`
- **Async strategy:** Azure SDK provides native async support via `aio` submodule — `from azure.storage.blob.aio import ContainerClient`.
- **Note:** Azure uses "containers" instead of "buckets" and "blobs" instead of "objects". The config and code use Azure-native terminology.

---

## Failure Handling

Cloud output follows the **same failure semantics** as HTTP and RDBMS output. The existing `halt_on_failure` and DLQ logic in the main loop applies unchanged:

| Failure | `halt_on_failure=true` | `halt_on_failure=false` |
|---|---|---|
| **Auth failure** (invalid credentials, expired token) | Stop batch, hold checkpoint | DLQ, skip |
| **Bucket/container not found** | Stop batch, hold checkpoint | DLQ, skip |
| **Network error** (timeout, DNS) | Retry with backoff, then stop | Retry, then DLQ |
| **Rate limiting** (429 / SlowDown) | Retry with backoff, then stop | Retry, then DLQ |
| **Server error** (5xx, ServiceUnavailable) | Retry with backoff, then stop | Retry, then DLQ |
| **Access denied** (403) | Stop batch, hold checkpoint | DLQ, skip |
| **Object too large** (>5GB single PUT) | Stop batch, hold checkpoint | DLQ, skip |

### Error Classification

```python
def _is_transient(self, exc: Exception) -> bool:
    """Classify cloud errors as transient (retryable) or permanent."""
    # S3: ClientError with code in (RequestTimeout, SlowDown, 500, 503)
    # GCS: ServiceUnavailable, TooManyRequests
    # Azure: HttpResponseError with status 429, 500, 503
    ...

def _error_class(self, exc: Exception) -> str:
    """Return a short classification string for metrics."""
    # "auth", "not_found", "access_denied", "rate_limit",
    # "server_error", "connection", "client_error"
    ...
```

### Retry Strategy

Each cloud forwarder has its own retry logic with exponential backoff, matching the `db/db_base.py` pattern:

```python
for attempt in range(1, max_retries + 1):
    try:
        await self._upload_object(key, body)
        return {"ok": True, ...}
    except TransientError:
        delay = min(backoff_base * (2 ** (attempt - 1)), backoff_max)
        await asyncio.sleep(delay)
```

**Note:** boto3 also has its own built-in retry logic (configurable via `botocore.config.Config(retries=...)`). We disable or limit boto3's internal retries to avoid double-retry (our retry wraps boto3's). The forwarder's retry is the authoritative one, consistent with the RDBMS retry pattern.

---

## DELETE Handling

When `method="DELETE"` (the document was deleted in Couchbase):

| Strategy | Config Value | Behavior |
|---|---|---|
| **Delete object** (default) | `on_delete: "delete"` | Delete the object from the bucket. |
| **Upload tombstone** | `on_delete: "tombstone"` | Upload a tombstone JSON object: `{"_id": "...", "_deleted": true, "_rev": "...", "deleted_at": "..."}` |
| **Ignore** | `on_delete: "ignore"` | Skip — do nothing. (Use when `ignore_delete: true` is already set in `processing`.) |

Default: `"delete"` — the object is removed from the bucket.

---

## Metrics

New Prometheus metrics for cloud output (extending the existing `MetricsCollector`):

| Metric | Type | Description |
|---|---|---|
| `changes_worker_cloud_uploads_total` | counter | Total object uploads (PUT) |
| `changes_worker_cloud_deletes_total` | counter | Total object deletions |
| `changes_worker_cloud_errors_total` | counter | Total cloud write errors |
| `changes_worker_cloud_upload_time_seconds` | summary | Upload latency |
| `changes_worker_cloud_bytes_uploaded_total` | counter | Total bytes sent to cloud store |
| `changes_worker_cloud_retries_total` | counter | Total retries on transient errors |

Per-engine/per-job labels follow the `DbMetrics` pattern from `db/db_base.py`:

```promql
changes_worker_cloud_uploads_total{provider="s3",job_id="orders_archive"} 50000
changes_worker_cloud_upload_time_seconds{provider="s3",job_id="orders_archive",quantile="0.99"} 0.150
```

---

## Dependencies (additions to `requirements.txt`)

```txt
# Cloud blob storage drivers (install the one you need):
#   pip install boto3                  # for S3 output (AWS, MinIO, S3-compatible)
#   pip install google-cloud-storage   # for GCS output
#   pip install azure-storage-blob     # for Azure Blob Storage output
```

Startup validation checks that the required SDK is installed. `S3OutputForwarder.__init__()` raises `RuntimeError` if `boto3` is not importable — same pattern as `db_postgres.py` checking for `asyncpg`.

---

## Implementation Order

### Phase 1: S3 Foundation (This PR / Chat)

1. ✅ `cloud/__init__.py` — Factory function + cloud mode constants
2. ✅ `cloud/cloud_base.py` — Abstract base class with shared logic (key templating, serialization, retry, metrics proxy)
3. ✅ `cloud/cloud_s3.py` — AWS S3 output forwarder using boto3
4. ✅ Integration into `main.py` — Add `s3` to the output mode routing
5. ✅ Config schema — Add `s3` block to `config.json`
6. ✅ Update `requirements.txt` — Add boto3 as optional dependency
7. ✅ Tests — Unit tests for key templating, serialization, send/delete, error classification

### Phase 2: GCS + Azure

8. ⬜ `cloud/cloud_gcs.py` — Google Cloud Storage forwarder
9. ⬜ `cloud/cloud_azure.py` — Azure Blob Storage forwarder
10. ⬜ Admin UI — Cloud provider config fields, credential validation

### Phase 3: Advanced Features

11. ⬜ Batch upload — Accumulate N docs into a single NDJSON object (reduces API calls)
12. ⬜ Multipart upload — For very large objects (>5GB)
13. ⬜ Lifecycle policy helper — Generate suggested lifecycle rules (JSON/YAML) for archival tiering

---

## Adding a New Cloud Provider

Use `cloud/cloud_s3.py` as the starting point. The steps are:

### 1. Copy the forwarder class

Copy `cloud_s3.py` to `cloud_<provider>.py` and rename the class (e.g., `GCSOutputForwarder`). Update the constructor to read from the provider-specific config key (e.g., `out_cfg.get("gcs", {})`).

### 2. Replace the SDK

Swap `boto3` for the provider's SDK:

| Provider | SDK | Upload Call |
|---|---|---|
| AWS S3 | `boto3` | `s3.put_object(Bucket=..., Key=..., Body=...)` |
| GCS | `google-cloud-storage` | `blob.upload_from_string(data)` |
| Azure | `azure-storage-blob` | `container.upload_blob(name=..., data=...)` |

Update `connect()`, `close()`, `send()`, and `test_reachable()` to use the new SDK.

### 3. Implement error classification

Each SDK has its own exception hierarchy:

| Provider | Transient Errors | Permanent Errors |
|---|---|---|
| S3 | `ClientError(RequestTimeout, SlowDown, 500, 503)` | `ClientError(AccessDenied, NoSuchBucket, 400)` |
| GCS | `ServiceUnavailable, TooManyRequests` | `NotFound, Forbidden, BadRequest` |
| Azure | `HttpResponseError(429, 500, 503)` | `HttpResponseError(403, 404, 400)` |

### 4. Add the SDK to `requirements.txt`

Add a commented entry following the existing pattern.

### 5. Register in `create_cloud_output()`

Add the new provider to the factory function in `cloud/__init__.py`.

### 6. Add driver detection in `web/server.py`

Update `_detect_db_drivers()` (or add a `_detect_cloud_drivers()`) so the Admin UI can show which SDKs are installed.

---

## S3-Compatible Stores (MinIO, LocalStack, Ceph)

The S3 forwarder works with any S3-compatible store out of the box. The only config difference is `endpoint_url`:

### MinIO

```jsonc
{
  "output": {
    "mode": "s3",
    "s3": {
      "bucket": "my-bucket",
      "endpoint_url": "http://minio:9000",        // MinIO endpoint
      "access_key_id": "minioadmin",
      "secret_access_key": "minioadmin",
      "region": "us-east-1"                        // required by boto3, any value works
    }
  }
}
```

### LocalStack (Local Development / Testing)

```jsonc
{
  "output": {
    "mode": "s3",
    "s3": {
      "bucket": "test-bucket",
      "endpoint_url": "http://localhost:4566",
      "access_key_id": "test",
      "secret_access_key": "test",
      "region": "us-east-1"
    }
  }
}
```

No code changes needed — boto3 handles all S3-compatible endpoints via `endpoint_url`.

---

## Batching (Implemented)

Sending individual small documents (e.g., 512 bytes) as separate S3 PUTs is expensive — S3 charges $5/million PUTs. The forwarder supports **optional batching** that accumulates documents into a single NDJSON object, flushing when any of three thresholds is hit:

| Threshold | Config Key | Default | Description |
|---|---|---|---|
| **Document count** | `batch.max_docs` | 100 | Flush after N documents |
| **Byte size** | `batch.max_bytes` | 1,048,576 (1 MB) | Flush when accumulated JSON exceeds this |
| **Time window** | `batch.max_seconds` | 5.0 | Flush after N seconds since first doc in buffer |

Whichever threshold is hit first triggers a flush. The batch is uploaded as a single `.ndjson` object (one JSON document per line).

### Batch Config

```jsonc
"s3": {
  "batch": {
    "enabled": false,       // false = one S3 PUT per doc (default)
    "max_docs": 100,        // flush after 100 docs
    "max_bytes": 1048576,   // flush after 1 MB
    "max_seconds": 5.0      // flush after 5 seconds
  }
}
```

### When to Use Batching

| Scenario | Recommendation |
|---|---|
| Small docs (< 1 KB), high volume | **Enable batching** — saves on PUT costs |
| Large docs (> 10 KB) | **Disable** — each doc is already a reasonable PUT |
| Real-time downstream consumers | **Disable** — batching adds up to `max_seconds` latency |
| Data lake / analytics workload | **Enable** — downstream tools (Athena, Spark) prefer fewer, larger files |

---

## Open Questions

- **Object versioning:** If the bucket has versioning enabled, overwrites create new versions automatically. Should the forwarder detect this and adjust behavior (e.g., skip `on_delete: "delete"` because versions are preserved)? Recommendation: no — let the user configure their bucket policy separately.
- **Async SDK:** S3 uses `boto3` + `run_in_executor` (boto3 is sync). `aioboto3` is an alternative but adds a dependency and may have compatibility gaps. `run_in_executor` is proven and matches the RDBMS pattern.

---

## File Changelist Summary

These are all the files that will be created or modified across the implementation phases:

### New Files

| File | Description |
|---|---|
| `cloud/__init__.py` | Factory function `create_cloud_output()` + cloud mode constants |
| `cloud/cloud_base.py` | Abstract base class with shared logic (key template, retry, metrics) |
| `cloud/cloud_s3.py` | AWS S3 output forwarder using boto3 |
| `cloud/cloud_gcs.py` | GCS output forwarder (Phase 2) |
| `cloud/cloud_azure.py` | Azure Blob Storage output forwarder (Phase 2) |
| `tests/test_cloud_s3.py` | Unit tests for S3 forwarder |
| `tests/test_cloud_base.py` | Unit tests for key templating, base class logic |

### Modified Files

| File | Change |
|---|---|
| `main.py` | Add `s3`/`gcs`/`azure` to output mode routing in `poll_changes()`, cleanup on shutdown |
| `config.json` | Add `output.s3` block (example config) |
| `requirements.txt` | Add `boto3`, `google-cloud-storage`, `azure-storage-blob` as optional deps |
| `docs/DESIGN.md` | Add cloud output to the RIGHT stage description, data flow diagram, failure table |
| `web/server.py` | Add cloud SDK detection for Admin UI (Phase 2) |
