-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- The decisions are in docs/architecture/decisions; this file is only the
-- shape they arrived at. Verified by applying the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- KeeperMap offset guard (ADR 0061/0068)
CREATE TABLE IF NOT EXISTS span_ingest_offsets
(
    `partition_key` String,
    `min_offset` Int64,
    `max_offset` Int64,
    `state` String
)
ENGINE = KeeperMap('/span_ingest_offsets')
PRIMARY KEY partition_key
