-- `door` stays. ClickHouse refuses to drop or rename a sorting-key column, and a
-- migration here is one statement, so the key cannot first lose it. Kept, it is
-- harmless to the code before 000040: that writer names its columns, so its rows
-- read '' here, and its reads never mention the column. Running 000040 again finds
-- the column and the same key, and changes nothing but `server_peer_attr`.
ALTER TABLE service_edges_1m
    DROP COLUMN IF EXISTS server_peer_attr
