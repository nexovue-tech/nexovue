-- Service dependency edges (P1-55). One row per caller→callee pair per minute.
--
-- An edge is a CLIENT span whose child is a SERVER span, or a PRODUCER whose
-- child is a CONSUMER — the pairing the OpenTelemetry service graph connector
-- uses. It is derived in the trace pipeline *before* the sampler, for the same
-- reason the counters are: sampling keeps a tenth of healthy traces, so edges
-- measured after it would report a tenth of the real call rate.
--
-- Shaped like `endpoint_stats_1m`, which answers the same kind of question one
-- level down, so the two read the same way and merge the same way.
CREATE TABLE IF NOT EXISTS service_edges_1m
(
    tenant_id              LowCardinality(String) CODEC(ZSTD(1)),
    derived_retention_days UInt16 DEFAULT 395 CODEC(T64, ZSTD(1)),
    project_id             LowCardinality(String) CODEC(ZSTD(1)),
    client_service         LowCardinality(String) CODEC(ZSTD(1)),
    server_service         LowCardinality(String) CODEC(ZSTD(1)),
    -- CLIENT→SERVER or PRODUCER→CONSUMER. Kept because a queue hop and a direct
    -- call are different edges even between the same two services.
    edge_kind              LowCardinality(String) CODEC(ZSTD(1)),
    minute                 DateTime CODEC(Delta(4), ZSTD(1)),
    call_count             SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    error_count            SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    -- Measured on the client span: what the caller experienced, which includes
    -- the network. The server's own view is already in endpoint_stats_1m.
    latency_quantiles      AggregateFunction(quantiles(0.5, 0.95, 0.99), Float64) CODEC(ZSTD(1))
)
ENGINE = AggregatingMergeTree
PARTITION BY (toYYYYMM(minute), derived_retention_days)
ORDER BY (tenant_id, project_id, client_service, server_service, edge_kind, minute)
TTL minute + toIntervalDay(derived_retention_days)
SETTINGS index_granularity = 8192
