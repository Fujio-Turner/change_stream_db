# Admin UI – Dashboard & Management

This document describes the web-based admin UI for Changes Worker: the dashboard, config editor, schema mapping editor, and the API endpoints that power them.

---

## Overview

The admin UI is a lightweight aiohttp web server (`web/server.py`) that provides:

1. **Dashboard** — Real-time health status indicators, three-column metrics breakdown, and live line charts
2. **Config Editor** — Form-based and raw JSON editing of the worker configuration
3. **Schema Mappings** — Visual JSON mapping editor with source/target split-pane, drag-and-drop field mapping, transform functions, and relationship visualization
4. **Transforms Reference** — Documentation page listing all 58 available transform functions with descriptions and examples

All pages use DaisyUI + Tailwind CSS with a shared dark/light theme toggle that persists across pages via `localStorage`.

---

## Running the Admin UI

```bash
# Standalone (local development)
python web/server.py --port 8080

# Docker Compose (alongside the worker)
docker compose up admin-ui
```

The admin UI runs on port **8080** by default. The worker runs separately on its own port and exposes a Prometheus metrics endpoint on port **9090**.

---

## Pages

### Dashboard (`/`)

The dashboard provides a single-screen overview of the entire worker pipeline.

#### Status Indicators

A horizontal bar at the top shows four colored dots representing system health:

| Indicator | Green | Yellow | Red |
|---|---|---|---|
| **Changes Feed** | Polling with < 10% error ratio | Polling with > 10% error ratio | Metrics unreachable / worker not running |
| **Processing** | Worker uptime > 30s | Worker uptime < 30s (warming up) | Worker not running |
| **Couchbase Lite** | CBL available (`USE_CBL=True`) | -- | CBL not installed |
| **Output** | stdout mode + worker up, or HTTP endpoint up with no errors | HTTP endpoint up but > 10% error ratio | HTTP endpoint down |

Status for Changes Feed, Processing, and Output is computed **client-side** from the Prometheus metrics the dashboard already fetches (no separate round-trip). CBL status comes from `/api/status` (server-side only knowledge).

All four status dots start as **yellow** (unknown) on page load. Once data is fetched, each dot transitions to its computed color. When the worker's metrics endpoint is unreachable or metrics are disabled in the config, the feed/processing/output dots remain **yellow** (unknown / not monitored) — never red. The CBL dot is set independently from `/api/status`.

#### Charts Row

Three live line charts sit directly below the status bar in their own card row, one per pipeline stage. Charts use inline `style` dimensions (not Tailwind classes) to ensure ECharts has a non-zero container height before Tailwind JS processes.

#### Grouped Stats

Below the charts, stats are organized into rounded cards (`rounded-2xl`) with related metrics grouped together. Each group has a DaisyUI tooltip (question-mark badge) explaining the group on hover.

| LEFT — Changes Feed | MIDDLE — Processing | RIGHT — Output |
|---|---|---|
| **Polling** — Poll Cycles, Poll Errors, Retries | **Document Processing** — Docs Processed, Docs Fetched | **Requests** — Total, Success, Errors, PUT, DELETE |
| **Changes Received** — Received, Last Batch, Bytes, Deletes, Removes | **Filtered** — Total, Deletes, Removes | **Throughput** — Bytes Sent, Dead Letters, Endpoint |
| **Checkpoint** — Seq, Saves | **Config Summary** — Key settings | **Response Times** — p50, p99 |

All values come from the Prometheus metrics endpoint (`/api/metrics`) and auto-refresh every **5 seconds**. Values are formatted with human-readable suffixes (K, M, G for numbers; KB, MB, GB for bytes).

#### Line Charts

Each column includes a live line chart at the bottom showing per-interval deltas (not cumulative totals). The charts use ECharts and maintain a rolling 30-point window:

| LEFT chart | MIDDLE chart | RIGHT chart |
|---|---|---|
| Changes Received (green) | Docs Processed (blue) | Output Success (green) |
| Poll Errors (red) | Docs Filtered (yellow) | Output Errors (red) |
| | | Dead Letters (yellow) |

Charts adapt to the current theme (dark/light) automatically. When the metrics endpoint is unreachable, charts still render and advance with zero-value data points (flat lines at zero), so the time axis stays live and the multi-line legend remains visible.

#### Debug Mode

Add `?debug=true` to the dashboard URL to enable verbose `console.log()` output in the browser:

```
http://localhost:8080/?debug=true
```

Debug output (prefixed with `[CW]`) shows:
- Every API fetch (URL, status, content-type)
- Prometheus parser results (parsed count, keys found)
- Key metric values (uptime, poll cycles, errors, output counts)
- Status dot computation (input values and resulting color per indicator)
- Config load results
- CBL status response

This is useful for diagnosing why status indicators show unexpected colors.

---

### Config Editor (`/config`)

A full-featured editor for the worker's `config.json` with two views:

#### Form Editor (default)

Collapsible sections for each config block:

| Section | Fields |
|---|---|
| **Gateway** | Source type, URL, database, scope, collection, self-signed certs toggle |
| **Auth** | Method selector (basic/session/bearer/none) with conditional field visibility |
| **Changes Feed** | Feed type, poll interval, active only, include docs, channels, throttle, HTTP timeout |
| **Processing** | Ignore delete, ignore remove, sequential, max concurrent, dry run |
| **Output** | Mode (stdout/http/db), target URL, output format, halt on failure |
| **Checkpoint** | Enabled, client ID, every N docs |
| **Metrics** | Enabled, host, port |
| **Logging** | Level (DEBUG/INFO/WARNING/ERROR) |

#### Raw JSON

A monospace textarea for direct JSON editing. Switching between tabs syncs data in both directions.

#### Save / Reset

- **Save Config** -- `PUT /api/config` with the current form or JSON content
- **Reset** -- Reloads from the server, discarding unsaved changes

Toast notifications confirm save success or show errors.

---

### Schema Mappings (`/schema`)

A visual JSON mapping editor with a split-pane layout for mapping source documents to target tables.

#### Top Action Bar

- **Filename** input, **Save**, **Download** buttons
- **Sample Templates** dropdown -- Pre-built mappings organized by output mode (Tables: Orders, Profiles, Products; JSON: Orders, Events, Sensors)
- **Saved Files** dropdown -- Load or delete previously saved mapping files

#### Left Panel — Source (45%)

Three input modes for providing source document schema:

| Tab | Description |
|---|---|
| **Paste JSON** | Paste a sample JSON document from the _changes feed |
| **JSON Schema** | Paste a JSON Schema (json-schema.org format) |
| **Live Sample** | Fetch a random document from the _changes feed (fetches 100 docs via `?limit=100`, returns a random pick each click) via `/api/sample-doc` |

Below the input, **Source Fields** shows all extracted JSON paths with types. Fields are draggable — drag a field and drop it onto a column mapping input on the right.

#### Right Panel — Target Output (55%)

An **Output Mode** toggle at the top switches between **Tables** and **JSON** mode:

**Tables mode:**
- **Source Match** -- Define which documents this mapping applies to (field + value match)
- **Table Tabs** -- Each target table gets its own tab. Click "+ Table" to add tables
- **Table Settings** -- Table name, primary key, on-delete behavior
- **Parent / Foreign Key** -- For child tables: parent selector, FK column, source array, replace strategy
- **Column Mappings** -- Map source JSON paths to target column names. Each column has a **Transform** dropdown (categorized with optgroups) and an editable text input to customize parameters or write custom chains. Selecting a transform from the dropdown auto-injects the source path into the function (e.g., selecting `trim()` with source path `$.total` populates `trim($.total)` in the edit field)

**JSON mode:**
- **JSON Field Mappings** -- Map source JSON paths to target JSON keys for JSON-to-JSON remapping
- Each field has the same transform dropdown + editable input with auto-injection of source paths
- No table/FK/parent concepts — just flat key-value mappings with optional transforms

#### Source Path Autocomplete

All source path inputs (`col-path` and `jf-path`) have a custom autocomplete dropdown that appears as you type, suggesting:
- **Source Fields** — JSON paths extracted from the loaded source document
- **Transforms** — matching function names from the transform library

Supports keyboard navigation (↑/↓/Enter/Escape) and click selection.

#### Relationship Diagram

Renders an ECharts force-directed graph showing:
- **Blue nodes** = parent tables (sized by column count)
- **Yellow nodes** = child tables
- **Arrows** = foreign key relationships with FK column labels

The diagram updates automatically whenever the mapping changes.

#### Mapping Coverage Stats

Live coverage indicators show mapping completeness on both sides of the editor:

**Source Coverage** (left panel, below Source Fields):
- Shows what percentage of source fields are mapped to the right side (e.g., "50% — 5 / 10 fields mapped")
- Progress bar color: green (100%), yellow (≥ 50%), red (< 50%)
- Expandable list of unmapped source fields (only counts leaf fields — objects and arrays are excluded)

**Target Coverage** (right panel, below column mappings):
- Shows what percentage of target columns have a source path filled in (e.g., "100% — 6 / 6 columns filled")
- Progress bar color: green (100%), red (< 100%)
- Any target columns with an empty source path are highlighted with red warning badges
- For Tables mode, reads directly from the DOM for the active table so edits are reflected immediately
- Columns from inactive tables (other tabs) are also counted from saved state

Both indicators update in real time on every mapping change.

#### Generated Mapping JSON

A collapsible section at the bottom shows the complete mapping JSON that will be saved. This is the same format consumed by the worker.

#### Sample Templates

Six pre-built templates organized by output mode, each with source documents and complete mappings:

**Tables templates:**

| Template | Parent Table | Child Table | Demonstrates |
|---|---|---|---|
| **Orders** | `orders` (6 cols) | `order_items` (4 cols) | Flat fields + nested array extraction |
| **Profiles** | `profiles` (10 cols) | `profile_tags` (2 cols) | Nested object flattening + simple array |
| **Products** | `products` (9 cols) | `product_variants` (4 cols) | Deep nested paths + variant arrays |

**JSON templates:**

| Template | Fields | Demonstrates |
|---|---|---|
| **Orders (JSON)** | 10 | Date format conversion (`to_epoch()` vs `to_iso8601()` vs `from_epoch()`), chained `from_epoch().format_date()`, `to_decimal()`, `trim()` |
| **Events (JSON)** | 9 | Epoch→ISO conversion, chained `trim().lowercase()` on email, `replace().propercase()`, `join()` on arrays |
| **Sensors (JSON)** | 12 | `to_epoch()` / `format_date()` on timestamps, `to_decimal()` for precision, `to_string()` type coercion, `sha256()` hashing |

---

### Transform Functions Reference (`/transforms`)

A static documentation page listing all 58 available transform functions organized by category:

| Category | Count | Functions |
|---|---|---|
| **String** | 19 | `trim`, `ltrim`, `rtrim`, `uppercase`, `lowercase`, `camelcase`, `propercase`, `concat`, `replace`, `replace_regex`, `strip_chars`, `pad_left`, `pad_right`, `substr`, `split`, `join`, `length`, `urlencode`, `urldecode` |
| **Numeric** | 9 | `to_int`, `to_float`, `to_decimal`, `to_string`, `round`, `ceil`, `floor`, `abs`, `coalesce` |
| **Date / Time** | 9 | `to_iso8601`, `to_epoch`, `from_epoch`, `format_date`, `parse_date`, `date_add`, `date_diff`, `truncate_date`, `now` |
| **Array / Object** | 4 | `flatten`, `slice`, `keys`, `values` |
| **Encoding / Hash** | 8 | `json_safe`, `json_parse`, `json_stringify`, `base64_encode`, `base64_decode`, `md5`, `sha256`, `uuid` |
| **Conditional** | 1 | `if` |

Each function includes a description, parameter syntax, and example showing input → output. The page also documents transform chaining (dot notation) and the mapping JSON format for transforms.

---

## API Endpoints

### Config

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config` | Load current config (from CBL or `config.json`) |
| `PUT` | `/api/config` | Save config (to CBL or `config.json`) |

### Mappings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/mappings` | List all mapping files |
| `GET` | `/api/mappings/{name}` | Get one mapping file |
| `PUT` | `/api/mappings/{name}` | Create or update a mapping file |
| `DELETE` | `/api/mappings/{name}` | Delete a mapping file |

### Dead Letter Queue

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/dlq` | List all DLQ entries |
| `GET` | `/api/dlq/count` | Count of DLQ entries |
| `GET` | `/api/dlq/{id}` | Get one entry (includes full doc body) |
| `POST` | `/api/dlq/{id}/retry` | Mark entry as retried |
| `DELETE` | `/api/dlq/{id}` | Delete one entry |
| `DELETE` | `/api/dlq` | Clear all DLQ entries |

### Status & Metrics

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | CBL availability status |
| `GET` | `/api/metrics` | Proxy to worker's Prometheus `/_metrics` endpoint |
| `GET` | `/api/sample-doc` | Fetch 100 docs from the _changes feed and return one at random for schema mapping |

The metrics proxy normalizes the bind address (`0.0.0.0` -> `127.0.0.1`) before connecting to the worker's metrics server. The sample-doc endpoint reads the current gateway/auth config and fetches a single document with `limit=1&include_docs=true`.

### Static & Pages

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard page |
| `GET` | `/config` | Config editor page |
| `GET` | `/schema` | Schema mappings page |
| `GET` | `/transforms` | Transform functions reference page |
| `GET` | `/static/...` | CSS, JS assets (DaisyUI, Tailwind, ECharts) |

---

## Theme System

All pages share a consistent theme toggle:

- **Toggle** -- `Dark [toggle] Light` label with a DaisyUI toggle switch
- **Persistence** -- Theme is stored in `localStorage` key `cw_theme` (`"dark"` or `"light"`)
- **Pre-paint** -- An inline IIFE in `<head>` applies the saved theme before the page renders, preventing a flash of the wrong theme
- **Cross-page** -- Theme carries over when navigating between Dashboard, Config, Schema Mappings, and Transforms pages

---

## Shared Page Structure

Every page follows the same layout:

```
<nav>   Navbar with title, page links (Dashboard, Config, Schema Mappings, Transforms — active highlight), theme toggle
<main>  Page-specific content
<footer> "Changes Worker v1.2.0"
```

Consistent classes across all pages: `min-h-screen flex flex-col bg-base-200` on `<body>`, `<nav class="navbar bg-base-100 shadow-sm px-4">` for the navbar.

---

## Dependencies

| Asset | Source | Location |
|---|---|---|
| DaisyUI CSS | CDN (bundled) | `/static/css/daisyui.css` |
| DaisyUI Themes | CDN (bundled) | `/static/css/themes.css` |
| Tailwind JS | CDN (bundled) | `/static/js/tailwind.js` |
| ECharts | CDN (bundled) | `/static/js/echarts.min.js` |
| Favicon SVG | Local | `/static/favicon.svg` |

All assets are served locally from `/static/` -- no external CDN calls at runtime. The admin UI works fully offline once the container is built.
