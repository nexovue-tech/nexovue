-- Follows `spans`: this is an index over it and expires with it. Refilled
-- by 000054, so replaced rather than exchanged.
CREATE OR REPLACE TABLE span_trace_lookup
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `span_start` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `span_end` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) CODEC(Delta(8), ZSTD(1))
    ,`retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, trace_id)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192
