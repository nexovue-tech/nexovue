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

**Which direction it fails.** Toward duplication, in every path, exactly as 0090 requires. Nothing
is recorded as finished: no offset committed, no watermark advanced, no counter claimed stored.

**A latent boot crash, found on the way and fixed here.** `usage.New` built its `counting` *after*
`consume.Dial` returned, while `Dial` starts consuming — so a rebalance callback could reach a nil
`counting` and panic the worker at start-up. It has never been seen, and adding two more callbacks
to that window is what made it worth closing rather than widening: `counting` is now built before
`Dial`, and reaches the loop's retry budget through the pipeline instead of capturing it.

**Measured — what was and was not run.**

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
- **Not run: the two-worker rig.** 0090's numbers came from `tools/rebalanceprobe` driving a real
  rebalance across two workers on an isolated topic. The equivalent measurement here is
  `sum(value) FROM metrics_sum WHERE metric_name = 'nexovue.spans.count' AND service_name =
  'nvprobe-spans-<run>'` against the spans the probe accepted, plus `sum(call_count) FROM
  service_edges_1m` and the tenant's `usage_daily` row, before and after. It was **not** run: the
  rig needs two workers pointed at a probe topic and leaves rows in a shared dev stack that other
  work is using at the same time, including billable `usage_daily_services` rows the probe's own
  header warns about. **So the fix is proved at the value that reaches the store, and not yet on a
  live rebalance.**
