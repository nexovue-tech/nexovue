CREATE MATERIALIZED VIEW IF NOT EXISTS span_trace_lookup_mv TO span_trace_lookup
AS SELECT
    tenant_id,
    trace_id,
    min(timestamp) AS span_start,
    max(timestamp) AS span_end
FROM spans
GROUP BY tenant_id, trace_id
