# 0065 — Compression codecs chosen per column, by measurement

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

No column in any table declared a `CODEC`, so every column sat on the server default, LZ4. Measured
on 12M live dev spans, four columns were 44% of the whole table:

| column | stored | ratio |
|---|---|---|
| `span_id` | 180.59 MiB | 1.0 |
| `trace_id` | 84.53 MiB | 4.3 |
| `timestamp` | 63.89 MiB | 1.4 |
| `parent_span_id` | 57.98 MiB | 3.1 |

`span_id` at ratio 1.0 is the tell. The ids are stored as hex, so eight bytes of entropy occupy
sixteen stored bytes, and LZ4 finds nothing to remove because every value is unique.

The obvious move was to copy the OpenTelemetry ClickHouse exporter, which writes `CODEC(ZSTD(1))` on
every column and `CODEC(Delta(8), ZSTD(1))` on timestamps. We measured instead, and the blanket form
turned out to be wrong for us.

## Decision

Codecs are chosen per column against measured data, not from a rule table.

| column kind | codec | measured |
|---|---|---|
| hex ids (`span_id`, `trace_id`, `parent_span_id`) | `ZSTD(1)` | all three roughly halve |
| `timestamp` `DateTime64(9)` | `Delta(8), ZSTD(1)` | 16.4 MiB, against 32.6 for DoubleDelta and 63.9 for the default |
| `duration_ms` `Float64` | `Gorilla, ZSTD(1)` | 1.66 MiB against 2.40 for plain ZSTD |
| `http_status` `UInt16` | `T64, ZSTD(1)` | halves |
| `LowCardinality(String)` | `ZSTD(1)` | 50–70% off the dictionary |
| `events`, `resource_attrs` | **default (LZ4)** | ZSTD(1) measured *worse* |

Two of those contradict the folklore and both were measured, not reasoned.

`DoubleDelta` is the codec usually named for timestamps, and it was twice the size of `Delta` here,
because our sort key orders by hour then trace id — the column is not the constant-stride sequence
DoubleDelta is built for.

More importantly, **ZSTD(1) is not universally better than LZ4**. On `events` — today a run of
identical two-byte values — LZ4 stored 15.65 KiB and ZSTD(1) stored 54.77 KiB, three and a half times
worse. ZSTD(3) stored 5.29 KiB. Level 1's fast strategy does not find the long repeats that LZ4's
format encodes almost for free. A blanket `CODEC(ZSTD(1))` would have made those columns worse.

`events` and `resource_attrs` are therefore left on the default. Both are near-constant across rows
*today*, which is a property of dev data rather than of the column, and a codec chosen against
degenerate data is a decision that has to be unmade. They are re-measured when M3 fills them.

## Consequences

The `spans` table went from **405.89 MiB to 187.67 MiB** over the same ~12M rows once the parts
merged — **35.5 bytes per span down to 16.4**, a 54% reduction. `span_trace_lookup` went from
113.01 MiB to 71.83 MiB on the same rows.

The measured 16.4 bytes/span is far under the ~128 bytes/span that
[0056](0056-per-service-pricing.md)'s pricing rests on. That number is not disproved — dev spans
carry thin attributes, and attributes are what grows — but the storage side of the model now has
measured headroom rather than an estimate.

Unlike a sort key or a partition key, this door is not one-way: `ALTER TABLE ... MODIFY COLUMN ...
CODEC` is metadata, and merges adopt it. Nothing is rewritten. The cost is that existing parts keep
their old compression until they next merge, so the saving appears gradually rather than at once.

The remaining halving on the ids is not a codec problem. Storing them as `FixedString(8)` /
`FixedString(16)` binary instead of hex would remove the other half outright, and no codec can
recover what the hex encoding spends. That is a receiver and worker change, not a migration, and is
recorded here as the next step rather than taken.
