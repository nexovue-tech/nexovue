# ADR-010: Check secrets are sealed with their own key, and only their names travel through the queue

## Status

Accepted 2026-09-17, with the F18 design. **Amended 2026-09-17** by the F18 plan's Q1 — see *Amendment*. **Second
amendment accepted 2026-09-19** with the F21 design — see *Amendment — notification channel secrets*. Executed
in F18. **Third amendment 2026-10-11** (readiness group 5) — see *Amendment — a keyring*.

## Context

F18's checks use customer secrets — API tokens, basic-auth passwords, API keys, and credentials found during
import (spec FR19–FR22, BR10).
They must be write-only, encrypted at rest, never returned by any read, and **never carried in plaintext
through the scheduler's queue** (FR21, AC18).

Three facts constrain it:

- **Checks run in the scheduler, not the API.** And beat embeds each check's whole definition in the Celery
  task it dispatches, which sits in Redis. *(Since 2026-10-10, ADR-020: in a row of the Postgres `jobs`
  table.)*
- **`auth/encryption.py` already provides `CredentialBox`** — AES-256-GCM with associated data — used today
  for TOTP secrets under a key the API holds. It protects against a database dump, a backup or a read-only
  SQL injection; its own docstring says it does not protect against an attacker who owns the process holding
  the key.
- **The scheduler already reads the control plane directly** (`checks.py`, `pollers/integrations.py`).

## Decision

1. Check secrets live in a `check_secrets` table: project, name, sealed value.
2. They are sealed with `CredentialBox` under **a dedicated key**, not the TOTP key, **held by both the API and
   the scheduler**.
3. **Associated data binds each value to its project and name**, so a sealed value copied to another project
   or renamed fails to open.
4. ~~A task carries secret names only; the worker reads the sealed values from Postgres.~~ **Superseded by the
   amendment:** a task carries the secrets it uses **sealed**, and the worker opens them in-process immediately
   before use.
5. **Before anything is published or stored, every decrypted value is replaced** in every captured string —
   requests, responses, logs, error text (FR22). A minimum secret length stops a short secret redacting unrelated
   text everywhere.

## Rejected

- **Reusing the TOTP key.** One compromise would expose two unrelated kinds of secret, and rotating one would
  force rotating the other.
- **Decrypting in the API and sending plaintext in the task.** Puts every customer secret in Redis in the
  clear — exactly what AC11 forbids.
- **A KMS with envelope encryption.** Stronger against a process compromise, and the scheduler has already
  declined to invent one (`pollers/postgres_connect.py`). Worth revisiting with hosted deployment, when a
  cloud KMS exists to use.

## Consequences

- The scheduler gains a secret it did not hold before; its compromise exposes check secrets. That is the same
  exposure the API's key already carries for TOTP secrets, now in a second process.
- Key rotation must re-seal every value, and needs a procedure before hosted launch.
- Redaction is by value, so a secret that is reshaped before being sent — base64-encoded into a header, say —
  is not caught by exact replacement. The runner's captured output is limited to what is needed as evidence.

## Amendment — 2026-09-17: sealed values travel in the task

**What changed.** Decision 4 had workers read sealed secrets from Postgres on every run. That broke a rule already
recorded in `probes/tasks.py`: *beat hands the whole check definition over so a probe never touches Postgres*,
because a probe that cannot run while Postgres is briefly busy is a blind minute for a customer.

**Decision.** Beat reads the sealed values with the rest of the definition and puts them in the task, still sealed;
the queue holds ciphertext only, so AC18 holds. Run now does the same from the API. A check's confirmed state — the
one write a run causes — is a **separate task dispatched after the measurement is published**, so a slow database
delays a state change and never a reading.

**Why not names only.** Every run would put a Postgres read on the measurement path, which is the failure the
original rule exists to prevent.

**Consequence.** A replaced secret reaches runs at beat's next refresh, like any other edit. The sealed value is now
in the queue while a task waits — a row of the Postgres `jobs` table since 2026-10-10 (ADR-020), Redis before; it is
unreadable without the key, which the queue does not hold.

## Amendment — notification channel secrets (accepted 2026-09-19, F21)

**What changes.** F21's notification channels hold secrets too: Slack, Teams and Discord webhook URLs (the URL *is*
the credential), PagerDuty routing keys, and the signing secret of a generic webhook. They are sealed with the same
`SealedBox` and the same key, under their own purpose, `notification-channel`, with **associated data binding each
value to its organization and channel id**, so a sealed value copied to another channel fails to open.

**Why the same key rather than a third.** The process that opens them is the one that already holds this key — the
scheduler, at delivery — and the API seals them at save, as it does for check secrets. A separate key would add a
rotation procedure without separating anything: both keys would sit in the same two processes.

**What does not change.** Write-only; never returned by a read; replaced, never read back; never logged. Rotation
now re-seals channel secrets as well as check secrets, which the rotation procedure this ADR already requires must
cover.

## Amendment — a keyring (2026-10-11, readiness group 5)

Each key was one key with no id, so replacing a leaked one made everything it sealed unreadable at
once (readiness blocker 8). Every sealed value now names the key that sealed it: format 2, which is
`[2][first 8 bytes of SHA-256(key)][nonce][ciphertext]`, with the key id in the associated data. A
keyring holds the current key, which seals, and previous keys (`CHECK_SECRET_PREVIOUS_KEYS`,
`CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`), which only open. `python -m nexovue_api.keys status|reseal`
counts and moves what each key holds. The procedure is `docs/operations/key-rotation.md`. Format 1 is
not read: nexovue was pre-deployment, and the development stack held no sealed values (founder
decision). Production refuses to start without either key.
