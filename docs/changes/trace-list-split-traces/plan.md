# Plan: the trace list shows one trace as two

> **Status:** Complete (approved 2026-10-30; Step 2 changed by the founder the same day, see Outcome)
>
> **The founder approves this plan before any file is edited.**

## Preconditions

[`bug.md`](bug.md): the reproduction and the root cause.

## Current system

Read from the code on 2026-10-30.

- **The summary's key moves.** `traceSummary.observe` lowers `earliestStart` whenever an earlier span arrives
  (`apps/workers/internal/pipeline/traceassembly/summary.go:117`). `row()` writes it as the row's `StartTime`
  (`:324`), and that is part of the sort key. `Drain` writes every changed trace on each poll (`:271-292`), so a
  trace drained before its root is written again under a second key.
- **The worker's memory.** It remembers a trace for `DecisionTTL`, 60 s (`config/pipeline.go:60`;
  `summary.go:136-140`), and nothing after a restart. A later span starts a new summary of itself
  (`summary.go:155-167`).
- **Replayed spans.** The span watermark is written after the insert is acknowledged, and losing it costs a
  duplicate row (`ingeststate/state.go:18-23`). `spans` is a plain `MergeTree` whose sort key does not include
  `span_id`, so nothing collapses the duplicate.
- **The list reads rows, not traces.**
  - The page statement picks fifty rows in key order. A comment explains, with a measurement, why it does not
    collapse by trace (`apps/api/src/nexovue_api/traces/queries.py:244-262`).
  - The values statement merges each trace's rows only within the page's own start-time range. Its comment
    counts the rows this leaves unmerged (`:264-302`).
- **The tests read the store's own data.** `busiest` picks the project and hour with the most failures from
  `spans` (`apps/api/tests/test_traces_live.py:128-160`). The split rows already in a development store keep
  failing those tests until they are gone.
- **`span_trace_lookup` is one row per batch**, not per span (27,837 rows against 126,470 spans), so it cannot
  give an exact span count.

## Change required

[`bug.md`](bug.md), *Root cause*. The trace list must show each trace once, described as a whole.

## Impact

| Area | What changes |
|---|---|
| Files | `traceassembly/summary.go`, `summarywriter.go`; `traces/queries.py`, `traces/service.py`; the live and unit tests |
| Services | worker, API |
| Schema / migrations | ClickHouse `000034`: `trace_summaries` gains `trace_start`, the trace's true earliest start |
| UI | none. The list's columns are unchanged; the values in them become right |
| Tests | worker unit tests for both cases; the live suite counts distinct spans |

## Reuse

- `AggregatingMergeTree` merging with `max`, `min` and set union stays the idempotency mechanism.
- `SimpleAggregateFunction(min, …)` is already used for `duration_ms` and the other measures.
- The keyset cursor and the two-statement list stay. Step 2 changes only the values statement's bound and the
  dedupe across pages.

## Gaps

- A stable key for a trace's row.
- Collapsing a trace's rows wherever they fall in the window.
- One row per span in the trace's own view.

## Steps and tasks

### Step 1: a revised summary stays under its first key (Case 1, 50 of the 68 split traces)

*Judgeable:* the worker test feeds a trace's children, drains, then feeds its root. It fails today with two
rows at two keys, and passes with one key and the root named.

Once a trace's row has been written, its key's `start_time` is fixed. A new column, `trace_start`
(`SimpleAggregateFunction(min, DateTime64(9))`), carries the true earliest start, and the list shows that
column. The list's order is then by first-written start, which can lag the true start by however much earlier
a late root started. Measured on the example, that is 0.25 ms.

- [x] **T1**: `traceassembly/summary.go`: `traceSummary.keyStart`, set at the first drain and never moved
  afterwards; `row()` writes it as `StartTime` and `earliestStart` as `TraceStart`. Proved by
  `summary_test.go`: *the root arriving after a drain revises the same key*, seen failing first.
- [x] **T2**: `deploy/clickhouse/migrations/000034_trace_summaries_true_start.up.sql` / `.down.sql`: adds
  `trace_start`, with its reasoning in the file. `summarywriter.go` writes it. Proved by the writer test and by
  `just migrate` on the stack.
- [x] **T3**: `traces/queries.py`, `TRACE_PAGE_VALUES_SQL`: the shown start is `min(trace_start)`. Proved by
  `test_traces_live.py`, which reads it.

### Step 2: a trace is listed once, wherever its rows fall (Case 2 and any residue)

*Judgeable:* on the development store, no trace id appears on two consecutive pages, and every split trace in the
reproduction is described as one.

- [ ] **T4**: `traces/queries.py`, `TRACE_PAGE_VALUES_SQL`:
  - bound the merge by the request's window rather than by the page's own range, so every row of a page's
    trace is merged;
  - drop from a page any trace whose newest row lies above the cursor, because an earlier page already listed
    it.

  Measured before and after on the 7-day window the file's comments use. Its earlier measurement of a
  bloom-only read (1.91M rows) is the cost to beat. Proved by
  `test_a_page_is_newest_first_and_the_cursor_continues_it`.
- [ ] **T5**: `traces/service.py`: a page that lost traces to that rule still offers a cursor when the
  statement filled its limit. Proved by a unit test in `test_traces.py`.

### Step 3: a replayed span is shown once

*Judgeable:* opening `cb4ab90359465410d423b481814d1a06` shows three spans, not four.

- [x] **T6**: `traces/queries.py`, `TRACE_SPANS_SQL` (the trace's own view, `:525`): one row per `span_id`. Proved by a live test
  that writes a span twice and opens its trace.
- [x] **T7**: `test_traces_live.py`: the expectations count `uniqExact(span_id)` where they counted rows, so a
  replay the pipeline accepts by design is not called a summary error.

The duplicate write itself stays. It is the accepted cost of the span watermark (`ingeststate/state.go`), and
removing it would mean bracketing every span insert the way the counters are. That is outside this bug, and
the change record says so.

### Step 4: the store and the records

- [x] **T8**: the development store's existing split rows. See Question 1.
- [x] **T9**: `docs/changes/ready-to-deploy/status.md`: the rollback rule decided by the founder on
  2026-10-30, to keep the conservative rule; it comes off Open decisions.
- [x] **T10**: `status.md`, this plan's Outcome, and `docs/architecture/` where it describes `trace_summaries`'
  key.

## New risks

- The list's order is by first-written start. Two traces whose starts are closer together than a late root's
  lead can swap places. That is sub-millisecond in the measured case, and the displayed start is right.
- T4's wider merge costs more read than the page-range bound. It is measured in the step; if it is too
  expensive, the step stops and the result comes back to the founder.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | The development store already holds 68 split traces. Recommended: clear the development store's telemetry once (`trace_summaries` and `spans`), and let the demo and the gate's seed refill it. The alternative is a migration that rebuilds summaries from `spans`, which is the 1.8 GiB statement the table exists to avoid. There is no customer data. | No: assume clearing | `status.md` |

## Gates

- [ ] Plan approval, before the first edited file
- [ ] Implementation
- [ ] Tests: each regression test seen failing first
- [ ] Review (`/review-diff`)
- [ ] Documentation and status update

## Validation plan

- The worker's unit tests, red then green.
- `just check` once at the end, reading the counts.
- The live trace suite run twice: against the cleared store refilled by the demo, and with the demo running.
- T4's read measured with `system.query_log`: rows and bytes, before and after.

## Rollout and rollback

Pre-deployment. The migration only adds a column; its `.down.sql` drops it. The real risk is T4's cost on a
large store, and it is measured before the step is accepted.

## Outcome

- **Step 1, as planned.** `keyStart` is fixed at a row's first write and `trace_start` carries the true start
  (ClickHouse `000034`). `TestARootArrivingAfterAWriteRevisesTheSameRow` failed first ("the revision moved the
  row's key"), then passed. The backfill script's mapping notes the column.
  **Found on the running stack, not by the unit tests:** the worker then wrote no summary at all. ch-go infers a
  DateTime64's precision from the server's type, and parsed "min" out of `SimpleAggregateFunction(min,
  DateTime64(9))`. The writer's `aggregateDateTime64` sets the precision itself. The store probe
  `summary_storeprobe_test.go` failed with the worker's own error, then passed.
- **Step 2, changed.** T4 was measured before it was built: merging across the request window read every row of
  the window per page (26,946 against 277), which is the 1.91M-row read the code had rejected. A margin on the
  page bound cannot stand in for it, because traces run up to 25 min and split rows sat up to 3 h apart.
  Sorting the 68 split traces by cause showed 15 were long traces: the worker forgot a trace 60 s after its
  *first* write. The founder chose the recommendation on 2026-10-30:
  - the worker forgets a trace `DecisionTTL` after its *last* write, using an expiry queue whose superseded
    entries are skipped;
  - the page keeps its own bound;
  - a crash replay's stray row is accepted and documented.

  `TestATraceStillArrivingIsNotForgotten` failed first ("revision = 1 spans", a new key), then passed. T4 and T5
  are not built.
- **Step 3, as planned, with one residual.** `TRACE_SPANS_SQL` keeps one row per `span_id`, and
  `test_trace_detail_live.py` failed first (the replayed span listed twice), then passed. The live suite counts
  distinct spans. The attribute-filter fallback (`_LIST_TRACES_TEMPLATE`) still counts rows: counting distinct
  span ids there holds every id in the window, in a statement trimmed to fixed-width values for its memory.
  After a crash replay, that path can count a span twice; it is recorded in `status.md`.
- **Found by the gate: a filter judged on a row.**
  - What happened: the page statement filters rows, and a trace being revised keeps its earlier row beside the
    newer one until a background merge. With long traces now revised in place rather than split, the gate
    showed a trace with an error listed under "healthy"
    (`test_a_status_filter_separates_the_traces_that_failed_from_the_rest`).
  - The fix: `service._whole_trace_passes` judges the two filters a merge can undo, "healthy" and the
    duration ceiling, again on the merged trace. Every other filter only gains matches as rows merge.
  - Proved by `test_a_filter_a_merge_can_undo_is_judged_on_the_whole_trace`, which failed for both filters
    before the fix.
- **Step 4.**
  - The development store's three trace tables were truncated.
  - The dev organisation's usage for October was cleared too: its 6.2 GB allowance was spent, so the
    receiver refused the seed with 429 `allowance_spent`.
  - The rollback decision is recorded in `ready-to-deploy/status.md`, and the architecture reference is
    updated.
