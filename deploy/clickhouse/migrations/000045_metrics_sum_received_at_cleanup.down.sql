-- Recreated in the pre-received_at shape so 000044's down has something to
-- swap back to.
CREATE TABLE IF NOT EXISTS metrics_sum_rebuild
(
    `timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `start_timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
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
    `value` Float64 CODEC(Gorilla(8), ZSTD(1)),
    `aggregation_temporality` LowCardinality(String) CODEC(ZSTD(1)),
    `is_monotonic` Bool CODEC(ZSTD(1)),
    `exemplars.timestamp` Array(DateTime64(3)) CODEC(ZSTD(1)),
    `exemplars.value` Array(Float64) CODEC(ZSTD(1)),
    `exemplars.trace_id` Array(FixedString(32)) CODEC(ZSTD(1)),
    `exemplars.span_id` Array(FixedString(16)) CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp), series_hash, timestamp)
TTL toDateTime(timestamp) + toIntervalDay(90)
SETTINGS non_replicated_deduplication_window = 1000, index_granularity = 8192
