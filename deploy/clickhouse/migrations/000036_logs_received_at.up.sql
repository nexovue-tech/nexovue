-- The same defect ADR 0067 fixed in `spans`, in the last table that still has
-- it: `logs` partitioned and expired on `timestamp`, which the customer's SDK
-- supplies. That hands a customer control of our retention — a record dated a
-- year ahead is not dropped for a year and thirty days, one dated 1970 is
-- expired on arrival, and a scrambled clock creates one partition per bogus
-- date without bound.
--
-- Free here in a way it was not for `spans`: the table is empty and has no
-- writer yet, so there is nothing to copy and nothing to exchange. After M3
-- this is a rebuild of the busiest table in the system.
--
-- `timestamp` stays authoritative for the event. DEFAULT timestamp so a writer
-- that does not yet send the column degrades to the old behaviour instead of
-- writing epoch, which would partition into 1970 and expire on arrival.
CREATE OR REPLACE TABLE logs
(
    timestamp       DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    received_at     DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
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
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, severity_number, timestamp)
TTL toDateTime(received_at) + INTERVAL 30 DAY
