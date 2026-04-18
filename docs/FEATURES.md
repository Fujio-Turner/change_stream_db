# Features in Detail

### Startup Validation

Before anything runs, the worker validates **every setting** against the selected `gateway.src`. Invalid combinations produce clear error messages and **block startup**:

```
ERROR  ============================================================
ERROR    STARTUP ABORTED – config errors detected
ERROR  ============================================================
ERROR    ✗ auth.method=bearer is not supported by Edge Server – use 'basic' or 'session' instead
ERROR    ✗ changes_feed.feed_type=websocket is not supported by Edge Server
ERROR  ============================================================
ERROR  Fix the errors above in config.json and try again.
```

Non-fatal issues log warnings but allow the worker to continue.

### Connection Test (`--test`)

Run `python main.py --test` to verify everything is reachable before deploying:

```
============================================================
  Source type:           Sync Gateway
  Testing connection to: http://localhost:4984
  Keyspace:              http://localhost:4984/db.us.prices
  Auth method:           basic
============================================================

  [✓] Server root reachable
      version: 3.1.0
  [✓] Keyspace reachable  (db_name=db, state=Online)
  [✓] _changes endpoint OK  (last_seq=1234, sample_results=1)
  [✓] Checkpoint readable   (saved since=500)
  [✓] Output endpoint reachable (http://my-service:8080/docs)

============================================================
  Result: ALL CHECKS PASSED ✓
============================================================
```

Exits with code `0` on success, `1` on failure — works in CI and Docker health checks.

### Checkpoint Management (CBL-Style)

Checkpoints are stored **on Sync Gateway itself** as `_local/` documents, using the same key-derivation logic as Couchbase Lite:

```
UUID = SHA1(client_id + gateway_url + channels)
Doc path: {keyspace}/_local/checkpoint-{UUID}
```

The checkpoint document follows the CBL convention — `remote` indicates a pull replication (reading the `_changes` feed), and `time` is an epoch timestamp:

```json
{
  "client_id": "changes_worker",
  "SGs_Seq": "1500",
  "time": 1768521600,
  "remote": 42
}
```

If the gateway is unreachable for checkpoint operations, it falls back to a local `checkpoint.json` file.

### Feed Throttling (`throttle_feed`)

Large feeds (e.g., `since=0` with 91,000 documents) are best consumed in bites:

```jsonc
"throttle_feed": 10000
```

The worker requests `?limit=10000`, processes the batch, saves the checkpoint, then immediately requests the next batch with `since=<last_seq>`. It only sleeps `poll_interval_seconds` once a batch comes back **smaller** than the throttle limit (meaning you've caught up).

Example: 91K feed with `throttle_feed: 10000` → 9 full batches back-to-back, 1 partial batch of 1K, then sleep.

### HTTP Timeout (`http_timeout_seconds`)

A `since=0` catch-up can return hundreds of thousands of changes and take minutes. The default 30–75s HTTP timeout would kill the connection. Set `http_timeout_seconds` to give it room:

```jsonc
"http_timeout_seconds": 300   // 5 minutes — plenty for large catch-ups
```

This is a **per-request timeout** applied only to `_changes` calls. Other calls (bulk_get, checkpoint, etc.) use the session default.

### Doc Fetching (`include_docs` & `get_batch_number`)

When `include_docs=false`, the `_changes` feed returns only `_id` and `_rev`. The worker then fetches full document bodies:

- **Sync Gateway / App Services** → `POST _bulk_get` (one request per batch)
- **Edge Server** → individual `GET /{keyspace}/{docid}?rev={rev}` (no `_bulk_get` available), fanned out with a concurrency semaphore

Docs are fetched in batches of `get_batch_number` (default 100) to avoid overwhelming the server:

```jsonc
"get_batch_number": 100   // 950 docs = 10 batches (9×100 + 1×50)
```

### Output Forwarding (`output.mode=http`)

When `mode=http`, each processed doc is sent as a PUT, POST, or DELETE to `target_url/{doc_id}`:

- **Own retry config** — `output.retry` is separate from the gateway retry
- **Reachability check at startup** — verifies the endpoint responds before processing
- **Response time tracking** — logs min/max/avg per batch when `log_response_times=true`
- **Error handling**:
  - **5xx** → retries with exponential backoff
  - **4xx** → logged as client error (no retry)
  - **3xx** → logged as redirect (no retry)
  - **Connection failure** → retries exhausted
- **Halt on failure** (`halt_on_failure=true`):
  - If the output endpoint goes down, the worker **stops processing and does NOT advance the checkpoint**
  - On the next poll cycle, it re-fetches the same batch and retries
  - This guarantees no data is lost — you pick up right where you left off
- **Skip on failure** (`halt_on_failure=false`):
  - Logs the error, skips the failed doc, and continues
  - Failed docs are written to the dead letter queue (CBL or JSONL file)
  - ⚠️ Checkpoint still advances — failed docs are NOT retried automatically

### Custom Request Options (`output.request_options`)

You can inject additional query-string parameters and custom HTTP headers into every output request via `request_options`:

```jsonc
"output": {
  "mode": "http",
  "target_url": "https://my-service:8080/api/docs",
  "request_options": {
    "params": {
      "batch": "ok",
      "source": "cbl"
    },
    "headers": {
      "X-Source": "changes-worker",
      "X-Region": "us-east-1"
    }
  }
}
```

With the config above, a document with `_id = "doc123"` produces:

```
PUT https://my-service:8080/api/docs/doc123?batch=ok&source=cbl
X-Source: changes-worker
X-Region: us-east-1
Content-Type: application/json
```

| Field | Type | Description |
|---|---|---|
| `params` | `object` | Key/value pairs appended as query-string parameters to every request URL |
| `headers` | `object` | Key/value pairs merged into the request headers (overrides default headers except `Content-Type`) |

Both fields default to `{}` (no extras). Custom headers are merged **after** auth headers, so they can override auth-derived headers if needed. `Content-Type` is always set last based on `output_format` and cannot be overridden.

### Output Formats (`output.output_format`)

Not every consumer expects JSON. Choose the serialization format:

| Format | Content-Type | Library | Use Case |
|---|---|---|---|
| `json` | `application/json` | stdlib | Default. Universal. |
| `xml` | `application/xml` | stdlib | Legacy systems, SOAP, enterprise integrations |
| `form` | `application/x-www-form-urlencoded` | stdlib | HTML forms, legacy web frameworks |
| `msgpack` | `application/msgpack` | `pip install msgpack` | High-throughput microservices |
| `cbor` | `application/cbor` | `pip install cbor2` | IoT, constrained environments |
| `bson` | `application/bson` | `pip install pymongo` | MongoDB pipelines |
| `yaml` | `application/yaml` | `pip install pyyaml` | Config-style consumers |

```bash
# Install only what you need:
pip install msgpack     # for output_format=msgpack
pip install cbor2       # for output_format=cbor
pip install pymongo     # for output_format=bson
pip install pyyaml      # for output_format=yaml
```

The format applies to **both** `mode=stdout` and `mode=http`. Binary formats write to `sys.stdout.buffer` when piping. Startup validation **blocks launch** if the required library isn't installed.

### Prometheus Metrics (`/_metrics`)

The worker exposes a built-in `/_metrics` endpoint that serves all operational metrics in [Prometheus text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/). Enable it in `config.json`:

```jsonc
"metrics": {
  "enabled": true,       // Enable the metrics HTTP server
  "host": "0.0.0.0",     // Bind address (default: all interfaces)
  "port": 9090           // Port to listen on (default: 9090)
}
```

Once running, scrape metrics at:

```bash
curl http://localhost:9090/_metrics
# or
curl http://localhost:9090/metrics
```

**Sample output:**

```
# HELP changes_worker_uptime_seconds Time in seconds since the worker started.
# TYPE changes_worker_uptime_seconds gauge
changes_worker_uptime_seconds{src="sync_gateway",database="db"} 3621.450

# HELP changes_worker_poll_cycles_total Total number of _changes poll cycles completed.
# TYPE changes_worker_poll_cycles_total counter
changes_worker_poll_cycles_total{src="sync_gateway",database="db"} 362

# HELP changes_worker_changes_received_total Total number of changes received from the _changes feed.
# TYPE changes_worker_changes_received_total counter
changes_worker_changes_received_total{src="sync_gateway",database="db"} 91247

# HELP changes_worker_output_response_time_seconds Output HTTP response time in seconds.
# TYPE changes_worker_output_response_time_seconds summary
changes_worker_output_response_time_seconds{src="sync_gateway",database="db",quantile="0.5"} 0.012
changes_worker_output_response_time_seconds{src="sync_gateway",database="db",quantile="0.9"} 0.045
changes_worker_output_response_time_seconds{src="sync_gateway",database="db",quantile="0.99"} 0.120
```

**Prometheus scrape config:**

```yaml
scrape_configs:
  - job_name: 'changes_worker'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:9090']
```

All metrics are labeled with `src` (gateway type) and `database` (keyspace name) for multi-instance dashboards. The endpoint exposes counters, gauges, and a response time summary — everything you need for Grafana dashboards and alerting.

#### System & Runtime Metrics

In addition to pipeline metrics, the `/_metrics` endpoint exposes live system and Python runtime metrics (via [psutil](https://github.com/giampaolo/psutil)):

| Category | Metrics |
|---|---|
| **Process CPU** | `process_cpu_percent`, `process_cpu_user_seconds_total`, `process_cpu_system_seconds_total` |
| **Process Memory** | `process_memory_rss_bytes`, `process_memory_vms_bytes`, `process_memory_percent` |
| **Threads** | `process_threads` (OS-level), `python_threads_active` (Python `threading` count) |
| **Python GC** | `python_gc_gen{0,1,2}_count` (pending objects), `python_gc_gen{0,1,2}_collections_total` |
| **File Descriptors** | `process_open_fds` |
| **System CPU** | `system_cpu_count`, `system_cpu_percent` |
| **System Memory** | `system_memory_total/available/used_bytes`, `system_memory_percent`, `system_swap_total/used_bytes` |
| **Disk** | `system_disk_total/used/free_bytes`, `system_disk_percent` (root partition) |
| **Network I/O** | `system_network_bytes_sent/recv_total`, `system_network_packets_sent/recv_total`, `system_network_errin/errout_total` |
| **Storage** | `log_dir_size_bytes` (log directory), `cbl_db_size_bytes` (CBL database directory, when enabled) |

All system metrics use the same `changes_worker_` prefix and are collected on each scrape (no background polling). Example PromQL alerts:

```promql
# Alert if RSS exceeds 512 MB
changes_worker_process_memory_rss_bytes > 536870912

# Alert if disk is > 90% full
changes_worker_system_disk_percent > 90

# Track GC pressure
rate(changes_worker_python_gc_gen2_collections_total[5m]) > 1
```

📄 **For a complete metrics reference** with types, descriptions, PromQL examples, and charting suggestions, see [`metrics.html`](../metrics.html).

### Worker Control Endpoints

The metrics server (port 9090) also exposes control endpoints for managing the worker at runtime:

| Endpoint | Method | Description |
|---|---|---|
| `/_restart` | `POST` | Stop the current changes feed, reload config, and restart with the new settings. In-flight batch processing completes before the feed stops. |
| `/_shutdown` | `POST` | Graceful shutdown: stop consuming the changes feed, finish all in-flight output operations, save the checkpoint, then exit. |
| `/_metrics` | `GET` | Prometheus metrics (see above). |

**Restart example** — switch feed type without restarting the container:

```bash
# 1. Update config (via admin UI or directly)
curl -X PUT http://localhost:8080/api/config -d @config.json

# 2. Signal the worker to reload (automatic when using the admin UI)
curl -X POST http://localhost:9090/_restart
```

The worker will:
1. Stop the current feed loop (longpoll / continuous / websocket)
2. Wait for any in-flight `_process_changes_batch` to finish
3. Reload config from CBL store (or `config.json`)
4. Validate the new config
5. Start the feed with the new settings, resuming from the last checkpoint

**Graceful shutdown example:**

```bash
curl -X POST http://localhost:9090/_shutdown
```

> **Note:** The admin UI automatically calls `/_restart` on the worker after saving config via `PUT /api/config`, so config changes take effect immediately without manual intervention.

### Dry Run

Set `processing.dry_run=true` to process the `_changes` feed and log what *would* be sent without actually sending anything:

```
INFO  [DRY RUN] Would PUT http://my-service/docs/doc123 (application/json, 482 bytes)
INFO  [DRY RUN] Would DELETE http://my-service/docs/doc456 (application/json, 28 bytes)
```

### Parallel vs Sequential Processing

| Setting | Behavior |
|---|---|
| `sequential: false` (default) | Changes within a batch are processed in parallel using `asyncio` tasks, limited by `max_concurrent` |
| `sequential: true` | Changes are processed one at a time, in order |

In both modes, the **checkpoint is only saved after the entire batch completes**. This prevents the sequence from advancing past unprocessed documents.

If you need strict per-document ordering, set `sequential: true`.

### Continuous Mode (`feed_type: continuous`)

For real-time change notifications with reliable large-feed handling, set `feed_type` to `continuous`. The worker uses a **two-phase approach**:

1. **Catch-up** — Batched one-shot requests (`feed=normal`, `limit=continuous_catchup_limit`) drain any backlog safely, checkpointing after each batch
2. **Stream** — Opens a `feed=continuous` connection and reads changes line-by-line in real-time

If the server disconnects, the worker applies exponential backoff (using `retry` config) and returns to catch-up before reopening the stream. No data is lost.

```jsonc
"changes_feed": {
  "feed_type": "continuous",
  "continuous_catchup_limit": 10000   // batch size for the catch-up phase
}
```

📄 **Full design details:** [`DESIGN.md`](DESIGN.md#continuous-feed-mode-feed_type-continuous)

### WebSocket Mode (`feed_type: websocket`)

For Sync Gateway and App Services, the worker supports a **WebSocket feed** that uses a real `ws://` (or `wss://`) connection to the `_changes` endpoint. Like continuous mode, it uses a two-phase approach:

1. **Catch-up** — Batched one-shot `feed=normal` requests drain any backlog
2. **Stream** — Opens a WebSocket connection to `/_changes?feed=websocket`, sends parameters as a JSON payload, and receives changes as WebSocket messages

On disconnect, the worker applies exponential backoff and returns to catch-up before reconnecting.

```jsonc
"changes_feed": {
  "feed_type": "websocket",
  "include_docs": true,
  "active_only": true
}
```

> **Note:** WebSocket mode is only available on Sync Gateway and App Services. Edge Server and CouchDB do not support it.

📄 **Full design details:** [`DESIGN.md`](DESIGN.md#websocket-feed-mode-feed_type-websocket)
