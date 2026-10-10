# Status: synthetics-suite-reads-a-clean-hour

## Lifecycle status

Implemented and verified — committed with this record.

## Current task

None.

## Completed

- Root cause recorded in `bug.md`; the fixture reads the newest clean hour.
- `uv run pytest apps/api/tests/test_synthetics_live.py`: **13 passed**, against the same store that
  gave 8 errors with the old fixture.

## Next action

None.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The suite still needs one clean hour of real probes in the last two days. If the demo stack stays
  gone past 2026-10-11 18:00, it fails with the reason — correctly: there is no real prober output to
  assert on. Bringing the demo back (`otel-demo.sh`) restores it within the hour.

## Validation

The suite run above, and `just check` with `refusals-narrowed-to-the-record`: Python 4174 passed,
0 errors — this suite included.

## Specification impact

None.

## Documentation impact

None.

## Last updated

2026-10-10
