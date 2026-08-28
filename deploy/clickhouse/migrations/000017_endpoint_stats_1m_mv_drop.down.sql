CREATE MATERIALIZED VIEW IF NOT EXISTS endpoint_stats_1m_mv TO endpoint_stats_1m
AS SELECT
    tenant_id,
    project_id,
    service_name,
    span_name,
    toStartOfMinute(timestamp)                   AS minute,
    count()                                      AS request_count,
    countIf(status_code = 'ERROR')               AS error_count,
    quantilesState(0.5, 0.95, 0.99)(duration_ms) AS latency_quantiles
FROM spans
GROUP BY tenant_id, project_id, service_name, span_name, minute
