-- A rollup, so it takes the derived retention rather than the raw one.
-- Not backfilled: these counters merge by summing, and a minute counted by
-- both a backfill and the live view is counted twice.
CREATE OR REPLACE TABLE endpoint_stats_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `span_kind` LowCardinality(String) CODEC(ZSTD(1)),
    `span_name` LowCardinality(String) CODEC(ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `request_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `error_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `latency_quantiles` AggregateFunction(quantiles(0.5, 0.95, 0.99), Float64) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, service_name, span_kind, span_name, minute)
TTL minute + toIntervalDay(derived_retention_days)
SETTINGS index_granularity = 8192
