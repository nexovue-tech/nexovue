# Plan: keys-can-be-rotated (readiness group 5)

> **Status:** Complete 2026-10-11
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` written; founder decision 2026-10-11: format v2 only, the v1 reader deleted.

## Current system

Read from the code:

1. **One construction, many callers.** `SealedBox(key, purpose)`
   (`packages/py-platform/src/nexovue_platform/checks/sealing.py:32-57`) writes
   `[1][nonce][ciphertext]` with associated data `purpose:owner`. It is built from one decoded key at
   every caller:
   - API: `checksecrets/service.secret_box` (:65), `channels/service.channel_box` (:88),
     `checkimports/service` (:71, through `secret_box`), `auth/encryption.credential_box` (TOTP,
     wrapped in `CredentialBox`);
   - scheduler: `probes/secrets.open_secrets` (:37), `alerts/tasks._secret_box` (:141).
2. **Four sealed columns**, each with a fixed purpose and owner:

   | Column | Key | Purpose | Owner |
   |---|---|---|---|
   | `check_secrets.sealed_value` | check | `check-secret` | `project_id:name` |
   | `notification_channels.sealed_secret` (nullable) | check | `notification-channel` | `org_id:channel_id` |
   | `users.totp_secret` (nullable) | credential | `totp` | `user_id` |
   | `totp_enrolments.sealed_secret` | credential | `totp` | `user_id` |

3. **Sealed check secrets ride inside queued jobs** (`scheduler/checks.py:166`); beat rebuilds every
   check's job from the database every 30 s (`beat_refresh_seconds`).
4. **Both keys are optional at boot everywhere** (API `settings.py:213,220`; scheduler
   `check_secret_key`, `settings.py`). `CREDENTIAL_ENCRYPTION_KEY` is in the dev compose only;
   `CHECK_SECRET_KEY` is required (`:?`) in the app and on-prem files.
5. **An operator command has a precedent:** `python -m nexovue_api.suspend` (argparse, its own
   engine, exit codes), run through `just`.
6. **The production validator pattern exists:** `settings._payments_match_the_estate` (group 3).

## Change required

`change.md` §Intended behavior.

## Impact

| Area | What changes |
|---|---|
| Files | py-platform `checks/sealing.py`; API `auth/encryption.py`, `auth/dependencies.py`, `checksecrets/service.py`, `checksecrets/router.py`, `channels/{service,router}.py`, `checkimports/{service,router}.py`, `settings.py`, new `keys.py`; scheduler `probes/secrets.py`, `probes/tasks.py`, `alerts/tasks.py`, `settings.py`; compose ×3, `.env.example`, `justfile` |
| Services | API, scheduler |
| Schema / migrations | None — the key id lives inside the sealed bytes |
| UI | None |
| Tests | new py-platform `test_sealing.py` (the sealing test moves out of `test_templating_and_windows.py`); new `apps/api/tests/test_keys_live.py`; settings tests in both services; existing secret, channel and second-factor suites keep passing |

## Reuse

- `SealedBox`'s purpose/owner binding, unchanged in meaning.
- `CredentialBox`'s 503-or-open behaviour.
- The `suspend.py` command shape and `just` recipes.
- `_payments_match_the_estate` as the shape of the production key check.

## Gaps

- A key id, a keyring, previous keys.
- A way to count and re-seal what each key holds.

## Steps and tasks

### Step 1 — a key id in every sealed value

*Judgeable:* a value sealed under key A opens with a keyring whose current key is B and whose
previous keys hold A; a value whose key is in neither, or in format v1, is refused.

- [x] **T1** — `sealing.py` · `Keyring(current, previous)`, `key_id(key)` = SHA-256(key)[:8],
  `keyring_from(current: str, previous: str)` (comma-separated, decoded, duplicates refused);
  `SealedBox(keyring, purpose)` seals `[2][key id][nonce][ct]` under the current key with the key id
  in the associated data, and opens by key id; v1 and unknown ids raise `SealError`;
  `sealed_key_id(sealed)` for the status command · `packages/py-platform/tests/test_sealing.py`
  (the existing sealing test moves here).

### Step 2 — both services read a keyring

*Judgeable:* with `CHECK_SECRET_KEY=B` and `CHECK_SECRET_PREVIOUS_KEYS=A`, a check secret and a
channel secret sealed under A are opened by the API and by the scheduler, and new ones are sealed
under B; the same for TOTP with the credential pair.

- [x] **T2** — API `settings.py` · `check_secret_previous_keys`, `credential_encryption_previous_keys`
  (str, default empty). Scheduler `settings.py` · `check_secret_previous_keys`.
- [x] **T3** — API callers: `secret_box`, `channel_box`, `credential_box`, `checkimports` take the pair;
  routers pass both settings · `test_channels_live.py`, `test_second_factor*.py` still green; new case
  in `test_second_factor.py`: an enrolment sealed under the previous key still signs in.
- [x] **T4** — scheduler callers: `open_secrets` and `_secret_box` take the pair; `probes/tasks.py` passes
  both · a probe test opening a secret sealed under the previous key.

### Step 3 — status and re-seal

*Judgeable:* on the dev stack, after rotating, `just keys-status` shows the values under the old key,
`just keys-reseal` moves them, and status then shows every value under the current key.

- [x] **T5** — API `keys.py` · `python -m nexovue_api.keys status|reseal`. The four columns are listed
  once (table, column, key, purpose, owner expression). Status: count per column by key id,
  `current` / `previous` / `unreadable`. Reseal: in batches of 500, open under the keyring and seal
  under the current key, `UPDATE … WHERE pk = … AND col = :old` so a value changed meanwhile is left
  alone; unreadable values are reported, never touched. Exit non-zero when anything is unreadable.
  `justfile` · `keys-status`, `keys-reseal` · `apps/api/tests/test_keys_live.py`: values under the old
  key are counted, re-sealed and still open to the same plaintext; a value changed between read and
  write is left alone; a v1 value is reported unreadable (red first).

### Step 4 — a deployed install has both keys

*Judgeable:* the API refuses to start with `ENV=production` and either key missing; the app and
on-prem compose files require `CREDENTIAL_ENCRYPTION_KEY`, so enrolment no longer answers 503 there.

- [x] **T6** — API `settings.py` · `_keys_are_set_in_production`, naming the variable. Scheduler
  `settings.py` · the same for `CHECK_SECRET_KEY` · settings tests in both, red first.
- [x] **T7** — `docker-compose.app.yaml`, `docker-compose.onprem.yaml` ·
  `CREDENTIAL_ENCRYPTION_KEY: ${…:?set …}` on the API; both `…_PREVIOUS_KEYS` passed (empty by
  default) to the API and, for the check key, the scheduler; dev compose the same with empty
  previous keys; `.env.example` documents all four · `test_compose_dependencies.py` if it covers env.

### Step 5 — the runbook, from a real rotation

- [x] **T8** — rotate both keys on the dev stack: a new current key, the old one previous, restart,
  status, reseal, status, remove the old key, restart; a check with a secret runs and a channel
  sends a test, and a two-factor sign-in works · `docs/operations/key-rotation.md` written from what
  was run.
- [x] **T9** — ADR-010 amended (the keyring), `current-system-reference.md`, readiness (blockers 8, 17,
  group 5), `status.md`.

## New risks

- Removing a previous key before re-sealing makes what it sealed unreadable. Status is the check, and
  the runbook puts it before the removal.
- Dev values sealed in v1 become unreadable on upgrade (founder decision); status names them.

## Questions

None blocking.

## Gates

- [x] Specification approval — not applicable
- [x] Design approval — the founder's 2026-10-11 decision
- [x] **Plan approval — before the first edited file** — 2026-10-11
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Documentation and status update

## Validation plan

Every regression test seen red first. `just check` with counts read. The live rotation in T8 is the
end-to-end proof.

## Rollback

Pre-deployment; `git revert`.

## Outcome

Built as planned in five steps; evidence in `status.md`.

What changed from the plan, and why:

- **Callers take a `ConfiguredKeys`** (the current key and the previous list, as configured), built
  by a `check_secret_keys` / `credential_keys` property on each service's settings, rather than two
  loose strings at every call site.
- **The dev compose keys became overridable** (`DEV_CHECK_SECRET_KEY` and friends, defaulting to the
  old constants) so the rotation could be rehearsed on the dev stack without editing the file.
- **`.env.example` had no entry for either key.** Both keys and both previous lists are documented now.
- **The dev stack held no sealed values at all**, so the v1 decision cost nothing to re-enter.
- **The full check exposed a flaky group 4 test,** fixed here. The live erasure test's tenants
  carried an enabled check that the dev stack really ran, so check results landed mid-run and the
  erasure (correctly) left the tenant owed. The fixture now removes the checks, as a real deletion
  does, and allows the second group that other suites' deletions cause.
