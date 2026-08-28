CREATE OR REPLACE TABLE metrics_summary
(
    `timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `start_timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(3) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `scope_name` LowCardinality(String) CODEC(ZSTD(1)),
    `scope_version` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_name` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_unit` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_description` String CODEC(ZSTD(1)),
    `resource_attrs` JSON,
    `attrs` JSON,
    `series_hash` UInt64 MATERIALIZED cityHash64(toJSONString(attrs)) CODEC(ZSTD(1)),
    `flags` UInt32 CODEC(T64, ZSTD(1)),
    `count` UInt64 CODEC(T64, ZSTD(1)),
    `sum` Float64 CODEC(ZSTD(1)),
    `quantiles.quantile` Array(Float64) CODEC(ZSTD(1)),
    `quantiles.value` Array(Float64) CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp), series_hash, timestamp)
TTL toDateTime(received_at) + toIntervalDay(90)
SETTINGS non_replicated_deduplication_window = 1000, index_granularity = 8192
