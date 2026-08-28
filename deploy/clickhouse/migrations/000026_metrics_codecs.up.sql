-- `metrics` keeps its shape. It is the table the derive pipeline writes, and
-- that pipeline is not being changed here (ADR 0066); the five OTLP tables land
-- beside it rather than replacing it.
--
-- Codecs only, on the same measured reasoning as 000020. A codec is metadata
-- and merges adopt it, so nothing about the derive write path changes: the
-- column list, the types and the insert-block hash that 000010's deduplication
-- depends on are all untouched.
ALTER TABLE metrics
    MODIFY COLUMN timestamp    CODEC(Delta(8), ZSTD(1)),
    MODIFY COLUMN tenant_id    CODEC(ZSTD(1)),
    MODIFY COLUMN project_id   CODEC(ZSTD(1)),
    MODIFY COLUMN metric_name  CODEC(ZSTD(1)),
    MODIFY COLUMN value        CODEC(Gorilla, ZSTD(1)),
    MODIFY COLUMN service_name CODEC(ZSTD(1))
