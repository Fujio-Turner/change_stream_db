# DLQ Stacked Bar Chart Query Analysis

## Summary of Changes

Changed **Reasons Breakdown** chart from **pie chart** to **stacked bar chart** with:
- **X-axis**: Time buckets (5-minute intervals)
- **Stacks**: Error reasons (data_error, client_error, server_error, etc.)
- **Y-axis**: Count of entries per reason per time bucket

## Updated Query

```sql
SELECT d.reason AS reason, COUNT(*) AS count,
  FLOOR(d.time / 300) * 300 AS time_bucket
FROM `change_stream` AS d
WHERE d.type = 'dlq' AND d.time > 0
GROUP BY d.reason, time_bucket
ORDER BY time_bucket
```

## Query Optimization & Index Recommendations

### Current Query Performance

The query filters on two conditions:
1. `d.type = 'dlq'` (equality)
2. `d.time > 0` (range)

Then groups by `reason` and `time_bucket`.

### Recommended Index

Create a **composite index** for optimal performance:

```sql
CREATE INDEX idx_dlq_time ON `change_stream`(type, time)
WHERE type = 'dlq'
```

**Why this index?**
- **First key (`type`)**: Filters to only DLQ documents (highly selective)
- **Second key (`time`)**: Supports the range filter `time > 0` and enables ORDER BY `time_bucket`
- **WHERE clause**: Partial index reduces size (only indexes DLQ docs)

### Alternative Indexes (if above isn't available)

If you need more flexibility:

```sql
CREATE INDEX idx_dlq_all ON `change_stream`(type, reason, time)
```

This supports:
- Filter on `type = 'dlq'`
- Filter and ORDER by `time`
- GROUP BY on `reason`

### Index Usage Verification

To verify the query uses the index, run:

```sql
EXPLAIN SELECT d.reason AS reason, COUNT(*) AS count,
  FLOOR(d.time / 300) * 300 AS time_bucket
FROM `change_stream` AS d
WHERE d.type = 'dlq' AND d.time > 0
GROUP BY d.reason, time_bucket
ORDER BY time_bucket
```

Look for `"#primary"` → index name in the EXPLAIN output. If you see `"#project"` or `"#filter"` on an `#IndexScan`, the recommended index is being used.

### Performance Considerations

**Without index:**
- Full table scan required
- ~O(n) complexity for every chart load

**With `idx_dlq_time` index:**
- Index scan (seek + range scan)
- ~O(k log n) where k = DLQ documents
- Typically 100-1000x faster depending on dataset size

## Data Structure in Backend

The `dlq_stats()` function now returns:

```python
{
  "timeline": {
    "2025-04-19 14:30": {"data_error": 5, "server_error": 2},
    "2025-04-19 14:35": {"data_error": 3, "client_error": 1},
    ...
  },
  "reason_counts": {...},
  "total": 100,
  "pending": 45,
  "retried": 55,
  "oldest_time": 1234567890
}
```

The timeline now groups by both **time bucket AND reason** to support the stacked bar rendering.

## Frontend Changes

- **Old behavior**: `renderReasonsChart()` received `reason_counts` only (pie chart)
- **New behavior**: `renderReasonsChart()` receives both `reason_counts` and `timeline` (stacked bar chart)
- **ECharts config**: Uses `type: 'bar', stack: 'total'` to create stacked bars
- **X-axis**: Time keys sorted chronologically
- **Legend**: Vertical legend shows error type colors

## Time Bucketing

- **Bucket size**: 300 seconds (5 minutes)
- **Formula**: `FLOOR(d.time / 300) * 300` produces Unix timestamp
- **Formatting**: Converted to `YYYY-MM-DD HH:MM` in Python for readability

This bucket size balances:
- Granularity (can see 5-min error patterns)
- Readability (not too many x-axis labels)
- Query efficiency (reasonable GROUP BY cardinality)
