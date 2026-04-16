# Changes Worker  v1.4.0

A production-ready, async Python 3 processor for the `_changes` feed. It connects to **Sync Gateway**, **Capella App Services**, **Couchbase Edge Server**, or **Apache CouchDB**, consumes document changes via longpoll or continuous streaming, and forwards them to a downstream consumer — stdout, HTTP endpoint, or RDBMS (PostgreSQL, MySQL, MS SQL, Oracle).

Built for real-world workloads: checkpoint management so you never re-process, throttled feed consumption for large datasets, configurable retry with exponential backoff on both the source and destination sides, and full async concurrency control.

![Changes Worker Architecture](img/architecture.png)

---

## Who Is This For?

- **Backend engineers** building event-driven pipelines off Couchbase mobile/edge data
- **Data engineers** syncing Couchbase document changes to external systems (search indexes, analytics, warehouses, third-party APIs)
- **DevOps / platform teams** that need a lightweight, containerized change-data-capture (CDC) sidecar
- **Anyone** who needs to react to Couchbase document mutations in near-real-time without running Couchbase Lite or a full replication stack

---

## How It Works

```
┌──────────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│  Sync Gateway /      │         │                  │         │  HTTP Endpoint      │
│  App Services /      │ ──GET── │  changes_worker  │ ──PUT── │  (any REST API)     │
│  Edge Server /       │ _changes│                  │  POST   ├─────────────────────┤
│  CouchDB             │ ◄─JSON─ │  • Schema Mapping│  DELETE │  RDBMS              │
│                      │         │  • Serialize     │ ──────► │  (Postgres/MySQL/   │
│  /{db}/_changes      │         │  • Checkpoint    │         │   MSSQL/Oracle)     │
│                      │         │  • Dead Letter Q │         ├─────────────────────┤
│                      │         │  • CBL metadata  │         │  stdout             │
└──────────────────────┘         └──────────────────┘         └─────────────────────┘
```

1. **Consume** — Longpoll `_changes` on a configurable interval, or use continuous mode (batched catch-up → real-time stream with auto-reconnect)
2. **Filter** — Skip deletes, removes, or limit to specific channels (SG/App Services)
3. **Fetch** — If `include_docs=false`, fetch full docs via `_bulk_get` (SG/App Services/CouchDB) or individual `GET` (Edge Server), in batches of `get_batch_number`
4. **Map** — Apply schema mappings to transform JSON documents into table rows, remapped JSON, XML, etc.
5. **Forward** — Serialize each doc (JSON, XML, msgpack, etc.) and send to stdout, HTTP endpoint, or RDBMS
6. **Checkpoint** — Save `last_seq` to a `_local/` doc on SG/CouchDB (CBL-style) so restarts resume exactly where they left off

---

## Embedded Storage (Couchbase Lite CE)

When running in Docker, the worker uses **Couchbase Lite CE 3.2.1** as an embedded database for all persistent state — config, checkpoints, schema mappings, and the dead letter queue. No external database required.

| Data | CBL Doc ID | Fallback (no CBL) |
|---|---|---|
| Configuration | `config` | `config.json` file |
| Checkpoint | `checkpoint:{uuid}` | `checkpoint.json` file |
| Schema mappings | `mapping:{filename}` | `mappings/*.yaml` files |
| Dead letter queue | `dlq:{doc_id}:{timestamp}` | `failed_docs.jsonl` file |

The CBL database (`changes_worker_db`) is stored in a Docker volume at `/app/data`. When CBL is not available (e.g., local development), the system falls back to file-based storage automatically.

📄 **Full database schema:** [`docs/CBL_DATABASE.md`](docs/CBL_DATABASE.md)

---

## One Process Per Collection

Unlike Couchbase Lite, which can open and sync multiple collections in a single connection, the Sync Gateway / App Services / Edge Server `_changes` API serves **one collection at a time** (CouchDB serves one database at a time). This means:

- **Each changes_worker process monitors exactly one collection.**
- To watch multiple collections, run **one container (or process) per collection**, each with its own `config.json` pointing at a different `scope` + `collection`.

For example, to monitor three collections you would run three instances:

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  changes_worker  │   │  changes_worker  │   │  changes_worker  │
│  us.prices       │   │  us.inventory    │   │  eu.orders       │
│  config-a.json   │   │  config-b.json   │   │  config-c.json   │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

Each instance maintains its own checkpoint, retry state, and metrics independently. When running with Docker Compose, define one service per collection (or use `docker compose up --scale` with per-instance config mounts).

---

## Quick Start

### Prerequisites

- Python 3.11+
- A running Sync Gateway, Capella App Services, Edge Server, or CouchDB instance

### Install & Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Edit config.json with your gateway URL, database, and credentials
# (see Configuration Reference below)

# Test connectivity first
python main.py --config config.json --test

# Run the worker
python main.py --config config.json
```

### Run with Docker

```bash
# Build the image
docker build -t changes-worker .

# Run with your config mounted in
docker run --rm \
  -v $(pwd)/config.json:/app/config.json \
  changes-worker

# Test mode
docker run --rm \
  -v $(pwd)/config.json:/app/config.json \
  changes-worker --test
```

### CLI Options

| Flag | Description |
|---|---|
| `--config <path>` | Path to config.json (default: `config.json`) |
| `--test` | Test connectivity to source + output endpoints, then exit (exit code 0 = pass, 1 = fail) |

---

## Configuration Reference

All settings live in a single `config.json` file. Here is a complete reference with the default config:

```jsonc
{
  "gateway": {
    "src": "sync_gateway",           // "sync_gateway" | "app_services" | "edge_server" | "couchdb"
    "url": "http://localhost:4984",   // Base URL of the gateway
    "database": "db",                 // Database name
    "scope": "us",                    // Scope (optional — omit for default scope)
    "collection": "prices",           // Collection (optional — omit for default collection)
    "accept_self_signed_certs": false // Set true for dev/test with self-signed TLS
  },

  "auth": {
    "method": "basic",               // "basic" | "session" | "bearer" | "none"
    "username": "bob",               // For method=basic
    "password": "password",          // For method=basic
    "session_cookie": "",            // For method=session (SyncGatewaySession cookie)
    "bearer_token": ""               // For method=bearer (SG / App Services only)
  },

  "changes_feed": {
    "feed_type": "longpoll",         // "longpoll" | "continuous" | "normal" | "sse" (Edge) | "eventsource" (CouchDB)
    "poll_interval_seconds": 10,     // Seconds to wait between longpoll cycles
    "active_only": true,             // Exclude deleted/revoked docs from the feed
    "include_docs": true,            // Inline doc bodies; false = bulk_get after
    "since": "0",                    // Starting sequence ("0" = use checkpoint)
    "channels": [],                  // Channel filter, e.g. ["channel-a", "channel-b"]
    "limit": 0,                      // Per-request limit (0 = no limit)
    "heartbeat_ms": 30000,           // Heartbeat interval to keep connection alive
    "timeout_ms": 60000,             // SG-side longpoll timeout
    "http_timeout_seconds": 300,     // Client-side HTTP timeout (for large since=0 catch-ups)
    "throttle_feed": 0,              // Eat the feed N docs at a time (0 = no throttle)
    "continuous_catchup_limit": 10000 // Batch size for continuous mode catch-up phase
  },

  "processing": {
    "ignore_delete": false,          // Skip deleted docs in the feed
    "ignore_remove": false,          // Skip removed-from-channel docs
    "sequential": false,             // true = process one doc at a time (strict order)
    "max_concurrent": 20,            // Semaphore limit for parallel processing
    "dry_run": false,                // Log what would happen without sending
    "get_batch_number": 100          // Batch size for bulk_get / individual doc fetches
  },

  "checkpoint": {
    "enabled": true,                 // Persist last_seq between runs
    "client_id": "changes_worker",   // Used in CBL-style checkpoint key derivation
    "file": "checkpoint.json",        // Local fallback file if SG is unreachable
    "every_n_docs": 0                // Save checkpoint every N docs within a batch (0 = per-batch)
  },

  "output": {
    "mode": "stdout",                // "stdout" | "http"
    "target_url": "",                // HTTP endpoint for mode=http
    "target_auth": {                 // Auth for the output endpoint
      "method": "none",              // "basic" | "session" | "bearer" | "none"
      "username": "",
      "password": "",
      "session_cookie": "",
      "bearer_token": ""
    },
    "retry": {                       // Output-specific retry (separate from gateway)
      "max_retries": 3,
      "backoff_base_seconds": 1,
      "backoff_max_seconds": 30,
      "retry_on_status": [500, 502, 503, 504]
    },
    "halt_on_failure": true,         // Stop & freeze checkpoint if output fails
    "log_response_times": true,      // Track min/max/avg response times per batch
    "output_format": "json",         // "json"|"xml"|"form"|"msgpack"|"cbor"|"bson"|"yaml"
    "dead_letter_path": "failed_docs.jsonl", // JSONL file for docs that failed output delivery
    "request_options": {             // Extra options added to every output HTTP request
      "params": {},                  // Query-string parameters (e.g. {"batch":"ok"})
      "headers": {}                  // Custom headers (e.g. {"X-Source":"changes-worker"})
    }
  },

  "retry": {                         // Gateway-side retry (for _changes, bulk_get, etc.)
    "max_retries": 5,
    "backoff_base_seconds": 1,
    "backoff_max_seconds": 60,
    "retry_on_status": [500, 502, 503, 504]
  },

  "metrics": {
    "enabled": false,                // Enable Prometheus /_metrics endpoint
    "host": "0.0.0.0",              // Bind address
    "port": 9090                     // Port for the metrics HTTP server
  },

  "logging": {
    "level": "DEBUG"                 // "DEBUG" | "INFO" | "WARNING" | "ERROR"
  }
}
```

---

## Features in Detail

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

📄 **For a complete metrics reference** with types, descriptions, PromQL examples, and charting suggestions, see [`metrics.html`](metrics.html).

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

📄 **Full design details:** [`docs/DESIGN.md`](docs/DESIGN.md#continuous-feed-mode-feed_type-continuous)

---

## Source Type (`gateway.src`)

The `_changes` APIs are very similar across Couchbase products and CouchDB but **not identical**. Set `gateway.src` to tell the worker which product it's talking to.

### Compatibility Matrix

| Capability | Sync Gateway | App Services | Edge Server | CouchDB |
|---|:---:|:---:|:---:|:---:|
| Default public port | `4984` | `4984` | `59840` | `5984` |
| Feed types | `longpoll`, **`continuous`**, `websocket` | `longpoll`, **`continuous`**, `websocket` | `longpoll`, **`continuous`**, **`sse`** | `longpoll`, **`continuous`**, **`eventsource`** |
| `active_only` param | ✅ | ✅ | ✅ | ❌ not supported |
| `version_type` param (`rev` / `cv`) | ✅ | ✅ | ❌ | ❌ |
| Bearer token auth | ✅ | ✅ | ❌ basic / session only | ✅ |
| Session cookie auth | ✅ | ✅ | ✅ | ❌ not supported |
| Channels filter | ✅ | ✅ | ✅ | ❌ use `filter` instead |
| `timeout` max | no hard cap | no hard cap | **900,000 ms** (15 min) | no hard cap |
| `heartbeat` minimum | none | none | **25,000 ms** | none |
| `_bulk_get` endpoint | ✅ | ✅ | ❌ individual `GET` | ✅ (JSON response) |
| `_local/` checkpoint docs | ✅ | ✅ | ✅ | ✅ |
| Scoped keyspace (`db.scope.collection`) | ✅ | ✅ | ✅ | ❌ database only |

### What the Worker Does Automatically

| Situation | Automatic behavior |
|---|---|
| `src=edge_server` + `feed_type=websocket` | Falls back to `longpoll` with a warning |
| `src=couchdb` + `feed_type=websocket` | Falls back to `longpoll` with a warning |
| `src=couchdb` + `feed_type=sse` | Switches to `eventsource` (CouchDB equivalent) |
| `src≠edge_server` + `feed_type=sse` | Falls back to `longpoll` with a warning |
| `src=edge_server` + `auth.method=bearer` | **Blocks startup** with an error |
| `src=couchdb` + `auth.method=session` | **Blocks startup** with an error |
| `src=edge_server` + `timeout_ms > 900000` | Clamps to `900000` with a warning |
| `src=edge_server` | Omits the `version_type` query param |
| `src=couchdb` | Omits `active_only`, `channels`, `version_type` params; skips scope/collection in URL |
| `src=edge_server` + `include_docs=false` | Fetches docs individually (no `_bulk_get`), warns about performance |
| `src=sync_gateway` or `app_services` | Sends `version_type=rev` by default (configurable to `cv`) |
| `src=app_services` + `http://` URL | Warns that App Services is typically HTTPS |

### Key Differences to Know

- **Sync Gateway** and **App Services** share the same API. App Services is the hosted/Capella-managed version — endpoints are always HTTPS.
- **Edge Server** is a lightweight, embedded gateway. It does **not** support Bearer token auth, `_bulk_get`, or `version_type`. It does add unique features (sub-documents, SQL++ queries) but those are outside the scope of this worker.
- **CouchDB** uses the same `_changes` response format (`results`, `last_seq`) but does not have scopes/collections, channels, or `active_only`. Its `_bulk_get` returns JSON (not multipart) which the worker handles natively. CouchDB's `eventsource` feed type is equivalent to Edge Server's `sse`.
- The `_changes` response schema (`results`, `last_seq`) is the same across all four products.

---

## Admin UI

A web-based admin dashboard is available at `http://localhost:8080` when running the `admin-ui` service. It provides:

- **Dashboard** (`/`) -- Real-time status indicators (green/yellow/red) for the changes feed, processing, Couchbase Lite, and output. Three-column metrics breakdown with live line charts, auto-refreshing every 5 seconds. Add `?debug=true` for browser console diagnostics.
- **Config Editor** (`/config`) -- Form-based and raw JSON editing with save/reset. Collapsible sections for gateway, auth, feed, processing, output, checkpoint, metrics, and logging. CouchDB-specific fields (scope, collection, channels, active_only) auto-hide when `couchdb` is selected.
- **Schema Mappings** (`/schema`) -- Visual split-pane editor with drag-and-drop field mapping, transform functions, table relationship diagrams, live sample fetch, DB import, and DDL parsing.
- **Transforms** (`/transforms`) -- Reference page for all 58 built-in transform functions with descriptions and examples.
- **Setup Wizard** (`/wizard`) -- 3-step guided setup: connect source → configure output → map fields. Generates a complete `config.json` and mapping file.

Dark/light theme persists across all pages via `localStorage`.

Full documentation: [`docs/ADMIN_UI.md`](docs/ADMIN_UI.md)

---

## Piping Output

When `mode=stdout`, pipe to other tools:

```bash
# Pipe to jq for pretty-printing
python main.py | jq '.'

# Pipe to another service
python main.py | while IFS= read -r line; do
  curl -s -X PUT http://other-service/ingest -d "$line" -H 'Content-Type: application/json'
done

# Write to a file
python main.py >> changes.jsonl
```

---

## Project Structure

```
change_stream_db/
├── main.py                  # Main worker script (single file, all logic)
├── cbl_store.py              # Couchbase Lite CE storage layer (CBLStore class)
├── config.json               # Configuration (edit this)
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container image (includes CBL-C 3.2.1)
├── docker-compose.yml        # Docker Compose setup (shared cbl-data volume)
├── metrics.html              # Prometheus metrics reference (open in browser)
├── tests/
│   └── test_changes_worker.py  # Unit tests
├── rest/
│   ├── __init__.py           # Public exports
│   └── output_http.py        # HTTP output, dead letter queue, serialization
├── web/
│   ├── server.py             # Admin UI web server (config, mappings, DLQ APIs)
│   ├── templates/            # HTML pages
│   └── static/               # CSS, JS assets
├── docs/
│   ├── ADMIN_UI.md           # Dashboard & admin UI documentation
│   ├── CBL_DATABASE.md       # Couchbase Lite database schema reference
│   ├── CBL_STORE.md          # CBL implementation plan
│   ├── DESIGN.md             # Architecture, failure modes & trade-offs
│   ├── SCHEMA_MAPPING.md    # Schema mapping documentation
│   └── WIZARD.md            # Setup wizard documentation
└── README.md                 # This file
```

---

## Logging

The worker uses Python's stdlib `logging` module with [icecream](https://github.com/gruns/icecream) for debug tracing.

| Level | What you see |
|---|---|
| `ERROR` | Startup failures, non-retryable errors, output endpoint failures |
| `WARNING` | Config warnings, retry attempts, checkpoint fallbacks |
| `INFO` | Batch progress, throttle status, output stats, connection test results |
| `DEBUG` | Per-request details, icecream traces, individual doc processing |

Set via `logging.level` in config.json.

---

## License

See [LICENSE](LICENSE).
