# Source Types

The `_changes` APIs are very similar across Couchbase products and CouchDB but **not identical**. Set `gateway.src` to tell the worker which product it's talking to.

## Compatibility Matrix

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

## What the Worker Does Automatically

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

## Key Differences to Know

- **Sync Gateway** and **App Services** share the same API. App Services is the hosted/Capella-managed version — endpoints are always HTTPS.
- **Edge Server** is a lightweight, embedded gateway. It does **not** support Bearer token auth, `_bulk_get`, or `version_type`. It does add unique features (sub-documents, SQL++ queries) but those are outside the scope of this worker.
- **CouchDB** uses the same `_changes` response format (`results`, `last_seq`) but does not have scopes/collections, channels, or `active_only`. Its `_bulk_get` returns JSON (not multipart) which the worker handles natively. CouchDB's `eventsource` feed type is equivalent to Edge Server's `sse`.
- The `_changes` response schema (`results`, `last_seq`) is the same across all four products.
