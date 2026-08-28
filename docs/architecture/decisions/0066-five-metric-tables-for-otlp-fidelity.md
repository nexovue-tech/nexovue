# 0066 — Five metric tables, for OTLP fidelity

- **Status:** Accepted
- **Date:** 2026-08-28
- **Supersedes:** [0018](0018-data-layer-design.md) in part — the `metrics` shape for OTLP ingest

## Context

`metrics` ([000003](../../../deploy/clickhouse/migrations/000003_metrics_sum.up.sql), sort key amended by
000007) is one `Float64` per row. OTLP metrics do not fit in it. Missing: aggregation temporality,
start timestamp, monotonicity, histogram bucket counts and bounds, exponential histogram scale, zero
count and offsets, summary quantiles, exemplars, and the no-recorded-value flag.

**Aggregation temporality is the unrecoverable one.** Most SDKs default to CUMULATIVE counters.
Turning a cumulative counter into a rate requires detecting its resets, and detecting a reset
requires the start timestamp — a counter that falls has either wrapped or restarted, and only the
start timestamp says which. Without both fields every process restart in a customer's fleet silently
produces a wrong rate, and neither field can be backfilled: it was never received. P1-29 writes this
table next month, which is why it is being fixed first.

The reference implementation is the OpenTelemetry Collector's ClickHouse exporter, whose DDL lives in
`exporter/clickhouseexporter/internal/sqltemplates`. It uses **five** tables —
`otel_metrics_gauge`, `_sum`, `_summary`, `_histogram`, `_exp_histogram` — because the point shapes
genuinely do not share a row. A gauge has no temporality; a summary has neither temporality nor
exemplars; an explicit-bucket histogram carries a bound list an exponential histogram deliberately
omits in favour of a scale and two offsets. One table for all five means most columns are null most
of the time and the reader has to know which are meaningful.

The binding constraint is on our side: `apps/workers/internal/pipeline/derive` writes to `metrics`
today, one row per service, endpoint and minute, and its correctness rests on 000010's insert
deduplication over a byte-identical block. That pipeline could not be changed as part of this work.

## Decision

Five new tables — `metrics_gauge`, `metrics_sum`, `metrics_histogram`, `metrics_exp_histogram`,
`metrics_summary` — following the reference implementation's split, and **`metrics` is left exactly
as it is** as the derived-counter table. Adapted rather than copied:

- Tenancy columns, and `project_id` second in every sort key, per
  [0057](0057-project-id-in-every-telemetry-sort-key.md).
- Attributes as native JSON per [0034](0034-clickhouse-attributes-as-native-json.md), not
  `Map(LowCardinality(String), String)`. The exporter's `cityHash64(Attributes)` sort-key expression
  becomes a named `series_hash` column, materialized from the attributes, so a series is contiguous
  within the hour and a query can group by it without re-serialising JSON.
- `aggregation_temporality` as `LowCardinality(String)` holding DELTA or CUMULATIVE, in the short
  enum form `span_kind` and `status_code` already use, rather than OTLP's raw `Int32`.
- Histogram `sum`, `min` and `max` are `Nullable`, because OTLP marks them optional: a histogram with
  no sum and one summing to zero are different facts.
- Scope reduced to `scope_name` and `scope_version`; scope attributes, schema URLs and dropped-attribute
  counts are dropped as envelope bookkeeping, as `spans` already drops them.
- Codecs per [0065](0065-compression-codecs-chosen-by-measurement.md).

**Exemplars are stored**, not deferred. An exemplar is the only link from a metric point back to a
trace, and the jump from a spike to the request that caused it is the product. Its `FilteredAttributes`
are dropped — the trace it points at carries them.

000010's deduplication carries over: every new table declares
`non_replicated_deduplication_window = 1000`. That is parity, not protection —
[0061](0061-counter-overcount-across-a-store-restart.md) already established that the dedup log does
not survive a ClickHouse restart and that the KeeperMap offset guard is what actually holds.

## Confirmed scope

"Full fidelity" was read as OTLP's data, not its envelope bookkeeping. `scope_name` and
`scope_version` are kept; scope attributes, schema URLs and dropped-attribute counts are not, which
matches what `spans` already stores. Exemplars **are** kept — they are the only link from a metric
back to a trace, which is the product. Confirmed 2026-08-28.

## Consequences

A reader has to know which table a metric lives in. That is the honest cost of the split and the
reason the exporter pays it too: the alternative is one table where most columns are null and the
reader has to know which ones mean anything, which is the same knowledge with none of the type safety.

Nothing about the derive write path changed — same table, same column list, same types, same
insert-block hash. Confirmed live: derive kept writing across the migration, 31,842 to 31,896 rows in
75 seconds, no worker errors.

The cost accepted is that nexovue now has two metric write paths. `metrics` holds our own derived
pre-sampling counters; the five new tables hold ingested OTLP. Those derived counters are delta sums
and belong in `metrics_sum` with `aggregation_temporality = 'DELTA'`, `is_monotonic = true` and a
start timestamp. Moving them is a Go change to `derive`, deliberately not made here, and until it
happens the duplication stands. It is recorded as the follow-up rather than left to be rediscovered.

`Dynamic` and `Variant` were not used anywhere in this design, for the reason in
[0062](0062-log-body-as-rendered-text-plus-typed-map.md): `ch-go` v0.74.0 cannot write them.
