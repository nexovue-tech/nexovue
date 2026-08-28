-- Recreated in the pre-received_at shape so the exchange in 000030's down has
-- something to swap back to. 000029's data is not restored: the down path
-- exists to undo the schema, and spans refill from Kafka.
CREATE TABLE IF NOT EXISTS spans_rebuild
(
    timestamp          DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    tenant_id          LowCardinality(String) CODEC(ZSTD(1)),
    project_id         LowCardinality(String) CODEC(ZSTD(1)),
    trace_id           FixedString(32) CODEC(ZSTD(1)),
    span_id            FixedString(16) CODEC(ZSTD(1)),
    parent_span_id     FixedString(16) CODEC(ZSTD(1)),
    service_name       LowCardinality(String) CODEC(ZSTD(1)),
    span_name          LowCardinality(String) CODEC(ZSTD(1)),
    span_kind          LowCardinality(String) CODEC(ZSTD(1)),
    duration_ms        Float64 CODEC(Gorilla(8), ZSTD(1)),
    status_code        LowCardinality(String) CODEC(ZSTD(1)),
    status_message     String CODEC(ZSTD(1)),
    http_method        LowCardinality(String) CODEC(ZSTD(1)),
    http_route         LowCardinality(String) CODEC(ZSTD(1)),
    http_status        UInt16 CODEC(T64, ZSTD(1)),
    deployment_version LowCardinality(String) CODEC(ZSTD(1)),
    resource_attrs     JSON,
    span_attrs         JSON CODEC(ZSTD(1)),
    events             String,
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192
