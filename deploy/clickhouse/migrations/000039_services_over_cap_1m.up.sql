-- Which minutes of which organization went over the per-tenant service cap, and
-- by how much: the durable record that `service_stats_1m` is missing services
-- for that minute (docs/changes/browser-door, decision 1).
--
-- The worker's derive stage writes one row per tenant-minute a drain capped
-- (derive/services.go, `capServices`), so a screen can say "this minute was
-- capped" beside the rollup the capped services are missing from. The cap is
-- per organization, not per project, so the row names no project; the tenant
-- still leads the sort key, as `span_trace_lookup`'s does.
--
-- **`services_left_out` is summed across drains.** Each worker caps its own
-- partitions' share of a minute, and a minute reopened by late spans is drained
-- again, so one service can be left out by two drains and counted twice, or
-- left out by one and kept by another. Read any row as "this tenant-minute was
-- capped"; the number says roughly how far over, not exactly which services.
--
-- `000035`'s engine and deduplication window: the writer sends an
-- `insert_deduplication_token` derived from the rows (insertdedup, 000021), so
-- a retried insert after a lost acknowledgement is not summed in twice.
--
-- Kept for the lesser of the derived retention and 35 days, as `000035` is:
-- `derived_retention_days` is the longest among the rows the drain left out, so
-- the record lasts as long as the rows it explains would have.
CREATE TABLE IF NOT EXISTS services_over_cap_1m
(
    `tenant_id` LowCardinality(String) CODEC(ZSTD(1)),
    `derived_retention_days` UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    `minute` DateTime CODEC(Delta(4), ZSTD(1)),
    `services_left_out` SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, minute)
TTL minute + toIntervalDay(least(derived_retention_days, 35))
SETTINGS non_replicated_deduplication_window = 1000
