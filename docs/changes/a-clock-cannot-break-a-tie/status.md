# Status: a-clock-cannot-break-a-tie

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- Stable `(created_at, id)` ordering on the admin member list, the admin project list and an
  incident's notifications.
- A regression test that asserts the tie exists as well as how it is broken, seen red first.

## Next action

Founder: review the diff and say **push**.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Other `created_at` orderings may share the flaw.** Three were found and fixed by reading every
  `order_by(...created_at)` in `apps/api/src`; orderings on other columns, and orderings in the Go
  services, were not audited. A structural test — "any ordering by a timestamp column carries a
  tiebreak" — would hold the class rather than these three instances, and is not written.
- **The premise is a Postgres detail.** `now()` being transaction time is why the rows tie. Anything
  that moves these inserts into separate transactions would hide the tie and make the tests look
  unnecessary; the test says so in its own failure message.

## Validation

- `just check`, exit 0: **4131 Python passed, 2 skipped**; **1335 web** (123 files); **120** browser
  SDK; Go, format, lint, `mypy` (573 files) and the contract checks clean.
- **Seen red** by reversing the tiebreak: *"the owner is the older row and must come first"*.
- The defect itself was observed rather than reasoned about — the failing assertion is quoted in
  `bug.md`.

## Specification impact

None.

## Documentation impact

None beyond this record.

## Last updated

2026-09-21
