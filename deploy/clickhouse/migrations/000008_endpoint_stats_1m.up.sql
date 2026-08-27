-- P1-23. The per-minute rollup dashboards read and Phase 2's detectors watch.
--
-- The shape matters and the obvious form is wrong. An implicit-inner-table
-- materialized view over AggregatingMergeTree with count()/countIf() produces
-- plain UInt64 columns, and plain columns do not merge — rows accumulate rather
-- than combining, so the counts silently inflate. The fix is an explicit target
-- table, which this is: SimpleAggregateFunction for the counters (it stores the
-- raw value and merges by summing) and AggregateFunction for the quantiles.
--
-- Partitioned by month, not day: this is a 13-month table and daily partitions
-- would leave roughly four hundred of them.
CREATE TABLE IF NOT EXISTS endpoint_stats_1m
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
