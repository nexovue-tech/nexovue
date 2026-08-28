-- Existing rows predate the column. Their own timestamp is the only reading
-- available, and it is what they were partitioned by until now, so it keeps
-- them in the partitions they already occupied.
INSERT INTO spans_rebuild SELECT
    timestamp, timestamp AS received_at, tenant_id, project_id, trace_id,
    span_id, parent_span_id, service_name, span_name, span_kind, duration_ms,
    status_code, status_message, http_method, http_route, http_status,
    deployment_version, resource_attrs, span_attrs, events
FROM spans
-- Bounded blocks throughout. The defaults squash inserts up to 256 MiB before
-- writing a part and fan the read across threads holding a block each, which
-- exhausts the server on a table this wide. A backfill runs once and has no
-- deadline, so it trades speed for a working set that fits.
SETTINGS max_threads = 1,
         max_insert_threads = 1,
         max_block_size = 8192,
         max_insert_block_size = 8192,
         min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432
