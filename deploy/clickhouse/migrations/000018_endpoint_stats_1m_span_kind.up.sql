-- 000009 aggregated every span whatever its kind, so an outbound HTTP call
-- counted as a request served. On the dev fleet CLIENT spans are 20% of all
-- spans: request rate inflated by a fifth, error rate diluted by the same, and
-- "slowest endpoints" would have listed outbound calls as endpoints.
--
-- `span_kind` becomes a dimension, not only a filter. The filter lives in the
-- view (000019) and can be widened whenever; the column cannot be added later
-- without rebuilding a 13-month table. It precedes `span_name` in the sort key
-- as the lower-cardinality of the two, so "every SERVER row for this service"
-- is one contiguous range. ADR 0064.
--
-- Not backfilled from `spans`, unlike 000015. These counters merge by *summing*,
-- so a row counted by both a live view and a backfill is counted twice — and
-- unlike the min/max lookup table there is no ordering that makes overlap
-- harmless. The rollup refills itself from the next minute onward.
CREATE OR REPLACE TABLE endpoint_stats_1m
(
    tenant_id         LowCardinality(String) CODEC(ZSTD(1)),
    project_id        LowCardinality(String) CODEC(ZSTD(1)),
    service_name      LowCardinality(String) CODEC(ZSTD(1)),
    span_kind         LowCardinality(String) CODEC(ZSTD(1)),
    span_name         LowCardinality(String) CODEC(ZSTD(1)),
    minute            DateTime CODEC(Delta, ZSTD(1)),
    request_count     SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    error_count       SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    latency_quantiles AggregateFunction(quantiles(0.5, 0.95, 0.99), Float64) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, project_id, service_name, span_kind, span_name, minute)
TTL minute + INTERVAL 13 MONTH
