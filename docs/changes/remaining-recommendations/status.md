# Status: remaining-recommendations

## Lifecycle status

In development — Step 1 (security) built, not committed

## Current task

Step 1 (part A, security) built and checked; awaiting the founder's "commit".

## Completed

- Four surveys of every remaining recommendation against the code (2026-10-11).
- `change.md`, `design.md`.

## Next action

Founder says "commit" for Step 1; then Step 2 (part B, data safety), T7.

## Open decisions

- D6 plan prices — the founder's, later. Nothing in this change depends on it.

Decided 2026-10-11: every other ⚑ recommendation in `design.md`.

## Blocked

Nothing.

## Risks

See `design.md`.

## Validation

Step 1:
- New tests seen red first: `test_auth_lockout_live.py` (5), `test_cross_origin.py` (11),
  `test_admin_mode_live.py` and `test_super_admin_seed_live.py` (3), `test_deploys.py` (replay and
  missing id) and the concurrent case in `test_deploys_live.py` (red without the fix, green with),
  `test_pollers_auth.py` (5), `test_heartbeat_routes.py`, `test_logging.py` (ticket).
- Live on dev: a same-origin change 200 and the same change marked cross-site 403. The lock was not
  observable over HTTP from one machine: the per-address limit (10 a minute) answers 429 first; the
  lock is proven by its live suite against Postgres.
- `just check`: exit 0 — Python 4334 passed, 3 skipped; web 1357; SDK 120.

## Specification impact

D2 needs a short spec; A3 amends the super-admin spec.

## Documentation impact

Listed in `change.md`.

## Last updated

2026-10-11
