# 0090 — A partition that leaves takes its buffered spans with it

- **Status:** Accepted
- **Date:** 2026-09-01
- **Corrects in part:** [0079](0079-span-rows-are-idempotent-across-a-restart.md), whose Consequences
  bound the residual duplication to "a rare fast-path span" inside the last sampling window. That
  bound holds for a restart. It does not hold for a rebalance, where **every** span in the losing
  worker's buffer is written twice — the whole last sampling window on every moving partition.

## Context

[0079](0079-span-rows-are-idempotent-across-a-restart.md) closed span duplication across a restart
and said plainly what it had not measured: *"behaviour under a real consumer-group rebalance across
two workers is untested, because only one worker runs here."* That is the hole this ADR fills, and
the answer is that a rebalance was worse than a crash.

**Nothing has to die.** A graceful join is enough. `consume` registered
`kgo.OnPartitionsAssigned` and nothing else — no `OnPartitionsRevoked`, no `OnPartitionsLost` — and
`sample/buffer.go` had no notion of a partition at all. So the losing worker was never told the
partition had gone. It went on expiring its sampling buffer and writing those spans, while the
gaining worker resumed from the last committed offset and wrote them too.

Measured here on 2026-09-01, on an isolated rig — its own topic (`spans-probe`, 12 partitions), its
own consumer group, two workers on the same binary, and `tools/rebalanceprobe` producing
identity-exact envelopes straight to the broker. One resident worker; a second joined and left
gracefully four times across a six-minute run; **nothing was killed**.

| | accepted | distinct stored | rows | duplicated | max copies | loss |
|---|---|---|---|---|---|---|
| before | 21,597 | 21,597 | 24,351 | **2,754 (12.75%)** | 2 | **0** |

The shape is the signature, not the rate. The six partitions that moved duplicated **459 spans
each**; the six that never moved duplicated **zero**. No span had three copies. Nothing was lost.

**One measurement in the other direction is worth recording, because it is why this nearly went
unreproduced.** At the shipped `WORKERS_SAMPLING_WINDOW=10s`, the same arm shape run against the
shared `spans-raw` topic and the dev stack's own worker fleet produced **0 duplicates in 17,997
spans**. The reason is a coincidence of two timers: franz-go's
cooperative two-phase rebalance took **~10s** from the joining process starting to its first fetch
(`08:13:04.59` started, `08:13:14.86` first batch), which is exactly long enough for the losing
worker to expire its 10s buffer, insert it and record `written_through` before the gaining worker
ever read the watermark. The gaining worker's log shows the watermark then doing its job —
`batch_from=24 store_from=100` on partition 7, skipping 76 offsets it correctly believed stored. The
race is a photo finish at the default, and the loser can win it. The measurement above was taken at
`WORKERS_SAMPLING_WINDOW=60s`, which decouples the two timers and makes the outcome certain rather
than lucky. **The exposure is not created by that setting; only its size is** — the losing worker
still writes what is in its buffer either way, and whether that lands before or after the gaining
worker's watermark read is a race, not a design. **Inference, not measurement:** a slower store or a
heavier load should make the 10s case lose that race too, which would explain the 7.8% recorded when
the defect was found. That stack's volumes have since been destroyed, so it cannot be checked.

Logs and metrics are immune structurally and were not re-measured: `logwriting` and `metricwriting`
insert inside `Absorb`, so nothing of theirs survives a handover in memory.

## Decision

**Drop on revoke.** When the group takes partitions away, the worker discards everything it was
holding for them and claims nothing. The records are uncommitted, so they are still on the broker,
and the worker that takes the partition over reads them again.

Three pieces, and all three have to move together.

**The buffer learns where a span came from.** `sample.Facts` gains a `Partition`, and
`openTrace.rows` becomes `[]buffered[T]` — one held span with the Kafka partition its record arrived
on. `Buffer.Disown(partitions)` filters those rows and deletes any trace left empty. The topic is
implicit: one buffer serves one pipeline, and a pipeline consumes one topic.

Provenance is kept **per row, not per trace**. A trace is normally one partition's whole world — the
receiver keys spans by trace id (ADR 0009) — but keying ownership on the trace would mean a trace
that did arrive on two partitions either loses spans belonging to a partition still owned (a hole)
or keeps spans belonging to one that left (the duplicate again).

**The loop releases its own state and takes a lock while it works.** `consume.Loop` prunes two
things in the callback: records polled but never absorbed, and held offsets that have not been
committed. A batch left with no offsets goes with them, so an emptied batch at the front of the
queue stops reporting a commit lag it is not causing.

The lock is not decoration. `BlockRebalanceOnPoll` *almost* serialises the callbacks against
processing on its own — franz-go's own words are that the callbacks "cannot be called if you have
polled and are processing records" — but it releases that block for an **empty** fetch
(`consumer.go`: `defer func() { if len(fetches) == 0 { c.unaddPoller() } }()`), and the loop still
absorbs, settles and commits after an empty poll. Without a lock of our own a revoke could run while
`Settle` was releasing traces, which is the one moment it exists to stop. The lock is held across
absorb/settle/commit and released before the poll, because a poll blocks on any callback in flight
and holding it across one would deadlock.

**The in-process replay guard forgets the same partitions.** `dedupe.Offsets.Forget` runs in the
same critical section, and it runs for the opposite reason to the rest of that type: left in place,
a partition that came back would have its uncommitted records recognised as replays and skipped —
and those spans were dropped, not stored. That is a hole.

**Revoked and lost differ, and it is not the release.**

- **Revoked** — cooperative, session still live. Drop, log at INFO, carry on serving the partitions
  we kept. The durable cache stays correct: nothing else has touched those partitions yet, and the
  assignment that brings one back already invalidates it (ADR 0061).
- **Lost** — a fatal group error, where franz-go warns "it is unlikely that commits will succeed".
  The same drop, plus `Guard.Invalidate`: these partitions may have been another worker's for some
  time, and its settlements are not in this process's copy. Logged at WARN.

That the release is *identical* in both is an argument for this shape rather than an accident.
Dropping claims nothing, so it is exactly as safe against a broker that has stopped believing in us
as against one that has not.

**What bounds the callback.** It runs on the client's goroutine with the whole group's rebalance
waiting on it, so it does memory work only — no insert, no commit, no watermark write. Its own cost
is O(open traces + buffered spans + held offsets), bounded by `WORKERS_SAMPLING_MAX_TRACES` (50,000)
and `WORKERS_SAMPLING_MAX_SPANS` (500,000). A test (`TestAHandoverMakesNoCallOutOfTheProcess`) runs
it against a store that panics on any use. Beyond its own work it waits for at most one turn of the
loop, which is the exposure `BlockRebalanceOnPoll` already accepts for every non-empty poll.

**Which direction it fails.** Toward duplication, in every path. Nothing is ever recorded as
finished: no offset is committed, no watermark is advanced, no row is claimed stored. The worst
outcome of a handover is that the new owner writes a row this worker already wrote — recoverable —
and there is no ordering in which a span nothing wrote is skipped. "No data loss between receiver
and storage" is a phase-1 invariant; duplication is not.

## Alternatives considered

**Drain on revoke** — flush the revoked partitions' buffered spans, insert them, and advance their
watermark before the handover completes, so the gaining worker skips them. **Rejected**, for three
reasons that compound.

1. It puts a ClickHouse insert and a KeeperMap write inside a callback that blocks the whole group's
   rebalance. Those calls go through `Loop.Retry`, whose budget is **two minutes**, against
   franz-go's default **sixty-second** rebalance timeout. Blowing it costs the session, which moves
   every *other* partition this worker owns — a handover fix that cascades into a bigger handover.
2. It needs a second, different behaviour for `OnPartitionsLost`, where a write may be refused or
   duplicated anyway — and that behaviour is drop. So drain is drop plus a fast path, not instead of
   it.
3. Drop reproduces, deliberately, the case already measured at zero: a killed worker never writes
   its buffer, and the restart arm duplicated nothing. Drain invents a path nothing has run.

What drain would have bought is real and is the cost accepted below: no re-processing, and no second
sampling verdict on a moved trace.

**Marking the partitions disowned and filtering at release time**, leaving the actual drop to the
loop's own goroutine. **Rejected**: franz-go guarantees only that "once the callback has completed
any subsequent polls will not return records for the revoked partitions", so the new owner may start
the moment the callback returns. A drop that happens some microseconds later shrinks the window
instead of closing it, which is a patch, not a fix.

**Giving `sample.Buffer` its own mutex** so the callback could work on it directly without a loop
lock. **Rejected**: the callback has to release the loop's offset queue and the record batch in the
*same* instant as the buffer, or the three disagree about what this worker still owns. One lock
around the turn is the honest boundary; three fine-grained ones are the same lock with more ways to
get it wrong.

**Committing the revoked partitions' stored offsets in the callback** before dropping. **Rejected**:
the band between `written_through` and the committed offset is exactly what the gaining worker's
watermark read already skips (`store_from` above `batch_from`), so the commit buys nothing and costs
a broker round trip inside the rebalance.

## Consequences

**What it costs.** Per buffered span, one `int32` of provenance plus whatever alignment padding the
row's own struct forces — at most 8 bytes each, so under 4 MB at the 500,000-span cap, against a
870 MiB `GOMEMLIMIT`. One uncontended mutex acquire and release per turn of the loop. On a
handover, up to one sampling window of work on the moving partitions is thrown away and done again
by their new owner, so those spans reach the store later; and their traces are judged by the sampler
a second time, from scratch, so a moved trace can be sampled differently than it would have been.
Both are the price of not writing it twice.

**Every pipeline gets the loop's half, only trace assembly needs the stages' half.** `logwriting`,
`metricwriting` and the three `usage` pipelines now also prune their unabsorbed records and
uncommitted offsets on a revoke. They buffer nothing across `Absorb`, so they need no release hook —
their durable watermarks already recognise the replay.

**Two residuals remain, and they share one root.** `written_through` is a prefix over poll order,
while the rows released at one instant can come from batches beyond that prefix: a span written
early by the already-decided fast path at `Add`, and a span added by a later batch to a trace an
earlier batch opened. Both are inserted before their own batch enters the prefix, so a handover
drops their offsets and the new owner writes them again. This is the same class 0079 named and did
not close; **what this ADR corrects is the claim that it was the *only* exposure a handover had.**
Neither is exercised by `rebalanceprobe` — every probe trace is one envelope, so it is one record in
one batch, and no span of it ever arrives late — so **the residual is reasoned, not measured**.

**The counter half of the same root is not fixed, and it is not made worse.** `derive.Counters` and
`serviceedges` also have no notion of a partition, and after a revoke the losing worker still holds
that partition's contributions in its minute bucket and still writes them, while the gaining worker
re-counts the same records. Pruning the offset queue does stop this worker from *claiming* those
offsets counted, so the error stays a duplicate rather than becoming a hole — but `metrics_sum` and
`service_edges_1m` can still be over-summed by a handover. Closing it is not a small change: those
accumulators aggregate *across* partitions into one series, so "drop the revoked partition's
contribution" needs per-partition sub-accumulators through both stages. **Raj's call to scope; not
attempted here.**

**Measured after the change**, same rig, same arm, same binary in both workers:

| | accepted | distinct stored | rows | duplicated | max copies | loss |
|---|---|---|---|---|---|---|
| before | 21,597 | 21,597 | 24,351 | 2,754 (**12.75%**) | 2 | 0 |
| after | 21,600 | 21,600 | 21,600 | **0** | 1 | **0** |

Per partition after the change: **1,800 rows and 1,800 distinct spans on every one of the twelve**,
moved and unmoved alike. Before, the six that moved held 2,259 rows against 1,800 distinct.

`spans_disowned` is a new heartbeat counter and the direct evidence: it steps at each handover and is
flat otherwise. The resident worker's own log names the moment, four times in the run —
`released buffered spans with their partitions … partitions="[6 11 9 10 8 7]" spans=1404 traces=468`,
alongside `unpolled_records=8 uncommitted_records=468` from the loop.

**Both arms ran with the OpenTelemetry Demo producing into the same store**, so the absolute row
counts above are not from a quiet stack. The probe is immune to it — its identities are unique and
its topic is its own — but nobody should read these as clean-room numbers. The rig was deliberately
isolated: `spans-probe`, consumer group `nexovue-probe`, two workers on the same binary, which is
also what keeps the arms from being disturbed by whatever else is running against the dev stack.

**Not verified.** Four things.

- **`OnPartitionsLost` has never fired here.** It is unit-tested and reasoned; no fatal group error
  was induced. Its release path is the one the revoke arm exercises, but the WARN log line and the
  cache invalidation are not something anything has watched happen live.
- **The lock has never been contended.** The empty-poll window it exists to close was reasoned from
  franz-go's source, not observed racing. No race detector run covers the callback goroutine against
  the loop, because the loop needs a broker.
- **The two residuals above** are reasoned from the code, not measured.
- **The 7.8% recorded when this defect was found is not reproducible any more.** The dev stack's
  volumes were destroyed at 08:04 on 2026-09-01, taking 12.5M `spans` rows and every committed
  offset with them. The before-number in this ADR is a fresh measurement on the rebuilt stack at a
  60s sampling window, not that one. Two windows on two different stacks do not make a comparison.
