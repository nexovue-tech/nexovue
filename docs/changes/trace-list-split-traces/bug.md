# Bug: the trace list shows one trace as two

## Reported behavior

Four tests in `apps/api/tests/test_traces_live.py` fail against the development store, with the OpenTelemetry
Demo stopped:

- `test_a_page_is_newest_first_and_the_cursor_continues_it`: "the keyset repeated a trace across two pages".
- `test_a_service_filter_returns_traces_that_contain_that_service`: "the row counted the matching spans, not
  the trace", 3 against 4.
- `test_the_summary_row_says_what_the_spans_of_that_trace_say`: `cb4ab90359465410d423b481814d1a06` counts 1,
  not 4.
- `test_the_summary_and_the_span_grouping_name_the_same_traces`: 8 against 9.

The earlier note that they fail only "under live demo traffic" was wrong. They fail on what the store already
holds.

A customer would see the same trace listed twice, on different pages. One of the two entries is a partial trace:
too few spans, and the wrong root operation.

## Reproduction

Against the development stack (`just stack`), with the store as it stood on 2026-10-30:

```bash
uv run pytest -q -p no:randomly apps/api/tests/test_traces_live.py
# 4 failed, 17 passed
```

How many traces hold more than one summary row:

```sql
SELECT count() traces, countIf(r > 1) split
FROM (SELECT trace_id, uniqExact(start_time) r FROM trace_summaries
      GROUP BY tenant_id, project_id, trace_id)
-- 26886 traces, 68 split (0.25%)
```

**Case 1: the root arrives after the first summary.** Trace `d211d9b7195209203bf1fbe6ca74a116` has two rows:

| `start_time` | `span_count` | `root_name` |
|---|---|---|
| 14:57:15.555067220 | 70 | `user_checkout_single` |
| 14:57:15.555314303 | 48 | `user_add_to_cart` |

The worker wrote the trace when 48 spans had arrived. The real root started 0.25 ms earlier, but it arrived later:
OpenTelemetry exports a span when it ends, and a root ends last. The revised, complete row went under the new
`start_time`. The 48-span row is under a different sort key, so the background merge never collapses it.
This example holds no duplicate span, and neither do 50 of the 68 split traces. Those 50 are not all this case:
the later sort in `status.md` puts about 38 of the 68 here, and 15 in a third case, long traces the worker
forgot while they were still arriving.

**Case 2: a span replayed after the worker forgot the trace.** Trace `cb4ab90359465410d423b481814d1a06` has
three distinct spans and four rows in `spans`: span `12efaab3631fa7c0` (`flagd`) is stored twice. Its summary
rows:

| `start_time` | `received_at` | `span_count` | `root_service` |
|---|---|---|---|
| 15:34:30.981109 | 15:34:34.995 | 3 | `frontend-proxy` |
| 15:34:35.881685 | 15:34:44.234 | 1 | `flagd` |

The replayed `flagd` span reached a worker that no longer held the trace, so it began a new summary with only
itself in it. 18 of the 68 split traces hold a duplicate span. All 29 duplicated span rows in the store arrived in one
hour, 2026-10-28 15:00, during that day's restart rehearsals.

## Root cause

`trace_summaries` is an `AggregatingMergeTree` ordered by `(tenant_id, project_id, start_time, trace_id)`
(`deploy/clickhouse/migrations/000028_trace_summaries.up.sql`). Its rows merge only when they share that key.

**1. The worker moves the key.** `traceSummary.observe` lowers `earliestStart` whenever an earlier span arrives
(`apps/workers/internal/pipeline/traceassembly/summary.go:117`). `row()` writes it as `StartTime` (`:324`). A trace
drained before its root arrived is written again under a different key. The first row stays a separate,
partial "trace" for as long as the data is kept.

**2. A forgotten trace starts again from nothing.** The worker remembers a trace for `DecisionTTL`, 60 s by
default (`summary.go:136-140`), and nothing at all after a restart. A span that arrives later, whether genuinely
late or replayed, becomes a summary of itself.

**3. A replay can store a span twice.** The span watermark is written after the insert is acknowledged.
Losing that write costs a duplicate on the next replay
(`apps/workers/internal/pipeline/ingeststate/state.go:18-23`), and the store's block deduplication catches only
a byte-identical batch.

**The read path accepts the split by design.** `TRACE_PAGE_VALUES_SQL` merges a page's trace ids only between
the page's own first and last start times. The comment above it records the 962 rows it leaves unmerged
(`apps/api/src/nexovue_api/traces/queries.py:264-276`). The page statement keys on rows, not traces
(`:244-251`). So a trace with rows on two pages is listed on both, and each entry is described from the rows on
that page alone.

`max(span_count)` is right only when one row is a complete snapshot. That holds for Case 1. It does not hold for
Case 2, where the rows describe different spans.

## Specification impact

None. The trace list was always specified as one entry per trace, described as a whole.

## Regression test

Written in the plan, Step 1: worker unit tests that feed the root after a drain, and the replay after a forget.
The live suite's expectations count distinct spans. Each test is seen failing before its fix.

## Fix plan and validation

[`plan.md`](plan.md).

## Status

[`status.md`](status.md).
