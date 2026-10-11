# Status: keys-can-be-rotated

## Lifecycle status

Implemented and verified on the development stack. Not committed.

## Current task

None.

## Completed

- Code read; `change.md` and `plan.md` written. Founder decision 2026-10-11: format v2 only.
- **Step 1 — a key id in every sealed value.** `sealing.py`: `Keyring`, `key_id` (SHA-256[:8]),
  format 2 `[2][key id][nonce][ct]` with the key id in the associated data, `SealedBox(keyring)`
  opening by key id, v1 and unknown keys refused, `ConfiguredKeys`. Red then green: opening with the
  current key alone failed the rotation case.
- **Step 2 — both services read a keyring.** `CHECK_SECRET_PREVIOUS_KEYS` (API and scheduler),
  `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS` (API); every box built from the pair. Red then green: an
  enrolment and a probe secret sealed under the old key failed to open when the previous keys were
  ignored.
- **Step 3 — status and re-seal.** `python -m nexovue_api.keys status|reseal` over the four sealed
  columns, in batches of 500, committing per batch, replacing a value only if it still holds what
  was read; `just keys-status`, `just keys-reseal`. Red then green: values stayed under the old key
  when the re-seal was disabled; a stale write replaced a changed value when the compare was removed.
- **Step 4 — a deployed install has both keys.** The API refuses `ENV=production` without
  `CHECK_SECRET_KEY` or `CREDENTIAL_ENCRYPTION_KEY`, the scheduler without `CHECK_SECRET_KEY` (red
  then green); both deployed compose files require `CREDENTIAL_ENCRYPTION_KEY` and pass the previous
  keys; `.env.example` documents all four.
- **Step 5 — the runbook from a real rotation.** On the dev stack: a throwaway organization with a
  check secret, a webhook channel and a two-factor enrolment. Before rotating, and after each step
  (new key current with the old previous; re-sealed; old key removed), a two-factor sign-in returned
  200, a Run now using the secret came back `up`, and a webhook send test reached the receiver (405
  from example.com, so the signing secret opened). Status went `previous 2/1/1` → `re-sealed 2/1/1`
  → `current 2/1/1`. Rotated back to the dev defaults the same way. `docs/operations/key-rotation.md`
  written from it; ADR-010 amended; readiness and the system reference updated.
- **`just check` exit 0**: Python 4219 passed, 3 skipped; web 1352; browser SDK 120; lint and types
  clean. Two runs before this one each failed one test:
  - the group 4 erasure test, fixed (see the plan's Outcome);
  - `test_dbstats_live.py::test_a_statement_timed_twice_does_not_weight_the_trend_it_is_in`, once.
    That test passes alone and is untouched by this change; it reads live dev data.

## Residual risks

- The keys live in an environment file on the host; a secrets manager is a readiness recommendation.
- No rotation schedule is set.
- `test_dbstats_live` failed once under the parallel run; not investigated here.
- The rehearsal left one throwaway organization, "Rotation Rehearsal", in the dev database.

## Next action

Founder reviews the diff; on approval, commit. Then readiness group 6 (deployment configuration tells
the truth).
