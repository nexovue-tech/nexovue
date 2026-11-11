-- Per-service, per-version, per-minute request counts and latency histogram:
-- what a release is compared on (F28, design section 1; ADR-025).
--
-- `000035`'s shape with one column more. Filled by the worker's derive stage,
-- before the sampler, from exactly the spans that fill `service_stats_1m`, so
-- for one service and minute the version rows sum to that table's row (FR16).
-- Spans with no `service.version` count under the empty version.
--
-- **Versions are folded, never dropped.** A version past a cap, over 200
-- characters or failing the shared name check counts under `__other__`, so
-- totals stay exact while one version's own count can be short (FR17). The caps
-- are per drain and first come at arrival (derive/versions.go), so a reader
-- must treat any `__other__` request in its window as "versions were combined".
--
-- `version` sits after `minute` in the sort key because a read asks for one
-- service across a window, then splits it by version.
--
-- The `sumForEach` state, the 256-row granules and the deduplication window are
-- `000035`'s, for `000035`'s measured reasons; the writer sends an
-- `insert_deduplication_token` (insertdedup, 000021).
--
-- Kept for the lesser of the derived retention and 35 days, as `000035` is.
CREATE TABLE IF NOT EXISTS service_versions_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `version` LowCardinality(String) CODEC(ZSTD(1)),
    `request_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `error_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `latency_buckets` AggregateFunction(sumForEach, Array(UInt32)) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, service_name, minute, version)
TTL minute + toIntervalDay(least(derived_retention_days, 35))
SETTINGS index_granularity = 256, non_replicated_deduplication_window = 1000
