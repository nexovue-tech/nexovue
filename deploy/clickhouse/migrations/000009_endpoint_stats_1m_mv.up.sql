-- Feeds 000008 as spans arrive. Separate file because the migration runner sends
-- one statement per file.
--
-- This aggregates *stored* spans, so it reflects what survived tail sampling.
-- The exact pre-sampling counters live in `metrics`, written by the worker before
-- the sampler runs — that separation is what keeps the numbers the detectors
-- watch honest while storage stays sampled (ADR 0010).
--
-- `status_code` holds the short OTel form: the worker strips the STATUS_CODE_
-- prefix before writing. Comparing against the prefixed enum name matches nothing
-- and reports every endpoint as healthy.
CREATE MATERIALIZED VIEW IF NOT EXISTS endpoint_stats_1m_mv TO endpoint_stats_1m
AS SELECT
    tenant_id,
    project_id,
    service_name,
    span_name,
    toStartOfMinute(timestamp)                   AS minute,
    count()                                      AS request_count,
    countIf(status_code = 'ERROR')               AS error_count,
    quantilesState(0.5, 0.95, 0.99)(duration_ms) AS latency_quantiles
FROM spans
GROUP BY tenant_id, project_id, service_name, span_name, minute
