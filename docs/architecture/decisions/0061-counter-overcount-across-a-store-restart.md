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

## What the research settled

Two of the four candidates are answered by ClickHouse's own
[kafka-connect design](https://github.com/ClickHouse/clickhouse-kafka-connect/blob/main/docs/DESIGN.md),
which solves precisely this problem and is the reference implementation.

**Storing offsets in ClickHouse is rejected by its own authors** — *"ClickHouse is not
strongly consistent by default… With careful configuration you can ensure linearizable
inserts… however this adds significant insertion latency… a redundant component and
unnecessary overhead."* Our narrower form — the watermark inside the counter block, atomic
because it is one block — holds on a single node and stops holding the moment ClickHouse is
replicated, which is the wrong property for a fix to a durability bug.

**`ReplacingMergeTree` is worse than it looks.** It collapses duplicates on merge, on
ClickHouse's own schedule, and *does not deduplicate at query time*. A `sum()` over unmerged
parts still double-counts, so every counter query would have to pay for `FINAL`.

**What the connector actually does: `KeeperMap`** — a table engine over ClickHouse Keeper,
holding a per-partition state machine of `minOffset`/`maxOffset` with a flag written *before*
the insert and another *after* the acknowledgement, so a replay can tell a completed batch
from an interrupted one. Keeper is chosen because it is strongly consistent and *"ideal for
persisting small data quantities."*

The engine is present in our build. **A Keeper is not.** Adopting this means adding one to
the stack, against an inventory [0052](0052-full-production-container-inventory.md) fixed at
fourteen — though Keeper is also what replicated ClickHouse requires, so it is a cost that
arrives anyway the day storage needs high availability.

## Decision

**Not yet taken.** With the field narrowed to two, the choice is:

1. **Accept and publish the bound.** 0.18%, only when the store restarts mid-flight, always
   over and never under. The research supports it as normal practice — most pipelines run
   at-least-once and reconcile in the query. Invariant 3 then reads *exact under sampling,
   bounded across a store restart*, which is a weaker promise than the one written down but
   an honest one.
2. **Adopt `KeeperMap` with the connector's state machine.** Correct, vendor-proven, and
   survives anything. Costs a Keeper.

The recommendation is **(1) now, (2) when ClickHouse becomes replicated** — at which point
Keeper is already in the stack and KeeperMap is nearly free. Deduplication from
[0060](0060-counter-idempotency-via-clickhouse-dedup.md) stays either way: it still covers
the common case, a worker restarting while the store stays up.

## Consequences

Whatever is chosen, two things are already true and worth keeping separate. **Invariant 2,
no data loss, is proven** — the chaos test drives it and it passed on the measurement
above. **Invariant 3, exact counts, is proven for sampling** — 600 spans in, 600 counted,
50 stored — and is now known to be inexact across a store restart, by this much and in
this direction.

Recording the gap with a number is better than a guarantee that quietly is not one.
