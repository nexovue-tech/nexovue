-- F17: profile samples, the fourth signal (docs/features/profiling/design.md).
--
-- One row per sample the receiver converted (ADR-015): its stack inline, as
-- parallel arrays leaf first, and its value in the profile type's own unit.
-- Stacks repeat heavily; LowCardinality and ZSTD carry the repetition, and
-- ordering by stack_hash within the hour puts identical stacks side by side,
-- which is what lets them compress — no join on the read path. A deduplicated
-- stacks table is the fallback if the storage target (NFR3) is missed.
--
-- A frame the profiler could not name has an empty function and keeps its
-- binary and address, so it is shown as unsymbolized, never as nothing.
--
-- Trace and span ids are hex, as `spans` stores them, so a span's profile is a
-- plain equality on the two columns; the bloom filter keeps that read cheap.
-- Retention defaults to 14 days (spec D2); the worker stamps each row with
-- min(the project's raw retention, 14).
CREATE TABLE IF NOT EXISTS profile_samples
(
    `timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `received_at` DateTime64(3) DEFAULT timestamp CODEC(Delta(8), ZSTD(1)),
    `retention_days` UInt16 DEFAULT 14 CODEC(T64, ZSTD(1)),
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `host_name` LowCardinality(String) CODEC(ZSTD(1)),
    `service_version` LowCardinality(String) CODEC(ZSTD(1)),
    `environment` LowCardinality(String) CODEC(ZSTD(1)),
    `sample_type` LowCardinality(String) CODEC(ZSTD(1)),
    `sample_unit` LowCardinality(String) CODEC(ZSTD(1)),
    `value` Int64 CODEC(T64, ZSTD(1)),
    `stack_hash` UInt64 CODEC(ZSTD(1)),
    `frame_functions` Array(LowCardinality(String)) CODEC(ZSTD(3)),
    `frame_files` Array(LowCardinality(String)) CODEC(ZSTD(3)),
    `frame_lines` Array(UInt32) CODEC(ZSTD(3)),
    `frame_binaries` Array(LowCardinality(String)) CODEC(ZSTD(3)),
    `frame_addresses` Array(UInt64) CODEC(ZSTD(3)),
    `trace_id` FixedString(32) CODEC(ZSTD(1)),
    `span_id` FixedString(16) CODEC(ZSTD(1)),
    `resource_attrs` JSON,
    `attrs` JSON,
    INDEX idx_span_id span_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, service_name, sample_type, toStartOfHour(timestamp), stack_hash, timestamp)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS non_replicated_deduplication_window = 1000, index_granularity = 8192
