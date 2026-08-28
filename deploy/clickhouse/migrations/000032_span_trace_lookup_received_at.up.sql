-- Same defect as `spans`, one table along: 000013 partitioned this on
-- `span_start` and expired it on `span_end`, both derived from the client's
-- clock. It is rebuilt rather than altered because a partition key cannot be
-- changed, and it is cheap to rebuild because 000034 refills it from `spans`.
--
-- `span_start`/`span_end` stay: they are what bounds a trace-id lookup to a
-- time range, and that range is the event's, not ours. ADR 0067.
CREATE OR REPLACE TABLE span_trace_lookup
(
    tenant_id   LowCardinality(String) CODEC(ZSTD(1)),
    trace_id    FixedString(32) CODEC(ZSTD(1)),
    span_start  DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    span_end    DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    received_at DateTime64(9) CODEC(Delta(8), ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, trace_id)
TTL toDateTime(received_at) + INTERVAL 30 DAY
