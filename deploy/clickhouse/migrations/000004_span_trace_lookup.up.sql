-- Opening one trace by id has no time bound, so a query against `spans` alone
-- leans entirely on the bloom filter and touches every partition in the 30-day
-- range. This table answers "when did trace X happen?" from its own ordering
-- key, and the answer bounds the query that follows.
--
-- One row per insert block, not per trace: a trace spread over several batches
-- lands several rows, so readers aggregate (min(span_start), max(span_end)).
CREATE TABLE IF NOT EXISTS span_trace_lookup
(
    tenant_id  LowCardinality(String),
    trace_id   FixedString(32),
    span_start DateTime64(9),
    span_end   DateTime64(9)
)
ENGINE = MergeTree
ORDER BY (tenant_id, trace_id)
TTL toDateTime(span_end) + INTERVAL 30 DAY
