# Log Collection — Quick Start

## TL;DR

```bash
# Download diagnostics zip from running worker
curl -X POST http://localhost:9090/_collect -o diagnostics.zip

# Without profiling (faster, smaller file)
curl -X POST "http://localhost:9090/_collect?include_profiling=false" -o diag.zip

# Extract and inspect
unzip diagnostics.zip
cat csdb_collect_*/collect_info.json          # Metadata
cat csdb_collect_*/metrics_snapshot.txt       # Prometheus metrics
cat csdb_collect_*/config/config_redacted.json  # (Secrets redacted)
cat csdb_collect_*/profiling/psutil_process.json  # Process stats
```

## Prerequisites

1. **Worker is running** and metrics server is enabled (default: port 9090)
2. **Network access** to metrics port (typically localhost or internal network)

## Endpoint

```
POST http://<host>:9090/_collect
```

## What You Get

```
csdb_collect_<hostname>_<timestamp>.zip
├── project_logs/           → Application logs
├── cbl_logs/              → Couchbase Lite logs
├── system/                → OS diagnostics
├── profiling/             → CPU, memory, threads, process stats
├── config/                → Redacted config + version
├── metrics_snapshot.txt   → Prometheus metrics
├── status.json            → Worker status
└── collect_info.json      → Metadata
```

## Common Tasks

### Diagnose High CPU/Memory
```bash
curl -X POST http://localhost:9090/_collect -o diag.zip
unzip diag.zip
cat csdb_collect_*/profiling/cprofile.txt      # Top CPU consumers
cat csdb_collect_*/profiling/psutil_process.json  # Memory usage
cat csdb_collect_*/profiling/thread_stacks.txt # Thread state
```

### Check System Health
```bash
unzip diag.zip
cat csdb_collect_*/system/ps_aux.txt      # Running processes
cat csdb_collect_*/system/df.txt          # Disk usage
cat csdb_collect_*/system/netstat.txt     # Network connections
```

### Review Configuration
```bash
unzip diag.zip
cat csdb_collect_*/config/config_redacted.json
# Sensitive values are masked (e.g., "p***d" for password)
```

### Check Metrics
```bash
unzip diag.zip
grep -i "error\|fail" csdb_collect_*/metrics_snapshot.txt
# Find error and failure counters
```

### Inspect Logs
```bash
unzip diag.zip
tail -100 csdb_collect_*/project_logs/changes_worker.log
grep ERROR csdb_collect_*/project_logs/changes_worker.log*
```

## Configuration

Optional. Add to `config.json` to customize collection:

```json
{
  "collect": {
    "max_log_size_mb": 200,
    "profile_seconds": 5,
    "system_command_timeout_seconds": 30
  }
}
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 404 error | Metrics port not enabled. Check `metrics.enabled: true` in config |
| 500 error | Check application logs for "Error generating diagnostics" |
| Zip is too large | Use `include_profiling=false` to skip CPU/memory profiling |
| Profiling data missing | Profiling takes ~5s; ensure request doesn't timeout |
| Some system commands failed | Check `system/*_error.txt` files in zip; may need elevated privileges |

## Performance Notes

- **Collection time:** 5–15 seconds (with profiling), ~1–2 seconds without
- **Typical size:** 1–10 MB compressed
- **Profiling duration:** Default 5 seconds (configurable)
- **Impact:** Minimal; doesn't interfere with normal operation

## Security

- **Sensitive data is redacted:** passwords, tokens, API keys masked
- **Admin-only endpoint:** Served on metrics port, not exposed publicly
- **No plaintext secrets:** All config keys filtered
- **Safe to share:** Can be sent to support without exposing credentials

## Related Documentation

- [Full API Reference](./LOG_COLLECTION_API.md)
- [Implementation Details](../IMPLEMENTATION_LOG_COLLECTION.md)
- [Original Plan](./LOG_COLLECTION_PLAN.md)
