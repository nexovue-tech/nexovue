-- `spans` partitioned and expired on `timestamp`, which the client supplies.
-- That handed a customer control of our retention: a span dated a year ahead
-- is not dropped until a year after that, and a span dated 1970 is expired on
-- arrival and silently deleted. A client with a scrambled clock also creates
-- one partition per distinct bogus date, without bound.
--
-- `received_at` is our own clock, taken by the receiver and already carried on
-- the envelope. Partition and TTL move to it; `timestamp` stays authoritative
-- for the event itself, because a trace's waterfall is drawn from the client's
-- own relative timings and clamping each span independently would flatten it.
--
-- A partition key cannot be altered, so this is a rebuild via EXCHANGE rather
-- than CREATE OR REPLACE: the table holds data that has to survive. ADR 0067.
CREATE TABLE IF NOT EXISTS spans_rebuild
(
    timestamp          DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    -- Defaulted to the client's own timestamp so a writer that does not yet
    -- send the column degrades to the old behaviour. Without it an unupgraded
    -- worker writes epoch, which partitions to 1970 and is TTL'd on arrival —
    -- a schema deploy ahead of the binary would silently delete live traffic.
    received_at        DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
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
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)
TTL toDateTime(received_at) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192
