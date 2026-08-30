-- RPC identity on the promoted columns. A gRPC span carries none of the HTTP
-- attributes: measured against the OpenTelemetry Demo, five of twelve services
-- (currency, payment, product-catalog, recommendation, checkout) left
-- http_method, http_route and http_status empty on every span. Their identity
-- is in `rpc.method` and their outcome in the RPC status attribute, neither of
-- which had anywhere to land.
--
-- Separate columns rather than reusing the HTTP ones. `http_status` is UInt16
-- over the 1xx-5xx space; a gRPC status is 0-16, or a string once semconv
-- 1.39.0 changed it, and the two code spaces in one column make every
-- `http_status >= 500` filter silently wrong. `http_method` is the verb —
-- every gRPC call is a POST — while `rpc.method` is the operation, so it is
-- the route's analogue, not the method's.
--
-- The writer normalizes both convention generations onto one vocabulary before
-- these columns, so `rpc_status = 'UNAVAILABLE'` matches an SDK emitting the
-- legacy integer 14 and one emitting the current string alike.
--
-- ADD COLUMN is metadata only: no rewrite. Empty string is what an existing row
-- and a non-RPC span both mean, which is the same degradation http_method has.
ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS rpc_system LowCardinality(String) CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS rpc_method LowCardinality(String) CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS rpc_status LowCardinality(String) CODEC(ZSTD(1))
