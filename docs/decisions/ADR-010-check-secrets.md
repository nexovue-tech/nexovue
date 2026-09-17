# ADR-010: Check secrets are sealed with their own key, and only their names travel through the queue

## Status

Accepted 2026-09-17, with the F18 design. **Amended 2026-09-17** by the F18 plan's Q1 — see *Amendment*. Executed
in F18.

## Context

F18's checks use customer secrets — API tokens, basic-auth passwords, API keys, and credentials found during
import (spec FR19–FR22, BR10).
They must be write-only, encrypted at rest, never returned by any read, and **never carried in plaintext
through the scheduler's queue** (FR21, AC18).

Three facts constrain it:

- **Checks run in the scheduler, not the API.** And beat embeds each check's whole definition in the Celery
  task it dispatches, which sits in Redis.
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
in Redis while a task waits; it is unreadable without the key, which Redis does not hold.
