-- The trace's true earliest start, apart from the row's key (change record
-- `trace-list-split-traces`).
--
-- `start_time` is in the sort key, and rows merge only when their keys match.
-- The worker used to move it whenever an earlier span arrived — and a root ends
-- last, so it is exported after its children and often after the trace was
-- first written. The revision then landed under a second key, never merged, and
-- the first row stayed in the list as a partial trace of its own: about 38 of
-- 26,886 traces in the dev store. The worker now fixes `start_time` at the first write
-- and carries the real start here, which the list shows; `min` merges it like
-- the other measures.
--
-- DEFAULT start_time: a row written before this, or by the backfill, has the two
-- equal — the backfill derives both from min(timestamp).
ALTER TABLE trace_summaries
    ADD COLUMN IF NOT EXISTS `trace_start` SimpleAggregateFunction(min, DateTime64(9))
        DEFAULT start_time CODEC(Delta(8), ZSTD(1)) AFTER start_time
