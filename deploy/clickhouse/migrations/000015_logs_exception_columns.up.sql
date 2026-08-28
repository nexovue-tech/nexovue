-- The other half of P1-30, and the reason it is a second migration: exceptions
-- are moving from span events to log records.
--
-- OpenTelemetry is deprecating the Span Events API in favour of log-based
-- events (opentelemetry.io/blog/2026/deprecating-span-events). The default is
-- still a span event and existing instrumentation keeps emitting one, with
-- OTEL_SEMCONV_EXCEPTION_SIGNAL_OPT_IN offering `logs` and `logs/dup` for a
-- phased move. So both shapes arrive, from different customers, for years.
--
-- Promoting the same two fields here means one grouping query reads both
-- sources instead of one query per era. `logs` holds zero rows, so this is
-- free; ADD COLUMN would be cheap regardless.
ALTER TABLE logs
    ADD COLUMN IF NOT EXISTS exception_type    LowCardinality(String) CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS exception_message String CODEC(ZSTD(1))
