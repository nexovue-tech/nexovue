-- Which door a stored log record came through (F28 D38, plan T4.2): the same column,
-- values and reason as 000041 on `spans`. An exception can arrive as a log record, and
-- the release page reads both tables (F28 FR29).
ALTER TABLE logs ADD COLUMN IF NOT EXISTS door LowCardinality(String) CODEC(ZSTD(1))
