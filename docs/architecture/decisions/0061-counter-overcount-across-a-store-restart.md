# 0061 — Insert deduplication does not survive a ClickHouse restart

- **Status:** Proposed — the measurement is settled, the remedy is not
- **Supersedes in part:** [0060](0060-counter-idempotency-via-clickhouse-dedup.md) — its stated limit, now measured
- **Date:** 2026-08-27

## Context

[0060](0060-counter-idempotency-via-clickhouse-dedup.md) closed the counter-replay
hole with ClickHouse's own insert deduplication, and recorded two limits. One was that
the deduplication log's persistence across a **ClickHouse** restart is undocumented, and
that P1-27's chaos test — which kills the store mid-load — was the right place to find
out. It also said that if deduplication did not hold there, the ADR should be superseded
by evidence rather than by argument.

The evidence is in. Killing Kafka and then ClickHouse under sustained load:

```
receiver acknowledged   282,490 spans
counters recorded       283,002 spans   (+512, 0.18%)
consumer lag            0 — fully drained, nothing lost
```

**No data was lost.** Every acknowledged span was counted; 512 were counted twice. The
replay path is exactly the one 0060 described: counters for a minute are written, the
store dies before the offsets commit, and the restarted read re-derives that minute. The
block is byte-identical — `derive.Drain` sorts for precisely this reason — but the
deduplication log that would have recognised it did not survive the restart that caused
the replay.

## Decision

**Not yet taken.** The measurement is recorded so the choice is made against a number
rather than a fear. Three candidates, none free:

1. **A durable watermark carried inside the counter block.** A per-partition high-water
   offset written as a row in the same insert as the counters, so it lands atomically with
   them — ClickHouse has no cross-table transaction, but it does have one block. On
   partition assignment the worker reads it and declines to re-count at or below it. Sound
   and survives anything; costs internal rows in a customer-facing table and a read on
   every assignment.
2. **`ReplacingMergeTree` on the series identity.** Structural rather than a transient log,
   so it survives restarts. But it collapses on merge, not on read, so a `sum()` over
   unmerged parts still double-counts unless every query pays for `FINAL`.
3. **Accept and publish it.** 0.18%, only when the store restarts mid-flight, always in the
   direction of over-counting. Cheapest, and defensible for a metric read as a rate — but
   invariant 3 says the numbers are exact, and "exact except during an outage" is a
   different promise than the one written down.

## Consequences

Whatever is chosen, two things are already true and worth keeping separate. **Invariant 2,
no data loss, is proven** — the chaos test drives it and it passed on the measurement
above. **Invariant 3, exact counts, is proven for sampling** — 600 spans in, 600 counted,
50 stored — and is now known to be inexact across a store restart, by this much and in
this direction.

Recording the gap with a number is better than a guarantee that quietly is not one.
