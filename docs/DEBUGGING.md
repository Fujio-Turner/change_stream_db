# Logs & Debugging

The **Logs & Debugging** page (`/logs`) provides a real-time, filterable log viewer with smart error analysis built into the Admin UI.

---

## Accessing the Page

Navigate to **System → Logs** in the sidebar, or go directly to:

```
http://localhost:8080/logs
```

---

## Architecture

```
┌──────────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│   changes_worker     │     │   GET /api/logs      │     │   /logs (browser)    │
│                      │     │                      │     │                      │
│  pipeline_logging.py │────▸│  server.py           │────▸│  logs.html           │
│  writes to:          │     │  _parse_log_line()   │     │  JS client-side      │
│  logs/changes_       │     │  returns JSON array  │     │  filtering, coloring │
│  worker.log          │     │  of parsed entries   │     │  & error insights    │
└──────────────────────┘     └──────────────────────┘     └──────────────────────┘
```

### Files Involved

| File | Role |
|---|---|
| `pipeline_logging.py` | Writes structured log lines to `logs/changes_worker.log` |
| `web/server.py` | `GET /api/logs` and `GET /api/log-files` endpoints — reads and parses log files |
| `web/templates/logs.html` | UI — log viewer, filters, error insight panel |
| `web/static/icons/logs.svg` | Sidebar icon |
| `web/static/js/sidebar.js` | Sidebar nav entry under "System" |

---

## Log Format

Each log line follows a structured format:

```
TIMESTAMP [LEVEL] LOGGER: MESSAGE [LOG_KEY] key=value key=value...
```

**Example:**

```
2026-04-19 00:52:12.008 [ERROR] changes_worker: permanent error [OUTPUT] doc_id=postgres_02 mode=postgres error_detail=DataError: invalid input for query argument $3: 77777777777 (value out of int32 range)
```

### Parsed Fields

| Field | Description |
|---|---|
| `timestamp` | ISO-ish timestamp with milliseconds (`.` separator) |
| `level` | `ERROR`, `WARNING`, `INFO`, `DEBUG`, or `TRACE` |
| `logger` | Logger name (typically `changes_worker`) |
| `message` | Human-readable message text |
| `log_key` | Pipeline category in brackets: `CHANGES`, `OUTPUT`, etc. |
| `fields` | Structured key=value pairs (doc_id, mode, error_detail, etc.) |

### Timestamp Format

Timestamps use `.` as the millisecond separator (not `,`):

```
2026-04-19 00:52:12.008    ← current (JS-friendly)
2026-04-19 00:52:12,008    ← legacy (still parsed for backward compat)
```

This was changed in `pipeline_logging.py` via `default_msec_format = "%s.%03d"` on the `RedactingFormatter`. The API and UI accept both formats.

---

## Pipeline Stage Coloring

Every log line is color-coded by its pipeline stage based on the `log_key`. This matches the color conventions used on the Dashboard (`index.html`).

| Stage | Color | Log Keys | Description |
|---|---|---|---|
| **Source** | 🟢 Green | `CHANGES`, `HTTP`, `CHECKPOINT` | Incoming data from the changes feed |
| **Process** | 🔵 Blue | `PROCESSING`, `MAPPING`, `METRICS`, `CBL` | Filtering, routing, schema mapping |
| **Output** | 🟡 Yellow/Orange | `OUTPUT`, `RETRY` | Writing to the target database |
| **DLQ** | 🔴 Red | `DLQ` | Dead letter queue operations |

Each log line gets:
- A **tinted background** (8% opacity of the stage color)
- A **colored left border** (3px solid)

The stage mapping is defined in `logs.html`:

```javascript
var LOG_KEY_STAGE = {
  CHANGES: 'source', HTTP: 'source', CHECKPOINT: 'source',
  PROCESSING: 'process', MAPPING: 'process', METRICS: 'process', CBL: 'process',
  OUTPUT: 'output', RETRY: 'output',
  DLQ: 'dlq'
};
```

---

## Log File Picker

The **Log File** dropdown lists all `.log` files found in the `logs/` directory. Select a file to load its contents into the viewer. File size and last-modified time are shown next to the dropdown.

- Uses `GET /api/log-files` to list available files
- Passes `?file=<name>` to `GET /api/logs` when loading

---

## Date Range Slider

A dual-handle slider lets you narrow the visible logs to a specific time window:

- **Left handle** — sets the start time
- **Right handle** — sets the end time
- The label above the slider shows the selected `from → to` timestamps
- All filters (level, stage, log key, search) and **charts** update in real time as you drag

The slider covers the full range of timestamps in the loaded log data.

---

## Charts

Four charts below the log viewer react to all active filters (including the time range slider):

| Chart | Type | Shows |
|---|---|---|
| **Activity Timeline** | Multi-line | Per-minute counts of Total (dotted), Errors, Changes In, and Output Ops |
| **Pipeline Timeline** | Multi-line | Per-minute counts by pipeline stage: Source, Process, DLQ, Output |
| **Log Levels** | Bar | ERROR / WARNING / INFO / DEBUG distribution |
| **Pipeline Stages** | Bar | Source / Process / DLQ / Output distribution |

Charts use [Apache ECharts](https://echarts.apache.org/) — the same library used on the Dashboard page.

### Double-Click to Scroll

Double-clicking anywhere on the **Activity Timeline** or **Pipeline Timeline** chart scrolls the log viewer to the first log entry matching that timestamp bucket and flash-highlights it. This lets you quickly jump from a spike in the chart to the corresponding log lines.

### Stakes

Click a log line and select **📌 Stake** to pin a vertical dashed marker on the timeline charts at that entry's timestamp. Stakes help you visually correlate events across the timeline:

- Each stake gets a unique color and label (`S1`, `S2`, …)
- Staked log lines show a dashed right border and a colored tag
- A **📌 N stakes** badge appears above the Activity Timeline with a **✕** button to clear all stakes
- Click a staked line again to remove its stake
- The Y-axis scale can be toggled between **Linear** and **Log** using the button above the Pipeline Timeline

---

## Filters

### Pipeline Stage Filters

The top row of filter buttons (ordered **Source → Process → DLQ → Output**) lets you show/hide entire pipeline stages at once. Click a stage button to toggle it — dimmed = hidden. Each button shows a count of matching entries.

### Level Filters

Filter by log severity: **ERROR** (red), **WARNING** (yellow), **INFO** (blue), **DEBUG** (gray). All are enabled by default. Click to toggle.

### Log Key Filters

Fine-grained filtering by individual log key (`CHANGES`, `PROCESSING`, `MAPPING`, `OUTPUT`, `HTTP`, `CHECKPOINT`, `RETRY`, `METRICS`, `CBL`, `DLQ`). Click to toggle.

### Text Search

The search input filters log lines by text match across the message and all structured fields.

---

## Tail Mode

Click **▶ Tail** to enable live tailing:
- Polls `GET /api/logs` every **3 seconds**
- Auto-scrolls to the bottom of the log viewer
- Button changes to **⏸ Pause** (yellow) while active
- Click again to stop tailing

---

## Feed Control

The **Online / Offline** button in the top-right corner of the page lets you pause or resume the changes feed directly from the logs page — useful when debugging errors without new changes flooding in.

- Calls `POST /api/offline` or `POST /api/online`
- Polls `GET /api/worker-status` every 10 seconds to stay in sync
- Shows a green dot (●) when online, red when offline
- Same API as the sidebar Online/Offline toggle

---

## Error Insight Panel

Click any log line to select it. For **ERROR** lines, an insight panel appears below the log viewer with three sections:

### 1. Full Error Message

The complete raw log line displayed in a code block for easy reading and copying.

### 2. Structured Fields

A table showing all parsed key=value fields from the log line:

| Field | Example |
|---|---|
| `doc_id` | `postgres_02` |
| `mode` | `postgres` |
| `error_detail` | `DataError: invalid input for query argument $3: 77777777777 (value out of int32 range)` |

### 3. Smart Hints (💡 Suggestions)

The panel automatically detects common error patterns and shows actionable suggestions:

| Pattern | Hint |
|---|---|
| `out of int32 range` | 🔢 The value exceeds PostgreSQL INTEGER max (2,147,483,647). Change the column type to BIGINT. |
| `out of .* range` | 📏 Value exceeds the range of the target column type. Check your column DDL and consider using a larger type. |
| `DataError` or `data_type` | ⚠️ Data type mismatch between source value and target column. Check your schema mapping transforms. |
| `connection refused` | 🔌 Cannot connect to the target database. Verify host, port, and that the database is running. |
| `does not exist` | 🗄️ The referenced table or column does not exist. Run your CREATE TABLE DDL against the target database. |
| `unrecognised transform` | 🔧 Unknown transform function. Check the Glossary page for available transforms. |
| `duplicate key` / `unique violation` | 🔑 Primary key conflict. Check that your primary_key column is correctly mapped to a unique source field. |
| `permission denied` / `authentication failed` | 🔒 Database authentication failed. Check username and password in Settings. |
| `timeout` / `timed out` | ⏱️ Operation timed out. Check network connectivity and database performance. |

### Copy Error

The **📋 Copy Error** button copies the full raw log line to the clipboard for sharing or pasting into issue trackers.

---

## API Reference

### `GET /api/log-files`

Returns a list of `.log` files in the `logs/` directory, sorted newest first.

**Response:**

```json
[
  { "name": "changes_worker.log", "size_bytes": 1048576, "modified": "2026-04-19T02:13:23+00:00" }
]
```

### `GET /api/logs`

Returns parsed log entries from a log file.

**Query Parameters:**

| Param | Default | Max | Description |
|---|---|---|---|
| `lines` | `500` | `2000` | Number of lines to read from the end of the file |
| `file` | `changes_worker.log` | — | Name of the `.log` file to read (no path separators allowed) |
| `level` | _(all)_ | — | Minimum severity to return: `ERROR`, `WARNING`, `INFO`, or `DEBUG`. Filters server-side to reduce payload. |

**Response:**

```json
[
  {
    "timestamp": "2026-04-19 00:52:12.008",
    "level": "ERROR",
    "logger": "changes_worker",
    "message": "permanent error",
    "log_key": "OUTPUT",
    "fields": {
      "doc_id": "postgres_02",
      "mode": "postgres",
      "error_detail": "DataError: invalid input for query argument $3: 77777777777 (value out of int32 range)"
    }
  }
]
```

### Log Parsing Rules

The server-side parser in `server.py` (`_parse_log_line()`) handles:

1. **Timestamp** — accepts both `.` and `,` as millisecond separators
2. **Log key** — extracted from `[BRACKETS]` in the message body
3. **Simple fields** — `key=value` pairs where the key is in a known set (`doc_id`, `seq`, `mode`, `batch_size`, etc.)
4. **`error_detail`** — always parsed as the last field because its value can contain spaces, equals signs, and special characters

---

## Log Keys Reference

These are the structured `log_key` values used throughout the pipeline (defined in `pipeline_logging.py`):

| Log Key | Stage | Description |
|---|---|---|
| `CHANGES` | Source | `_changes` feed input — batch sizes, sequence numbers |
| `HTTP` | Source | HTTP requests/responses — bulk_get, doc fetches |
| `CHECKPOINT` | Source | Checkpoint load/save — sequence tracking |
| `PROCESSING` | Process | Filtering, routing, startup messages |
| `MAPPING` | Process | Schema mapping — transforms, field resolution |
| `METRICS` | Process | Metrics server events |
| `CBL` | Process | Couchbase Lite operations — open/close/maintenance |
| `OUTPUT` | Output | Database writes — upserts, inserts, errors |
| `RETRY` | Output | Retry/backoff decisions |
| `DLQ` | DLQ | Dead letter queue — add/retry/purge/list |

---

## Common Debugging Workflows

### "Value out of range" errors

1. Open `/logs` and filter to **OUTPUT** (or click the yellow **Output** stage button)
2. Find the ERROR line — click it to see the insight panel
3. The hint will tell you to change the column type (e.g., `INTEGER` → `BIGINT`)
4. Fix the column in your database DDL, then click **Online** to resume the feed

### "Table does not exist" errors

1. Filter to **OUTPUT** errors
2. The insight panel will suggest running your CREATE TABLE DDL
3. Use the DDL from the Schema Mapping page (import/export) to create the table
4. Resume the feed

### Debugging transform issues

1. Filter to **MAPPING** — look for "unrecognised transform" warnings
2. The insight panel links you to the Glossary page for the correct function name
3. Update your mapping JSON with the correct transform name

### Investigating slow performance

1. Enable **Tail** mode and watch the log stream
2. Filter to **HTTP** to see doc fetch times (`elapsed_ms` field)
3. Filter to **OUTPUT** to see database write times
4. Look for `timeout` or `timed out` patterns

### Pausing for investigation

1. Click the **Online** button → **Offline** to pause the feed
2. The worker stays alive but stops processing new changes
3. Review the log history, fix the issue
4. Click **Offline** → **Online** to resume
