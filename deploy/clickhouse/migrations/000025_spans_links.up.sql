-- Span links on the trace store (P1-55 follow-up).
--
-- A link is a causal reference from one span to another, and across a queue it
-- is the *only* evidence of who published the message: modern messaging
-- instrumentation starts the consumer in a new trace and links back to the
-- producer rather than making it a child (OpenTelemetry messaging semantic
-- conventions — links are the default correlation because a span has one parent
-- and a batch consume has many producers). Measured against the OpenTelemetry
-- Demo: `checkout` emitted 20 PRODUCER spans and `accounting` 26 CONSUMER
-- spans, and not one trace held both halves — so `service_edges_1m` had never
-- held a row with edge_kind = 'queue', and could not.
--
-- Nested, which is the shape the OpenTelemetry ClickHouse exporter uses. With
-- flatten_nested = 1 it becomes the parallel arrays `links.trace_id`,
-- `links.span_id` and `links.attributes`, so a link can be ARRAY JOINed and
-- compared against `spans.trace_id` and `spans.span_id` directly — same hex
-- FixedString widths as the columns it points at, so no conversion.
--
-- No per-link trace_state, which the exporter does store: we do not store a
-- span's own tracestate either, because nothing queries it.
--
-- Attributes as a JSON String, like `events` rather than like `span_attrs`: a
-- link's attributes are read with the link and never filtered on, so giving
-- every link's keys their own JSON subcolumn buys no query.
--
-- ADD COLUMN is metadata only: no rewrite. An existing row reads as an empty
-- array, which is exactly what a span carrying no links means.
ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS links Nested(
        trace_id FixedString(32),
        span_id FixedString(16),
        attributes String
    ) CODEC(ZSTD(1))
