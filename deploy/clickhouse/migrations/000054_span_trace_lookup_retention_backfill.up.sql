-- Refills after the rebuild. Runs after the view exists: readers aggregate
-- min/max, so an overlap is idempotent while a gap is not.
INSERT INTO span_trace_lookup
SELECT tenant_id, trace_id, min(timestamp), max(timestamp), min(received_at), max(retention_days)
FROM spans GROUP BY tenant_id, trace_id
SETTINGS max_threads = 1, max_insert_threads = 1, max_block_size = 8192,
         max_insert_block_size = 8192, min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432
