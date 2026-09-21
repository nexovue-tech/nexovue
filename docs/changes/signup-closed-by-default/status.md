# Status: signup-closed-by-default

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- `signup_enabled` defaults to **False**. Held by
  `test_a_deployment_that_says_nothing_gets_a_closed_signup`.
- All three Compose targets state their choice.
- Two stale claims corrected: the suite docstring saying a self-hosted install keeps signup open,
  and the readiness row citing "there is no invite flow".
- **Production-readiness blocker 3 cleared**, and the other seven re-checked against the code rather
  than re-read: 1, 2, 4, 5, 6, 7 and 8 all still hold.

## Next action

Founder: review the diff and say **push**.

## Open decisions

None.

## Risks

- **Read at start-up.** A deployment already running keeps whatever it was configured with until it
  restarts. Nothing here closes a door that is currently open on a live install; there is no live
  install.
- **The blocker list is not trustworthy without re-checking.** Two of eight entries were stale
  today, both describing work that had shipped. The remaining six were verified against the code
  this time, but nothing stops the list drifting again — a readiness assessment is a photograph,
  and this repository takes them rarely.

## Validation

- `just check`, exit 0: **4144 Python passed, 2 skipped**; **1335 web** (123 files); **120** browser
  SDK; Go, format, lint, `mypy` (575 files) and the contract checks clean.
- **The default was verified live**, not only in a unit test: `Settings()` with nothing set reports
  `signup_enabled = False`, and the development API restarted with the new code still answers
  `{"enabled":true}` at `GET /system/signup` because `docker-compose.yaml` now says so.
- **The failure was seen before the fix**: `test_signup.py` returned 403 with *"nexovue cloud is set
  up after purchase"* — the closed-signup message reaching a suite that had never had to think about
  it.
- **The other seven blockers were re-checked**: no `.github` (2), no alembic step in either
  deployment file (5), no self-monitoring anywhere in `apps/` or `deploy/` (7), `NEXOVUE_INGEST_HOST`
  still demanded and unset (4). 1 and 6 are unchanged by their nature.

## Specification impact

None. F31's FR30 and AC31 specify the switch; this changes its default.

## Documentation impact

`docs/operations/production-readiness.md` — blocker 3 and the invite-only row.

## Last updated

2026-09-21
