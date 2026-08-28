-- Back to 000013's client-clock shape.
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
