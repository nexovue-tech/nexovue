-- 000004 shipped this table with no PARTITION BY, which silently disables its
-- own TTL. ADR 0038 turned on `ttl_only_drop_parts`, so expiry happens by
-- dropping whole parts, and a part only drops once its *newest* row expires. In
-- a single partition merges combine parts spanning the whole 30-day window, so
-- the newest row is always fresh and nothing is ever dropped. The table had
-- grown to 2.6M rows / 113 MiB while `spans` beside it was trimmed on schedule.
--
-- Daily partitions on the start timestamp, matching the OpenTelemetry
-- ClickHouse exporter's own trace-id lookup table (`*_trace_id_ts`:
-- `PARTITION BY toDate(Start)`, `ttl_only_drop_parts = 1`). Retention stays the
-- 30 days this table already declared, which is `spans`' window.
--
-- A partition key cannot be altered, so this is a rebuild. 000015 refills it.
-- ADR 0063.
CREATE OR REPLACE TABLE span_trace_lookup
(
    tenant_id  LowCardinality(String) CODEC(ZSTD(1)),
    trace_id   FixedString(32) CODEC(ZSTD(1)),
    span_start DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    span_end   DateTime64(9) CODEC(Delta(8), ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(span_start)
ORDER BY (tenant_id, trace_id)
TTL toDateTime(span_end) + INTERVAL 30 DAY
