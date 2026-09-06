-- Per-minute p75 rollup of the web vitals. 13 months, like every other rollup.
--
-- A Core Web Vital is defined at the 75th percentile, so the screen's headline
-- number is a quantile and not an average. A quantile cannot be filtered after
-- the fact — the p75 of "all devices" says nothing about the p75 of mobile —
-- so every dimension the screen slices by has to be part of the key, and the
-- state has to be kept rather than a finished number.
--
-- Shaped like `endpoint_stats_1m` (000008), which answers the same kind of
-- question for the server door, so the two read and merge identically.
--
-- **One minute, even though nobody reads a web vital per minute.** A p75 over a
-- single minute of one page's traffic is a p75 of two or three samples. The
-- bucket is this fine anyway because a minute merges up into an hour, a day or
-- Google's 28-day window at read time, and an hour does not merge down: too
-- fine costs storage, too coarse cannot be undone.
--
-- **The cardinality cost is real and is named here.** `constants.ts` fixes the
-- page-view span name to a literal precisely so a per-URL name cannot mint a
-- rollup row per URL per minute in `endpoint_stats_1m`, and `url_path` in this
-- key does exactly that thing on purpose — there is no route template on a
-- vital record to group by (see 000030). The bound is that rows per minute can
-- never exceed readings per minute, so this table is never taller than the raw
-- one; a site with per-id URLs simply degenerates to one rollup row per reading,
-- each carrying a quantile state larger than the Float64 it replaced. That is a
-- storage cost, bounded by `derived_retention_days`, not a wrong answer.
-- Normalising `url_path` into a page group is what would remove it.
--
-- `reading_count` is not decoration: it is what lets the screen refuse to show
-- a p75 computed from four samples.
--
-- Carrying 0.5 and 0.95 beside 0.75 is free — the state is one sample reservoir
-- whatever parameters are asked of it — and the parameter list is fixed at DDL
-- time, so a median wanted later would otherwise be a table rebuild.
--
-- The deduplication window needs an `insert_deduplication_token` from the
-- writer to do anything on the `input()` insert path; see 000030.
CREATE TABLE IF NOT EXISTS web_vitals_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `vital_name` LowCardinality(String) CODEC(ZSTD(1)),
    `url_path` String CODEC(ZSTD(1)),
    `device_class` LowCardinality(String) CODEC(ZSTD(1)),
    `country` LowCardinality(String) CODEC(ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `reading_count` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    `value_quantiles` AggregateFunction(quantiles(0.5, 0.75, 0.95), Float64) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, vital_name, url_path, device_class, country, minute)
TTL minute + toIntervalDay(derived_retention_days)
SETTINGS index_granularity = 8192, non_replicated_deduplication_window = 1000
