-- How far each partition of the span topic has been counted into the derived
-- rollups, and when that record was received (F6, design "When a minute is
-- settled").
--
-- The scheduler judges a minute only once every partition has delivered past
-- it. It compares each row here with Kafka's high watermark: a partition with
-- nothing unread is caught up, and otherwise it is through
-- `counted_through_received_at`.
--
-- Written by the trace worker after ClickHouse has acknowledged a settlement's
-- derived inserts, beside `span_ingest_offsets`' AFTER, and only for that
-- settlement's own partitions (C1). A refused derived insert holds it for every
-- partition in the settlement, for at most 15 minutes (C22, design 2.1). It
-- never moves backwards.
--
-- No tenant column: it describes the pipeline, not a customer. It is exempt
-- from the tenant-structure test for `detection.settle` alone, and excluded
-- from erasure.
--
-- KeeperMap, like 000010, 000026 and 000029: Keeper survives a restart of the
-- store, and a backup restores this table empty (deploy/backup/common.sh). A
-- separate table rather than columns on `span_ingest_offsets` because KeeperMap
-- refuses ADD COLUMN (000026).
CREATE TABLE IF NOT EXISTS span_derived_progress
(
    `partition_key` String,
    -- The highest offset whose spans are in every derived rollup.
    `counted_through` Int64,
    -- When the receiver took that record: the only clock in the path we control.
    `counted_through_received_at` DateTime64(9)
)
ENGINE = KeeperMap('/span_derived_progress')
PRIMARY KEY partition_key
