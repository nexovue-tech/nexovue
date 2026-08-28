-- Four corrections to `logs` before M3 writes to it. The table is empty, which
-- ADR 0049 calls the only moment any of them is free.
--
-- 1. `severity_number`. SDKs emit WARN, WARNING, W and warn for one level, so
--    "warn and above" is not expressible against the text. OTLP defines a
--    numeric SeverityNumber for exactly this; the text stays, as OTLP keeps
--    both.
-- 2. Resource attributes split from record attributes, as `spans` already
--    separates `resource_attrs` from `span_attrs`. One merged column cannot
--    answer "which host emitted this" without also searching the record.
-- 3. `body_structured`, because OTLP's body is an AnyValue rather than a
--    string. ADR 0062.
-- 4. A text index instead of `tokenbf_v1`, which ClickHouse's own docs call
--    deprecated. `preprocessor = lower(body)` is applied to the search term as
--    well as to the data, so `hasToken(body, 'error')` matches ERROR.
CREATE OR REPLACE TABLE logs
(
    timestamp       DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    tenant_id       LowCardinality(String) CODEC(ZSTD(1)),
    project_id      LowCardinality(String) CODEC(ZSTD(1)),
    -- Empty when the log record carried no trace context.
    trace_id        FixedString(32) CODEC(ZSTD(1)),
    span_id         FixedString(16) CODEC(ZSTD(1)),
    service_name    LowCardinality(String) CODEC(ZSTD(1)),
    severity        LowCardinality(String) CODEC(ZSTD(1)),
    -- OTLP SeverityNumber, 1..24. 0 means the record carried none.
    severity_number Int32 CODEC(T64, ZSTD(1)),
    -- The rendered body, and what the text index searches. For a non-string
    -- AnyValue this is its JSON text.
    body            String CODEC(ZSTD(1)),
    -- The AnyValue body when it was a map, so structured logs keep their fields
    -- as typed subcolumns; `{}` otherwise.
    body_structured JSON,
    resource_attrs  JSON,
    log_attrs       JSON,
    -- GRANULARITY is omitted because a text index ignores it.
    INDEX idx_body body TYPE text(tokenizer = splitByNonAlpha, preprocessor = lower(body)),
    INDEX idx_log_trace trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, service_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
