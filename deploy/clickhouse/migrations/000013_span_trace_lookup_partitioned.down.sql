-- Back to the unpartitioned shape of 000004. Left empty: 000012's down
-- recreates the view, which refills it as spans arrive.
CREATE OR REPLACE TABLE span_trace_lookup
(
    tenant_id  LowCardinality(String),
    trace_id   FixedString(32),
    span_start DateTime64(9),
    span_end   DateTime64(9)
)
ENGINE = MergeTree
ORDER BY (tenant_id, trace_id)
TTL toDateTime(span_end) + INTERVAL 30 DAY
