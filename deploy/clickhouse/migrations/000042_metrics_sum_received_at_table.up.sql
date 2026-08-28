-- The defect ADR 0067 fixed in `spans` and `logs`, in the last tables that
-- still carry it. These partition and expire on `timestamp`, which for a
-- customer-sent metric is the customer's own clock — so a point dated a year
-- ahead is immortal, one dated 1970 is deleted on arrival, and a scrambled
-- clock creates a partition per bogus date.
--
-- Free right now: P1-29 has not shipped, so nothing but our own derived
-- counters has ever been written here. After M3 this is a rebuild.
--
-- `timestamp` stays authoritative for the measurement. DEFAULT timestamp so a
-- writer that does not send the column degrades to the old behaviour instead
-- of writing epoch into a 1970 partition that expires immediately.
--
-- This one holds the derived counters, so it is rebuilt via EXCHANGE
-- rather than replaced: the rows have to survive.
CREATE TABLE IF NOT EXISTS metrics_sum_rebuild
(
    `timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `start_timestamp` DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(3) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `scope_name` LowCardinality(String) CODEC(ZSTD(1)),
    `scope_version` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_name` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_unit` LowCardinality(String) CODEC(ZSTD(1)),
    `metric_description` String CODEC(ZSTD(1)),
    `resource_attrs` JSON,
    `attrs` JSON,
    `series_hash` UInt64 MATERIALIZED cityHash64(toJSONString(attrs)) CODEC(ZSTD(1)),
    `flags` UInt32 CODEC(T64, ZSTD(1)),
    `value` Float64 CODEC(Gorilla(8), ZSTD(1)),
    `aggregation_temporality` LowCardinality(String) CODEC(ZSTD(1)),
    `is_monotonic` Bool CODEC(ZSTD(1)),
    `exemplars.timestamp` Array(DateTime64(3)) CODEC(ZSTD(1)),
    `exemplars.value` Array(Float64) CODEC(ZSTD(1)),
    `exemplars.trace_id` Array(FixedString(32)) CODEC(ZSTD(1)),
    `exemplars.span_id` Array(FixedString(16)) CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp), series_hash, timestamp)
TTL toDateTime(received_at) + toIntervalDay(90)
SETTINGS non_replicated_deduplication_window = 1000, index_granularity = 8192
