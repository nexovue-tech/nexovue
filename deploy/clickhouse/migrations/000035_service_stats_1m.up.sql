-- Per-service, per-minute request counts and latency histogram: anomaly
-- detection's input (F6, design "Inputs").
--
-- Filled by the worker's derive stage, before the sampler, from the same
-- inbound SERVER and CONSUMER spans as `endpoint_stats_1m`. Every span is
-- counted, so the counts and the histogram are exact and merge by addition.
--
-- `latency_buckets` is 64 counts over fixed log-spaced boundaries from 1 ms to
-- 60 s (derive/services.go defines them). A count above any boundary, and a
-- window's p95, are therefore reproducible exactly from the stored rows.
--
-- **AggregateFunction(sumForEach), not SimpleAggregateFunction or a
-- SummingMergeTree.** Rows for one service-minute arrive from every worker that
-- owns a partition, and again for a minute reopened by late spans, so merges
-- must add the arrays element by element. Measured on 26.3.20.7:
-- `SimpleAggregateFunction(sumForEach, …)` is refused ("Unsupported aggregate
-- function sumForEach"), and SummingMergeTree does not sum a plain array. The
-- worker sends `Array(UInt32)` and the server folds it with
-- `arrayReduce('sumForEachState', …)`, as `endpoint_stats_1m` does for its
-- quantiles; readers use `sumForEachMerge`, which returns `Array(UInt64)`.
--
-- The sort key leads with the tenant and the project, so every read is bound
-- to both, and ends with the minute a window reads as a range.
--
-- **Granules of 256 rows, not the default 8,192.** The project job reads each
-- service's last few minutes every minute, and a read takes whole granules: at
-- 8,192 a month-full partition read 90,112 rows for 69, and at 256 it reads
-- 3,072 (T8.2). The price is a primary index 32 times larger in memory: 2.1 MiB
-- for the 78 million rows of AC29's month, measured.
--
-- Kept for the lesser of the derived retention and 35 days: detection's normal
-- reads four weeks plus the current day, and nothing else reads this table.
--
-- The deduplication window needs the writer's `insert_deduplication_token` to
-- do anything on the `input()` insert path (insertdedup, 000021).
CREATE TABLE IF NOT EXISTS service_stats_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `request_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `error_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `latency_buckets` AggregateFunction(sumForEach, Array(UInt32)) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, service_name, minute)
TTL minute + toIntervalDay(least(derived_retention_days, 35))
SETTINGS index_granularity = 256, non_replicated_deduplication_window = 1000
