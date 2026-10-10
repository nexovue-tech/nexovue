# Status: tests-do-not-need-the-demo

## Lifecycle status

Implemented and verified — a small change under the founder's 2026-10-10 rule: no plan, a completion
record. Committed; not pushed.

## Current task

None.

## Completed

- `gateseed`: the gate's synthetic check, two service edges (one failing), two database gauges, and the
  waits for them. Its tests: the payload shapes (`TestServiceEdgesAreTwoCallsAndOnlyOneFails`,
  `TestDatabaseReadingsCarryWhatTheScreensGroupBy`) and the arrival rule extended for `Exists` waits.
- Live: first run created the check and was readable after **48.5 s**; the second after **12.1 s**.
  Stored afterwards: `gateseed-caller → gateseed-healthy` 2 calls 0 errors, `→ gateseed-failing` 2 calls
  2 errors; four `gateseed-postgres` readings.
- The three suites with the demo absent: **25 passed, none skipped** — the "failing only" service-map
  test, which skips when nothing fails, ran. The synthetics suite read the gate check's current hour.

## Next action

None.

## Open decisions

None.

## Blocked

Nothing.

## Risks

In `change.md`. One more: the service-map and database suites still choose the tenant with the most data,
which is the development tenant either way; they do not insist on the seeded rows. They no longer *need*
the demo's — the evidence is the demo-less run above — but they would also accept them.

## Validation

- `go test ./tools/gateseed/...` and the live runs above.
- `just check`: exit 0, with the demo absent — lint 0 issues, `mypy` no issues in 582 files, Go 36
  packages `ok`, `gateseed` readable after 12.0 s, Python 4174 passed and 3 skipped, web 1350, browser
  SDK 120. (The first run failed `lint-go` on ten findings in the new `gateseed` code — two unchecked
  errors and eight repeated JSON keys — fixed by naming the keys and checking both errors.)

## Specification impact

None.

## Documentation impact

None outside this record.

## Last updated

2026-10-10
