-- Where each partition's spans have reached the store, so a restart does not
-- write them again.
--
-- Delivery is at-least-once and the offsets commit late: measured on this
-- deployment, p50 43s and p90 72s behind ingest, because a batch waits for its
-- counters' minute bucket as well as for its sampling window. Everything in
-- that window is replayed after a crash, and until now every span in it was
-- inserted a second time. Measured over six hours before this change: 6135 of
-- 14980 distinct spans had more than one copy, up to six, every copy sharing
-- `received_at` to the nanosecond — the same envelope, not a re-send.
--
-- Block deduplication (000019) cannot catch it. After a restart the records
-- re-batch and the sampler releases them in different groupings, so the block
-- hash never matches the one that landed.
--
-- A table of its own, beside `span_ingest_offsets` rather than a column on it.
-- The column was the intent and it is not available: measured on 26.3.20.7,
-- `ALTER TABLE span_ingest_offsets ADD COLUMN` fails with "Alter of type
-- 'ADD_COLUMN' is not supported by storage KeeperMap", and recreating that
-- table would throw away the counter watermarks it is holding right now to
-- save one small write per settle. Separate is also honest: these are two
-- durable facts that move at different speeds.
--
-- No BEFORE/AFTER bracket, unlike the counters (ADR 0061). A counter
-- re-applied is a wrong number, while this is a plain fact recorded only once
-- ClickHouse has acknowledged the insert. Losing the write costs a duplicate
-- on the next replay — which is today's behaviour — and never a hole.
--
-- KeeperMap for the same reason 000010 chose it: the state has to survive the
-- restart that causes the replay, and Keeper does where the store's own
-- deduplication log does not.
CREATE TABLE IF NOT EXISTS span_written_offsets
(
    `partition_key` String,
    -- The offset written through, not the next one to write. -1 is "nothing
    -- written": Kafka offsets start at zero, so zero would claim the first
    -- record was stored and skip it.
    `written_through` Int64
)
ENGINE = KeeperMap('/span_written_offsets')
PRIMARY KEY partition_key
