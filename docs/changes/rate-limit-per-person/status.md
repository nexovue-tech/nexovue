# Status: rate limit per person

## Lifecycle status

Verified

## Current task

None — awaiting the founder's review of the diff.

## Completed

- `ratelimit/policy.py`: `Tier` gains `org_quota` and `org_max_concurrent`. Telemetry: 60 a minute and 8 in
  flight per person (unchanged numbers), 600 a minute and 32 in flight per organization. Default: 300 a
  minute per person, 1,500 per organization.
- `middleware/ratelimit.py`: a request carrying a token that names a **live session** is counted against
  that person (`user:<id>`, so a person's tabs share one budget) and their organization, with
  concurrency held in both. Everything else — no token, an invented token, and every sign-in route (AUTH
  tier) — is counted as before: per address, plus session and email where they apply. The plan
  multiplier now reads the organization from the session.
- `auth/sessions.py`: `SessionStore.peek`, a read with no "last seen" write, for the limiter.
- `tools/e2e/playwright.config.ts`: the worker-budget comment says what now binds.

## Next action

Founder: read the diff and say **push**.

## Open decisions

None. The decision was taken 2026-09-20: per person, with a per-organization ceiling.

## Blocked

Nothing.

## Risks

- **One person moving fast still meets their own limit.** In the fixed e2e run, 24 reads were refused, all
  on the telemetry tier and all from specs navigating synthetics screens faster than a person would; no
  test failed (screens refetch). This is the per-person limit doing its job, not the defect.
- **The organization numbers are judgement, not measurement.** 600 and 32 are sized for about ten people
  with a screen each open; a larger team would need the plan multiplier, which is still fixed at 1.0
  (`FixedPlanLimits`).
- The limiter now reads Redis once more per signed-in request (the session peek). Redis already serves
  the session read a moment later in the same request.

## Validation

- `tests/test_ratelimit_callers_live.py` (6): failed before the fix — *colleagues behind one address each
  have their own budget*, *an organization is held to its ceiling*, *another organization is not held to
  this one's ceiling* — and passes after. The three that passed before (one person's own budget, an
  invented token and a signed-out caller counted per address) are the protections that must not move.
- `tests/test_ratelimit.py`: passes.
- `just e2e`, clean start (ClickHouse and Redis restarted, one idle minute): **22 passed, 0 failed**.
  Before the fix, from the same start: 2 failed on the pushed code, 5 with F26's `members.spec.ts`.
- `just check`: Go and lint clean; Python 3,724 passed — the two tenant-isolation tests that need recent
  seed traffic failed once for want of it, then passed (9) after `just load`; web 1,291 and browser SDK 120;
  contracts clean.

## Specification impact

None to a feature spec. Resolves the open item recorded in `docs/features/synthetic-checks/status.md`
and the one in `docs/features/team-and-api-access/status.md`.

## Documentation impact

`tools/e2e/playwright.config.ts` comment; both status files above.

## Last updated

2026-09-20
