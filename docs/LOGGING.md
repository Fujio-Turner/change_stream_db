# Logging Guide

This document describes the logging architecture, conventions, and best practices
used throughout the **changes_worker** project.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Application Code                       │
│                                                          │
│   ic(value)              log_event(logger, level, ...)   │
│       │                          │                        │
│       ▼                          ▼                        │
│   TRACE level             Structured log_key + fields     │
│   via ic.configureOutput  via LogKeyLevelFilter           │
│       │                          │                        │
│       └──────────┬───────────────┘                        │
│                  ▼                                        │
│          RedactingFormatter                               │
│          (sensitive data redaction)                       │
│                  │                                        │
│         ┌────────┴────────┐                               │
│         ▼                 ▼                               │
│   Console Handler    ManagedRotatingFileHandler           │
│   (StreamHandler)    (logs/ directory, auto-rotation)     │
└──────────────────────────────────────────────────────────┘
```

All logging is managed by the `pipeline_logging` module, which is inspired by
Couchbase Sync Gateway's logging configuration.  Every log record flows through
the same pipeline: **level check → log_key filter → redaction → output**.

---

## Two Logging Primitives

The project uses exactly **two** mechanisms for emitting log messages.
Every new module should use both.

### 1. `ic()` — Developer Traces (TRACE level)

[IceCream](https://github.com/gruns/icecream) is configured at startup to route
its output through the standard `logging.Logger` at a custom **TRACE** level
(numeric value `5`, below `DEBUG`).

```python
from icecream import ic

# TRACE-level breadcrumbs for developer debugging
ic("checkpoint load", url)
ic(self._uuid, self._local_doc_id, raw)
ic(changes_url, params, since)
```

**When to use `ic()`:**

- Tracing code paths and control flow (`ic("send", method, doc_id, url)`)
- Dumping internal state for debugging (`ic(cfg)`, `ic(self._uuid)`)
- Confirming which branch was taken (`ic("test_reachable: OK", resp.status)`)
- Logging values *before* an operation starts and *after* it completes

**Safe import pattern** (for modules that may be imported without icecream):

```python
try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731
```

> **Key rule:** `ic()` calls should never be the *only* logging for an error.
> They supplement structured `log_event()` calls — not replace them.

### 2. `log_event()` — Structured Production Logging

The `log_event()` helper emits structured log records with a **log_key** and
typed **keyword fields** that the `RedactingFormatter` renders as `key=value`
pairs.

```python
from pipeline_logging import log_event

log_event(
    logger,
    "info",              # level: trace | debug | info | warn | error | critical
    "CHECKPOINT",        # log_key: categorizes the message
    "saved checkpoint",  # human-readable message
    operation="UPDATE",  # structured fields ↓
    seq=seq,
    doc_id=self._local_doc_id,
    storage="sg",
)
```

**Output:**
```
2025-07-01 12:00:01,234 [INFO] changes_worker: saved checkpoint [CHECKPOINT] operation=UPDATE seq=42 doc_id=checkpoint-abc123 storage=sg
```

---

## Log Keys

Every `log_event()` call requires a **log_key**.  These are the valid keys:

| Log Key        | Scope                                              |
| -------------- | -------------------------------------------------- |
| `CHANGES`      | `_changes` feed input (polling, parsing, batching)  |
| `PROCESSING`   | Filtering, routing, batch orchestration             |
| `MAPPING`      | Schema mapping (doc → SQL ops)                      |
| `OUTPUT`       | HTTP / DB / cloud output forwarding                 |
| `HTTP`         | HTTP request/response details (non-output)          |
| `CHECKPOINT`   | Checkpoint load / save                              |
| `RETRY`        | Retry / backoff decisions                           |
| `METRICS`      | Metrics server lifecycle                            |
| `CBL`          | Couchbase Lite operations                           |
| `DLQ`          | Dead letter queue operations                        |

### Filtering by Log Key

In `config.json`, handlers can subscribe to specific keys and override their
level:

```json
{
  "logging": {
    "console": {
      "log_level": "info",
      "log_keys": ["CHANGES", "OUTPUT", "CHECKPOINT"],
      "key_levels": {
        "HTTP": "warn",
        "RETRY": "debug"
      }
    }
  }
}
```

- `log_keys: ["*"]` — accept all keys (default)
- `key_levels` — override the base level for specific keys

---

## Log Levels

| Level      | Numeric | When to Use                                               |
| ---------- | ------- | --------------------------------------------------------- |
| `TRACE`    | 5       | `ic()` output, extremely verbose developer traces         |
| `DEBUG`    | 10      | Per-document forwarding results, response details         |
| `INFO`     | 20      | Lifecycle events, startup, config loaded, stats summaries |
| `WARNING`  | 30      | Recoverable problems, fallbacks, skipped docs             |
| `ERROR`    | 40      | Failed operations, unreachable endpoints, bad data        |
| `CRITICAL` | 50      | Unrecoverable failures (not commonly used)                |

---

## Structured Fields

The `RedactingFormatter` recognizes these extra fields and renders them as
`key=value` pairs.  Use only these field names for consistency:

| Field            | Type        | Example                           |
| ---------------- | ----------- | --------------------------------- |
| `log_key`        | str         | `"OUTPUT"`                        |
| `operation`      | str         | `"INSERT"`, `"UPDATE"`, `"DELETE"`, `"SELECT"` |
| `doc_id`         | str         | `"user::123"`                     |
| `seq`            | str \| int  | `"42"` or `42`                    |
| `status`         | int         | `200`, `404`, `503`               |
| `url`            | str         | `"http://sg:4984/db/_changes"`    |
| `attempt`        | int         | `3`                               |
| `elapsed_ms`     | float       | `12.5`                            |
| `http_method`    | str         | `"PUT"`, `"DELETE"`               |
| `bytes`          | int         | `4096`                            |
| `batch_size`     | int         | `100`                             |
| `delay_seconds`  | float       | `2.0`                             |
| `host`           | str         | `"0.0.0.0"`                       |
| `port`           | int         | `9090`                            |
| `storage`        | str         | `"sg"`, `"cbl"`, `"file"`, `"fallback"` |
| `mode`           | str         | `"http"`, `"db"`                  |
| `db_name`        | str         | `"changes_worker_db"`             |
| `db_path`        | str         | `"/app/data"`                     |
| `db_size_mb`     | float       | `12.5`                            |
| `doc_count`      | int         | `1000`                            |
| `duration_ms`    | float       | `45.2`                            |
| `error_detail`   | str         | `"TimeoutError: timed out"`       |
| `input_count`    | int         | `50`                              |
| `filtered_count` | int         | `5`                               |
| `field_count`    | int         | `12`                              |
| `doc_type`       | str         | `"order"`                         |
| `manifest_id`    | str         | `"abc123"`                        |
| `maintenance_type` | str       | `"compact"`                       |

### The `operation` Field

Use the `infer_operation()` helper or set manually:

```python
from pipeline_logging import infer_operation

op = infer_operation(doc=doc, method=method)
# Returns: "INSERT" | "UPDATE" | "DELETE" | "SELECT"
```

### The `error_detail` Field

When logging errors, always include the exception type and message:

```python
except aiohttp.ClientConnectorError as exc:
    log_event(
        logger, "error", "OUTPUT",
        "connection failed (DNS / refused / unreachable)",
        doc_id=doc_id,
        url=url,
        error_detail=f"{type(exc).__name__}: {exc}",
    )
```

---

## Sensitive Data Redaction

The `Redactor` class automatically redacts sensitive data based on the
`redaction_level` setting:

| Level     | Behavior                                                       |
| --------- | -------------------------------------------------------------- |
| `none`    | No redaction (development only)                                |
| `partial` | Shows first/last character: `p*******d` (default)             |
| `full`    | Replaces entirely: `<ud>XXXXX</ud>`                           |

Auto-detected sensitive fields: `password`, `token`, `bearer_token`,
`session_cookie`, `authorization`, `cookie`, `secret`, `api_key`,
`access_token`, `refresh_token`.

URL credentials (`http://user:pass@host`) and Bearer tokens in strings are
also redacted automatically.

---

## Configuration Reference

### Full SG-Style Config (recommended)

```json
{
  "logging": {
    "redaction_level": "partial",
    "console": {
      "enabled": true,
      "log_level": "info",
      "log_keys": ["*"],
      "key_levels": {
        "HTTP": "warn",
        "RETRY": "debug"
      }
    },
    "file": {
      "enabled": true,
      "path": "logs/changes_worker.log",
      "log_level": "debug",
      "log_keys": ["*"],
      "key_levels": {},
      "rotation": {
        "max_size": 100,
        "max_age": 7,
        "rotated_logs_size_limit": 1024
      }
    }
  }
}
```

### Legacy Config (auto-upgraded)

```json
{
  "logging": {
    "level": "DEBUG"
  }
}
```

Legacy configs are automatically upgraded to the full format at startup by
`_ensure_full_logging_config()`.

### Rotation Settings

| Setting                    | Default | Description                              |
| -------------------------- | ------- | ---------------------------------------- |
| `max_size`                 | 100     | MB per log file before rollover          |
| `max_age`                  | 7       | Days to retain rotated files             |
| `rotated_logs_size_limit`  | 1024    | Total MB cap for all rotated files       |

---

## Exception Handling Best Practices

### Catch Specific Exceptions

Break broad exception handlers into specific types for better diagnostics:

```python
# ✅ Good — specific handlers with descriptive messages
except asyncio.TimeoutError:
    log_event(logger, "error", "OUTPUT", "request timed out",
              doc_id=doc_id, url=url,
              error_detail=f"timeout after {self._request_timeout}s")

except aiohttp.ClientConnectorError as exc:
    log_event(logger, "error", "OUTPUT",
              "connection failed (DNS / refused / unreachable)",
              doc_id=doc_id, url=url,
              error_detail=f"{type(exc).__name__}: {exc}")

except aiohttp.ClientSSLError as exc:
    log_event(logger, "error", "OUTPUT", "SSL/TLS handshake failed",
              doc_id=doc_id, url=url,
              error_detail=f"{type(exc).__name__}: {exc}")

# ✅ Catch-all at the end for anything unexpected
except (ConnectionError, aiohttp.ClientError) as exc:
    log_event(logger, "error", "OUTPUT", "output failed after retries",
              doc_id=doc_id, url=url,
              error_detail=f"{type(exc).__name__}: {exc}")
```

```python
# ❌ Bad — bare except or overly broad handler
except Exception as exc:
    log_event(logger, "error", "OUTPUT",
              "something went wrong: %s" % exc, url=url)
```

### HTTP Error Hierarchy

For REST operations, handle errors from most-specific to least-specific:

1. `_ClientHTTPError` / `ClientHTTPError` — 4xx (non-retryable)
2. `_RedirectHTTPError` / `RedirectHTTPError` — 3xx
3. `_ServerHTTPError` / `ServerHTTPError` — 5xx (retryable)
4. `asyncio.TimeoutError` — request timed out
5. `aiohttp.ClientConnectorError` — DNS failure, connection refused
6. `aiohttp.ClientSSLError` — TLS/SSL handshake failure
7. `aiohttp.InvalidURL` — malformed URL
8. `aiohttp.ClientError` — catch-all for remaining aiohttp errors
9. `ConnectionError` — generic Python connection error

### Pair `ic()` with `log_event()`

Every error branch should have **both** an `ic()` trace and a `log_event()` call:

```python
except aiohttp.ClientConnectorError as exc:
    ic("send: connection error", doc_id, url, type(exc).__name__, str(exc))
    log_event(
        logger, "error", "OUTPUT",
        "connection failed (DNS / refused / unreachable)",
        doc_id=doc_id, url=url,
        error_detail=f"{type(exc).__name__}: {exc}",
    )
```

- `ic()` gives the raw developer trace at TRACE level
- `log_event()` gives the structured production log at ERROR/WARN level

---

## Adding Logging to a New Module

Follow this checklist when creating or updating a module:

### 1. Imports

```python
import logging
from pipeline_logging import log_event, infer_operation

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

logger = logging.getLogger("changes_worker")
```

### 2. Lifecycle Events → `log_event()` at INFO

```python
log_event(logger, "info", "OUTPUT", "forwarder initialized",
          mode=self._mode, url=self._target_url)
```

### 3. Per-Item Operations → `log_event()` at DEBUG

```python
log_event(logger, "debug", "OUTPUT", "forwarded document",
          operation=infer_operation(doc=doc, method=method),
          doc_id=doc_id, status=status, elapsed_ms=round(elapsed_ms, 1))
```

### 4. Developer Traces → `ic()`

```python
ic("send", method, doc_id, url)
ic("send: response", doc_id, status, round(elapsed_ms, 1))
```

### 5. Errors → `ic()` + `log_event()` at ERROR with `error_detail`

```python
except SomeError as exc:
    ic("operation failed", context, exc)
    log_event(logger, "error", "LOG_KEY", "descriptive message",
              doc_id=doc_id, error_detail=f"{type(exc).__name__}: {exc}")
```

### 6. Recoverable Issues → `log_event()` at WARN

```python
log_event(logger, "warn", "CHECKPOINT",
          "checkpoint save fell back to local storage: %s" % exc,
          operation="UPDATE", seq=seq, storage="fallback")
```

---

## Notable Warnings

### 🍦 `_bulk_get` Missing Documents

When `include_docs=false` the worker fetches document bodies via
`POST /{keyspace}/_bulk_get` in batches.  After each batch the returned count
is compared against the requested count.  If any documents are missing, the
following log messages may appear:

| Level   | Log Key | Message | Meaning |
| ------- | ------- | ------- | ------- |
| `WARN`  | `HTTP`  | `🍦 _bulk_get returned fewer docs than requested` | The server returned fewer documents than were requested.  `batch_size` is the number requested, `doc_count` is how many came back, `input_count` is how many are missing. |
| `INFO`  | `HTTP`  | `got N document(s) from failed _bulk_get via individual GET` | Missing documents were successfully recovered by falling back to individual `GET /{keyspace}/{docid}?rev=` requests with exponential retry. |
| `ERROR` | `HTTP`  | `failed to get N doc(s) from failed _bulk_get after retries` | One or more documents could not be recovered even after exponential-backoff retries on individual GETs.  These documents will be missing from the batch output. |

**Example log output:**
```
2025-07-01 12:00:02,100 [WARNING] changes_worker: 🍦 _bulk_get returned fewer docs than requested [HTTP] batch_size=100 doc_count=98 input_count=2
2025-07-01 12:00:03,400 [INFO]    changes_worker: got 2 document(s) from failed _bulk_get via individual GET [HTTP] doc_count=2 batch_size=2
```

The individual-GET fallback uses exponential backoff (1 s, 2 s, 4 s, 8 s, 16 s …
capped at 60 s) for up to 5 attempts per document.

---

## Common Patterns in the Codebase

### Timing Operations

```python
t_start = time.monotonic()
# ... do work ...
elapsed_ms = (time.monotonic() - t_start) * 1000

log_event(logger, "debug", "OUTPUT", "forwarded document",
          elapsed_ms=round(elapsed_ms, 1))
```

### File / CBL Storage Branching

```python
ic("DLQ.write", doc_id, seq, "cbl" if self._use_cbl else "file")

if self._use_cbl and self._store:
    self._store.add_dlq_entry(...)
    log_event(logger, "warn", "DLQ", "entry written to CBL",
              storage="cbl", doc_id=doc_id)
else:
    # file fallback
    log_event(logger, "warn", "DLQ", "entry written to file",
              storage="file", doc_id=doc_id)
```

### Config Dump at Startup

```python
cfg = load_config(path)
ic(cfg)  # dumps entire config at TRACE level
```

---

## Viewing Logs

### Console (real-time)

Controlled by `logging.console`. Set `log_level` to `debug` or `trace` for
more verbosity.

### File

Written to `logging.file.path` (default: `logs/changes_worker.log`).
Rotated automatically based on `rotation` settings.

### TRACE / ic() Output

Set the file handler's `log_level` to `trace` to capture `ic()` output:

```json
{
  "logging": {
    "file": {
      "log_level": "trace",
      "log_keys": ["*"]
    }
  }
}
```

### Filtering by Key

To see only HTTP and OUTPUT logs at DEBUG level on the console:

```json
{
  "logging": {
    "console": {
      "log_level": "debug",
      "log_keys": ["HTTP", "OUTPUT"]
    }
  }
}
```
