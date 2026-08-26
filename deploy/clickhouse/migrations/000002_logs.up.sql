CREATE TABLE IF NOT EXISTS logs
(
    timestamp    DateTime64(9),
    tenant_id    LowCardinality(String),
    project_id   LowCardinality(String),
    -- Empty when the log record carried no trace context.
    trace_id     FixedString(32),
    span_id      FixedString(16),
    service_name LowCardinality(String),
    severity     LowCardinality(String),
    body         String,
    attrs        JSON,
    -- Token bloom filter: free-text log search without a full scan.
    INDEX idx_body body TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4,
    INDEX idx_log_trace trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, service_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
