-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- This file is only the shape the decisions arrived at. Verified by applying
-- the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- the log store
CREATE TABLE IF NOT EXISTS logs
(
    `timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1)),
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
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, service_name, severity_number, timestamp)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192
