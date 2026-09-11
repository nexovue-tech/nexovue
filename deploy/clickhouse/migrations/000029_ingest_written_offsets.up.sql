-- Where each partition of the log and metric topics has reached the store, so
-- a restart does not write its rows again.
--
-- Same root cause as `span_written_offsets` (000026), one pipeline shape later.
-- Delivery is at-least-once and offsets commit after the insert, so everything
-- between the ClickHouse acknowledgement and the Kafka commit is replayed after
-- an abrupt exit and, until now, inserted a second time. Measured here over a
-- controlled series of 60 SIGKILL restarts under load: 49,580 of 905,020
-- distinct log rows had two copies, 5.5%.
--
-- Much smaller than the spans' 41% because the window is much narrower. These
-- two pipelines insert inside Absorb, so a batch is exposed from its insert to
-- its commit rather than for the 43-72s spans waited on a sampling window and a
-- minute bucket. Narrower is not closed: an abrupt kill lands in it often
-- enough to matter, and a crash loop lands in it repeatedly.
--
-- One table for both signals, not one each. The key is `{topic}:{partition}`
-- and the two pipelines consume different topics, so their rows cannot collide;
-- each reads and writes only its own topic's keys, so neither can move the
-- other's watermark or block it. Two tables of identical shape would be the
-- duplication rule 11 exists to stop, and there is no failure they would
-- separate that this key does not already separate.
--
-- Not shared with `span_written_offsets`, which is the same shape again. That
-- table is live and holds the span watermarks right now; merging would mean
-- dropping and recreating it, and a KeeperMap table's rows go with it. The cost
-- of merging is a hole in span idempotency, which is exactly what 000026 was
-- written to prevent.
--
-- No BEFORE/AFTER bracket, because this is a plain fact recorded only once
-- ClickHouse has acknowledged the insert, not a counter that would be wrong if
-- applied twice. Losing the write costs a duplicate on the next replay, which is
-- today's behaviour, and never a hole.
--
-- KeeperMap because the state has to survive the restart that causes the
-- replay, and Keeper does where the store's own deduplication log does not.
CREATE TABLE IF NOT EXISTS ingest_written_offsets
(
    `partition_key` String,
    -- The offset written through, not the next one to write. -1 is "nothing
    -- written": Kafka offsets start at zero, so zero would claim the first
    -- record was stored and skip it.
    `written_through` Int64
)
ENGINE = KeeperMap('/ingest_written_offsets')
PRIMARY KEY partition_key
