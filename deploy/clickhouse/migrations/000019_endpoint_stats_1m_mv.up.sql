-- Entry points only. CLIENT and INTERNAL spans are work the service *did*, not
-- requests it *served*, and counting them is the bug 000018 corrects. CONSUMER
-- is included because a queue handler is an entry point in the same sense a
-- SERVER span is: a unit of inbound work with a latency and an outcome.
--
-- `status_code` holds the short OTel form: the worker strips the STATUS_CODE_
-- prefix before writing. Comparing against the prefixed enum name matches
-- nothing and reports every endpoint as healthy.
CREATE MATERIALIZED VIEW IF NOT EXISTS endpoint_stats_1m_mv TO endpoint_stats_1m
AS SELECT
    tenant_id,
    project_id,
    service_name,
    span_kind,
    span_name,
    toStartOfMinute(timestamp)                   AS minute,
    count()                                      AS request_count,
    countIf(status_code = 'ERROR')               AS error_count,
    quantilesState(0.5, 0.95, 0.99)(duration_ms) AS latency_quantiles
FROM spans
WHERE span_kind IN ('SERVER', 'CONSUMER')
GROUP BY tenant_id, project_id, service_name, span_kind, span_name, minute
