-- One frozen reading per service per judged minute: what detection saw, the
-- normal it compared with, and what it decided (F6, design "Readings, frozen").
--
-- Frozen because a span arriving after its minute was judged still lands in
-- `service_stats_1m`. A judgement must stay explainable from what was known at
-- the time, so it is never recomputed from the rollup.
--
-- Written only by the scheduler's `nexovue_detection_writer` account
-- (ADR-023). A crash between this write and the Postgres commit judges the
-- minute again, so a minute can be written twice: ReplacingMergeTree keeps the
-- highest `version`, and reads use `argMax(…, version)` so the duplicate is
-- counted once before a merge as well as after.
--
-- **The key starts with `tenant_id`** (design 2.1, security M1). Two tenants
-- can have a service of the same name; without the tenant in the key a
-- replacing merge would keep one of them and drop the other's reading.
--
-- **The baseline is stored per signal.** Each signal's normal has its own mode
-- and day count (traffic waits 21 days, a weekend class can learn later), so a
-- single set of baseline columns could not describe all three.
--
-- **Per-endpoint failures are a Map of (requests, failures).** A name and its
-- two counts travel together, where parallel arrays could disagree in length.
-- A replacing table never merges the value, so no aggregate type is needed.
--
-- A NULL statistic means there was none: a learning normal, or too few
-- requests for a p95. Verdicts are strings so the method's vocabulary
-- (T2c.2) is defined in one place, the scheduler.
--
-- Kept for the lesser of the derived retention and 35 days. A finding copies
-- what it needs into its own record, so it stays explainable after its readings
-- expire (AC40).
CREATE TABLE IF NOT EXISTS detection_readings
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `project_id` LowCardinality(String) CODEC(ZSTD(1)),
    `service_name` LowCardinality(String) CODEC(ZSTD(1)),
    -- The judged minute, UTC.
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    -- Higher wins. The writer sets it; a re-judged minute writes a higher one.
    `version` UInt64 CODEC(T64, ZSTD(1)),

    -- The window judged, ending at `minute`.
    `window_minutes` UInt8 CODEC(T64, ZSTD(1)),
    `window_requests` UInt64 CODEC(T64, ZSTD(1)),
    `window_errors` UInt64 CODEC(T64, ZSTD(1)),
    `window_p95_ms` Nullable(Float64) CODEC(ZSTD(1)),
    -- Requests in the window slower than `slower_threshold_ms` (L).
    `window_above_threshold` UInt64 CODEC(T64, ZSTD(1)),
    -- Endpoints over the per-endpoint request floor only.
    `endpoint_failures` Map(String, Tuple(requests UInt64, failures UInt64)) CODEC(ZSTD(1)),

    -- Losing traffic: its normal, on the square-root scale, and its verdict.
    `traffic_mode` LowCardinality(String) CODEC(ZSTD(1)),
    `traffic_days` UInt8 CODEC(T64, ZSTD(1)),
    `traffic_median` Nullable(Float64) CODEC(ZSTD(1)),
    `traffic_spread` Nullable(Float64) CODEC(ZSTD(1)),
    `traffic_verdict` LowCardinality(String) CODEC(ZSTD(1)),

    -- Failing more: its normal rate, wander, burst profile and verdict.
    `failing_mode` LowCardinality(String) CODEC(ZSTD(1)),
    `failing_days` UInt8 CODEC(T64, ZSTD(1)),
    `failing_median` Nullable(Float64) CODEC(ZSTD(1)),
    `failing_spread` Nullable(Float64) CODEC(ZSTD(1)),
    `failing_burst_height` Nullable(Float64) CODEC(ZSTD(1)),
    `failing_burst_minutes` UInt16 CODEC(T64, ZSTD(1)),
    `failing_verdict` LowCardinality(String) CODEC(ZSTD(1)),

    -- Slower: its normal p95, wander, L and verdict.
    `slower_mode` LowCardinality(String) CODEC(ZSTD(1)),
    `slower_days` UInt8 CODEC(T64, ZSTD(1)),
    `slower_median` Nullable(Float64) CODEC(ZSTD(1)),
    `slower_spread` Nullable(Float64) CODEC(ZSTD(1)),
    `slower_threshold_ms` Nullable(Float64) CODEC(ZSTD(1)),
    `slower_verdict` LowCardinality(String) CODEC(ZSTD(1))
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, service_name, minute)
TTL minute + toIntervalDay(least(derived_retention_days, 35))
SETTINGS index_granularity = 8192
