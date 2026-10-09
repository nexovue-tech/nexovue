# Bug: two defects found running `just check` after `just fresh`

Found 2026-10-09 while verifying that every `just` recipe still works, on the founder's request,
after `just fresh` rebuilt the dev stack from a wiped Postgres/ClickHouse/Kafka volume set.

## 1. The TOTP replay test re-derives the code it claims to replay

### Reported behavior

`apps/api/tests/test_second_factor_routes.py::test_the_enrolment_code_cannot_also_sign_in` failed
once, in a six-worker `pytest -n 6 --dist loadfile` run: `assert replayed.status_code == 401` saw
`200`.

### Reproduction

The test enrols 2FA through the shared `_enrol(client)` helper, which submits
`pyotp.TOTP(secret).now()` to `/two-factor/confirm` internally. The test itself then computed:

```python
secret, _ = await _enrol(client)
used_to_enrol = pyotp.TOTP(secret).now()
```

`used_to_enrol` is a **second, independent call** to `.now()`, made after `_enrol` has already
returned. It is not the string `_enrol` actually sent to `/two-factor/confirm` — it is whatever code
is correct for whichever 30-second step the clock is in at the moment this line runs.

Proved deterministically with `pyotp.TOTP(secret).at(timestamp)` at two timestamps two seconds
apart, straddling a step boundary (script kept at
`/private/tmp/claude-501/-Users-raj-Personal-nexovue/b23a58e1-54e5-4741-9b95-c440530d5418/scratchpad/totp_boundary_repro.py`):

```
code actually submitted to /two-factor/confirm: 765370
code the test re-derives via a second .now()-style call: 275259
identical: False
```

### What was ruled out

- **The replay guard itself** (`totp.SecondFactorStore.claim_step`, `second_factor._totp_accepts`).
  Both enrolment and login key the spent-step marker on the same `user.id` and the same code string
  (`_spent_step_key`), and `claim_step`'s Redis `SET NX` is the correct exclusion primitive. Nothing
  about the guard depends on wall-clock alignment once it is handed the right string.
- **Redis state leaking between workers.** `test-py` runs `--dist loadfile`, so this file's tests
  share one worker and a shared fixture scope; the marker is keyed by `user.id` and `code`, both
  freshly generated per test, so no other test's state could collide here.

### Root cause

`apps/api/tests/test_second_factor_routes.py`, `test_the_enrolment_code_cannot_also_sign_in`. The
code the test replays is not captured from the request that actually enrolled the factor — it is
recomputed from `.now()` a second time, after the enrolment round-trip has already happened. TOTP
steps are 30 seconds and aligned to the UNIX epoch, not to when the test started, so any run whose
two `.now()` calls straddle a step boundary derives a code that was never submitted to
`/two-factor/confirm` and therefore was never claimed. Replaying it then correctly returns `200` —
the assertion is failing because its premise (`used_to_enrol` is the code that armed 2FA) is false,
not because the product's replay guard let a used code through.

This is a timing-window defect in the test, the same class the workflow calls out directly: two
independent re-derivations of a time-dependent value, racing a periodic boundary.

### Customer impact

Pre-deployment, so no customer is affected today. The test was asserting something true about the
product by accident on every run that happened not to straddle the boundary — meaning the actual
anti-replay guard had no reliable regression coverage. A real regression in `claim_step` or
`_totp_accepts` could have shipped and this test would have caught it only on the runs unlucky
enough to straddle a step boundary at the same moment — observed here at roughly 1 run in several
dozen, by the step width (30s) against the gap between the two `.now()` calls under parallel load.

## 2. The signup-uniqueness test writes a `User` row under an `Organization` that was never created

### Reported behavior

`apps/api/tests/test_first_run_seed_live.py::test_a_taken_address_is_refused_rather_than_raised`
failed: `sqlalchemy.exc.IntegrityError` / `asyncpg.exceptions.ForeignKeyViolationError`:
`insert or update on table "users" violates foreign key constraint "fk_users_org_id_organizations"`,
`DETAIL: Key (org_id)=(00000000-0000-7000-8000-000000000001) is not present in table "organizations"`.

### Reproduction

1. Confirmed no row exists for the platform organization in the live dev Postgres:
   `select id, slug, name from organizations where id = '00000000-0000-7000-8000-000000000001';` →
   `(0 rows)`.
2. Confirmed why: `seed_super_admin` is the only code path that creates that row, it runs from
   `apps/api/src/nexovue_api/lifespan.py` only when `SUPER_ADMIN_EMAIL`/`SUPER_ADMIN_PASSWORD` are
   set, and neither is in `.env.example` or this machine's `.env`.
3. The test builds `User(org_id=PLATFORM_ORG_ID, email=taken, ...)` directly and flushes it,
   assuming that organization row already exists. It does not, so the flush is what raises.
4. Confirmed `provision_tenant`/`seed_first_organization` are never reached: the traceback's INSERT
   is the test's own setup statement, not `provision_tenant`'s.
5. Confirmed the sibling file already treats this as environment-dependent:
   `test_the_platform_organization_alone_is_still_a_first_run` in the same module calls
   `pytest.skip("no platform organization seeded here, so there is nothing to exclude")` when the
   row is absent — the five skips in the gate's own summary line account for this.

### Root cause

`apps/api/tests/test_first_run_seed_live.py`,
`test_a_taken_address_is_refused_rather_than_raised`. The test needs to prove "an address the
platform operator already holds is refused rather than crashing the boot," which requires a real
`Organization` row at `PLATFORM_ORG_ID` to satisfy `users.org_id`'s foreign key. It fabricated the
`User` row alone, skipping the `Organization` row that only `seed_super_admin` creates, instead of
calling `seed_super_admin` — the same function `test_super_admin_seed_live.py` uses for the identical
purpose. The test's own docstring already states the intended scenario correctly ("The operator is
seeded first"); the code beneath it did not seed the operator, it hand-wrote one row of a two-row
precondition.

### Customer impact

Pre-deployment, so no customer is affected today. In a real deployment this exact precondition
(the platform operator's address reused as `FIRST_OWNER_EMAIL`) is reachable any time an operator
installs with `SUPER_ADMIN_EMAIL` set and then names the same address when provisioning the first
customer organization — `first_run.py`'s own `except ConflictError` branch exists for it. This test
was the only coverage for that branch and it was not exercising it: it crashed before
`seed_first_organization` was ever called, so a regression in the `ConflictError` handling would not
have been caught either.

## Specification impact

None. Both are test-only defects; no production code in `apps/api/src/nexovue_api/auth/`,
`apps/api/src/nexovue_api/signup/`, or `apps/api/src/nexovue_api/superadmin/` changed.

## Regression test

- **1:** The fix *is* the regression test — `test_the_enrolment_code_cannot_also_sign_in` now
  captures `used_to_enrol` once and submits that exact string to `/two-factor/confirm`, then replays
  the same string object. No second `.now()` call exists for it to race against, so the assertion is
  deterministic rather than probabilistic. Observed: the unfixed version is provably wrong via the
  boundary repro above (the two independently-derived codes differ); the fixed version passed
  (`1 passed`) and, by construction, cannot straddle a boundary because only one code value is ever
  computed.
- **2:** `test_a_taken_address_is_refused_rather_than_raised` now calls `seed_super_admin(db,
  _settings(SUPER_ADMIN_EMAIL=taken, SUPER_ADMIN_PASSWORD=PASSWORD))` to create both rows itself,
  independent of whatever the environment happens to seed. Observed failing before the fix
  (`ForeignKeyViolationError`, confirmed by re-running the test with the fix stashed) and passing
  after (`1 passed`).

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.
