-- The shape 000003 created, as 000007 sorted it and 000010 and 000026 settled
-- it. Empty: `derive` writes to `metrics_sum`, and a rollback restores the
-- table rather than its contents.
CREATE TABLE IF NOT EXISTS metrics
(
    timestamp    DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    tenant_id    LowCardinality(String) CODEC(ZSTD(1)),
    project_id   LowCardinality(String) CODEC(ZSTD(1)),
    metric_name  LowCardinality(String) CODEC(ZSTD(1)),
    value        Float64 CODEC(Gorilla(8), ZSTD(1)),
    service_name LowCardinality(String) CODEC(ZSTD(1)),
    attrs        JSON
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, metric_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192, non_replicated_deduplication_window = 1000
