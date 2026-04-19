# Couchbase Lite Exception Handling Fixes

## Summary
Added comprehensive exception handling to `cbl_store.py` for SQL++ (N1QL) queries and K/V operations, aligning with Couchbase Lite best practices as documented in `docs/CBL_STORE.md` and `docs/CBL_DATABASE.md`.

---

## Changes Made

### 1. **N1QL Query Functions** – Added try/except + logging
**File:** `cbl_store.py` lines 228-293

#### `_run_n1ql()`
- **Before:** No error handling – exceptions propagate uncaught
- **After:** 
  - Wraps `N1QLQuery.execute()` in try/except
  - Logs error with query, params, and exception details
  - Raises `RuntimeError` with context chain (`from e`)

#### `_run_n1ql_scalar()`
- **Before:** No error handling
- **After:**
  - Wraps iterator + `row[0]` access in try/except
  - Logs scalar query failures with context
  - Raises `RuntimeError` on failure

#### `_run_n1ql_explain()`
- **Before:** No error handling; `q.explanation` could be None
- **After:**
  - Wrapped in try/except
  - Returns empty string (`""`) if explanation is None
  - Logs explain failures

---

### 2. **K/V Document Purge** – Added error checking
**File:** `cbl_store.py` lines 192-210

#### `_coll_purge_doc()`
- **Before:** Called C API `CBLCollection_PurgeDocumentByID()` without checking return value
- **After:**
  - Checks `ok` return value
  - If purge fails:
    - Logs error with doc_id and collection name
    - Raises `RuntimeError` with details
  - Now consistent with `_coll_save_doc()` pattern

---

### 3. **Database Transaction** – Added EndTransaction error handling
**File:** `cbl_store.py` lines 356-370

#### `_transaction.__exit__()`
- **Before:** Called `CBLDatabase_EndTransaction()` and ignored result
- **After:**
  - Captures `ok` return value
  - If EndTransaction fails:
    - Logs error with commit status and error code
    - **Does NOT raise** – transaction state is inconsistent; only log
    - Allows original exception to propagate if one occurred
  - Comments explain the rationale

---

### 4. **DLQ Entry Retrieval** – Added JSON parse + outer exception handling
**File:** `cbl_store.py` lines 1009-1077

#### `get_dlq_entry()`
- **Before:** `json.loads(doc_data)` could fail silently; no outer error handling
- **After:**
  - Inner try/except for JSON parsing:
    - Catches `json.JSONDecodeError`
    - Logs warning with parse error details
    - Falls back to empty dict `{}`
  - Outer try/except:
    - Catches any exception (K/V read errors, etc.)
    - Logs error at ERROR level
    - Returns `None` on failure (graceful degradation)
  - Updated docstring

---

### 5. **DLQ Page Listing** – Added error handling for paginated queries
**File:** `cbl_store.py` lines 877-955

#### `list_dlq_page()`
- **Before:** Multiple N1QL calls with no error handling; could crash mid-pagination
- **After:**
  - Entire function wrapped in try/except
  - On error:
    - Logs error with offset, limit, query context
    - Returns empty page: `{"entries": [], "total": 0, "filtered": 0}`
  - API contract maintained (caller always gets safe dict)
  - Updated docstring

---

### 6. **DLQ Statistics** – Added error handling for aggregation queries
**File:** `cbl_store.py` lines 939-1008

#### `dlq_stats()`
- **Before:** Multiple aggregation queries (COUNT, MIN, GROUP BY) with no error handling
- **After:**
  - Entire function wrapped in try/except
  - On error:
    - Logs error with operation context
    - Returns empty stats: `{"total": 0, "pending": 0, "retried": 0, "oldest_time": None, "reason_counts": {}, "timeline": {}}`
  - Safe defaults prevent UI crashes (charts show empty state)
  - Updated docstring

---

## Error Logging Pattern

All exceptions log via `log_event()` with consistent fields:

```python
log_event(
    logger,
    "error",              # or "warn" for non-fatal
    "CBL" or "DLQ",       # component
    "Human description",  # short message
    sql=...,              # query text (first 200 chars)
    params=...,           # query params (first 100 chars)
    doc_id=...,           # if applicable
    collection=...,       # if applicable
    error_detail=...,     # exception string (first 200 chars)
)
```

This enables:
- ✅ Structured log aggregation
- ✅ Error tracing without exposing raw stack traces
- ✅ SQL++ query debugging (see which queries failed)
- ✅ K/V operation auditing

---

## Per Docs

### From `CBL_STORE.md`:
> "raw CFFI functions for...document expiration, database transactions, and maintenance operations"

**Rationale:** These C API calls must check error codes; Python doesn't have exceptions.

### From `CBL_DATABASE.md`:
> "The Python CBL bindings do not expose all APIs directly...N1QL queries (`N1QLQuery`)"

**Rationale:** N1QL execution is wrapped by Python bindings but still can fail; now we catch and log.

---

## Testing Checklist

- [x] Compilation: `python3 -m py_compile cbl_store.py` ✓
- [ ] Unit tests for DLQ operations (if exist)
- [ ] Manual test: Trigger N1QL error (e.g., invalid collection name)
- [ ] Manual test: Corrupt JSON in `doc_data` field → should log warning, return `{}`
- [ ] Manual test: Database transaction conflict → should log error, allow rollback
- [ ] Verify logs show error context (query, params) without exposing full trace
- [ ] Verify DLQ UI still works when queries fail (returns empty page)

---

## Backward Compatibility

- ✅ All changes are additive (error handling only)
- ✅ Return types unchanged
- ✅ API contracts maintained
- ✅ Graceful degradation (return empty results instead of crash)
- ✅ Works with existing `USE_CBL` flag pattern

---

## Related Documentation

- `docs/CBL_STORE.md` – Architecture and implementation plan
- `docs/CBL_DATABASE.md` – Database schema and C API usage
- `cbl_store.py` line 120–199 – K/V helpers with error patterns
