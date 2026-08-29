# 0075 — The read-before-retry is removed; it lost late data and its premise was false

- **Status:** Accepted
- **Date:** 2026-08-29
- **Relates to:** [0061](0061-counter-overcount-across-a-store-restart.md) — whose remedy stands; this removes a later addition, not that decision

## Context

`consume.WriteOnce` asked a `landed` callback whether a block was already present
before retrying a counter or edge insert. A production review found it unsound
in two independent ways.

**It cannot answer the question it is asked.** Both implementations queried
*"does any row exist for this series and this minute"*, which is not *"did this
batch land"*. A summing rollup cannot tell those apart, and the difference is
not hypothetical — `NewCounters` documents that a minute is deliberately
reopened by late data:

> A span arriving after that reopens its minute and is flushed as a second row
> for it. The table is summed on read, so two rows for one minute total the same
> as one — late data costs a row, never accuracy.

So a legitimate second write for a reopened minute is indistinguishable from a
retry. On a lost acknowledgement the check saw the *first* batch's row and
skipped the second, discarding the late data — silent loss into a rollup nothing
reconciles.

**Its stated premise is false.** All three comments gave one reason for the
guard: that ClickHouse's deduplication log "does not survive a restart of
ClickHouse — and a restart is exactly what tends to break the acknowledgement in
the first place."

Measured on `clickhouse:26.3.20.7`, which is what we pin. A `SummingMergeTree`
with `non_replicated_deduplication_window = 1000` was given a block; the identical
block was then sent again after each of:

| | sum after re-insert |
|---|---|
| graceful restart (`uptime()` 13s after) | **100** |
| `docker compose kill` — SIGKILL (`uptime()` 2s after) | **100** |

100, not 200, in both. The log survived. A genuinely different block sent
afterwards still landed (107), so the window was not simply refusing everything.
The hard kill is tested because that is the shape
[0061](0061-counter-overcount-across-a-store-restart.md) observed, and a graceful
shutdown alone would not have been evidence about it.

## Decision

Delete `consume.WriteOnce` and both `Applied` implementations. Counter and edge
inserts use plain `consume.Retry`.

122 lines removed, 16 added.

**This does not change 0061's remedy.** That decision's answer was `KeeperMap`
— `span_ingest_offsets`, the per-partition `BEFORE`/`AFTER` state machine that
brackets each settlement and decides whether a replayed range may be counted at
all. It is untouched, still bracketing these writes, and
`TestReplayedOffsetsAreNotCountedTwice` still covers it. `WriteOnce` was a later,
additional guard layered on top of it, and it is only that layer being removed.

What makes the plain retry safe is the store's own block deduplication, which is
exact where the read-back was approximate: it compares a checksum of the formed
part, so it recognises a retry and nothing else. Both tables have carried a
window since [0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
`000016`, and `derive` and `serviceedges` both sort their drained rows so a
re-derived block is byte-identical.

## Alternatives considered

**Keep `Applied` and consult it only when it can be right** — track which minutes
this process has drained, use the read-back for a minute's first write and plain
retry once reopened. Rejected: it preserves a guard whose premise measured false,
and adds a second piece of state to keep correct in order to do so.

**A drained-minute watermark**, retiming late data so a minute is never reopened
and the check's assumption becomes true. Rejected once `NewCounters` was read
properly: reopening is intended, and this would trade silent loss for silent
inaccuracy.

**Leave it.** Rejected because the loss is silent and permanent, and because the
code carried a false claim the next reader would have trusted.

## Consequences

Late data reaching a reopened minute is now written instead of dropped.

**The removed guard was never preventing the overcount.** 0061's 512 double-counted
spans reproduce exactly, across three chaos runs, on both sides of this change:

| run | acknowledged | counted | delta |
|---|---|---|---|
| 0061, guard present | 282,490 | 283,002 | +512 |
| M3 chaos, guard present | 284,896 | 285,408 | +512 |
| this change, **guard removed** | 279,009 | 279,521 | +512 |

Identical, and identical to `tools/loadgen`'s `batch_size` of 512 — one batch
counted twice, not a scattering. Removing `WriteOnce` did not change it by a
single span, which is the clearest evidence available that the read-back was
paying its cost without buying the protection it was added for.

0061 attributed that overcount to the deduplication log being lost. That cannot
be right, since the log survives a SIGKILL. The likelier cause is that the
re-derived block is not byte-identical — after a crash the rebuilt minute can
contain late spans the first did not, which changes the checksum, so
deduplication correctly declines to treat it as a repeat. **This was not
isolated**, only observed three times at the same magnitude. `KeeperMap` is what
bounds it, as it already did.

**What this costs.** Protection is bounded by the window: a retry separated from
its original by more than 1000 intervening blocks on that table would no longer
be recognised. Inside a `Retry` loop that gap is seconds. `metrics_sum` has two
writers — derived counters and customer OTLP sums — sharing one window, which at
the metric-ingest rate measured during a chaos run (~30 blocks a minute) turns
over in roughly 33 minutes.

**Not proven end to end.** The deduplication and restart behaviour are measured
directly, as above. The reopened-minute loss is reasoned from the code and was
not reproduced against a running pipeline; no failing test was written before the
fix.
