-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- The decisions are in docs/architecture/decisions; this file is only the
-- shape they arrived at. Verified by applying the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- OTLP exponential histograms
CREATE TABLE IF NOT EXISTS metrics_exp_histogram
(
    `timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `start_timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(3) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
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
    `sum` Nullable(Float64) CODEC(ZSTD(1)),
    `scale` Int32 CODEC(T64, ZSTD(1)),
    `zero_count` UInt64 CODEC(T64, ZSTD(1)),
    `positive_offset` Int32 CODEC(T64, ZSTD(1)),
    `positive_bucket_counts` Array(UInt64) CODEC(T64, ZSTD(1)),
    `negative_offset` Int32 CODEC(T64, ZSTD(1)),
    `negative_bucket_counts` Array(UInt64) CODEC(T64, ZSTD(1)),
    `min` Nullable(Float64) CODEC(ZSTD(1)),
    `max` Nullable(Float64) CODEC(ZSTD(1)),
    `aggregation_temporality` LowCardinality(String) CODEC(ZSTD(1)),
    `exemplars.timestamp` Array(DateTime64(3)) CODEC(ZSTD(1)),
    `exemplars.value` Array(Float64) CODEC(ZSTD(1)),
    `exemplars.trace_id` Array(FixedString(32)) CODEC(ZSTD(1)),
    `exemplars.span_id` Array(FixedString(16)) CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp), series_hash, timestamp)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS non_replicated_deduplication_window = 1000, index_granularity = 8192
