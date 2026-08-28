-- Per-tenant retention, shaped so a part can expire as a whole.
--
-- ADR 0038 proposed a conditional TTL and claimed it would stay a cheap part
-- drop. Tested on 26.3 it does not: with two tenants in one part and
-- ttl_only_drop_parts=1, the expired row went and its neighbour stayed, so the
-- part was rewritten. At volume that is a rewrite on every merge, plus DDL that
-- grows with the tenant list and a job to regenerate it.
--
-- Instead the retention a row is entitled to is stamped on the row and joins
-- the partition key. Every row in a part then shares one expiry, the part drops
-- whole, and no job exists. Cardinality is the number of distinct retention
-- values, not the number of tenants — which is what 0038 rightly refused to
-- partition by. ADR 0072.
--
-- 12M rows here, so an exchange rather than a replace.
CREATE TABLE IF NOT EXISTS spans_rebuild
(
    `timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(9) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 30 CODEC(T64, ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `span_id` FixedString(16) CODEC(ZSTD(1)),
    `parent_span_id` FixedString(16) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `span_name` LowCardinality(String) CODEC(ZSTD(1)),
    `span_kind` LowCardinality(String) CODEC(ZSTD(1)),
    `duration_ms` Float64 CODEC(Gorilla(8), ZSTD(1)),
    `status_code` LowCardinality(String) CODEC(ZSTD(1)),
    `status_message` String CODEC(ZSTD(1)),
    `http_method` LowCardinality(String) CODEC(ZSTD(1)),
    `http_route` LowCardinality(String) CODEC(ZSTD(1)),
    `http_status` UInt16 CODEC(T64, ZSTD(1)),
    `deployment_version` LowCardinality(String) CODEC(ZSTD(1)),
    `resource_attrs` JSON,
    `span_attrs` JSON CODEC(ZSTD(1)),
    `events` String,
    INDEX idx_trace_id trace_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS index_granularity = 8192
