-- No column in any table declared a CODEC, so everything sat on the server
-- default (LZ4). Measured on 12M live dev spans, three columns were 44% of the
-- table: span_id 180.6 MiB at ratio 1.0, timestamp 63.9 MiB at 1.4,
-- parent_span_id 58.0 MiB at 3.1.
--
-- Every codec below was measured against that data rather than chosen from a
-- table of rules. What the measurement showed:
--   ids are stored hex, so 8 bytes of entropy occupy 16 stored bytes and LZ4
--     finds nothing in them; ZSTD(1) recovers the expansion, halving all three.
--   timestamp wants Delta(8), ZSTD(1) — 16.4 MiB against 32.6 for DoubleDelta,
--     because the sort key orders by hour then trace id, so the series is not
--     the constant-stride sequence DoubleDelta is built for.
--   duration_ms wants Gorilla, ZSTD(1): 1.66 MiB against 2.40 for plain ZSTD.
--   ZSTD(3) was no better than ZSTD(1) on the ids and costs more to write.
--
-- The remaining JSON and free-text columns keep the default. ZSTD(1) measured
-- *worse* than LZ4 on `events` and `resource_attrs` — both are near-constant
-- across rows today, which is a property of dev data rather than of the column,
-- so they are left until M3 fills them with real payloads.
--
-- A codec is metadata. Existing parts keep their old compression until they
-- next merge; nothing is rewritten here.
ALTER TABLE spans
    MODIFY COLUMN timestamp          CODEC(Delta(8), ZSTD(1)),
    MODIFY COLUMN tenant_id          CODEC(ZSTD(1)),
    MODIFY COLUMN project_id         CODEC(ZSTD(1)),
    MODIFY COLUMN trace_id           CODEC(ZSTD(1)),
    MODIFY COLUMN span_id            CODEC(ZSTD(1)),
    MODIFY COLUMN parent_span_id     CODEC(ZSTD(1)),
    MODIFY COLUMN service_name       CODEC(ZSTD(1)),
    MODIFY COLUMN span_name          CODEC(ZSTD(1)),
    MODIFY COLUMN span_kind          CODEC(ZSTD(1)),
    MODIFY COLUMN duration_ms        CODEC(Gorilla, ZSTD(1)),
    MODIFY COLUMN status_code        CODEC(ZSTD(1)),
    MODIFY COLUMN status_message     CODEC(ZSTD(1)),
    MODIFY COLUMN http_method        CODEC(ZSTD(1)),
    MODIFY COLUMN http_route         CODEC(ZSTD(1)),
    MODIFY COLUMN http_status        CODEC(T64, ZSTD(1)),
    MODIFY COLUMN deployment_version CODEC(ZSTD(1)),
    MODIFY COLUMN span_attrs         CODEC(ZSTD(1))
