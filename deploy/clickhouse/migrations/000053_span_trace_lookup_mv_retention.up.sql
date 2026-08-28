-- Carries the retention through from `spans`, so the index expires on the
-- same schedule as the rows it points at.
CREATE MATERIALIZED VIEW IF NOT EXISTS span_trace_lookup_mv TO span_trace_lookup
AS SELECT
    tenant_id,
    trace_id,
    min(timestamp) AS span_start,
    max(timestamp) AS span_end,
    min(received_at) AS received_at,
    max(retention_days) AS retention_days
FROM spans
GROUP BY tenant_id, trace_id
