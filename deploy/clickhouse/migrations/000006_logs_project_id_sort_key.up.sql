-- ADR 0049 moved `project_id` into the spans sort key: a project *is* an
-- environment, so every query filters it, and without it staging and production
-- rows interleave in the same granules — every production read pulls staging off
-- disk to discard it. The argument was always identical for logs; it was left as
-- a follow-up because no query patterns existed yet.
--
-- Applied now because the table is still empty, which ADR 0049 calls the only
-- moment it is free. CREATE OR REPLACE is atomic and one statement, which the
-- migration runner requires.
CREATE OR REPLACE TABLE logs
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
ORDER BY (tenant_id, project_id, service_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
