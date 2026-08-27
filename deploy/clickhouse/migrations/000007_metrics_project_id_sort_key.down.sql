CREATE OR REPLACE TABLE metrics
(
    timestamp    DateTime64(3),
    tenant_id    LowCardinality(String),
    project_id   LowCardinality(String),
    metric_name  LowCardinality(String),
    value        Float64,
    service_name LowCardinality(String),
    attrs        JSON
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, metric_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
