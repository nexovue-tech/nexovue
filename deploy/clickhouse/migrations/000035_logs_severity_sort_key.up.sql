-- `severity_number` joins the sort key, before the timestamp. The table is
-- still empty, and a sort key cannot be altered in place, so this is the last
-- moment it is free rather than a rebuild.
--
-- Before the timestamp, not after: after it the column is decoration, because
-- the key is already fully ordered by the time any severity comparison is
-- reached. The OpenTelemetry ClickHouse exporter orders its log table the same
-- way, severity ahead of time.
--
-- The trade is deliberate. "Errors in this service" now reads one contiguous
-- range instead of scanning the window. A pure time-range query reads one range
-- per severity present rather than one overall — a handful of ranges, all still
-- time-ordered within themselves, which is the cheaper side of the trade for a
-- product whose first log question is "what is broken". ADR 0069.
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
ORDER BY (tenant_id, project_id, service_name, severity_number, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
