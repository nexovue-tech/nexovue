-- Promoted columns for what every screen filters on; everything else stays in
-- the native JSON columns, where each attribute path is its own subcolumn.
CREATE TABLE IF NOT EXISTS spans
(
    timestamp          DateTime64(9),
    tenant_id          LowCardinality(String),
    project_id         LowCardinality(String),
    trace_id           FixedString(32),
    span_id            FixedString(16),
    parent_span_id     FixedString(16),
    service_name       LowCardinality(String),
    span_name          LowCardinality(String),
    span_kind          LowCardinality(String),
    duration_ms        Float64,
    status_code        LowCardinality(String),
    status_message     String,
    http_method        LowCardinality(String),
    http_route         LowCardinality(String),
    http_status        UInt16,
    deployment_version LowCardinality(String),
    resource_attrs     JSON,
    span_attrs         JSON,
    events             String,
    -- ORDER BY favours service/time scans, so opening one trace by id would
    -- otherwise read every part in the range.
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, service_name, toStartOfHour(timestamp), trace_id)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
