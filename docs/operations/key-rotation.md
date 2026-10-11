# Key rotation

Replacing `CHECK_SECRET_KEY` (check and alert-channel secrets) or `CREDENTIAL_ENCRYPTION_KEY`
(two-factor secrets) without losing what either sealed. Do it on a schedule, and at once if a key may
have leaked.

## Current verified behavior

Executed on the development stack on 2026-10-11 (`docs/changes/keys-can-be-rotated/`), for both keys
at once, and then again in reverse to return to the dev defaults. At every step, a two-factor
sign-in, a Run now that used a check secret, and a webhook send test, which signs with a sealed
secret, all worked.

Every sealed value names the key that sealed it. The current key seals; keys listed as previous only
open. So a rotation never makes anything unreadable while the old key is still listed.

## Procedure

Run `just keys-status` and `just keys-reseal` against the stack being rotated. On a deployed install,
run the same commands inside the API container:
`docker compose exec api python -m nexovue_api.keys status|reseal`.

1. **Generate the new key.**
   `uv run python -c "from nexovue_platform.checks.sealing import generate_key; print(generate_key())"`
2. **Make it current and keep the old one as previous.** For the check key, set it in both the API
   and the scheduler, because both read it:
   - `CHECK_SECRET_KEY=<new>`
   - `CHECK_SECRET_PREVIOUS_KEYS=<old>`

   For the two-factor key, set it in the API only:
   - `CREDENTIAL_ENCRYPTION_KEY=<new>`
   - `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS=<old>`

   On the dev stack these are `DEV_CHECK_SECRET_KEY`, `DEV_CHECK_SECRET_PREVIOUS_KEYS`,
   `DEV_CREDENTIAL_ENCRYPTION_KEY` and `DEV_CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`.
3. **Restart** the API, `scheduler-beat`, `scheduler-light` and `scheduler-deliveries`. New values
   are now sealed with the new key, and old values still open.
4. **`just keys-status`.** Every existing value shows as `previous`.
5. **`just keys-reseal`.** Each value is re-sealed with the new key. The run says how many it moved
   and leaves alone any value changed while it ran. Run it again until `previous` is 0.
6. **Wait a few minutes.** Jobs queued before step 3 carry values sealed under the old key. Beat
   rebuilds every check's job from the database every 30 s, so these drain within minutes.
7. **Remove the old key.** Empty `…_PREVIOUS_KEYS` and restart again.
8. **`just keys-status` once more.** Everything must be `current`, with `unreadable` at 0. The
   command exits non-zero if anything is unreadable.

**Never remove a previous key while status shows values under it.** Those values become unreadable:
the checks that use them fail as prober errors, the channels cannot deliver, and the two-factor users
must sign in with a recovery code.

## Not yet established

- The keys live in an environment file on the host. An external secrets manager is a readiness
  recommendation (`production-readiness.md`).
- No rotation schedule is set yet.
- The rotation has not been executed against a deployed install, because none exists yet.
