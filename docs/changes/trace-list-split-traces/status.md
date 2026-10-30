# Status: trace-list-split-traces

## Lifecycle status

Implemented, uncommitted: plan approved 2026-10-30, Step 2 changed by the founder the same day; the gate passes.

## Current task

Commit and pull request, on the founder's word.

## Completed

- Reproduced on 2026-10-30: `test_traces_live.py` 4 failed, 17 passed, with the demo stopped. 68 of 26,886
  stored traces hold more than one summary row.
- Root cause, from the store and the code ([`bug.md`](bug.md)):
  - 50 of the 68: the worker re-keys a trace's row when its root arrives after the first drain;
  - 18 of the 68: a replayed span starts a summary of itself.
- Step 1 (T1-T3): the worker fixes a row's key at its first write; `trace_start` (ClickHouse `000034`)
  carries the true start, and the list shows it. `TestARootArrivingAfterAWriteRevisesTheSameRow` failed first
  ("the revision moved the row's key from …20.001 to …20"), then passed.
- Step 2 measured on 2026-10-30 (`system.query_log`, the busiest project's 7-day window, 26,946 rows). Merging
  a page's traces across the request window read 26,946 rows per page, against 277 with the page's own bound.
  On the store the code's comments measured, that is the 1.91M-row read they rejected. A margin on the page
  bound cannot replace it: traces run up to 25 min, and split rows sit up to 3 h apart.
- The 68 split traces by cause:
  - 38: the root arrived after the first write (rows under 1 s apart). Step 1 fixes these.
  - 15: spans kept arriving more than 60 s after the trace's first span. The worker forgets a trace 60 s after
    it first saw it (`forgetAt` is never refreshed, `summary.go:155-167`), not 60 s after its last span.
  - 15: a span replayed after a restart.
- Ruled out by measurement: live demo traffic, the earlier explanation. The suite fails with the demo stopped.

## Next action

Commit on `fix/traces-live-under-load`, push, and open the pull request. It carries a migration, so the next release's upgrade takes the restore path on rollback.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- Accepted: a span replayed after a crash, once its trace is forgotten, writes a second row. The list can show
  that trace twice, once as a partial entry. Opening it shows each span once.
- A trace silent for longer than `DecisionTTL` (60 s) that then sends another span splits the same way.
- The attribute-filter fallback counts stored rows, so a replayed span counts twice on that path.
- A page can hold fewer traces than its limit when `_whole_trace_passes` drops one. Its cursor still continues.
- The sort of the 68 by cause rests on the gap between rows and on one example of each; it is an inference.
- `test_services_live.py::test_one_service_counts_exactly_as_its_row_in_the_ranking` fails when live traffic lands
  between its two reads (421,944 against 421,943, demo running). It is not touched here; unfixed.

## Validation

- `just check`, demo stopped: exit 0. Python 4,507 passed, 3 skipped; web 1,373; SDK 124; mypy 648 files.
- Each regression test was seen failing first: the worker's two, the store probe, `test_trace_detail_live.py`,
  and the filter test.
- With the fixed worker and the demo running for 8 minutes: 2,414 traces, 0 split, the longest 10 minutes. No
  worker crash was exercised in that run.
- The store probe runs by hand; the gate only compiles it. The chaos suite was not run.

## Specification impact

None.

## Documentation impact

`docs/architecture/current-system-reference.md` updated.

## Last updated

2026-10-30
