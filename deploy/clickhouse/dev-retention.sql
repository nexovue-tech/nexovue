-- Development-only retention. Run AFTER the migrations, never in deployment.
--
-- The migrations carry the production TTLs (30d raw, 90d metrics). Three days
-- of spans on a laptop SSD is the point at which the disk stops being the
-- developer's problem — docs/delivery/dev-environment.md rule 3.
--
-- MODIFY TTL is the same mechanism the per-tenant retention job uses, and with
-- ttl_only_drop_parts=1 it drops whole parts rather than running a mutation.
--
--   docker exec -i nexovue-clickhouse-1 clickhouse-client \
--     --user nexovue --password nexovue_dev --database nexovue \
--     --multiquery < deploy/clickhouse/dev-retention.sql

ALTER TABLE spans   MODIFY TTL toDateTime(timestamp) + INTERVAL 3 DAY;
ALTER TABLE logs    MODIFY TTL toDateTime(timestamp) + INTERVAL 3 DAY;
ALTER TABLE metrics MODIFY TTL toDateTime(timestamp) + INTERVAL 7 DAY;
