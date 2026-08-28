-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- The decisions are in docs/architecture/decisions; this file is only the
-- shape they arrived at. Verified by applying the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- trace-id to time-range index over spans
CREATE TABLE IF NOT EXISTS span_trace_lookup
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `span_start` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `span_end` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, trace_id)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192
