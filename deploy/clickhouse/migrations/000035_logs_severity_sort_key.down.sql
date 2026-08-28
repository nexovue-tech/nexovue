-- Back to 000016's key, without severity_number in it.
CREATE OR REPLACE TABLE logs
(
    timestamp       DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    tenant_id       LowCardinality(String) CODEC(ZSTD(1)),
    project_id      LowCardinality(String) CODEC(ZSTD(1)),
    trace_id        FixedString(32) CODEC(ZSTD(1)),
    span_id         FixedString(16) CODEC(ZSTD(1)),
    service_name    LowCardinality(String) CODEC(ZSTD(1)),
    severity        LowCardinality(String) CODEC(ZSTD(1)),
    severity_number Int32 CODEC(T64, ZSTD(1)),
    body            String CODEC(ZSTD(1)),
    body_structured JSON,
    resource_attrs  JSON,
    log_attrs       JSON,
    INDEX idx_body body TYPE text(tokenizer = splitByNonAlpha, preprocessor = lower(body)),
    INDEX idx_log_trace trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, service_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
