# Change: keys-can-be-rotated — a leaked encryption key can be replaced without losing what it sealed

## Classification

Security. Readiness group 5: blockers 8 and 17 (`docs/operations/production-readiness.md`).

## Current behavior and problem

Read from the code:

1. **Each key is one key with no id (blocker 8).** `SealedBox`
   (`packages/py-platform/src/nexovue_platform/checks/sealing.py`) writes
   `[version 1][nonce][AES-256-GCM ciphertext]` under the single key it was built with. Nothing in the
   value says which key sealed it, and every caller builds its box from one variable:
   - `CHECK_SECRET_KEY` seals check secrets (`check_secrets.sealed_value`, API
     `checksecrets/service.py:65`, opened by the scheduler `probes/secrets.py:37`) and notification
     channel secrets (`notification_channels.sealed_secret`, API `channels/service.py:88`, opened by
     the scheduler `alerts/tasks.py:141`). Sealed check secrets also travel inside queued jobs
     (`scheduler/checks.py:166`).
   - `CREDENTIAL_ENCRYPTION_KEY` seals TOTP secrets (`users.totp_secret`, `totp_enrolments.sealed_secret`;
     API `auth/encryption.py`).

   Replacing a leaked key makes every check secret, every Slack/Teams/Discord/PagerDuty/webhook
   channel and every two-factor enrolment unreadable at once. So a leak cannot be answered.
2. **Two-factor sign-in cannot be enabled on a deployed install (blocker 17).**
   `CREDENTIAL_ENCRYPTION_KEY` is not passed by `docker-compose.app.yaml` or
   `docker-compose.onprem.yaml`, so enrolment answers 503 (`auth/dependencies.py:299-301`). Both keys
   are optional at boot in every environment (`settings.py:213,220`), so a production install can
   start without either and nobody is told until a customer hits the 503.

## Intended behavior

The standard pattern, as Rails (`store_key_references`), Lockbox and GitLab's key rotation use it:
**a key id in every sealed value, one current key that seals, previous keys that only open, and a
re-seal pass**.

1. **Format v2:** `[2][key id: first 8 bytes of SHA-256(key)][nonce][ciphertext]`. The id is
   derived from the key, so an operator never manages ids and cannot mislabel one. The id is also
   bound into the associated data, so a value cannot be pointed at another key.
2. **A keyring per key.** `CHECK_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` stay the key that
   seals. New `CHECK_SECRET_PREVIOUS_KEYS` and `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS` (comma-separated)
   only open. A value whose key id is in neither is refused, as today.
3. **Re-seal and status, as an operator command in the API image:**
   `python -m nexovue_api.keys status` counts the sealed values in each of the four columns by key id
   (current, previous, unknown). `python -m nexovue_api.keys reseal` re-seals, in batches, every value
   not under the current key. Run once after a rotation; when status shows nothing under the old key,
   the old key is removed from `…_PREVIOUS_KEYS`. Jobs already queued carry values sealed under the
   old key; the scheduler keeps it as a previous key until they drain (minutes — beat rebuilds every
   job from the database every 30 s).
4. **Production refuses to start without either key (blocker 17).** Both compose files for deployed
   installs pass both keys, required. Development and test keep starting without them, with the
   routes answering 503 as now.
5. **A runbook, written from a rotation actually executed on the dev stack:**
   `docs/operations/key-rotation.md`.

**Founder decision (2026-10-11): v2 only.** The v1 reader is deleted, and `keys status` reports v1
values as `unreadable`. Nexovue is pre-deployment, so v1 values exist only in development databases,
where the few there are re-entered: check secrets, channel secrets, and a two-factor enrolment, where
the sign-in falls back to a recovery code or `just seed`.

## Affected specifications and architecture

- ADR-010 (secrets sealed in the API, opened in the worker): amended with the keyring.
- `docs/architecture/current-system-reference.md`, the security section.
- Readiness document: blockers 8 and 17, group 5.
- Compose ×3 and `.env.example`: the previous-key variables; `CREDENTIAL_ENCRYPTION_KEY` in the
  deployed files.

## Risks and rollback

- **Removing a previous key too early** makes values sealed under it unreadable. `keys status` is the
  check before removal, and the runbook says so.
- **The re-seal writes to user rows.** It touches only the sealed column, compares the old value in
  the `UPDATE` so a value the customer changed meanwhile is left alone, and is idempotent.
- Rollback: pre-deployment, `git revert`.

## Plan and validation

`plan.md`.

## Status

`status.md`.
