-- One row per stored trace, so "newest first" is a sort-key read.
--
-- The trace list used to answer that by grouping every span in the window:
-- one hash-table entry per trace, filtered or not. Measured over 12.47M spans
-- / 2.73M traces in the dev store, a 7-day window held 292 MiB of aggregation
-- state and read 607 MiB — a per-request cost that ten readers multiply and
-- that grows with traffic rather than with the page. Before the strings were
-- taken out of the group it was 1.8 GiB and the server killed it.
--
-- This is the third table filled by the trace pipeline rather than by a view
-- over `spans`, after `endpoint_stats_1m` (000024) and `service_edges_1m`
-- (000014). Unlike those two it is written *after* the sampler, deliberately:
-- it indexes stored spans, so a row here names a trace that can be opened.
-- ADR 0010's "metrics from all traces before sampling" is about metrics; a
-- list of traces you cannot open would be worse than useless.
--
-- ORDER BY puts `start_time` before `trace_id` so the page is a reverse read
-- of the key with no aggregate at all. `received_at` — our own clock — carries
-- the partition and the TTL, as on `spans` and `span_trace_lookup` (ADR 0067):
-- the customer supplies `start_time` and it cannot be trusted to expire data.
-- `retention_days` is the raw one, not the derived one, because a summary
-- outliving the spans it points at is a row that claims a trace exists after
-- it is gone.
--
-- Every measure merges idempotently — min, max, anyLast, set union, and
-- deliberately no sum. The writer emits a *complete* snapshot of the trace
-- each time it emits, so re-writing one changes nothing: a retried insert, a
-- replayed batch and a late span all reduce to the same row. That is what lets
-- this table be written with no deduplication token and no watermark of its
-- own; the token that `endpoint_stats_1m` needs (`insertdedup`) exists because
-- its counters sum, and this one does not.
--
-- `span_services` and `span_endpoints` are what keep the P1-41 filters
-- answerable without touching `spans`. A trace matches a span filter when ONE
-- span satisfies all of it together, so the service and the operation have to
-- travel as a pair: `span_endpoints` holds the distinct `service \x1f
-- operation` pairs of the trace and `span_services` the distinct services, one
-- for `has()` and one for the pair test. Measured over a day of the busiest
-- project, distinct pairs per trace are p50 2, p99 19, max 48.
-- `groupUniqArrayArray` returns `Array(String)`, so these cannot be
-- `Array(LowCardinality(String))` — the server rejects the mismatch.
--
-- The bloom filter is for the second read: the page's fifty ids are looked up
-- by `trace_id`, which is last in the key, exactly as `spans` is read for the
-- root of each trace today.
CREATE TABLE IF NOT EXISTS trace_summaries
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `start_time` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `received_at` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1)),
    `duration_ms` SimpleAggregateFunction(max, Float64) CODEC(Gorilla(8), ZSTD(1)),
    `span_count` SimpleAggregateFunction(max, UInt32) CODEC(T64, ZSTD(1)),
    `error_count` SimpleAggregateFunction(max, UInt32) CODEC(T64, ZSTD(1)),
    -- 2 error, 1 an explicit OK, 0 a status nobody set. The API maps it back.
    `worst_severity` SimpleAggregateFunction(max, UInt8) CODEC(T64, ZSTD(1)),
    `root_service` SimpleAggregateFunction(anyLast, LowCardinality(String)) CODEC(ZSTD(1)),
    `root_name` SimpleAggregateFunction(anyLast, LowCardinality(String)) CODEC(ZSTD(1)),
    `span_services` SimpleAggregateFunction(groupUniqArrayArray, Array(String)) CODEC(ZSTD(1)),
    `span_endpoints` SimpleAggregateFunction(groupUniqArrayArray, Array(String)) CODEC(ZSTD(1)),
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = AggregatingMergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, start_time, trace_id)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192
