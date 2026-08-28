-- Back to the shape 000008 left. Left empty: 000017's down recreates the view,
-- which refills it as spans arrive.
CREATE OR REPLACE TABLE endpoint_stats_1m
(
    tenant_id         LowCardinality(String),
    project_id        LowCardinality(String),
    service_name      LowCardinality(String),
    span_name         LowCardinality(String),
    minute            DateTime,
    request_count     SimpleAggregateFunction(sum, UInt64),
    error_count       SimpleAggregateFunction(sum, UInt64),
    latency_quantiles AggregateFunction(quantiles(0.5, 0.95, 0.99), Float64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, project_id, service_name, span_name, minute)
TTL minute + INTERVAL 13 MONTH
