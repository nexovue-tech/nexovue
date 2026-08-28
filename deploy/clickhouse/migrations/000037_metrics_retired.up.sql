-- The single-Float64 `metrics` table retires. ADR 0066 replaced it with five
-- tables that can represent OTLP; `derive` now writes its per-minute counters
-- to `metrics_sum` with the aggregation temporality, interval and monotonicity
-- that make a rate derivable rather than guessed.
--
-- Dropped rather than left in place: two metric tables, one written and one
-- not, is the state where somebody later reads the wrong one. Its contents are
-- our own derived counters, which the pipeline reproduces from Kafka.
DROP TABLE IF EXISTS metrics
