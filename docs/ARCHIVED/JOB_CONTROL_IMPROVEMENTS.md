# Job Control Endpoint Improvements - Final Update

## Changes Made

### 1. **Increased Proxy Timeout** (`web/server.py`)
- **Changed:** Proxy timeout from 5 seconds → 30 seconds
- **Reason:** Job control operations involve thread pool executor + CBL operations which can take time
- **Lines:** 824-863 in `job_control_proxy()`

```python
# Before
async with session.post(url, timeout=_aiohttp.ClientTimeout(total=5)) as resp:

# After
async with session.post(url, timeout=_aiohttp.ClientTimeout(total=30)) as resp:
```

### 2. **Better Error Handling** (`web/server.py`)
- Added specific exception handlers:
  - `ClientConnectorError` - Network connectivity issues
  - `TimeoutError` - Operation exceeded timeout
  - Generic exceptions - Other errors

Returns detailed error messages with appropriate HTTP status codes:
- **502** - Metrics server unreachable
- **504** - Operation timed out
- **502** - Other proxy errors

### 3. **Debug Logging** (`web/server.py`)
Added logging to track proxy operations:
```python
logger.debug(f"Proxying request to {url}")
logger.debug(f"Proxy response: {resp.status}")
```

### 4. **Job Control Handler Logging** (`rest/api_v2_jobs_control.py`)
Added structured logging to metrics server handlers:
```python
logger.info(f"[JOB_CONTROL] Starting job {job_id}")
logger.info(f"[JOB_CONTROL] Job {job_id} start result: {success}")
logger.error(f"[JOB_CONTROL] Error starting job {job_id}: {exc}")
```

## How to Debug

If you still see timeouts after rebuilding containers, check for these logs:

### In changes-worker container:
```
[JOB_CONTROL] Starting job {id}
[JOB_CONTROL] Job {id} start result: True/False
```

If you see these logs, the endpoint IS being called and your fix is working.

### In admin-ui container:
```
Proxying request to http://changes-worker:9090/api/jobs/{id}/start
Proxy response: 200
```

If the proxy is connecting and getting a response, the route exists and handler ran.

## What Each Change Does

| Change | Purpose |
|--------|---------|
| 30s timeout | Prevents premature timeout while operations run in thread pool |
| Error handlers | Distinguishes between network issues, timeouts, and server errors |
| Debug logging | Tracks request/response flow for troubleshooting |
| Handler logging | Shows job control operations executing on metrics server |

## Next Steps

1. Rebuild containers:
   ```bash
   docker-compose build
   docker-compose up -d
   ```

2. Try starting a job and check logs:
   ```bash
   docker-compose logs -f changes-worker
   docker-compose logs -f admin-ui
   ```

3. Look for `[JOB_CONTROL]` messages which confirm endpoint is working

4. If still timing out after 30s, check if:
   - Network connectivity between containers
   - Metrics server is actually listening on port 9090
   - PipelineManager operations are extremely slow

## Files Modified
- `web/server.py` - Timeout, error handling, logging
- `rest/api_v2_jobs_control.py` - Handler logging
