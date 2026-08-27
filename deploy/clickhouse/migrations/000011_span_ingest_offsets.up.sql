-- Where the trace-assembly pipeline records what each Kafka partition has
-- already counted, so a replay after a store restart is not counted twice.
--
-- ADR 0060 relied on ClickHouse's own insert deduplication to recognise a
-- replayed counter block. ADR 0061 measured the hole: that log does not survive
-- a ClickHouse restart, which is the very event that causes the replay. Keeper
-- does survive it.
--
-- The protocol is ClickHouse's own, from their Kafka connector: `state` is
-- BEFORE while an insert of [min_offset, max_offset] is in flight and AFTER
-- once the store has acknowledged it, so a recovered BEFORE says the insert may
-- or may not have landed and a recovered AFTER says it did.
CREATE TABLE IF NOT EXISTS span_ingest_offsets (
    partition_key String,
    min_offset    Int64,
    max_offset    Int64,
    state         String
) ENGINE = KeeperMap('/span_ingest_offsets')
PRIMARY KEY partition_key
