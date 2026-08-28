CREATE OR REPLACE TABLE logs
(
    `timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `span_id` FixedString(16) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `severity` LowCardinality(String) CODEC(ZSTD(1)),
    `severity_number` Int32 CODEC(T64, ZSTD(1)),
    `body` String CODEC(ZSTD(1)),
    `body_structured` JSON,
    `resource_attrs` JSON,
    `log_attrs` JSON,
    INDEX idx_body body TYPE text(tokenizer = splitByNonAlpha, preprocessor = lower(body)) GRANULARITY 100000000,
    INDEX idx_log_trace trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, severity_number, timestamp)
TTL toDateTime(received_at) + toIntervalDay(30)
SETTINGS index_granularity = 8192
