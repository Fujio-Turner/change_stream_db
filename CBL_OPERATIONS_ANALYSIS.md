# Couchbase Lite Operations Hot Path Analysis

## Executive Summary

**Problem**: Writing to Couchbase Lite is expensive. Each checkpoint save can trigger multiple CBL operations.

**Finding**: The checkpoint save is the **primary CBL operation** in the hot path, happening every N documents (configurable).

---

## Hot Code Path: `_process_changes_batch()` → `checkpoint.save()`

### Location
- **Entry**: [`rest/changes_http.py:843-1477`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py#L843-L1477) - `_process_changes_batch()`
- **Checkpoint Call**: [`rest/changes_http.py:1284, 1331, 1467, 1477`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py#L1284)
- **Checkpoint Class**: [`main.py:2180-2450`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/main.py#L2180) - `Checkpoint` class

---

## CBL Operations Per Checkpoint Save (Fallback Path)

When **SG checkpoint save fails** (network error, unavailable, etc.), the system falls back to CBL storage.

### Call Chain
1. `checkpoint.save()` → tries SG (`PUT {keyspace}/_local/checkpoint-{uuid}`)
2. **On Exception** → `_save_fallback(seq)` 
3. `_save_fallback()` → `CBLStore().save_checkpoint(uuid, seq, client_id)`
4. [`storage/cbl_store.py:816-839`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/storage/cbl_store.py#L816)

### Operations per `save_checkpoint()` call:

```
1 × GET (mutable)   [_coll_get_mutable_doc @ L820]
    └─ CBLCollection_GetMutableDocument()
    
1 × SAVE            [_coll_save_doc @ L827]
    └─ CBLCollection_SaveDocumentWithConcurrencyControl()
```

**Total: 2 CBL operations per fallback checkpoint save**

---

## Checkpoint Save Frequency (Configuration-Dependent)

### Sequential Mode with `every_n_docs`
[`rest/changes_http.py:1198-1287`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py#L1198)

```python
if every_n_docs > 0 and sequential:
    for i in range(0, len(filtered), every_n_docs):
        sub_batch = filtered[i : i + every_n_docs]
        # ... process sub_batch ...
        await checkpoint.save(since, ...)  # AFTER each sub-batch
```

**Saves per batch**: `len(filtered) / every_n_docs` (rounded up)

### Sequential Mode with Stride (Default)
[`rest/changes_http.py:1290-1340`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py#L1290)

```python
else:
    # Sequential checkpoint stride: save every N docs rather than
    # after every single doc
    checkpoint_stride = proc_cfg.get("checkpoint_stride", 100)
```

**Saves per batch**: `len(filtered) / checkpoint_stride`

### Parallel Mode
[`rest/changes_http.py:1345-1400`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/rest/changes_http.py#L1345)

```python
# Wait for all tasks to complete, then checkpoint once
tasks = [...]
await asyncio.gather(*tasks)
await checkpoint.save(since, ...)  # ONCE per entire batch
```

**Saves per batch**: `1` (single checkpoint after entire parallel batch completes)

---

## Empirical Impact Example

### Scenario: Default Sequential Mode
- **Batch size**: 100 documents
- **checkpoint_stride**: 100 (default)
- **SG availability**: 10% (fails 90% of time, triggering fallback)

**Per batch:**
- 1 checkpoint save
- ~90% trigger fallback path
- `0.9 × 2 CBL ops = 1.8 CBL operations per batch`

### Scenario: Sequential with `every_n_docs=10`
- **Batch size**: 100 documents  
- **every_n_docs**: 10
- **SG availability**: 10%

**Per batch:**
- 10 checkpoint saves (100 / 10)
- ~90% trigger fallback per save
- `10 saves × 0.9 fallback rate × 2 CBL ops = 18 CBL operations per batch`

**⚠️ 10x more expensive than default stride mode!**

---

## CBL Operation Details

### Operation 1: GET (Mutable)
[`storage/cbl_store.py:199-209`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/storage/cbl_store.py#L199)

```python
def _coll_get_mutable_doc(db, collection_name: str, doc_id: str):
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    doc_ref = lib.CBLCollection_GetMutableDocument(
        coll, stringParam(doc_id), _cbl_gError
    )
    # ...
```

**Cost**: Medium
- Collection lookup (O(1))
- Document fetch from CBL storage
- Tracked as: `"operation": "SELECT"` in logs

### Operation 2: SAVE
[`storage/cbl_store.py:212-223`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/storage/cbl_store.py#L212)

```python
def _coll_save_doc(db, collection_name: str, doc) -> None:
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    doc._prepareToSave()
    ok = lib.CBLCollection_SaveDocumentWithConcurrencyControl(
        coll,
        doc._ref,
        0,  # kCBLConcurrencyControlLastWriteWins
        _cbl_gError,
    )
```

**Cost**: High
- Serialization (`_prepareToSave()`)
- Disk I/O (CBL database file write)
- Concurrency control check
- Tracked as: `"operation": "INSERT"` or `"operation": "UPDATE"`

---

## Load Path (On Startup)

[`main.py:2250-2333`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/main.py#L2250) - `Checkpoint.load()`

When SG checkpoint fails to load (network error), fallback calls:
[`main.py:2408-2432`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/main.py#L2408)

```python
def _load_fallback(self) -> str:
    if USE_CBL:
        data = self._get_fallback_store().load_checkpoint(self._uuid)
```

Which does:
[`storage/cbl_store.py:780-814`](file:///Users/fujio.turner/Documents/GitHub/change_stream_db/storage/cbl_store.py#L780)

```python
def load_checkpoint(self, uuid: str) -> dict | None:
    doc = _coll_get_doc(self.db, COLL_CHECKPOINTS, doc_id)  # 1 GET
```

**Cost**: 1 CBL operation (GET) during startup

---

## Configuration Recommendations to Reduce CBL Ops

### Priority 1: Increase `checkpoint_stride`
**Default**: 100 → **Recommended**: 500-1000

```json
{
  "processing": {
    "checkpoint_stride": 500
  }
}
```

**Impact**: Reduces checkpoint saves by 5-10×
- 100 doc batch: 1 save instead of ~10 saves
- **If in fallback mode**: 2 CBL ops instead of 20

### Priority 2: Ensure SG Connectivity
The fallback only triggers on SG errors. **Maintain 100% SG availability**.

**Current health check**: None. Consider adding SG heartbeat monitoring.

### Priority 3: Use Parallel Mode
Sequential mode can checkpoint more frequently.
```json
{
  "processing": {
    "sequential": false,
    "max_concurrent": 10
  }
}
```

**Impact**: Single checkpoint per batch (regardless of size)

### Priority 4: Disable Fallback (If SG Always Available)
If you guarantee SG is always reachable, disable fallback:
```json
{
  "checkpoint": {
    "enabled": false
  }
}
```

**Impact**: Zero CBL operations (no checkpoint storage)

---

## Metrics to Monitor

All checkpoint operations are tracked:

| Metric | Path | Meaning |
|--------|------|---------|
| `checkpoint_saves_total` | rest/changes_http.py:1286 | How many checkpoint saves attempted |
| `checkpoint_load_errors_total` | main.py:2318, 2331 | SG load failures triggering fallback |
| `checkpoint_save_errors_total` | main.py:2398 | SG save failures triggering fallback |

**Log Events** (storage/cbl_store.py):
- `"CBL checkpoint saved"` with `operation="INSERT"` or `operation="UPDATE"`
- `"CBL checkpoint loaded"` with `operation="SELECT"`
- Duration in `duration_ms`

---

## Summary Table

| Scenario | Checkpoints/Batch | CBL Ops/Checkpoint | Total CBL Ops/Batch |
|----------|-------------------|--------------------|---------------------|
| **Parallel** (100 docs) | 1 | 0 (SG success) | **0** |
| **Parallel** (100 docs, SG fails) | 1 | 2 (fallback) | **2** |
| **Sequential (stride=100)** | 1 | 2 (fallback) | **2** |
| **Sequential (stride=10)** | 10 | 2 each (fallback) | **20** ⚠️ |
| **Sequential (stride=1)** | 100 | 2 each (fallback) | **200** 🔴 |

**Recommendation**: Use **Parallel mode** or increase `checkpoint_stride` to 500+.
