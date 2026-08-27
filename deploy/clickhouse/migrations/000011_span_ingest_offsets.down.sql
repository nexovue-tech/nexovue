-- Dropping the last table on a KeeperMap path clears its Keeper data too, so
-- this really does undo the migration rather than leaving orphaned state behind.
DROP TABLE IF EXISTS span_ingest_offsets
