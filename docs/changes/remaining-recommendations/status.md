# Status: remaining-recommendations

## Lifecycle status

Built — steps 1-5 committed (e47280b, 4cd46f5); T21 (personal data) built, not committed

## Current task

Awaiting the founder's "commit" for T21.

## Completed

- Four surveys of every remaining recommendation against the code (2026-10-11).
- `change.md`, `design.md`.

## Next action

Commit T21 on the founder's word.

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

Steps 2-5: see the plan's Outcome. `just check` exit 0 — Python 4355, web 1358, SDK 124, chunks under
500 kB; E2E sign-in, services, synthetics 8 passed.

T21: `docs/features/personal-data/status.md`.

## Specification impact

D2 has its spec (`docs/features/personal-data/`), which amends the team spec's BR6 and FR11; A3 amends the super-admin spec.

## Documentation impact

Listed in `change.md`.

## Last updated

2026-10-11
