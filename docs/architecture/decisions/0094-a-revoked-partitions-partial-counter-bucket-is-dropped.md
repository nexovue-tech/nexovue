# 0094 — A revoked partition's partial counter bucket is dropped

- **Status:** Accepted
- **Date:** 2026-09-04
- **Completes:** [0090](0090-a-partition-that-leaves-takes-its-buffered-spans-with-it.md), whose
  Consequences named this half and left it: *"The counter half of the same root is not fixed… those
  accumulators aggregate across partitions into one series, so 'drop the revoked partition's
  contribution' needs per-partition sub-accumulators through both stages. Raj's call to scope; not
  attempted here."* This is that change. Everything 0090 argues about why a revoke drops rather
  than drains, about the lock around a turn of the loop, and about which direction the failure has
  to fall, holds here unchanged and is not repeated.

## Context

0090 stopped a partition handover writing the losing worker's whole sampling buffer a second time.
It did not stop the same handover counting the same records a second time, and it said so.

**A counter is worse than a row, in one specific way.** A duplicated span row is visible and
removable: two rows carry the same span id and a runbook collapses them
(`docs/delivery/runbooks/collapse-duplicate-span-rows.md`). A counter is an aggregate. Once 100
requests have been stored as 130, the individual contributions are gone — there is no row to delete
and nothing that says which increments were real. `metrics_sum` and `service_edges_1m` are summed on
read, so the two workers' rows simply add. It corrupts silently, permanently, and it is the number a
customer's alert threshold is set against.

**The blast radius is three stages, not two, and the third is the bill.** Read against the code
rather than against 0090's note:

| holds state across messages | affected | why |
|---|---|---|
| `sample.Buffer` | fixed in 0090 | per-span partition, dropped on revoke |
| `derive.Counters` — `metrics_sum` and `endpoint_stats_1m` | **yes** | per-minute buckets summed across partitions |
| `serviceedges.Accumulator` — pairing buffer and `service_edges_1m` | **yes** | same shape, plus spans parked waiting for a partner |
| **`usage.Meter` — `usage_daily`** | **yes** | per-tenant-day counters flushed every 10s |
| `traceassembly.summaries` — `trace_summaries` | no | every column merges with `max`, `anyLast` or `groupUniqArrayArray` and none sums (`000028`), so a row written twice reduces to the same answer |
| `logwriting`, `metricwriting` | no | insert inside `Absorb`; nothing of theirs survives a handover in memory |
| `dedupe.Offsets` | fixed in 0090 for spans; **not for usage** | see below |
| `ingeststate.Guard` | no | a cache of the durable state, re-read on assignment |

**`usage` is the one that costs money, and it was not in 0090's note at all.** The meter accumulates
spans, logs, points and bytes per tenant per day in memory and flushes them into `usage_daily` every
ten seconds. `usage_daily` **adds on conflict**. Bytes are what ADR
[0083](0083-the-period-is-the-month-and-the-allowance-is-bytes.md) ends the free tier on and what
ADR [0056](0056-per-service-pricing.md) prices against, so an over-count here is an
over-bill and an early cut-off, not a wrong graph.

**Its own guard does not stop it, and that is measured, not reasoned.** `usage_ingest_offsets` is
advanced by an upsert whose `WHERE settled.counted_through < excluded.counted_through` gates each
partition's row, and `Apply` skips the counters entirely when *no* row advances. That catches a
retry of the same flush. It does not catch two workers, because one flush carries every partition
the worker owns: the partitions it kept still advance, `rows_affected` is not zero, and the whole
transaction proceeds — adding the revoked partition's counters with it. Run against the dev stack's
Postgres 18 on 2026-09-04, in a transaction that was rolled back: the gaining worker's flush wrote
5 spans; the losing worker's flush then advanced 1 of its 2 partitions and added 8 more, leaving
**13 where 8 is correct**. The same interleaving through the pipeline's own code, before the change
below, over-billed by the same margin — 13 spans and 253 bytes where 8 and 156 are correct, a 62%
over-bill.

**What makes dropping safe is an ordering that already exists**, and it had to be checked separately
for each accumulator rather than assumed from the span case.

- **Trace assembly.** A batch's offsets move only when `Stored` says so, and `Stored` requires
  `counters.Drained(latestMinute)` and `edges.Drained(latestEdgeMinute)` as well as the sampling
  window. `latestMinute` is the *newest* minute the batch fed and draining is monotone in the
  minute, so a committed batch has every minute it fed already drained. The durable
  counted-through watermark is narrower still: `Advance` is fed the *prefix* of held batches whose
  derived output has drained (`Queue.Reached`). **Therefore anything sitting in an open bucket
  belongs to a record whose Kafka offset and whose durable watermark have both not moved.**
- **Usage.** `Stored` is `flushes > produced.flushes`, and a flush drains the whole meter and
  advances the watermark in one Postgres transaction. Same conclusion by a shorter route: anything
  still in the meter belongs to records nothing has been told about.

So the records are still on the broker, and the worker that takes the partition over reads them
again and counts them. The alternative — keep them — is the over-count above.

## Decision

**A bucket keeps each partition's share apart, and a revoke drops the shares of the partitions that
left.** The shares are summed back together at drain time, so no row and no table changes: there is
no migration, and a reader of `metrics_sum` cannot tell.

Four pieces, and they move together with 0090's three.

**Provenance travels with the contribution, exactly as it does with a buffered span.** `derive.Span`
and `serviceedges.Span` gain a `Partition`, beside `sample.Facts.Partition`; `usage.Meter.Observe`
takes one. The map keys become `counted{series, partition}`, `countedEndpoint{endpointKey,
partition}`, `countedEdge{edgeKey, partition}` and `counted{tenantDay, partition}` — the output
identity embedded, with the partition alongside it. Embedding rather than widening keeps the output
key a value the drain can compare and emit directly.

**An edge belongs to its caller's partition.** An edge exists because a caller span arrived and is
bucketed on that span's minute, so that is the record a handover has to take it back with.

**The drain folds the shares in partition order.** Not map order: a floating-point sum depends on
the order it is taken in, and `metrics_sum`'s duration sum reaches ClickHouse inside a block whose
bytes must be identical when the same offsets are re-derived, or the store's insert deduplication
cannot recognise the retry (ADR [0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
[0075](0075-block-deduplication-replaces-the-read-before-retry.md)). Within a partition the order
is the record order,
which a replay reproduces; across partitions it is now ascending partition id, which a replay also
reproduces. **This is strictly more reproducible than before**, where the sum followed the order
records happened to interleave across partitions in a batch.

**The latency samples merge rather than one winning.** `latencysample.Reservoir` keeps the
`MaxSamples` smallest weights, and a weight is a hash of the span's own identity — so merging two
per-partition reservoirs gives exactly the sample the undivided bucket would have built. Which
partition a duration arrived on cannot change the answer.

**Parked spans are dropped, not settled.** `serviceedges` holds spans waiting for a partner.
Settling a revoked caller would *record* an edge — an unanswered one for a CLIENT, naming
`(uninstrumented)` — which is precisely the row the new owner is about to derive properly.

**Usage releases four things in one call**, because three of them alone are each a hole:

- the meter's shares for those partitions,
- `reached`, or the next flush advances their durable watermark over counters it no longer holds,
- `receivedThrough`, which feeds the same row,
- `dedupe.Offsets`, or a partition that came back would have those records recognised as replays and
  skipped — and they were dropped, not counted. That is an **under**-bill, and it is the mirror of
  the trap 0090 names for the span path.

`counted` is deliberately left: it caches what the store holds, which is still true, and the
assignment that brings a partition back replaces all of it.

**Revoked and lost still differ only in the cache.** `usage` gains `OnRevoked` and `OnLost` — it had
neither, only `OnAssigned` — and the lost path adds the watermark invalidation, the same asymmetry
0090 chose for trace assembly and for the same reason.

**Still no call out of the process.** Every drop above is map work bounded by what is held.

## Alternatives considered

**Drop the whole open bucket on any revoke**, without per-partition attribution. **Rejected**, and
it is the trap worth recording: it is safe for the revoked partitions and a silent hole for the
kept ones. A batch on a kept partition that fed the same open bucket is also uncommitted, but this
worker never re-reads it — it goes on consuming forward — and `Drained` is a function of wall-clock
time, not of what the bucket contains, so the offsets commit anyway with the counts thrown away.
That trades silent over-counting for silent under-counting, which is worse: an over-count at least
shows up as a number nobody can reconcile, while a hole in a counter looks like quiet traffic.

**Drain the revoked partitions' buckets and advance their watermark before the handover completes.**
**Rejected** for the three reasons 0090 gives for spans, and they are stronger here: a counter drain
is a ClickHouse insert *and* a KeeperMap bracket — three calls, all through a two-minute retry
budget, inside a callback the group's sixty-second rebalance timeout is running against. For
`usage` it is a Postgres transaction in the same place.

**One reservoir per bucket with the partition tagged on each sample**, so the sample cap stays at
`MaxSamples` for the whole bucket instead of per partition. **Rejected**: the scalar counts need
per-partition sub-accumulation regardless, so the bucket is already split; adding a second,
different splitting mechanism for the one field inside it buys a bounded amount of memory (see
Consequences) at the cost of two ways of doing the same thing in one struct. It is the change to
make if the memory ever bites, and the heartbeat says when.

**Keeping parked callee spans of a revoked partition**, so a cross-partition queue hop whose
consumer left could still pair with the producer we kept. **Rejected**: it is correct in that one
sub-case and wrong in its mirror — a revoked *caller* kept for the same reason records an edge the
new owner records too, and for a CLIENT that is an unanswered edge pointing at
`(uninstrumented)` beside the real one. One rule, uniformly applied, is the honest boundary.

## Consequences

**What it costs in memory.** A series held on *n* partitions is *n* map entries instead of one.
Below the sample cap this is only the map overhead — the samples themselves are per span either way
— so the cost is roughly the key and header of an entry (order 100 bytes) times the partitions a
series is actually spread over, against a 870 MiB `GOMEMLIMIT`. **Above** the cap it is real: a
saturated endpoint-minute can hold `MaxSamples` (8192, 128 KiB) per partition rather than in total,
so a single endpoint taking more than 8192 spans a minute *on every partition* — about 1,600 rps for
one endpoint on a twelve-partition topic — costs 1.5 MiB for that minute instead of 128 KiB. The
heartbeat's `counter_series`, `endpoint_series` and `edge_series` now count shares rather than
series, which is the number that matters for memory and the number that would show this growing.

**What it costs in data.** On a handover, the moving partitions' partial minute is counted again by
their new owner, so those counters reach storage later — bounded by the same minute bucket and late
grace that already gate them. One thing is genuinely lost rather than deferred: a **queue** hop
whose producer and consumer sat on *different* partitions and only one of them moved can no longer
be paired by either worker, so that edge is missing for that minute. Cross-partition queue pairing
already only happens while one worker holds both partitions (`serviceedges` package comment), and a
gap of one minute in one edge is filled by the next minute — where an over-summed `call_count` is
permanent. **Reasoned from the code, not measured.**

**Which direction it fails.** Toward duplication, in every path, exactly as 0090 requires: for a
record whose contribution is dropped, no offset is committed and no watermark advanced, so the new
owner reads it again.

The third thing that sentence used to claim — *no counter claimed stored* — is **not true**, and the
rig caught it. A minute bucket closes `clocktrust.Bucket + LateGrace` after its minute begins (70s),
while `Stored` also holds a batch's offsets for the sampling window measured from the instant it was
polled. Where that window outlasts what is left of the 70s, the bucket drains into ClickHouse while
the batch's offsets are still held; a revoke then finds nothing in the bucket to take back for those
records, and the new owner counts them a second time. It is still a duplicate and never a hole — the
direction stands — but the residual is not zero, and it is not zero at the shipped window either.
Measured over a six-minute run: **1,235 spans (+3.43%) at the rig's 60s sampling window and 70
(+0.19%) at the shipped 10s**. Shrinking the window inverts which side runs out first but does not
close the gap, which is a correction to a claim this ADR previously reasoned rather than measured:
see *The two-worker rig* below.

**A latent boot crash, found on the way and fixed here.** `usage.New` built its `counting` *after*
`consume.Dial` returned, while `Dial` starts consuming — so a rebalance callback could reach a nil
`counting` and panic the worker at start-up. It has never been seen, and adding two more callbacks
to that window is what made it worth closing rather than widening: `counting` is now built before
`Dial`, and reaches the loop's retry budget through the pipeline instead of capturing it.

**Measured at the value that reaches the store.** On a real rebalance the change takes
`metrics_sum` from **+22.78% to +3.43%** over what the broker accepted, `endpoint_stats_1m` the
same, `service_edges_1m` from **+22.76% to −0.01%**, and `usage_daily` — the bill — from **+1.40%
to exact on both spans and bytes**. At the shipped sampling window the counter residual is
**+0.19%**. The full method, the three arms and the one call nobody can yet account for are in
*The two-worker rig* below.

- **Run, and watched fail before the change.** Four negative controls, asserting on the values that
  reach the store rather than through any flag a double sets. Before: `nexovue.spans.count` drained
  **6 where 3 is correct** after a revoke, `endpoint_stats_1m` **6 requests, 6 errors and 6 latency
  samples where 3 each is correct**, `service_edges_1m` **2 calls where 1 is correct**, and
  `usage_daily` **13 spans and 253 bytes where 8 and 156 are correct**. After the change all four
  pass. The whole worker suite is **384 tests, 0 failures, 0 skips**, at `-count=1` and clean under
  `-race`.
- **Run, against the real store.** The Postgres statement above, on the dev stack's Postgres 18,
  inside a rolled-back transaction, confirming the usage guard does not stop a two-worker overlap —
  13 billed where 8 is correct. This is what makes the test's `recordedStore` a model of the SQL
  rather than a mock of it.
- **Run on the instrument, and watched fail before *its* fix.** A fifth negative control, on the rig
  rather than on the worker: `TestTwoWorkersSharesOfAMinuteDeriveDifferentBlocks` in
  `tools/rebalanceprobe` builds a minute of the arm's own traffic, splits it 6/6 by partition as the
  two workers hold it, and fails if the two shares would reach any of the three counter tables as
  the same block. It failed on all three; the numbers are in the rig section below.

## The two-worker rig — 2026-09-04

0090's numbers came from `tools/rebalanceprobe` driving a real rebalance across two workers on an
isolated topic. Three arms of the counter equivalent ran, and the method is recorded here because
0090's was not, and had to be reconstructed from scripts left behind in a temp directory.

**The arm.** Its own topic (`spans-probe-<arm>`, 12 partitions, created explicitly — auto-creation
is off on the broker and a missing topic makes the producer fail rather than the run), its own
consumer group, two workers on the same binary. One resident; a peer joins and leaves gracefully
four times across a six-minute run; **nothing is killed**. `WORKER_PIPELINES=trace-assembly`, which
starts the counters, the edge accumulator and the usage meter together — all four stages under test
in one run. The first two arms use `WORKERS_SAMPLING_WINDOW=60s` for the reason 0090 gives: at the
shipped 10s the handover is a photo finish the losing worker can win, and 60s makes the outcome
certain rather than lucky. The third arm runs the shipped 10s, because that window turned out to be
what decides the residual below.

**The denominator is the probe's own report**, which counts only what the broker acknowledged. The
probe was extended for this ADR so that one run gives each stage a denominator of its own — every
trace's first span is a CLIENT naming a peer nothing reports for, the rest are parentless SERVERs
that can never pair with it. So every accepted span counts in `metrics_sum`, only the SERVERs reach
`endpoint_stats_1m` (`derive/endpoints.go` `inbound`), and each caller settles unpaired into exactly
one `service_edges_1m` row named `(nvprobe-peer-<run>)`. `usage_daily` is checked on both spans and
bytes, the bytes being the OTLP payload inside the envelope, which is what `usage.countSpans` bills.
Each arm reports its own: **7,200 / 7,200 / 7,199 envelopes, 0 refused**.

**The arm ends on a condition, never on a sleep.** Commit lag is ~70s by design, so counts read
early look like loss that is really latency. It waits for a heartbeat *after* the probe finished
showing nothing held in either pipeline **and** zero broker lag for both groups — 150s, 100s and 70s
for the three arms.

**Every arm is read for the artefact before it is read for the total.** `DuplicatedInsertedBlocks`
moved **0** across all three, and `written_rows` from `system.query_log` matches the rows present
for `metrics_sum` (51 = 51 in each arm; the two rollups merge on write, so theirs is a ceiling).
This is the check whose absence let an earlier pair of arms report a 14.6% apparent *loss* that was
really the store discarding identical blocks — see *the instrument had to be fixed first* below.

**Before the change** — worker built at `c5bc989`, the fix's parent; run `bff30a12`, 60s window:

| | stored | correct | over |
|---|---|---|---|
| `metrics_sum` `nexovue.spans.count` | 44,200 | 36,000 | **+8,200 (+22.78%)** |
| `endpoint_stats_1m` SERVER requests | 35,360 | 28,800 | +6,560 (+22.78%) |
| `service_edges_1m` calls | 8,839 | 7,200 | +1,639 (+22.76%) |
| `usage_daily` spans | 36,505 | 36,000 | **+505 (+1.40%)** |
| `usage_daily` bytes | 3,000,711 | 2,959,200 | **+41,511 (+1.40%)** |
| `spans` rows / distinct | 36,000 / 36,000 | 36,000 | **0** |

The over-count is an identity, not a rate. **8,200 is exactly the resident's own `spans_disowned`**
for the run, and the endpoint figure is four-fifths of it to the unit, which is the SERVER share of
a probe trace. The edge figure is 1,639 against the 1,640 traces disowned — one short, and that one
is the same call the after arm is also short of; see the edge deficit below. The usage over-count
resolves the same way: 202 messages were uncommitted across the four revokes, `dedupe.Offsets`
recognised 101 of them as replays, and the remaining **101 × 5 spans = 505** and **101 × 411 bytes =
41,511** are the whole of it — 411 bytes being the payload of every probe envelope. The `spans`
table is clean at 36,000 rows and 36,000 distinct, so **0090's fix held while the counters beside it
doubled**: exactly the hole 0090 named and left.

**After the change** — worker built at `7d1d7b7`; run `00ecde7a`, same 60s window, same denominators:

| | stored | correct | over | was |
|---|---|---|---|---|
| `metrics_sum` `nexovue.spans.count` | 37,235 | 36,000 | **+1,235 (+3.43%)** | +22.78% |
| `endpoint_stats_1m` SERVER requests | 29,788 | 28,800 | +988 (+3.43%) | +22.78% |
| `service_edges_1m` calls | 7,199 | 7,200 | **−1 (−0.01%)** | +22.76% |
| `usage_daily` spans | 36,000 | 36,000 | **0 — exact** | +1.40% |
| `usage_daily` bytes | 2,959,200 | 2,959,200 | **0 — exact** | +1.40% |
| `spans` rows / distinct | 36,000 / 36,000 | 36,000 | 0 | 0 |

**The bill is exactly right.** `usage_daily` matched its denominator on both spans and bytes to the
unit, on a run where the release path fired at every handover — the resident dropped 6 tenant-days
and 33,291, 32,880 and 25,071 bytes at three of the four. That is the number ADR 0083 ends the free
tier on and ADR 0056 prices against, and it is the one the change existed for.

**The residual is 1,235 spans, and it is the same identity again.** The resident's heartbeat closed
the run at `spans_disowned=8,130` against `counted_spans_disowned=6,895`; the difference is
**1,235**, which is the `metrics_sum` over-count to the unit, and four-fifths of it (988) is the
endpoint over-count to the unit. Per handover the gap was 615, 0, 620, 0 — the two handovers where
the resident dropped spans it no longer had counter shares for. Those spans' counters were already
in ClickHouse while their offsets were not committed, so the new owner counted them again.

**The shipped window shrinks the residual 18-fold but does not remove it** — worker `7d1d7b7`, run
`9e5612da`, `WORKERS_SAMPLING_WINDOW=10s`, the value that ships:

| | stored | correct | over |
|---|---|---|---|
| `metrics_sum` `nexovue.spans.count` | 36,065 | 35,995 | **+70 (+0.19%)** |
| `endpoint_stats_1m` SERVER requests | 28,852 | 28,796 | +56 (+0.19%) |
| `service_edges_1m` calls | 7,212 | 7,199 | +13 (+0.18%) |
| `usage_daily` spans / bytes | 35,995 / 2,958,789 | same | **0 — exact** |
| `spans` rows / distinct | 35,995 / 35,995 | 35,995 | 0 |

**This corrects a claim that was reasoned rather than measured, and the reasoning was wrong.** The
earlier note said that at 10s the offsets wait until `poll + 10s` while the bucket closes at
`minute + 70s`, which is never earlier, so the residual *should not arise at all*. The mechanism
does invert exactly as predicted — at 10s the resident closed with `spans_disowned=2,035` against
`counted_spans_disowned=6,460`, the counters now holding **more** than the sampling buffer rather
than less, and `edge_calls_disowned` went from 0 to 885 — but the residual is **+70, not 0**. The
two timers are not the whole story: a bucket can still drain between the poll and the revoke inside
a 10s window. **What is measured is that the shipped configuration over-counts by 0.19% across a
handover, not that it does not.** Whether 0.19% is worth closing is Raj's call, and the bill is
exact at both windows.

**Two things an adversarial review of this section found, both recorded rather than quietly fixed.**
First, the "120× better than 22.78%" comparison that stood here has been removed: it set the *after*
binary at a 10s window against the *before* binary at 60s, changing two variables at once, and this
ADR's own arms show the window alone moves the residual 18-fold with the binary held constant. Most
of that ratio was the window, not the fix. Second, the `service_edges_1m` row above is disputed: the
review derives **+14** from the identity `+70 spans ÷ 5 spans-per-trace`, against the +13 that
`7,212 − 7,199` gives here. **Neither can now be settled, because the raw counter output was never
saved to a file and the rows were deleted during cleanup** — every figure in this section survives
only as prose in this document. That is the defect to fix before the next measurement: capture the
output, then delete the rows. **Fixed on 2026-09-06** — that instruction now lives in
`tools/rebalanceprobe`'s own header, in a section placed above the cleanup SQL, with the redirect
that captures the report and the query that counts the stored side under the same predicates the
DELETEs use. It was recorded here and in a commit message, which are both places nobody reads
before running a tool.

**One edge call short, in both 60s arms, and it is not the fix.** The after arm stored 7,199 calls
against 7,200 accepted. The before arm is short by the same one: its identity predicts
7,200 + 1,640 disowned traces = 8,840 and it stored 8,839, while the span and endpoint identities in
that same arm are exact. So the deficit is present in the pre-fix binary too. The 10s arm came out
**+13 over** rather than short, so it is not a systematic loss in either direction. **The cause is
not established.** It is 0.014% of one run's calls, it is the only under-count anywhere in three
arms, and it is recorded here unexplained rather than reasoned away.

**The instrument had to be fixed first, and that is the finding that nearly cost the measurement.**
An earlier pair of arms reported the after arm 14.6% *under* its denominator. That was not loss: a
partial counter block's counts are equal between two workers holding an even split of a
series-minute — that split being the case under measurement — so the durations behind them are the
only field that can tell the two blocks apart, and every probe span reported the same 5 ms. Watched,
before the probe was fixed: one minute of the rig's traffic split 6/6 across twelve partitions gives
both halves `15000.000000 ms over 3000 spans` in `metrics_sum`, `12000.000000 ms over 2400 spans` in
`endpoint_stats_1m` and `3000.000000 ms over 600 spans` in `service_edges_1m`. Three identical pairs;
three blocks ClickHouse discarded as retries. `rebalanceprobe` now derives each span's duration from
that span's own identity — 5.000 to 8.999 ms, still two hundred times under
`WORKERS_SAMPLING_SLOW_THRESHOLD`, and deterministic, so a genuine replay still reproduces its block
byte for byte. The three arms above were all run on that probe, and all three moved
`DuplicatedInsertedBlocks` by zero.

**Found on the way, and not recorded anywhere before.** Insert-block deduplication cannot tell a
retry from two workers legitimately deriving the same block. ADRs
[0060](0060-counter-idempotency-via-clickhouse-dedup.md) and
[0075](0075-block-deduplication-replaces-the-read-before-retry.md) turned the window on for exactly
the retry case and record two limits — the window's depth, and its survival of a store restart —
but not this one. Real traffic has duration jitter, which makes a collision unlikely; a service
whose spans are uniform (a health check at a constant duration, on two partitions, one span each)
is the case where it is not, and the loss is silent and unrecoverable in precisely the way this ADR
opens by describing. The probe made it visible; it did not create it. **Whether the window needs a
discriminator, or this is accepted and recorded as a known limit of 0060, is not decided here.**

**Not measured, and not measurable on this rig.** The cross-partition queue edge that Consequences
says is genuinely lost rather than deferred. Every probe trace is one envelope on one partition, so
a producer and a consumer never sit on different partitions and that path is never exercised. It
stays reasoned from the code.

**How an arm is read** — `<svc>` is `nvprobe-spans-<run>` from the probe's report:

```sql
SELECT sum(value)         FROM metrics_sum       WHERE metric_name = 'nexovue.spans.count'
                                                   AND service_name = '<svc>';   -- vs spans_accepted
SELECT sum(request_count) FROM endpoint_stats_1m WHERE service_name = '<svc>';    -- vs server_spans_accepted
SELECT sum(call_count)    FROM service_edges_1m  WHERE client_service = '<svc>';  -- vs edge_calls_accepted
SELECT count(), uniqExact(span_id) FROM spans    WHERE service_name = '<svc>';    -- 0090 comparability
-- Postgres, as a delta against a baseline taken before the arm: usage_daily adds on conflict.
SELECT spans, bytes FROM usage_daily WHERE org_id = :org AND project_id = :project AND day = :day;
```

Read before them, never after, or the totals are not what the workers derived: `system.events`
`DuplicatedInsertedBlocks` across the arm, and `written_rows` from `system.query_log` for the three
tables against the rows actually present.

**A run leaves rows, and one set is billable.** `rebalanceprobe`'s own header carries the statements
that remove them, extended here with `usage_ingest_offsets`, which a run also writes and the header
did not name. `usage_daily` is the one that cannot be separated from real traffic — it is keyed
(org, project, day) and adds on conflict — so an arm records its baseline first and reports its own
number as the delta. Removing the probe's share of that row afterwards is a subtraction nobody can
check; running the probe against an org and project of its own is the change that would end this,
and it is not made here.
