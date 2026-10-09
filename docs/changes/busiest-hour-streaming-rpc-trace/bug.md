# Bug: the busiest-hour trace-listing test's first page is dominated by traces that cannot fit

Found 2026-10-09 while verifying `just check` after an unrelated change. **Not a defect in
nexovue's product code** — the investigation below rules that out with evidence. The defect, such as
it is, is in the test's assumption that its first page of candidates will usually include a short
one; that assumption breaks under a data shape that is rare in normal use but not rare today.

## Reported behavior

`apps/api/tests/test_traces_live.py::test_the_summary_and_the_span_grouping_name_the_same_traces`
fails deterministically (reproduced twice in isolation, identical output):

```
AssertionError: 25 traces are in both answers and none lies wholly inside
2026-10-09T11:05:00..2026-10-09T11:55:00, so this compared nothing
```

## Reproduction

1. `uv run pytest apps/api/tests/test_traces_live.py::test_the_summary_and_the_span_grouping_name_the_same_traces -v`
   — fails with the exact message above, twice, with no change in between.
2. The busiest project's highest-error hour is `2026-10-09 11:00`–`12:00` (65 errors, 21,989 spans);
   the test narrows to `11:05`–`11:55` per its own T3-7 safeguard.

## What was ruled out, by measurement

- **Not a query or summary-computation bug.** A diagnostic script (kept at
  `/private/tmp/.../scratchpad/trace_diag.py`) ran the test's own two listing calls with a 200-row
  page instead of 25, over the identical hour. Plenty of traces came back correctly marked
  `inside=True` — short, well-formed, summary and raw spans agreeing — proving both listing paths
  and the trace-summary data are correct. The problem is specific to which 25 traces the *first
  page* contains, not whether any exist.
- **Not caused by anything changed today.** `apps/api/src/nexovue_api/traces/{queries,service}.py`
  is untouched by every change in this session (the masking fix, the gen_ai fixtures, the receiver
  gzip fix, the otel-demo profiling wiring). The pathological trace data below predates all of them
  — the earliest reconnect in it is timestamped 10:46, before this session's masking work began.

## Root cause

`apps/api/tests/test_traces_live.py`'s `_list` helper takes the **first page** of the trace listing
(`PAGE = 25`), which `traces/queries.py:261` orders `start_time DESC` — most recently started first.
Within an hour-long window, a trace that started late and runs long is the one most likely to still
be running past the window's edge; the "most recent 25" are therefore, by construction, the ones
most likely to straddle it. In normal use this costs nothing because long traces are rare. Today
they are not rare, because the OpenTelemetry Demo has been running continuously for roughly seven
hours in one session — far longer than a normal "bring it up, look at a screen, tear it down"
development cycle — and two upstream behaviors of the demo accumulate exactly the kind of trace this
exposes:

- **`payment`'s gRPC client to `flagd`'s flag-change event stream reconnects roughly every ten
  minutes.** Read directly off the raw spans: `grpc.flagd.evaluation.v2.Service/EventStream`
  (CLIENT, in `payment`) and its paired `flagd.evaluation.v2.Service/EventStream` (SERVER, in
  `flagd`) recur under **the same trace id** every ~600–620 seconds, each pair's own `duration_ms`
  sitting within a few hundred milliseconds of 600,000 — consistent with a ~10-minute gRPC stream
  idle/keepalive timeout the demo's images carry, not anything nexovue's receiver or worker does to
  it. One such trace (`c28742e9…`) has been reconnecting since 10:46 and was still doing so at
  17:00, the moment this was investigated.
- **k6's long-lived browser load-generator sessions** (`root_service = load-generator`) run for
  several minutes to, in two cases checked, over an hour, matching the exact class of trace this
  test's own docstring already names from an earlier incident (`T3-7`, 2026-09-07, "a seven-minute
  k6 checkout").

Enough of these have now accumulated in the busiest-error hour that they fill the entire first page
of 25, leaving nothing short enough in that specific page — even though the hour itself holds plenty
of ordinary, short, correctly-summarised traces, confirmed by the wider-page diagnostic above.

## Customer impact

None, and none would follow even in production. Nothing about the product's actual behavior is
wrong — both listing paths return correct data for every trace, as the diagnostic's `inside=True`
rows prove. This is a gap between a test's convenience assumption (the first page usually has a
short trace) and a specific, if unusual, traffic shape — not a defect a customer could ever observe.

## Fix plan and validation

Not implemented without a decision — see `status.md`'s *Open decisions*. Three options, weighed
there: leave it (self-resolving after the stack's data ages out or the demo is next restarted
clean), harden the test to look past the first page when the first page has nothing wholly inside,
or wipe ClickHouse now for an immediate clean slate.

## Status

See `status.md`.
