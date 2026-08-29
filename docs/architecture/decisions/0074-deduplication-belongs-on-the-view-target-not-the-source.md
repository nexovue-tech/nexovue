# 0074 — Insert deduplication belongs on a materialized view's target, not only on its source

- **Status:** Accepted
- **Date:** 2026-08-29

## Context

[0060](0060-counter-idempotency-via-clickhouse-dedup.md) gave the metric tables
`non_replicated_deduplication_window` so a retried insert could not be counted
twice, and `000016` gave `service_edges_1m` the same guard. Two write paths were
left without it, and a production review found both.

`insert spans` and `insert logs` retry through `consume.Retry`, which has no
landed-check: a lost acknowledgement sends the identical block again. Neither
table had a window, so the block was accepted twice.

For logs that is duplicate rows. For spans it is worse, because two materialized
views read the table — `endpoint_stats_1m_mv` into an AggregatingMergeTree, and
`span_trace_lookup_mv` into a plain MergeTree. Measured on the live schema
before any change: the same block inserted twice produced **8 requests in
`endpoint_stats_1m` where 4 was correct**.

`000016` had claimed this could not happen:

> `endpoint_stats_1m` is deliberately left without it. A materialized view fills
> it from `spans`, so it can only double-count if `spans` does, and the
> KeeperMap offset guard upstream is what stops that.

Both halves are wrong. `spans` had no guard of any kind, and the KeeperMap guard
covers the counter and edge writes — which use `consume.WriteOnce` — not the
span insert, which does not.

The correction that mattered most was found by testing rather than reasoning. A
window on the **source** table does not protect a view. Holding everything else
equal, on `clickhouse:26.3.20.7`:

| source window | target window | source rows | rollup |
|---|---|---|---|
| 0 | 0 | 2 | 200 |
| 1000 | 0 | 1 | **200** |
| 1000 | 1000 | 1 | **100** |

Deduplication drops the duplicate part on the source, but the view has already
processed the block. Only a window on the view's own target table stops the
rollup doubling. Had this migration been written from the reasoning in `000016`
instead of measured, it would have looked correct and fixed nothing.

## Decision

Give a window to every table on a retried insert path **and to every
materialized-view target reading one**: `spans`, `logs`, `endpoint_stats_1m`,
`span_trace_lookup` (`000019`–`000022`).

The span insert also sets
`deduplicate_blocks_in_dependent_materialized_views = 1` explicitly. It already
defaults to `1` on this server — verified in `system.settings`, not assumed — so
this pins a default we depend on rather than enabling anything. A profile that
turned it off would otherwise start double-counting retries silently.

This relies on the block checksum being deterministic. It is: the checksum is
taken from the formed part with defaults expanded, every default on these tables
is a constant or a copy of another column (checked in `system.columns`), and
`derive` and `serviceedges` already sort their rows for exactly this reason.

## Alternatives considered

**`consume.WriteOnce` on spans and logs, as counters and edges use.** Rejected.
It needs a "did this land" query against the store on every retry, on the
hottest write path we have, and log rows have no natural identity to ask about.
Deduplication answers the same question exactly, in the store, for the cost of
1000 checksums.

**Leave `endpoint_stats_1m` alone and guard only `spans`.** This is what the
reasoning in `000016` implies. The table above is the measurement that rejects
it: source window alone still gave a doubled rollup.

**Rely on the KeeperMap ingest state.** It already brackets the counter and edge
writes and is what covers a store restart
([0061](0061-counter-overcount-across-a-store-restart.md)). It does not bracket
the span insert, and extending it there would put two round trips on the hot
path to solve what a checksum already solves.

## Consequences

A retried span or log insert is now dropped by the store, and the rollups it
feeds are no longer doubled by it.

**What this costs.** The window holds the last 1000 block checksums per table.
A retry separated from its original by more than 1000 intervening blocks is no
longer recognised and will double-count. At the metric-ingest rate measured
during a chaos run — roughly one block per poll, about 30 a minute — that is
about 33 minutes, against a retry latency of seconds. Adequate now; it is a
ceiling, not a guarantee, and a much higher insert rate would shorten it.

**A deleted row does not clear its checksum.** Observed while testing: after
`ALTER TABLE spans DELETE` removed the probe rows, re-inserting the same block
was still dropped as a duplicate. Anyone reloading identical data after a delete
must change the block or wait out the window.

**`000016`'s stated reasoning is wrong and stays in the tree** as an applied
migration. `000021` quotes and corrects it rather than editing history.

**Not proven end to end through the worker.** The mechanism is proven in
controlled tables of the same engine shapes, and the migrations are proven
applied and reversible (up → down → up). An end-to-end probe through the real
`spans` table was attempted and was inconclusive — the test kept measuring its
own harness rather than the fix — so it is recorded here as not done.
