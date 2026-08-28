-- OTLP makes a log record's `Timestamp` optional and defines a second clock,
-- `ObservedTimestamp` — when the collection system saw it. A record may carry
-- one, both or neither, so the writer falls back: event time, else observed
-- time, else our own `received_at`. It must never write zero, which partitions
-- to 1970 and is deleted on arrival.
--
-- Which one was used is recorded rather than inferred. A log whose `timestamp`
-- is really our receive time answers a question about event time misleadingly,
-- and only this column lets a query or a screen say so. Free now: the table is
-- empty, and LowCardinality over three values costs nothing.
ALTER TABLE logs
    ADD COLUMN IF NOT EXISTS timestamp_source LowCardinality(String) DEFAULT 'event' CODEC(ZSTD(1))
