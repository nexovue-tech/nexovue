-- Restores the materialized view exactly as 000012 created it. Rolling back
-- also means stopping the worker from writing the table directly, or the two
-- writers double-count every row.
CREATE MATERIALIZED VIEW IF NOT EXISTS endpoint_stats_1m_mv TO endpoint_stats_1m
(
    `tenant_id` LowCardinality(String),
    `project_id` LowCardinality(String),
    `derived_retention_days` UInt16,
    `service_name` LowCardinality(String),
    `span_kind` LowCardinality(String),
    `span_name` LowCardinality(String),
    `minute` DateTime,
    `request_count` UInt64,
    `error_count` UInt64,
    `latency_quantiles` AggregateFunction(quantiles(0.5, 0.95, 0.99), Float64)
)
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
