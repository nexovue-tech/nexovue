# 0079 — Span rows are idempotent across a restart, on a watermark with no bracket

- **Status:** Accepted
- **Date:** 2026-08-30
- **Extends:** [0061](0061-counter-overcount-across-a-store-restart.md) — same problem, same store, one partition key; the counters were guarded and the span rows were not
- **Corrects:** [0058](0058-tail-sampling-window-is-ten-seconds.md), whose Consequences say a restart loses the in-flight buffer. It does not; the cost runs the other way.

## Context

31% of a day's spans were stored more than once. Over six hours before the fix, **6135 of 14980
distinct spans had more than one copy**, up to six copies each, every copy sharing `received_at` to
the nanosecond — the same envelope inserted again, not a client re-send. It was user-visible: a
trace with 2 distinct spans was stored as 6 rows, so a customer opening it saw each span three
times.

Three beliefs in the codebase had to be wrong together for this to survive as long as it did.

**"Re-inserting an identical span row changes nothing."** Written in `traceassembly`'s package
comment, in `apps/workers/CLAUDE.md`, and in [0054](0054-spans-written-through-until-tail-sampling.md).
`spans` is a plain `MergeTree` and nothing deduplicates it on read, so the second copy is simply a
second row.

**"Block deduplication covers the retry."** `000019` gave `spans` a
`non_replicated_deduplication_window`, and it does cover a retry inside one process. It cannot cover
a restart: the records re-batch on the replay and the sampler releases traces in different
groupings, so the block checksum never matches the one that landed.

**"A restart loses the buffer."** [0058](0058-tail-sampling-window-is-ten-seconds.md) recorded the
exposure as ten seconds of lost traces. Nothing is lost — the offsets that filled the buffer were
never committed, so the broker replays those records. The exposure is duplication, and it is not
bounded by the sampling window either.

The measurement that makes a fix possible is the gap between two deadlines. Offsets commit only once
a batch's spans have left the sampling buffer **and** its counters have left their minute bucket.
Across 2797 heartbeats the commit lag was **p50 43s, p90 72s, max 105s**, against a **10s** sampling
window. So on any replay, the spans of roughly **77–86%** of the replayed records are already in
ClickHouse — settled a sampling window ago while their offsets wait a minute bucket longer. That
band is the whole opportunity: it is recognisable, and it is most of the damage.

One property of the failure hid it from casual reproduction. A graceful `SIGTERM` drains the buffer
and commits, so `docker restart` duplicated 7 spans where an abrupt kill duplicated far more. **The
defect belongs to abrupt termination** — a rebuild, an OOM kill, a crash. Anyone reproducing this
with `docker restart` concludes there was never a bug.

## Decision

**A durable per-partition `written_through` watermark: the highest offset whose span rows ClickHouse
has acknowledged.** On replay, a record at or below it is decoded and masked — so a malformed one is
still parked — but is never offered to the sampling buffer, so its rows are never written again.
Migration `000026` creates `span_written_offsets`, a `KeeperMap` table keyed like
`span_ingest_offsets`.

**The ordering is the substance.**

```
buffer.Expire(now) → insert spans → [ClickHouse acknowledges]
  → write written_through
  → Kafka commit (later; it waits on the counters and edges too)
```

The watermark is computed *after* Settle's insert, never before. Claiming offsets early is the
dangerous direction: a batch still inside its sampling window has spans in memory and nothing in the
store, and a watermark covering it would make the replay skip spans nothing ever wrote. **A
duplicate row can be collapsed later; a missing span cannot be recovered** — and "no data loss
between receiver and storage" is a phase-1 invariant, while duplication is not. Written after the
acknowledgement and before the commit, the failure modes are: lose the watermark write and you get
today's duplicate, which is recoverable; there is no ordering in which you get a hole.

The bound is exact rather than approximate. `openTrace.closesAt` is set once when a trace opens and
never refreshed, `Expire` releases at `now >= closesAt`, and `spanStorage.spansStoredAt` is
`polled.At + Window` compared the same way — so every span a record touched has left the buffer by
that instant, and Settle inserts before it computes what reached the store.

**No `BEFORE`/`AFTER` bracket, unlike the counters.** This looks inconsistent with
[0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
[0061](0061-counter-overcount-across-a-store-restart.md) until the asymmetry is named. A counter
re-applied is a *wrong number*, and a lost acknowledgement leaves you unable to tell a finished
insert from an interrupted one — hence the bracket, and hence `Ambiguous`. A span watermark is a
*plain fact* recorded only after the store acknowledged the insert. There is no in-flight state to
recover, so there is nothing for a second flag to say. The watermark is believed as it stands even
when the counter half of the same partition is `BEFORE`.

## Alternatives considered

**A column on `span_ingest_offsets`, so both watermarks share one row and one write.** This was the
intended design and it is not available. Measured on `clickhouse:26.3.20.7`:

```
Alter of type 'ADD_COLUMN' is not supported by storage KeeperMap
```

That is a fact about the storage engine, not a preference. The obvious fallback — recreate the table
with the extra column — cannot be one migration either, because `clickhouse_migrate_dsn` pins
`x-multi-statement=false`; and it would discard the counter watermarks the table is holding live in
order to save one small write per settle. **Record this so the next person does not try it again.**

**A watermark in Postgres, in the shape of `usage_ingest_offsets`.** That table is correct because
the counters and the offsets are both in Postgres and move in one transaction — its own migration
says so. Span rows go to ClickHouse, so the atomicity the pattern rests on is simply absent. Worse,
`controlPlanePool` returns `nil` when `POSTGRES_DSN` is unset and `traceassembly.New` never receives
a pool: trace assembly runs without Postgres today, and this would have made an optional dependency
load-bearing for span ingest.

**Deduplicate at the table** — `ReplacingMergeTree`, or `LIMIT 1 BY` on read. Rejected: it changes
query semantics for every reader, costs every query, and treats a write bug as a read concern.

**A content-derived `insert_deduplication_token`** (`insertdedup` already does this for the
rollups). Rejected: after a restart the sampler genuinely releases different rows in a block, so
there is no stable content to hash.

## Consequences

**What it costs.** One extra small `KeeperMap` insert per settle when the watermark moved, and one
comparison per record. No extra reads: the guard's cache is already in memory and is re-read only on
rebalance, which is the `Refresh` the counter guard always did. `Writing` narrows to partitions that
actually advanced, so a quiet turn of the loop writes nothing.

**A residual remains, deliberately.** Inside the last sampling window a span can be written early —
a trace whose verdict is already remembered takes the fast path at `Add`. Closing that would need
per-row offset provenance through the sampler, changing `sample/`, `flatten.go` and `block.go`, and
is not worth it against a 10s bound.

**Measured, and stated as a count rather than a rate.** Across three abrupt restarts on the new
binary, **1428 spans were skipped** as already stored (914, 389, 125), with **zero** duplicated rows
in every minute observed. The watermark ran **190 records ahead of the committed Kafka offsets**, and
each partition logged `store_from` 20–55 offsets above `batch_from` while the counter scenario read
`new` throughout — which is why the pre-existing replay log line was silent through the whole skip.
A `spans_already_stored` heartbeat counter now reports it.

**We do not claim 41% → 0% as a steady-state rate, and the reason is worth keeping.** The 41% came
from a six-hour window containing a crash-loop at ~500 spans/min; the controlled baseline ran at
~40 spans/min with a ~16s commit lag, so its restarts produced 7 and 9 duplicates — too small a
signal to divide into. Two windows under different load do not make a ratio. `spans_already_stored`
is a direct count of spans that were already in the store and were not written again, and that is
the evidence. A later 90-minute window straddling the deployment measured 134 of 9640 (1.4%), which
is a mixture of before and after, not an after.

**Logs and metrics share the root and are sequenced separately.** `logwriting` and `metricwriting`
insert inside `Absorb` with `Stored` unconditionally true, so their window is one batch rather than
43–72s: **124 of 12105 distinct log rows had 2 copies, 1.0%, against 41% for spans**. Their fix is
also simpler — with no buffer, a batch's offsets map cleanly to its rows.

**The rows already stored are not touched by this change.** Collapsing them is a separate, reversible
operation on customer data and is Raj's call. Duplicates always share `received_at` and the partition
key is `(toDate(received_at), retention_days)`, so every copy sits in one partition:

```sql
OPTIMIZE TABLE spans PARTITION (toDate('2026-08-30'), 30) FINAL
  DEDUPLICATE BY tenant_id, project_id, service_name, timestamp,
                 trace_id, span_id, received_at, retention_days
```

The column list must include **every sort-key and partition-key column** — ClickHouse rejects a
shorter one, so the four-column identity you would reach for first does not work. Nothing has been
run against stored data.

**A trap found on the way: `just migrate-down` rolls back Postgres and ClickHouse together.**
Proving this ClickHouse migration reversible silently took Postgres from 0011 to 0010 and back,
which dropped and recreated `project_first_traces` and reset the first-traces carve-out counters.
Two agents have now been caught by it. The recipe should be split per store.

**Not verified.** Three things, and they are not upgraded by anything above. Absence of data loss was
not measured empirically — span volume per minute held steady across all three restarts, but traffic
varied enough that this is weak evidence; the loss direction is covered by an ordering test and a
negative control, not by observation. The `Ambiguous` interaction is unit-tested and reasoned, not
seen live: no settlement was interrupted mid-insert during testing. And behaviour under a real
consumer-group rebalance across two workers is untested, because only one worker runs here.
