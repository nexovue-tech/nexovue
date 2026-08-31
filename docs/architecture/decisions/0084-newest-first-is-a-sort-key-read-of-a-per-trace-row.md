# 0084 — Newest first is a sort-key read of a per-trace row written after the sampler

- **Status:** Accepted
- **Date:** 2026-08-31
- **Follows:** migration `000024`, which moved `endpoint_stats_1m` off a materialized view into the pipeline — this is the third table the worker fills rather than a view, after that one and `service_edges_1m`
- **Departs from:** [0010](0010-tail-sampling-defaults.md) in placement but not in substance — this table is written *after* the sampler, because it is an index and not a metric

## Context

The trace list answers "newest traces first" by assembling every trace in the window from its
spans. That is one hash-table entry per trace in the window, whatever the page size — a cost that
grows with traffic rather than with what the screen shows.

Measured today against the dev store, **12.49M spans / 2.73M traces**, on `clickhouse:26.3.20.7`
with a 2.5 GiB server:

| window | duration | peak memory | rows read | bytes read |
|---|---|---|---|---|
| 7 days | 393 ms → 1100 ms | 292 MiB / 290 MiB | 12.48M | 607 MiB |
| 24 hours | 9 ms → 12 ms | 3.4 MiB / 3.3 MiB | 50k | 2.5 MiB |
| 1 hour | 7 ms → 9 ms | 0.7 MiB / 1.1 MiB | 6.8k | 338 KiB |

Two runs several hours apart, both shown, because **the wall-clock times vary by 3×** on a server
this size while the memory and the rows read do not. Everything below is quoted from the second run,
which measured before and after back to back.

The 7-day figure is with `max_bytes_before_external_group_by = 256 MiB` already in place. Before
that bound and before the root strings were taken out of the group, the same window needed **1.8
GiB** and the server killed it: the screen offered a range that returned a 500. The bound stopped
the failure; it did not stop the growth, and 292 MiB is paid per request, so ten people looking at
the same week pay it ten times.

The same shape is visible from the other side: a probe run while writing this ADR —
`uniqExact((service_name, span_name))` per trace over the whole tenant — was itself killed with
`(total) memory limit exceeded: would use 1.71 GiB … maximum: 2.50 GiB`. Anything that groups this
store by trace id is at the edge.

Two facts about the data shape the answer.

**Traces gain spans after the sampler has released them.** Over twelve hours of the dev store, 410
traces, **6 of them had spans arriving more than 70 seconds apart** — one of them 92 minutes — and
zero had a spread between 10 and 70 seconds. So a grace period between the sampling window and the
summary buys nothing measurable: a late span is either in the same release or minutes late. Worse,
the late spans measured carried timestamps *earlier* than the rows already stored (00:42:59.09997
against 00:42:59.10015), which is the root span exported last — the case that moves a trace's start.

**A trace's distinct (service, operation) pairs are few.** Over a day of the busiest project, p50 2,
p99 19, max 48.

## Decision

**`trace_summaries` (migration `000028`): one row per stored trace, `AggregatingMergeTree`, ordered
`(tenant_id, project_id, start_time, trace_id)`.** "Newest first" is a reverse read of that key,
so the page costs the page.

**Written after the sampler, from the rows the sampler released.** Every other derived table in this
pipeline is written before it, because [0010](0010-tail-sampling-defaults.md) requires metrics to be
derived from all traces. This is not a metric: it is an index over what was stored. A list naming
traces whose spans were dropped would offer fifty rows of which forty-five 404 when clicked. The
invariant is untouched — `metrics_sum`, `endpoint_stats_1m` and `service_edges_1m` still see every
span — and the consequence is stated plainly: **the trace list shows the sampled subset, which is
what it already showed, because it already read `spans`.**

**Every column merges idempotently — `max`, `min`, `anyLast`, set union, and deliberately no `sum`.**
The writer holds a trace's accumulated state for the sampler's own `DecisionTTL` and emits a
*complete snapshot* each time the trace changes, never a delta. So a row written twice is the same
answer twice.

That is the whole idempotency argument, and it is why neither existing mechanism had to be extended:

- **`insertdedup`'s token is not needed.** It exists because
  `INSERT … SELECT … FROM input(…)` is not covered by block deduplication, and that form is only
  needed to fold an `AggregateFunction` state on the server. Every column here is a
  `SimpleAggregateFunction`, which stores its own underlying type, so the insert carries plain
  columns.
- **`span_written_offsets` (ADR [0079](0079-span-rows-are-idempotent-across-a-restart.md)) already
  covers the replay, and the ordering is load-bearing.** Summaries are inserted in the same `Settle`
  as the spans they describe and **before** the watermark advances. A replayed record whose spans
  are already stored never re-enters the sampling buffer, so it never reaches the summary either —
  and a crash between the two inserts loses the watermark write, not the summary. Writing the
  summary after the watermark would leave a stored trace with no row, invisible in the list for as
  long as its spans are kept. The watermark's lag is unchanged: no new milestone was added.

**The filters.** Service, operation, status and duration are answered from the row. The service and
the operation travel *joined* in `span_endpoints` (`service \x1f operation`) because a span filter
matches when **one** span satisfies all of it together; two independent lists would match a trace
whose service and whose operation sit on different spans. `span_services` carries the services alone
so the commonest filter is a `has()`.

**The arbitrary attribute filter still groups `spans`.** Attribute paths are unbounded — 125
distinct ones from 15 services in one hour of the OpenTelemetry Demo — so no per-trace row can carry
them. A request naming `attr` takes the old statements unchanged, bounded by
`max_bytes_before_external_group_by` as before. It is not dropped, and it is the only filter that
pays.

**The list is still two statements, and the second is bounded by the page's start times.** A trace
can hold more than one row, and rows sharing a key stay two rows until a background merge collapses
them, so the page's ids are re-read and merged. Bounding that read by the page's own span of start
times makes it a primary-key range: **7 ms reading 27k rows, against 18 ms reading 1.91M** when it
leaned on the `trace_id` bloom filter alone (both measured on the 7-day page).

## Alternatives considered

**Adaptive narrowing — scan a slice of `spans`, widen if it returns too few, refine over the
candidates.** Correct, and it needs no schema change. Rejected: three round trips on the common
path, and a widening heuristic that guesses wrong is a slow page rather than a wrong one, so it
would be tuned forever. It also leaves the growth in place — a wide enough slice is still a
window-sized group-by.

**`LIMIT 1 BY trace_id` in the page statement, to collapse a split trace in SQL.** Measured and
rejected: it stops ClickHouse reading in sort-key order. The same 7-day page went from **8 ms / 17
MiB / 120k rows** to **19 ms / 63.5 MiB / 1.02M rows**, and to 113 ms on one thread. There are 962
such rows in 2.73M, so the page is collapsed where it is free — over the fifty rows returned.

**`sum` semantics, with each release writing only its own delta.** Correct totals for a trace whose
spans arrive in two releases, and it is what the summing rollups do. Rejected: it makes the table
depend on the watermark for correctness rather than merely for tidiness, and a late span that starts
earlier than the rest would write a one-span row at a new key — a bogus one-span trace in the list.
Complete snapshots plus `max` degrade instead to "the most complete row seen", which is wrong by
omission and never wrong by invention.

**A grace period before writing, to fold late spans in.** Rejected on the measurement above: no
trace in twelve hours had a span arriving 10–70 s late. A grace long enough to catch the 92-minute
case is not a grace, and it would push the span watermark back by the same amount, undoing what
[0079](0079-span-rows-are-idempotent-across-a-restart.md) landed today.

**Keying the row on `received_at` — our own clock — so it cannot move.** Rejected: the list is
ordered by when a request happened, not by when we heard about it, and a client an hour behind
would have its traces filed an hour late on the only screen that is meant to answer "what just
happened".

**A materialized view over `spans`.** Rejected for the reason `endpoint_stats_1m_mv` was dropped in
`000024`: a view fires per insert block, so a trace whose spans arrive in two blocks produces two
partial rows and nothing reconciles them. The worker holds the trace; the view does not.

## Consequences

**What it costs to read, measured on the same store and the same windows.**

Measured back to back on the same store, page of 50, peak memory being the larger of the two
statements rather than their sum:

| window | before: group `spans` | after: page | after: merge | after total |
|---|---|---|---|---|
| 7 days | **1100 ms, 290 MiB, 12.48M rows, 607 MiB read** | 30 ms, 17.3 MiB, 120k rows | 14 ms, 0.7 MiB, 28k rows | **44 ms, 17.3 MiB, 5.8 MiB read** |
| 24 hours | 12 ms, 3.3 MiB, 50k rows | 5 ms, 1.5 MiB, 10.6k rows | 16 ms, 0.7 MiB, 28k rows | 21 ms, 1.5 MiB |
| 1 hour | 9 ms, 1.1 MiB, 6.8k rows | 5 ms, 0.4 MiB, 1.3k rows | 8 ms, 0.9 MiB, 28k rows | 13 ms, 0.9 MiB |

The week is **17× less memory, 105× fewer rows read, and it no longer grows with the window**.

**The hour and the day got slower in wall clock, and that is the trade.** 9 ms → 13 ms and 12 ms →
21 ms, because two round trips cost two round trips where one group-by over a few thousand rows was
already trivial. What they buy is that the cost stops being a function of the window at all: ten
concurrent readers of a week now cost ten pages rather than ten windows, and the second statement's
28k rows are the same 28k whatever range was asked for.

**What it costs to write.** One more insert per settle when a trace was released, and one map entry
per stored trace held for `DecisionTTL`. Measured at the cap — 50,000 traces of 10 spans and 10
distinct endpoint pairs — the accumulator holds **66.7 MiB, 1398 bytes per trace**. The cap is
`WORKERS_SAMPLING_MAX_TRACES`, and at the cap the oldest entries are evicted. **Eviction runs only
from a drain, when every held trace has already been written**, so what a full accumulator loses is
the chance to revise a row and never the row itself; `trace_summaries_evicted` in the heartbeat
counts it.

**A trace that gains a span after its row was written, and what appears on the screen.** Inside
`DecisionTTL` the trace is still held, so the next drain writes a new complete snapshot and the
reader sees the whole trace: the span count rises, the duration may lengthen, and a trace that was
`OK` can become `ERROR`. Past `DecisionTTL` the sampler has forgotten the trace too and judges the
late span as a new one; the accumulator has forgotten it as well, so a second row appears. The page
merges the two by `max`, which reports the more complete of them — **so the screen undercounts that
trace by the late spans, and the trace detail, which reads `spans`, shows them.** Measured: 962 of
2.73M rows in this store are a second row for a trace. A split whose two rows straddle a page
boundary shows the trace on both pages; nothing measured that, and it is bounded by the same 0.035%.

**A filter can be decided on a stale row.** The page statement filters rows, not merged traces, so a
trace with two rows matches if *either* matches. A trace whose failure arrived late is found by
`status=ERROR` from its later row while its earlier row still says `OK` — an over-match, never a
miss. Same 0.035%.

**The window means something slightly different.** The list now answers "traces that *started* in
this window" where it answered "traces with a span in this window". They differ only at the two
edges, and the first is what "newest first" was always trying to say. `test_traces_live.py` compares
the two paths over the interior of a window for that reason.

**An operation filter can miss a trace with more than 256 distinct (service, operation) pairs.** The
set is capped because nothing else bounds it; p99 in this store is 19 and the maximum 48, and
`trace_summaries_truncated` would say if that ever changed. Recorded because it is a filter that
silently returns less, which is the worst kind.

**One filter did not get cheaper.** An arbitrary attribute path still groups the window. A customer
who filters on `attr` over seven days pays what everyone paid before.

**Existing data needed a backfill.** 2.73M rows were derived from `spans` a partition at a time.
Without it the list is empty for anything older than the deployment — which is a real operational
step, not a detail: the table is not filled by the schema change.

**Not verified.** Three things. Behaviour under a consumer-group rebalance across two workers is
untested, as in [0079](0079-span-rows-are-idempotent-across-a-restart.md), because one worker runs
here. The `DecisionTTL` revision path is unit-tested and was not observed live: no trace in the
observation window gained a span 10–70 s late, which is the same measurement that justified having
no grace. And the read numbers above are single runs on a warm server, not distributions.

**An OOM was investigated and is not this.** `nexovue-worker-1` was OOM-killed four times against
its 1 GiB limit while this was being built. Every kill followed three `log-writing` batches of
~50,000 rows within a second, and at every kill trace assembly reported `spans_kept=0` and
`written_now=0` — the summary accumulator was empty. Stated as an observation about where to look,
not as a diagnosis of `logwriting`, which was not read.
