-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- This file is only the shape the decisions arrived at. Verified by applying
-- the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- per-minute endpoint rollup, 13 months
CREATE TABLE IF NOT EXISTS endpoint_stats_1m
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
