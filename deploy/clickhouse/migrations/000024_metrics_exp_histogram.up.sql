-- OTLP ExponentialHistogram: base-2 buckets described by a scale and two
-- offsets rather than an explicit bound list. It cannot be folded into the
-- explicit-bucket table without inventing the bounds it deliberately omits.
-- One of the five point-shape tables (ADR 0066). Columns before `flags` are
-- identical across all five; only the value shape below differs.
CREATE TABLE IF NOT EXISTS metrics_exp_histogram
(
    timestamp          DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    -- The start of the interval this point covers. Without it a cumulative
    -- counter cannot be turned into a rate across a process restart, and it
    -- cannot be reconstructed after the fact.
    start_timestamp    DateTime64(3) CODEC(Delta(8), ZSTD(1)),
    tenant_id          LowCardinality(String) CODEC(ZSTD(1)),
    project_id         LowCardinality(String) CODEC(ZSTD(1)),
    service_name       LowCardinality(String) CODEC(ZSTD(1)),
    -- Which instrumentation library emitted it: two libraries can publish the
    -- same metric name and mean different things.
    scope_name         LowCardinality(String) CODEC(ZSTD(1)),
    scope_version      LowCardinality(String) CODEC(ZSTD(1)),
    metric_name        LowCardinality(String) CODEC(ZSTD(1)),
    metric_unit        LowCardinality(String) CODEC(ZSTD(1)),
    metric_description String CODEC(ZSTD(1)),
    resource_attrs     JSON,
    attrs              JSON,
    -- A series is one attribute set. The hash keeps a series contiguous inside
    -- the hour, so reading one back is a range rather than a scan.
    series_hash        UInt64 MATERIALIZED cityHash64(toJSONString(attrs)) CODEC(ZSTD(1)),
    -- OTLP DataPointFlags. Bit 1 is NO_RECORDED_VALUE, which is not the same
    -- as a zero and must not be charted as one.
    flags              UInt32 CODEC(T64, ZSTD(1)),
    count                  UInt64 CODEC(T64, ZSTD(1)),
    sum                    Nullable(Float64) CODEC(ZSTD(1)),
    -- Bucket i covers (base^i, base^(i+1)] with base = 2^(2^-scale).
    scale                  Int32 CODEC(T64, ZSTD(1)),
    zero_count             UInt64 CODEC(T64, ZSTD(1)),
    positive_offset        Int32 CODEC(T64, ZSTD(1)),
    positive_bucket_counts Array(UInt64) CODEC(T64, ZSTD(1)),
    negative_offset        Int32 CODEC(T64, ZSTD(1)),
    negative_bucket_counts Array(UInt64) CODEC(T64, ZSTD(1)),
    min                    Nullable(Float64) CODEC(ZSTD(1)),
    max                    Nullable(Float64) CODEC(ZSTD(1)),
    -- DELTA or CUMULATIVE, in the short form `span_kind` and `status_code`
    -- already use. Most SDKs default to CUMULATIVE; reading one as the other
    -- silently produces a wrong rate at every process restart.
    aggregation_temporality LowCardinality(String) CODEC(ZSTD(1)),
    -- The only link from a metric back to a trace, which is the jump this
    -- product exists to make. OTLP's FilteredAttributes are not kept: the trace
    -- they point at carries them.
    exemplars Nested (
        timestamp DateTime64(3),
        value     Float64,
        trace_id  FixedString(32),
        span_id   FixedString(16)
    ) CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp), series_hash, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS non_replicated_deduplication_window = 1000
