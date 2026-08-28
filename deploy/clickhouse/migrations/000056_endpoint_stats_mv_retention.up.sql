-- Recreated over the rebuilt table, carrying the derived retention through.
CREATE MATERIALIZED VIEW IF NOT EXISTS endpoint_stats_1m_mv TO endpoint_stats_1m
AS SELECT
    tenant_id,
    project_id,
    max(derived_retention_days) AS derived_retention_days,
    service_name,
    span_kind,
    span_name,
    toStartOfMinute(timestamp) AS minute,
    count() AS request_count,
    countIf(status_code = 'ERROR') AS error_count,
    quantilesState(0.5, 0.95, 0.99)(duration_ms) AS latency_quantiles
FROM spans
WHERE span_kind IN ('SERVER', 'CONSUMER')
GROUP BY
    tenant_id,
    project_id,
    service_name,
    span_kind,
    span_name,
    minute
