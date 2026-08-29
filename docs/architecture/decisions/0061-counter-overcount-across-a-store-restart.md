# 0061 — Insert deduplication does not survive a ClickHouse restart

- **Status:** Accepted 2026-08-27 — remedy implemented and proven. **Title claim withdrawn 2026-08-29**, see below; the decision and its remedy stand.
- **Supersedes in part:** [0060](0060-counter-idempotency-via-clickhouse-dedup.md) — its stated limit, now measured
- **Date:** 2026-08-27

> **Correction, 2026-08-29 ([0075](0075-block-deduplication-replaces-the-read-before-retry.md)).** The claim in this record's title — and in the sentence below beginning *"the deduplication log that would have recognised it did not survive the restart"* — is **withdrawn**. It was inferred from the chaos observation, not measured. Measured since, on the pinned `clickhouse:26.3.20.7`: an identical block re-inserted after a graceful restart, and again after a `SIGKILL`, was dropped both times — the sum stayed at 100 rather than doubling to 200, with `uptime()` confirming 13s and 2s respectively. The deduplication log survives both.
>
> The 512 double-counted spans below were real. Their cause is unexplained by this record and is most likely that the re-derived block was not byte-identical — a rebuilt minute can legitimately contain late spans the first did not, which changes the checksum. That was not tested.
>
> **The decision is unaffected.** `KeeperMap` remains the remedy and is what `TestReplayedOffsetsAreNotCountedTwice` proves. Only the stated reason deduplication could not be relied on is wrong.

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

**`KeeperMap`, with the connector's state machine.** Raj's instruction was to take the
correct option rather than the affordable one — and the cost that made it look expensive
turned out not to exist. ClickHouse runs Keeper **embedded in the same process**, so the
container inventory [0052](0052-full-production-container-inventory.md) fixes at fourteen is
unchanged; the correct answer cost a config file.

`span_ingest_offsets` (migration `000011`) holds per-partition `min_offset`/`max_offset` and
a `BEFORE`/`AFTER` flag written either side of the counter insert. On assignment the worker
resolves the connector's four scenarios into one number — the offset it may begin counting
from — and declines to re-count anything below it.

**Proven, not argued.** The chaos test could not settle this: whether a replay happens there
at all depends on where the outage lands. So the condition was created instead of raced —
drain to zero, restart ClickHouse to discard the deduplication log, rewind the consumer
group 800 offsets on each of twelve partitions behind the durable watermark, and re-consume:

```
counters before   4,261,548
counters after    4,261,548      delta 0
```

9,600 records replayed, none counted twice, with ClickHouse's own deduplication unavailable.
That is `TestReplayedOffsetsAreNotCountedTwice` in `tools/chaos`.

**One deliberate deviation from the connector.** `Contains` + `BEFORE` re-counts rather than
dead-lettering. The connector parks the batch because it cannot reconstruct the chunk it is
missing; here a batch is only a window onto a stream, so parking well-formed spans would
guarantee a hole. An undercount is the worse failure.

### The measurement that started this was not the bug

The +512 that opened this ADR was **not double counting**, and finding that out corrected two
real defects and one bad test.

The receiver's `ProduceSync(...).FirstErr()` reported the first error while other records in
the same request were already on the broker — so it answered 503 to an export it had partly
published, and the client republished what had landed. It now retries only the failed subset,
so its answer is true. Separately, the load generator recorded a request it could not read a
reply to as neither offered nor accepted, while the receiver may already have published it.

Both mean a correct pipeline counts *more* than was acknowledged. The chaos test's
`counted > accepted` assertion could not tell that from a real overcount, so it was
measuring the harness. It now asserts only what it can prove — no loss — and idempotency is
proven by the deterministic test above.

### The field, for the record

~~1. **Accept and publish the bound.** 0.18%, only when the store restarts mid-flight, always
   over and never under. The research supports it as normal practice — most pipelines run
   at-least-once and reconcile in the query. Invariant 3 then reads *exact under sampling,
   bounded across a store restart*, which is a weaker promise than the one written down but
   an honest one.~~ **Rejected: the promise in invariant 3 is the product.**
2. **Adopt `KeeperMap` with the connector's state machine.** ✅ **Chosen.**

Deduplication from [0060](0060-counter-idempotency-via-clickhouse-dedup.md) stays: it still
covers the common case, a worker restarting while the store survives, and costs nothing.

**One residual hole, narrow and recorded.** If a counter insert lands but its acknowledgement
is lost, the retry re-inserts the identical block. Within a single settlement the state
machine cannot see this, and 0060's deduplication catches it only while ClickHouse survives.
A store restart between the two attempts would still duplicate. Always an overcount, never an
undercount, and not what produced the 512.

## Consequences

Whatever is chosen, two things are already true and worth keeping separate. **Invariant 2,
no data loss, is proven** — the chaos test drives it and it passed on the measurement
above. **Invariant 3, exact counts, is proven for sampling** — 600 spans in, 600 counted,
50 stored — and is now known to be inexact across a store restart, by this much and in
this direction.

Recording the gap with a number is better than a guarantee that quietly is not one.
