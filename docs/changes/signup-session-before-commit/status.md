# Status: signup session before commit

## Lifecycle status

Verified

## Current task

None.

## Completed

- Root cause found and confirmed by reproduction, not inference.
- `sign_up` commits the tenant before `start_session`.
- `signup/service.py` module docstring corrected: the request-scoped dependency is no longer the
  only commit point.
- Regression test added, verified to fail without the fix.
- The live tenant-isolation test now preserves the response body in its assertions.

## Next action

None once the gate is green.

## Blocked

Nothing.

## Validation

`just check` on 2026-09-12, exit code 0, output read rather than inferred: 2277 Python tests passed
(2276 before this change; the added one is the regression test), 120 browser-SDK, 613 web across 47
files, plus `buf lint` and `buf breaking`. The tenant-isolation live suite passed, including the
test that failed intermittently before.

The regression test was verified in both directions: it fails without the fix, reporting the
observed order `['provision', 'session']`, and passes with it.

A targeted reproduction — spend the per-IP auth window, sign up, call `/auth/me` at once — failed on
its third round before the fix and ran 25 rounds clean after it.

## Last updated

2026-09-12
