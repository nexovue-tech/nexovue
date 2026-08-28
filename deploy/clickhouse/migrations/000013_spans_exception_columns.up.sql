-- Error extraction (P1-30). OpenTelemetry records an exception as a span
-- *event* named `exception`, carrying `exception.type`, `exception.message`,
-- `exception.stacktrace` and `exception.escaped`. We already store `events` as
-- a JSON string, so nothing new is collected — the two fields worth grouping
-- and filtering by are promoted to columns.
--
-- ADD COLUMN is metadata only: no rewrite, no rebuild. Deliberately not in the
-- sort key, which would be one. The stacktrace stays in `events`: it is large,
-- rarely filtered on, and would dilute the columns beside it.
ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS exception_type    LowCardinality(String) CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS exception_message String CODEC(ZSTD(1))
