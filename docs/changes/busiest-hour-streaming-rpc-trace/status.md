# Status: busiest-hour-streaming-rpc-trace

## Lifecycle status

**Implemented, verified live, and committed.** Founder chose option 2: harden the test.
Committed as `ee8bbd7` (investigation) and `f68dbf5` (the fix).

## Current task

None.

## Completed

- Reproduced the failure deterministically, twice, identical output.
- Ruled out every change made in this session as the cause (different code path, and the
  pathological trace data predates all of them).
- Ruled out a correctness bug in the trace-listing query or the trace-summary data: a wider-page
  diagnostic over the identical hour found plenty of traces the product reports correctly as
  wholly inside the window.
- Identified the exact mechanism: `ORDER BY start_time DESC` with a 25-row first page concentrates
  recently-started, still-running traces into the page the test reads, and today's session has
  accumulated an unusually high density of those — a `payment`→`flagd` gRPC event-stream
  reconnecting roughly every ten minutes since 10:46, and several long k6 browser sessions — because
  the demo has been running continuously for about seven hours, far longer than ordinary use.

## Completed

- `apps/api/tests/test_traces_live.py::test_the_summary_and_the_span_grouping_name_the_same_traces`
  no longer takes one page from each listing path and fails if nothing on it is wholly inside the
  window. It now pages both independently (`MAX_PAGES_SEARCHED = 20`, 500 traces) via each path's own
  `next_cursor`, accumulating every page's results into the same comparison, until a shared trace
  wholly inside the window turns up or both sides are exhausted.
- Verified both directions against the real, untouched pathological data: reverted only this file
  and re-ran — failed with the exact assertion already on record. Restored the fix and re-ran —
  passed. Same ClickHouse state on both sides of the change.

## Next action

None. Committed; nothing further pending on this record.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- `MAX_PAGES_SEARCHED = 20` is a bound, not a guarantee — a window with genuinely nothing short
  enough anywhere in it still fails loudly, by design, rather than searching forever. If a future
  session leaves the demo running long enough that even 500 traces are all too long, this bound
  would need raising; nothing suggests that's imminent.
- Not investigated here, and worth a separate look if it recurs: whether the ~10-minute
  `payment`→`flagd` reconnect pattern is itself worth filing upstream against the OpenTelemetry Demo,
  since it accumulates ever-growing trace data for the life of the demo process with no natural end
  — a demo left running for days would presumably make this worse than today's seven hours did.

## Validation

- Red, reproduced a second time with the fix stashed: identical assertion text to the original
  report, against the same unmodified ClickHouse state.
- Green, with the fix restored: `1 passed`, same state, same query.
- `ruff check`/`ruff format --check`/`mypy .`: clean on the changed file (a transient mypy-cache
  error from a concurrently running `just check` process was observed once and did not reproduce on
  a clean run — not a real issue).
- `just check`, full run: **exit 0.** Python 4142 passed, 4 skipped, 0 failed; every Go package
  `ok`; web 1335 passed; `buf lint`/`buf breaking` clean.

## Documentation impact

- `docs/changes/README.md` — this record added to the index.

## Last updated

2026-10-09
