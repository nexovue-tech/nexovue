-- Same correction as 000006, and more urgent: P1-22 is about to write exact
-- pre-sampling counters here at one row per service, endpoint and minute. A
-- counter table whose sort key omits `project_id` makes every project-scoped
-- dashboard read another environment's rows off disk.
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
ORDER BY (tenant_id, project_id, metric_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
