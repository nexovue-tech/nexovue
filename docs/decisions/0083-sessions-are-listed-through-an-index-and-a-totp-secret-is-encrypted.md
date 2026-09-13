# 0083 — Sessions are listed through an index, and the one credential we cannot hash is encrypted

- **Status:** Accepted
- **Date:** 2026-09-14
- **Relates to:** migration 0019, whose refusal of a per-user session index is **narrowed, not reversed**; migration 0006, which deleted a column named `*_encrypted` that was not; `scheduler/pollers/postgres_connect.py`, which declined to invent envelope encryption

## Context

T64 asked for two things a customer has been unable to do: see what is signed in
as them, and hold a second factor. Both ran into decisions already on record.

## Listing sessions needed something 0019 refused

Sessions live in Redis under `session:<sha256(token)>`. Nothing could enumerate
them, and that was deliberate: migration 0019 considered a per-user index and
rejected it in favour of the `users.sessions_valid_from` epoch, which revokes
every session at once by moving a timestamp rather than by finding keys.

**That argument is about revocation and it still stands.** Ending every session
remains an epoch bump; no index is consulted and none can be stale enough to
matter. Listing is a different question with no equivalent trick — there is no
way to name what exists without something that records what exists.

So a sorted set `session-index:<user_id>`, scored by each session's expiry.
0019's two objections are answered rather than dismissed:

- **Cost.** One `ZADD` on sign-in, the rarest authenticated request there is.
- **Drift.** The index is a cache and the key is the truth. Expired members are
  trimmed by score, and a member whose key has gone is dropped on read. An index
  that disagrees under-reports; it can never invent a session, and it can never
  keep one alive.

**Scoping is by construction rather than by a filter.** The index key is built
from the user id the caller's own session names, and ending one checks
membership of that index before deleting anything. A session id that is real but
belongs to somebody else gets the same 404 as one that never existed.

## A TOTP secret cannot be hashed, so it is encrypted

Everything else here is hashed — passwords with argon2, API keys and session
tokens with SHA-256 — because nothing needs the original back. Verifying a
six-digit code means recomputing an HMAC with the *same* secret the
authenticator holds, so it has to survive in a recoverable form or the feature
cannot exist.

There was no encryption facility to reuse, and the two nearest records both say
why not: 0006 removed `integrations.config_encrypted` **because it was not
encrypted** — a name that promised what the code did not do — and the scheduler
declined to invent envelope encryption for a credential it could avoid storing.

This is the case neither of those covers, so it is the minimum honest thing:
AES-256-GCM, one key from `CREDENTIAL_ENCRYPTION_KEY`, **the user's id
authenticated as associated data** so a ciphertext copied into another row fails
to open. The column is `totp_secret`, not `totp_secret_encrypted`: 0006's lesson
is that the name is not the guarantee.

**What it buys, stated so nobody expects more.** A dump, a backup, a replica or a
read-only injection yields ciphertext. An attacker who owns the API process reads
the key and therefore the secrets — at which point the session store is theirs
anyway. A missing key is a 503 from the endpoints that need one, never a secret
stored in the clear.

Recovery codes take argon2 rather than the API-key SHA-256 path, on the line
`credentials.py` already draws by entropy: ten characters copied off paper is
about fifty bits, which a fast hash does not protect offline.

## The privacy decision, which is the part that is not obvious

**The reader of a session list may be the attacker.** It exists for somebody who
suspects another person is signed in — and if that person is, they are shown the
same screen.

So a User-Agent is reduced to "Chrome on macOS" *at arrival* and the raw string
is never written: build numbers and device models are a cross-site fingerprint
and tell an intruder what hardware their target uses, while helping the owner
recognise nothing. An address is kept as its network only (`/24`, `/48`), because
a precise one is a household or an office and publishing it to an intruder is a
physical-safety matter. The network still separates "my usual connection" from
"somewhere else", which is the question being asked. Abuse investigation belongs
to the request log, under a different retention regime.

## Consequences

- `CREDENTIAL_ENCRYPTION_KEY` is the repository's first encryption key and
  **has no rotation procedure written**. Rotation is a re-encrypt of every
  `totp_secret`, which is a job nobody has designed. Until it exists, losing the
  key means every enrolled account falls back to recovery codes.
- A recovery code still works with no key configured, deliberately: a key outage
  must not strand every enrolled account.
- The second factor is not covered by a production-readiness review.
  `docs/engineering/security.md` requires one before a rollout.
- pyotp's own documentation recommends WebAuthn for greenfield products. Noted
  and not acted on: TOTP is what was asked for, and WebAuthn is a larger decision
  about hardware and about recovery.
