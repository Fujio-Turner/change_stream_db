# Log Collection API (`POST /_collect`)

The `/_collect` endpoint provides a way to gather comprehensive diagnostics from a running `changes_worker` instance, similar to Sync Gateway's `sgcollect_info` tool.

## Overview

`POST /_collect` generates a `.zip` file containing:
- **Project logs** — rotating logs from `logs/changes_worker.log*`
- **Couchbase Lite logs** — CBL internal file logs (if CBL is enabled)
- **System info** — OS-level diagnostics (uname, ps, df, netstat, dmesg/sysctl, etc.)
- **Process profiling** — CPU profile (cProfile), memory profile (tracemalloc), thread stacks, psutil process stats, GC stats
- **Configuration** — redacted config.json
- **Metrics** — Prometheus metrics snapshot
- **Status** — worker status
- **Metadata** — collection timestamp, hostname, duration, any warnings

## Endpoint

```
POST /http://<metrics_host>:<metrics_port>/_collect
```

**Note:** The endpoint is served on the **metrics/admin port** (default: `http://localhost:9090/_collect`), not the main HTTP server port.

## Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `include_profiling` | bool | `true` | Include CPU/memory profiling snapshots |

## Response

**Status:** `200 OK`

**Content-Type:** `application/zip`

**Content-Disposition:** `attachment; filename=csdb_collect_<hostname>_<timestamp>.zip`

**Body:** Binary zip file containing the collected diagnostics.

## Zip File Structure

```
csdb_collect_<hostname>_<timestamp>/
├── cbl_logs/                        # Couchbase Lite file logs (if CBL enabled)
│   ├── *.cbllog
│   └── *.cbllog.*
├── project_logs/                    # Application rotating logs
│   ├── changes_worker.log
│   ├── changes_worker.log.*
│   └── ...
├── system/                          # OS-level diagnostics
│   ├── uname.txt
│   ├── ps_aux.txt
│   ├── df.txt
│   ├── top.txt
│   ├── free.txt / vm_stat.txt
│   ├── netstat.txt
│   ├── lsof.txt
│   ├── ifconfig.txt
│   ├── ulimit.txt
│   ├── dmesg.txt                    # Linux only
│   ├── sysctl.txt                   # macOS only
│   ├── env.txt
│   └── *_error.txt                  # If command failed
├── profiling/                       # Process profiling snapshots
│   ├── cprofile.txt                 # CPU profile (top 50 functions)
│   ├── tracemalloc_top50.txt        # Memory allocations (top 50)
│   ├── thread_stacks.txt            # Stack traces for all threads
│   ├── psutil_process.json          # Process stats (PID, memory, CPU, FDs, etc.)
│   ├── gc_stats.json                # Garbage collector stats
│   └── *_error.txt                  # If collection failed
├── config/                          # Configuration
│   ├── config_redacted.json         # Running config (sensitive fields redacted)
│   └── version.json                 # Version info (app, Python, platform)
├── metrics_snapshot.txt             # Prometheus metrics at time of collection
├── status.json                      # Worker status snapshot
└── collect_info.json                # Metadata (timestamp, hostname, duration, warnings)
```

## Configuration

Optional `collect` section in `config.json` (all fields have sensible defaults):

```json
{
  "collect": {
    "tmp_dir": "/tmp",
    "max_log_size_mb": 200,
    "profile_seconds": 5,
    "system_command_timeout_seconds": 30,
    "include_cbl_logs": true,
    "default_redaction": "partial"
  }
}
```

| Setting | Default | Description |
|---|---|---|
| `tmp_dir` | `/tmp` | Temporary directory for staging files |
| `max_log_size_mb` | `200` | Cap total project/CBL log size (oldest rotated files dropped first) |
| `profile_seconds` | `5` | Duration of CPU profiling snapshot |
| `system_command_timeout_seconds` | `30` | Timeout for each OS command |
| `include_cbl_logs` | `true` | Include Couchbase Lite logs |
| `default_redaction` | `partial` | Default redaction level for config (`none`, `partial`, `full`) |

## Usage Examples

### cURL (download to file)

```bash
curl -X POST http://localhost:9090/_collect \
  -o diagnostics.zip
```

### Python

```python
import requests

response = requests.post("http://localhost:9090/_collect?include_profiling=true", stream=True)
with open("diagnostics.zip", "wb") as f:
    for chunk in response.iter_content():
        f.write(chunk)
```

### JavaScript/Node.js

```javascript
const response = await fetch("http://localhost:9090/_collect", {
  method: "POST",
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = "diagnostics.zip";
a.click();
```

## Performance Considerations

- **Profiling overhead:** Including `include_profiling=true` adds ~5s to collection time (CPU profile duration configurable)
- **Log size:** Older rotated logs are dropped first if total exceeds `max_log_size_mb`
- **System commands:** Each command times out after `system_command_timeout_seconds` (default 30s)
- **Zip compression:** Output is compressed, typical size 1–10 MB depending on log volume

## Security

- **Config redaction:** Sensitive fields (passwords, tokens, API keys) are redacted using the configured redaction level
- **Environment variables:** Secrets are filtered (`*PASSWORD*`, `*SECRET*`, `*TOKEN*`, `*KEY*` patterns)
- **Admin-only endpoint:** Served on the metrics/admin port, should not be exposed publicly
- **No credentials in plaintext:** All sensitive data is obfuscated in the output

## Error Handling

If a collector fails:
1. A `<category>_error.txt` file is written to the zip with the error details
2. Collection continues for other categories
3. The `collect_info.json` includes a `warnings` array listing any partial failures
4. The overall response is still `200 OK` with the partial zip

## Logging

Collection operations are logged to the main application logger with `CONTROL` log key:

```
INFO  CONTROL diagnostics collection complete: csdb_collect_host_20240101_150000.zip
```

Errors are logged as warnings:

```
WARNING Error collecting profiling data: ...
```

## Troubleshooting

**Q: The zip file is empty or very small**
- Check that logs exist in `logs/` directory
- Verify `include_profiling=false` was not accidentally set
- Check application logs for collection errors

**Q: System commands return errors**
- Some commands (e.g., `dmesg`, `sysctl`) may require elevated permissions
- Errors are captured in `<command>_error.txt` files in the zip
- Collection continues despite individual command failures

**Q: Endpoint returns 500 error**
- Check application logs for detailed error message
- Verify metrics server is running (`metrics.enabled: true` in config)
- Ensure adequate disk space for temp files

## Comparison with sgcollect_info

| Feature | sgcollect_info | /_collect |
|---|---|---|
| Trigger | CLI tool | REST endpoint |
| Log collection | Multi-level SG logs | Project logs + CBL logs |
| System info | OS tasks (platform-specific) | Same approach, platform-aware |
| Profiling | Go pprof (profile, heap, goroutine, mutex, etc.) | Python cProfile, tracemalloc, thread stacks |
| Configuration | SG config + runtime state | Redacted config.json |
| Metrics | Expvar JSON | Prometheus scrape |
| Redaction | `password_remover.py` | Existing `Redactor` class |
| Output | Zip file (optional S3 upload) | Zip file via HTTP download |
