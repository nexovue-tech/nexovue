-- Recreated because 000030 exchanged `spans` and 000032 replaced the target.
-- Carries our receive clock through so the lookup expires on the same schedule
-- as the spans it points at.
CREATE MATERIALIZED VIEW IF NOT EXISTS span_trace_lookup_mv TO span_trace_lookup
AS SELECT
    tenant_id,
    trace_id,
    min(timestamp) AS span_start,
    max(timestamp) AS span_end,
    min(received_at) AS received_at
FROM spans
GROUP BY tenant_id, trace_id
