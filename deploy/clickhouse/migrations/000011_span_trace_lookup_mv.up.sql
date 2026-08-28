-- Squashed 2026-08-28. The 65 migrations this replaces rebuilt tables that had
-- been created hours earlier — `spans` alone was rebuilt three times — and
-- their reversibility protected data no environment held. Nothing was behind
-- them: one development database, no customers.
--
-- The decisions are in docs/architecture/decisions; this file is only the
-- shape they arrived at. Verified by applying the squashed set to an empty
-- database and diffing SHOW CREATE for every object against the live one.
--
-- maintains span_trace_lookup
CREATE MATERIALIZED VIEW IF NOT EXISTS span_trace_lookup_mv TO span_trace_lookup
(
    `tenant_id` LowCardinality(String),
    `trace_id` FixedString(32),
    `span_start` DateTime64(9),
    `span_end` DateTime64(9),
    `received_at` DateTime64(9),
    `retention_days` UInt16
)
AS SELECT
    tenant_id,
    trace_id,
    min(timestamp) AS span_start,
    max(timestamp) AS span_end,
    min(received_at) AS received_at,
    max(retention_days) AS retention_days
FROM spans
GROUP BY
    tenant_id,
    trace_id
