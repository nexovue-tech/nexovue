# 0091 — Log and metric rows are idempotent across a restart, on one watermark shared by both signals

- **Status:** Accepted
- **Date:** 2026-09-01
- **Extends:** [0079](0079-span-rows-are-idempotent-across-a-restart.md) — the same root cause, one
  pipeline shape later. Everything 0079 argues about at-least-once delivery, about a late commit,
  and about why a watermark is written after an acknowledgement holds here unchanged and is not
  repeated.
- **Records:** migration `000029`, which shipped in `aa6d507` with a thirty-line header comment and
  no decision record. Nothing below changes behaviour; this promotes what was decided then into the
  place decisions are kept.

## Context

[0079](0079-span-rows-are-idempotent-across-a-restart.md) made span rows idempotent across an abrupt
restart and closed by naming the sibling defect: *"Logs and metrics share the root and are sequenced
separately."* They were fixed a day later, with `ingest_written_offsets` (`000029`) and
`ingeststate.WriteGuard`. The reasoning went into the migration's header comment and nowhere else.

**This record exists because that gap was mistaken for a filled one.** ADR
[0085](0085-a-batch-is-bounded-in-rows-and-bytes-not-messages.md) was asserted to cover
`ingest_written_offsets`. It does not — 0085 is about batch bounds, *"a batch is bounded in rows and
bytes, not messages"*, and touches idempotency only to note that a batch which OOMs replays into the
same OOM. The claim was checked rather than believed, and the check found the hole.

**The same root, a much narrower window.** `logwriting` and `metricwriting` have no sampling buffer
and no minute bucket to wait on: they insert inside `Absorb`, so a batch is exposed from the moment
ClickHouse acknowledges its insert until Kafka commits its offsets — one turn of the loop, not the
p50 43s / p90 72s that 0079 measured for spans. A batch's offsets therefore map straight onto the
rows it produced, which is what makes the guard here so much smaller than the span one.

Narrower is not closed. `logs` is a plain `MergeTree` (`000002`) with a block-deduplication window
(`000020`) that a replay's re-batching defeats for exactly the reason 0079 gives, and every metric
table is the same shape. An abrupt kill lands in the window often enough to matter, and a crash loop
lands in it repeatedly.

## Decision

**One durable per-partition `written_through` watermark for both signals, in
`ingest_written_offsets`, keyed `{topic}:{partition}` — the highest offset whose rows ClickHouse has
acknowledged.** On replay a record at or below it is skipped whole rather than re-flattened;
`WriteGuard.Storing` is the test and `messages_already_stored` is the count both pipelines log.

**The ordering is the substance, and it is the same ordering 0079 chose:**

```
Absorb → insert rows → [ClickHouse acknowledges]
  → Advance: write written_through
  → Kafka commit (later, in the loop)
```

Never before the acknowledgement. A watermark claimed early covers rows that are still only in
memory, and the replay after a crash would then skip rows nothing ever wrote. **A duplicate row can
be collapsed later; a missing row cannot be recovered** — and *no data loss between receiver and
storage* is a phase-1 invariant ([phase-1 plan](../../delivery/phase-1-plan.md)) where duplication is
not. Written after the acknowledgement and before the commit, the failure modes are: lose the
watermark write and the next replay duplicates, which is the behaviour that existed before this
change and is recoverable; there is no ordering in which a row goes missing. `WriteGuard.Storing`
answers *true* for a partition it knows nothing about for the same reason.

**No `BEFORE`/`AFTER` bracket, unlike the counters.** The asymmetry with
[0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
[0061](0061-counter-overcount-across-a-store-restart.md) reads as an inconsistency until it is
written down, so: a counter re-applied is a *wrong number*, and a lost acknowledgement leaves you
unable to tell a finished apply from an interrupted one — hence the bracket and hence `Ambiguous`.
This is a *plain fact* recorded only after the store acknowledged the insert. There is no in-flight
state to recover and nothing for a second flag to say. Here the argument is even simpler than it was
for spans: there is no buffer between a record and its rows and no scenario to classify, so
`WriteGuard` holds one watermark, and `Guard`'s four-scenario state machine has no counterpart.

**One table for both signals, not one each.** The key carries the topic and the two pipelines consume
different topics — `logs-raw` and `metrics-raw` — so their rows cannot collide, and each reads and
writes only its own topic's keys, so neither can move the other's watermark or block it. The
pipelines' failure modes stay separate because the key already separates them; what they share is a
table name and a `KeeperMap` path, not any state. Two tables of identical shape would be the
duplication rule 11 exists to stop, and there is no failure they would separate that this key does
not.

**`span_written_offsets` was not merged into it**, though it is the same two-column shape a third
time. That table is live and holds the span watermarks right now, and a `KeeperMap` table's rows go
with the table: merging means dropping and recreating it, and the window between is a hole in span
idempotency — the exact thing `000026` was written to prevent. The shape is shared in code instead:
`ingeststate.watermarks` is one implementation pointed at a table name, so three tables of this shape
are one piece of access code.

## Alternatives considered

**A column on an existing `KeeperMap` table, so one row and one write carries everything.** Not
available, and worth recording verbatim so the next person does not spend an afternoon rediscovering
it. Measured on the pinned `clickhouse:26.3.20.7`:

```
Alter of type 'ADD_COLUMN' is not supported by storage KeeperMap
```

The obvious fallback — recreate the table with the extra column — cannot be one migration either,
because the runner's DSN pins `x-multi-statement=false` (`clickhouse_migrate_dsn` in the
`justfile`), so a drop and a create cannot share a file; and recreating discards the rows the live
table is holding. This is a fact about the storage engine and the runner, not a preference.

**Leaning on block deduplication** (`000020` for logs, and the metric tables' equivalents).
Rejected for the reason 0079 records: after a restart the records re-batch, so the block checksum
never matches the one that landed. It covers a retry inside one process and cannot cover a restart.

**Deduplicating at the table**, with `ReplacingMergeTree` or `LIMIT 1 BY` on read. Rejected on the
same grounds as in 0079: it changes query semantics for every reader, costs every query, and treats
a write bug as a read concern.

## Consequences

**What it costs.** One extra small `KeeperMap` insert per chunk when the watermark moved, and one
comparison per record. `beyondCache` narrows the write to partitions that actually advanced, so a
turn of the loop that stored nothing new writes nothing. The read is cached and re-read only when
`Invalidate` marks it stale from the assignment callback.

**Measured, and the two records of it do not agree.** The commit that shipped this
(`aa6d507`) records: *over **60 SIGKILL restarts under load**, **99,330 duplicate log rows before,
zero after**, and **no data lost — 3,854,420 accepted against 3,854,450 distinct stored***. Migration
`000029`'s own header, and the comment on
`TestRowsWrittenBeforeARestartAreNotWrittenAgain`, describe the before-state of the same run
differently: **49,580 of 905,020 distinct log rows had two copies, 5.5%**. Those are not the same
arithmetic over the same population — 99,330 is close to twice 49,580 but not equal to it, and the
denominators differ by a factor of four — and the two could not be reconciled by re-measuring,
because the dev databases were destroyed before this record was written. Both are recorded as
written rather than averaged into a number nobody took. The `3,854,450` distinct stored also exceeds
the `3,854,420` accepted by thirty rows, which is the direction that shows no loss and is otherwise
unexplained; it is not claimed as anything.

**Metric duplication was never reproduced, and that half is not upgraded.** No before/after delta
exists for metrics. Their idempotency rests on the skip counter (`messages_already_stored` in
`metricwriting`) and on construction: the same guard, the same table, the same ordering, and a
pipeline that inserts inside `Absorb` exactly as `logwriting` does. That is weaker evidence than the
log delta and is deliberately left weaker here.

**A partition that *moves* between two live workers is a separate defect, and logs are structurally
immune to it.** This was not known when `000029` was written. `consume.go` registers
`kgo.OnPartitionsAssigned` and nothing else, so a worker losing a partition is never told; for spans
that means the loser expires its sampling buffer and writes those rows while the gainer resumes from
the last committed offset and writes them too — no crash required. That is being fixed under **ADR
0090**, in progress as this is written, and its measurements and remedy belong there, not here. It
does not reach these two pipelines: `logwriting` and `metricwriting` hold no buffer across a poll,
so a worker that loses a partition has nothing left to write for it. The `Invalidate` on assignment
covers the other side — a partition arriving from another worker whose writes this process never
saw.

**This table inherits [0068](0068-keepermap-is-excluded-from-restore.md).** It is `KeeperMap`, so it
is excluded from a restore for the reasons that ADR records; a restored deployment starts with no
watermarks, every partition reads as unknown, and the first replay duplicates rather than skipping.
That is the recoverable direction, and it is the one this ordering was chosen to guarantee.

**Rows already duplicated are not touched.** As with spans, collapsing them is a separate, reversible
operation on stored data and is Raj's call. The runbook for the span case is
[collapse-duplicate-span-rows](../../delivery/runbooks/collapse-duplicate-span-rows.md); no
equivalent has been run for `logs`.

**Not verified.** Absence of data loss for metrics was not measured, only reasoned from the shared
construction above. The interaction with a genuine consumer-group rebalance is out of scope here and
belongs to ADR 0090. And the discrepancy between the two recorded before-figures stands unresolved:
the run cannot be repeated against the data it was taken from.
