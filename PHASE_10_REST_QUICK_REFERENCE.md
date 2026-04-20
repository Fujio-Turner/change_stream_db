# Phase 10: REST Job Control – Quick Reference

## Endpoints

### Single Job Control
```bash
# Start a job
curl -X POST http://localhost:9090/api/jobs/{job_id}/start

# Stop a job
curl -X POST http://localhost:9090/api/jobs/{job_id}/stop

# Restart a job
curl -X POST http://localhost:9090/api/jobs/{job_id}/restart

# Get job state
curl http://localhost:9090/api/jobs/{job_id}/state | jq
```

### Global Job Control
```bash
# Restart all jobs
curl -X POST http://localhost:9090/api/_restart

# Stop all jobs (offline mode)
curl -X POST http://localhost:9090/api/_offline

# Start all jobs (online mode)
curl -X POST http://localhost:9090/api/_online
```

## Response Formats

### Job State Response
```json
{
  "id": "job::uuid",
  "status": "running|stopped|error|starting",
  "uptime_seconds": 1234,
  "error_count": 0,
  "last_error": null
}
```

### Success Response
```json
{
  "status": "started",
  "job_id": "job::uuid"
}
```

### Error Response
```json
{
  "error": "Failed to start job xyz"
}
```

## HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (missing job_id) |
| 404 | Job not found |
| 500 | Server error (job control failed) |

## Integration Points

- **PipelineManager**: Controls job threads
- **Metrics Server**: Serves HTTP endpoints on port 9090
- **CBL Store**: Persists job state
- **Dashboard**: Can display job status from `/api/jobs/{id}/state`

## Testing

### Start service
```bash
python3 main.py --config config.json
```

### In another terminal
```bash
# List all jobs
curl http://localhost:9090/api/jobs | jq '.jobs[]'

# Start first job
JOB_ID=$(curl http://localhost:9090/api/jobs | jq -r '.jobs[0].id')
curl -X POST http://localhost:9090/api/jobs/$JOB_ID/start

# Check state
curl http://localhost:9090/api/jobs/$JOB_ID/state | jq
```

## Logging

Service logs registration:
```
[DEBUG] registered job control endpoints
```

When you control a job:
```
[INFO] job::xyz — started
[INFO] job::xyz — stopped
[INFO] job::xyz — restarted
```

## Common Issues

### 404 Not Found on endpoints
- Service might not have PipelineManager initialized
- Check logs for "registered job control endpoints"
- Ensure service started without early exit

### Job control returns 500
- Job might already be in that state (starting when already running)
- Timeout waiting for thread to respond
- Check PipelineManager logs for details

### No job_id in state response
- Invalid job ID
- Job doesn't exist
- Use `/api/jobs` to list valid job IDs

## See Also

- [PHASE_10_REST_INTEGRATION_STATUS.md](PHASE_10_REST_INTEGRATION_STATUS.md) — Full implementation details
- [PHASE_10_STATUS.md](PHASE_10_STATUS.md) — Threading architecture
- [DESIGN_2_0.md](docs/DESIGN_2_0.md) — Overall architecture
