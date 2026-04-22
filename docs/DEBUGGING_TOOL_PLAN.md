# CSDB Diagnostics Analyzer — Future Debugging Tool Plan

> **Goal:** Build a tool that ingests collected diagnostics bundles (from `/_collect` or `csdb_collect.sh`), parses all log sources into a unified timeline, and produces actionable debugging output.

---

## 1. The Problem

When debugging a production issue you currently have:
- **Project logs** (`changes_worker.log*`) — structured key=value format
- **CBL logs** (`*.cbllog`) — Couchbase Lite internal binary/text logs
- **System snapshots** (`ps`, `df`, `top`, etc.) — point-in-time text
- **Profiling** (thread stacks, process stats, GC stats, asyncio tasks)
- **Metrics** (Prometheus text format)
- **Config** (JSON)

These are scattered across files in different formats. Manually correlating "what happened at 13:04:35?" requires opening 5+ files and mentally stitching a timeline. This tool automates that.

---

## 2. Standardized Log Format Specification

### 2.1 Current Format (pipeline_logging.py)

```
2026-04-22 13:04:35.025 [DEBUG] changes_worker: _changes batch: 2 changes [CHANGES] batch_size=2
│                        │       │               │                        │         │
│                        │       │               │                        │         └─ structured fields (key=value)
│                        │       │               │                        └─ log_key tag
│                        │       │               └─ human message
│                        │       └─ logger name
│                        └─ level
└─ timestamp (local, millisecond precision)
```

### 2.2 Recommended Enhancements for Timeline Analysis

To make logs machine-parseable for timeline reconstruction, standardize on these rules:

| Field | Current | Recommended | Why |
|-------|---------|-------------|-----|
| **Timestamp** | `2026-04-22 13:04:35.025` (local) | `2026-04-22T13:04:35.025Z` (ISO 8601 UTC) | Unambiguous across timezones; sortable |
| **Event ID** | none | Add `event_id=<uuid-short>` to correlated events | Trace a doc through CHANGES→MAPPING→OUTPUT |
| **Correlation ID** | none | Add `batch_id=<int>` linking all events in one batch | Group related events |
| **Duration** | `elapsed_ms=50.1` (some events) | Always include on completion events | Measure every stage |
| **Sequence** | `seq=599500` (some events) | Always include when available | Order events |

### 2.3 Proposed Enhanced Format

```
2026-04-22T13:04:35.025Z [INFO] changes_worker: _changes batch: 2 changes [CHANGES] batch_id=42 batch_size=2 seq_start=599500 seq_end=599501
2026-04-22T13:04:35.025Z [DEBUG] changes_worker: change row [CHANGES] batch_id=42 doc_id=foo_39589 seq=599500
2026-04-22T13:04:35.027Z [DEBUG] changes_worker: transform applied [MAPPING] batch_id=42 doc_id=foo_39589 elapsed_ms=2.1
2026-04-22T13:04:35.078Z [DEBUG] changes_worker: executed SQL ops [OUTPUT] batch_id=42 doc_id=foo_39589 operation=UPSERT elapsed_ms=49.0 mode=postgres
2026-04-22T13:04:35.079Z [INFO] changes_worker: batch complete [PROCESSING] batch_id=42 succeeded=2 failed=0 elapsed_ms=54.0
```

### 2.4 Log Line Regex for Parsing

```python
import re

LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d{3}Z?)\s+"
    r"\[(?P<level>\w+)\]\s+"
    r"(?P<logger>\S+):\s+"
    r"(?P<message>.*?)\s+"
    r"\[(?P<log_key>\w+)\]"
    r"(?P<fields>(?:\s+\w+=\S+)*)"
    r"\s*$"
)

FIELD_PATTERN = re.compile(r"(\w+)=(\S+)")
```

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        csdb_analyze                                 │
│                                                                     │
│  ┌──────────┐   ┌───────────┐   ┌───────────┐   ┌──────────────┐  │
│  │  Ingest   │──▶│  Parse &  │──▶│ Correlate │──▶│   Render     │  │
│  │  (unzip)  │   │ Normalize │   │ & Analyze │   │   Output     │  │
│  └──────────┘   └───────────┘   └───────────┘   └──────────────┘  │
│       │              │               │                │             │
│  Read zip       Parse each      Build unified    Markdown report   │
│  files          source into      timeline +       JSON timeline    │
│                 Event objects     detect issues    HTML dashboard   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Unified Event Schema

All log sources get normalized into this internal structure:

```python
@dataclass
class Event:
    """A single event on the unified timeline."""
    timestamp: datetime          # UTC
    source: str                  # "project_log" | "cbl_log" | "system" | "metrics" | "profiling"
    level: str                   # "TRACE" | "DEBUG" | "INFO" | "WARN" | "ERROR" | "CRITICAL"
    log_key: str                 # "CHANGES" | "PROCESSING" | "MAPPING" | "OUTPUT" | "CHECKPOINT" | ...
    message: str                 # Human-readable message
    fields: dict[str, str]       # All structured key=value pairs

    # Derived / enriched
    doc_id: str | None = None    # Extracted from fields if present
    batch_id: str | None = None  # Extracted from fields if present
    seq: int | None = None       # Extracted from fields if present
    operation: str | None = None # INSERT | UPDATE | DELETE | SELECT
    elapsed_ms: float | None = None
    error: bool = False          # True if level is ERROR/CRITICAL or message indicates failure
```

---

## 5. Parsers (one per source type)

### 5.1 Project Log Parser
- **Input:** `project_logs/changes_worker.log*`
- **Format:** Line-based, regex parse with `LOG_PATTERN`
- **Output:** `Event` objects with all structured fields

### 5.2 CBL Log Parser
- **Input:** `cbl_logs/*.cbllog*`
- **Format:** Couchbase Lite internal log format
- **Strategy:** Parse header/timestamps, map CBL log levels to standard levels
- **Note:** Binary `.cbllog` files may need the `cbllog` tool to decode

### 5.3 System Snapshot Parser
- **Input:** `system/*.txt`
- **Strategy:** Each file becomes a single `Event` with the snapshot as the message body
- **Key extractions:**
  - `df.txt` → disk usage percentages (flag if > 85%)
  - `free.txt` → available memory (flag if < 10%)
  - `top.txt` → CPU load averages

### 5.4 Profiling Parser
- **Input:** `profiling/*.txt`, `profiling/*.json`
- **Strategy:** Parse structured data, extract key metrics
- **Key extractions:**
  - `psutil_process.json` → RSS, CPU times, thread count, FD count
  - `thread_stacks.txt` → Detect threads stuck in I/O or locks
  - `asyncio_tasks.txt` → Detect blocked/stuck tasks
  - `gc_stats.json` → Generation counts, collection pauses

### 5.5 Metrics Parser
- **Input:** `metrics_snapshot.txt`
- **Format:** Prometheus text exposition
- **Strategy:** Parse counter/gauge values, generate summary events
- **Key extractions:**
  - Error rates (poll_errors, output_errors, doc_fetch_errors)
  - Throughput (changes_processed_total, output_requests_total)
  - Resource usage (memory, CPU, FDs)
  - DLQ state (pending count, last write time)

---

## 6. Analysis Modules

### 6.1 Timeline Builder
- Sort all `Event` objects by timestamp
- Group by configurable windows (1s, 5s, 30s, 1min)
- Detect gaps (periods with no events → possible freeze/crash)

### 6.2 Error Detector
- Find ERROR/CRITICAL events
- Look backward for context (what happened before the error)
- Classify error patterns:
  - **Connection errors** → network/upstream issues
  - **Timeout errors** → slow upstream or resource exhaustion
  - **Permission errors** → auth/config issues
  - **Data errors** → malformed docs, schema mismatches

### 6.3 Performance Analyzer
- Track `elapsed_ms` per stage (CHANGES → MAPPING → OUTPUT)
- Calculate p50/p95/p99 per stage
- Detect latency spikes (> 3σ from mean)
- Identify slow docs (doc_ids with highest elapsed_ms)

### 6.4 Document Lifecycle Tracker
- Follow a specific `doc_id` through all stages:
  1. Received in `_changes` batch (`[CHANGES]`)
  2. Fetched via bulk_get/GET (`[HTTP]`)
  3. Schema-mapped (`[MAPPING]`)
  4. Forwarded to output (`[OUTPUT]`)
  5. Checkpoint saved (`[CHECKPOINT]`)
- Flag incomplete lifecycles (received but never output → stuck/failed)

### 6.5 Throughput Monitor
- Calculate docs/second over sliding windows
- Detect throughput drops (< 50% of rolling average)
- Correlate drops with errors, GC pauses, or system resource changes

### 6.6 Resource Correlator
- Overlay system metrics (CPU, memory, disk) with application events
- Detect: "errors started when disk hit 95%" or "latency spiked when RSS grew"

---

## 7. Output Formats

### 7.1 Terminal Summary (default)
```
═══════════════════════════════════════════════════════════════
  CSDB Diagnostics Analysis — 2026-04-22T13:04:34Z
  Bundle: csdb_collect_worker1_20260422_130434.zip
  Time range: 13:04:34.579 → 13:04:38.123 (3.5s)
═══════════════════════════════════════════════════════════════

🔴 ERRORS (2)
  13:04:36.100 [OUTPUT] Connection refused to postgres:5432  doc_id=foo_42700
  13:04:36.500 [OUTPUT] Connection refused to postgres:5432  doc_id=foo_42701

⚠️  WARNINGS (1)
  Project logs truncated (exceeded 200MB cap)

📊 THROUGHPUT
  Changes received:   5,000 docs (1,428/s)
  Changes processed:  4,998 docs (1,428/s)
  Output forwarded:   4,996 docs (1,427/s)
  Failed:             2 docs (sent to DLQ)

⏱️  LATENCY (OUTPUT stage)
  p50: 50ms  p95: 120ms  p99: 266ms  max: 266ms

💾 RESOURCES
  RSS: 245 MB  |  CPU: 12.3s user  |  Threads: 8  |  FDs: 42
  Disk: /app 45% used  |  /tmp 12% used

🔍 SLOW DOCS (top 5)
  foo_42694  elapsed_ms=266.0  operation=UPSERT
  foo_39589  elapsed_ms=180.2  operation=UPSERT
  ...
```

### 7.2 JSON Timeline (`--format json`)
```json
{
  "metadata": {
    "bundle": "csdb_collect_worker1_20260422_130434.zip",
    "time_range": {"start": "2026-04-22T13:04:34.579Z", "end": "2026-04-22T13:04:38.123Z"},
    "event_count": 12500
  },
  "timeline": [
    {
      "timestamp": "2026-04-22T13:04:34.579Z",
      "source": "project_log",
      "level": "DEBUG",
      "log_key": "CHECKPOINT",
      "message": "checkpoint save detail",
      "fields": {"operation": "UPDATE", "doc_id": "checkpoint-b84ef...", "seq": "599499", "storage": "sg"}
    }
  ],
  "errors": [...],
  "slow_docs": [...],
  "throughput": {"docs_per_second": [...]},
  "resources": {...}
}
```

### 7.3 HTML Dashboard (`--format html`)
- Interactive timeline with zoom/scroll
- Click events to see full context
- Filter by log_key, level, doc_id
- Overlay throughput + error rate charts

---

## 8. CLI Interface

```bash
# Basic analysis
csdb_analyze diagnostics.zip

# Follow a specific document
csdb_analyze diagnostics.zip --doc foo_39589

# Export JSON timeline for external tools
csdb_analyze diagnostics.zip --format json -o timeline.json

# Compare two bundles (before/after)
csdb_analyze --diff before.zip after.zip

# Only show errors and warnings
csdb_analyze diagnostics.zip --level warn

# Filter by time range
csdb_analyze diagnostics.zip --after "13:04:35" --before "13:04:36"

# Filter by log key
csdb_analyze diagnostics.zip --key OUTPUT,CHECKPOINT

# Export for LLM analysis (structured markdown)
csdb_analyze diagnostics.zip --format llm -o analysis_context.md
```

---

## 9. Multi-Bundle Analysis

When you have bundles from multiple time points or multiple instances:

```bash
# Merge and correlate
csdb_analyze bundle1.zip bundle2.zip bundle3.zip --merge

# This produces a combined timeline showing:
# - Events from all bundles on a single timeline
# - Cross-instance correlation (if hostnames differ)
# - Trend analysis (is the error rate growing?)
```

---

## 10. LLM Integration Mode

The `--format llm` output produces a structured markdown document optimized for feeding to an LLM (Claude, GPT, etc.) for deeper analysis:

```markdown
# CSDB Diagnostics Context

## System State
- Container: python:3.12-slim on arm64
- RSS: 245MB, CPU: 12.3s user, 8 threads, 42 FDs
- Disk: /app 45%, /tmp 12%

## Error Summary
2 errors found, both OUTPUT connection failures starting at 13:04:36.100

## Timeline (errors and surrounding context, ±5s)
| Time | Key | Message | Fields |
|------|-----|---------|--------|
| 13:04:35.079 | OUTPUT | document forwarded | doc_id=foo_42694 elapsed_ms=50.1 |
| 13:04:35.082 | CHECKPOINT | checkpoint saved | seq=599501 |
| 13:04:36.100 | OUTPUT | Connection refused | doc_id=foo_42700 |
| ... | | | |

## Configuration (redacted)
{ ... }

## Question
What caused the connection failures at 13:04:36? Analyze the timeline and suggest root cause and remediation.
```

---

## 11. Implementation Phases

### Phase 1 — Core Parser + Timeline (MVP)
- Project log parser (regex-based)
- Unified Event schema
- Timeline builder + sort
- Terminal summary output
- `csdb_analyze <zip>` CLI

### Phase 2 — Analysis Modules
- Error detector with classification
- Performance analyzer (latency percentiles)
- Document lifecycle tracker (`--doc <id>`)
- Throughput monitor

### Phase 3 — Additional Parsers
- Metrics parser (Prometheus text)
- Profiling parser (psutil JSON, thread stacks, asyncio tasks)
- System snapshot parser (disk/memory/CPU extraction)
- CBL log parser

### Phase 4 — Advanced Output
- JSON timeline export
- HTML dashboard (Chart.js or similar)
- LLM context export (`--format llm`)
- Multi-bundle merge + diff

### Phase 5 — Integration
- Auto-trigger collection on crash (watchdog)
- Upload to S3/GCS for remote debugging
- Slack/webhook notification with summary
- CI pipeline integration (analyze bundles from test runs)

---

## 12. File Layout

```
tools/
├── csdb_analyze.py          # CLI entry point
├── analyzers/
│   ├── __init__.py
│   ├── timeline.py          # Timeline builder
│   ├── errors.py            # Error detection + classification
│   ├── performance.py       # Latency analysis
│   ├── lifecycle.py         # Document lifecycle tracker
│   ├── throughput.py        # Throughput monitoring
│   └── resources.py         # Resource correlation
├── parsers/
│   ├── __init__.py
│   ├── project_log.py       # changes_worker.log parser
│   ├── cbl_log.py           # CBL .cbllog parser
│   ├── system.py            # System snapshot parser
│   ├── metrics.py           # Prometheus text parser
│   └── profiling.py         # Thread stacks, psutil, GC parser
├── renderers/
│   ├── __init__.py
│   ├── terminal.py          # Terminal summary + colors
│   ├── json_out.py          # JSON timeline export
│   ├── html.py              # HTML dashboard
│   └── llm.py               # LLM-optimized markdown
└── models.py                # Event dataclass + enums
```

---

## 13. Dependencies

Minimal — the tool should work with only stdlib + existing deps:

| Package | Purpose | Required? |
|---------|---------|-----------|
| Python 3.10+ | Dataclasses, match/case | Yes |
| `re` | Log parsing | stdlib |
| `zipfile` | Bundle extraction | stdlib |
| `json` | Metrics/config parsing | stdlib |
| `argparse` | CLI | stdlib |
| `statistics` | Percentile calculations | stdlib |
| `rich` | Terminal formatting | Optional (degrades gracefully) |
| `chart.js` | HTML dashboard charts | Optional (bundled as static asset) |

---

## 14. Key Design Decisions

1. **Parsers are tolerant** — Malformed lines are skipped, not fatal
2. **Timestamps are always UTC** — Convert on ingest, never display local time
3. **Fields are always strings** — Numeric conversion happens in analyzers, not parsers
4. **No database** — Everything fits in memory for typical bundles (< 100MB uncompressed)
5. **Streaming for large bundles** — If > 500MB uncompressed, switch to streaming mode
6. **Idempotent** — Running twice on the same bundle produces identical output
