# 0081 — A leaked key has a way out, and `last_used_at` is throttled by the credential cache

- **Status:** Accepted
- **Date:** 2026-08-31
- **Resolves:** the `last_used_at` follow-up in [phase-1-plan §7c](../../delivery/phase-1-plan.md), which said the column "is never written" and asked for a cheaper shape

## Context

**A customer whose ingest key leaked could not recover.** `apikeys/router.py` served exactly two
things — `GET ""` and `POST /{key_id}/revoke` — and the only code in the system that created an
`ApiKey` row was `signup/service.py`, during onboarding. So the choice on a leak was: leave the
live key live, or revoke it and stop your own telemetry **permanently**, because nothing could mint
a replacement. The service module's own docstring said "the replacement for a revoked key is a new
key" while no route could produce one.

**The published rotation lifecycle needs two things we did not have.** Verified rather than
asserted, and the brief that commissioned this work was wrong on the numbers:

- [GitGuardian](https://blog.gitguardian.com/api-key-rotation-best-practices/) gives the order —
  "create and deploy a new key before revoking the old one" — and the verification step: "monitor
  logs to ensure that the new key is being used… Once the new key is being used by your
  application, you can revoke the old key." **It states no overlap length and does not distinguish
  the leaked case.**
- [Zuplo](https://zuplo.com/learning-center/api-key-rotation-lifecycle-management) gives the
  overlap: "**7 to 30 days is typical**, depending on your consumer base", and "Set an expiration
  date on the old key rather than revoking it immediately". For exposure it inverts: "If a key is
  detected in a public repository, rotate immediately."
- The leak exception is well supported in secondary sources
  ([Custodia](https://custodia.dev/blog/api-key-exposed-in-code),
  [OpenReplay](https://blog.openreplay.com/api-keys-repo/)): revoke first, security over a smooth
  transition, because scanners find a public key within minutes.

The "24 hours to 7 days" figure this work was briefed with **appears in neither cited source**. It
comes from secondary write-ups ([Moments Log](https://www.momentslog.com/development/secrets-rotation-checklist-how-to-rotate-api-keys-without-breaking-production),
[darkbot](https://darkbot.io/blog/api-key-rotation)). Recorded here because the correction is the
kind that silently propagates once it is in a comment.

**The middle step — "confirm nothing is using the old key" — was unavailable.** `api_keys` has had
a `last_used_at` column since [migration 0001](../../../apps/api/migrations/versions/0001_control_plane_schema.py)
and **nothing has ever written to it**. The column was not missing; the write was. (A brief for
this work reserved migration 0012 to add the column. Checking the live schema instead of taking
that on trust is what stopped a duplicate column being created — and 0012 has since been taken by
another change anyway.)

The reason it was never written is real: the receiver authenticates on **every** export, and a row
update per request is not affordable on the ingest path.

## Decision

**Three decisions, and the second is the one with a real alternative.**

### 1. Create is a first-class action, and the plaintext exists exactly once

`POST /api/v1/api-keys` mints a key for one of the caller's own projects, leaving every existing
key live — which is what makes an overlap possible at all. Tenancy goes through `require_project`
like every other write; `org_id` comes from the session, never the body. Gated to owner/admin, the
same set that may revoke: minting grants ingest for the whole organization.

**The plaintext is a `SecretStr`, not a `str`.** Repr, `str()`, f-strings and `model_dump()` all
render `**********`; one named `field_serializer` discloses it into the create response body and
nothing else can. The redaction travels with the value rather than depending on every holder
remembering — which matters because the failure it prevents had already happened: a
`LiveTenant` dataclass in the live isolation suite printed a **working** ingest key into a pytest
failure log, because a plain `str` field lands in the dataclass's own repr and pytest prints that
verbatim on any failing assertion. The same shape was live in `ProvisionedTenant` and
`SignupResponse`. All four are now `SecretStr`.

### 2. `last_used_at` is throttled by the credential cache, and written off the request path

**The cache is the throttle.** The receiver records a use only when the credential came from a
fresh Postgres read — a cache miss — so a cached hit writes nothing. There is no second timestamp
map and no clock of its own: the write rate is bounded at **one per key per cache TTL per receiver
process** by construction rather than by arithmetic somebody has to keep correct.

**The write is not inside the request.** `Record` hands the prefix to a buffered channel and
returns; one goroutine drains it, started as `go keyUses.Run(ctx)` beside the existing traffic
summary. A full queue drops rather than blocks.

**The write is gated on the hash comparison passing** — this is a security property, not a detail;
see the first alternative below.

The statement is guarded so it can only move forward, because several receivers each hold their own
cache and their writes arrive unordered:

```sql
UPDATE api_keys SET last_used_at = $2
WHERE key_prefix = $1 AND revoked_at IS NULL
  AND (last_used_at IS NULL OR last_used_at < $2)
```

`revoked_at IS NULL` because a withdrawn key is not in use, and a stamp on one would read in the
console as still live.

### 3. Safe-to-revoke is 24 hours of silence, with a middle state that says "not yet"

Four traffic states, chosen for the decision in front of the customer rather than as status words:

| state | meaning |
|---|---|
| **Never used** | Nothing ever authenticated with it. Revoking stops nothing. |
| **In use** (< 1 h) | Revoking stops a running service. |
| **Going quiet** (1–24 h) | Not evidence of anything yet. Wait. |
| **No traffic** (≥ 24 h) | This is what "verified no traffic" looks like. |

Under an hour of silence is not evidence — a low-traffic service batches, a cron runs hourly, a
staging environment sleeps overnight — so the middle state exists to stop somebody reading twenty
minutes of quiet as a clearance.

**24 hours is the short end of the published 7–30 day range, and this product can afford it because
it can *see* traffic rather than assume it.** The range exists for providers who cannot observe
their consumers and must wait out a distribution of unknown deploy cadences. We are reading the key's
own authentications. Both thresholds sit far above the timestamp's resolution — the coarseness
cannot move a key between states.

The screen offers **Rotate** and **Revoke now — this key leaked** as separate, differently-worded
actions, and the leak confirmation says plainly that ingest stops until a replacement is deployed.
The exception has to be visible in the product, not just in a document.

## Alternatives considered

**Fold the update into the lookup: `UPDATE … RETURNING org_id, project_id, key_hash`.** This costs
**zero** extra round trips — the miss path already queries Postgres, so the write would be free. It
is the obvious optimisation and it is wrong, for a reason that is not obvious.

The statement runs **before** the hash comparison. Anyone who guesses a live key *prefix* — sixteen
characters, of which seven are secret, far cheaper than guessing the whole key — could then keep
`last_used_at` moving on a key they cannot use. A customer following the rotation procedure would
check that their old key had gone quiet, see recent traffic, and **be reading the attacker's probing
rather than their own services**. They would then either keep a leaked key alive indefinitely, or
conclude the reading is untrustworthy and go back to guessing.

That inverts the entire point of the column. **A security property was hiding inside a performance
decision**, and the cheaper design would have shipped looking correct.

**Write synchronously on the cache-miss path.** Three lines, no goroutine, no lifecycle, no drops.
The amortised cost is genuinely negligible — one extra round trip on roughly one request in a
thousand. Rejected on posture rather than on cost: the ingest path already states that a
control-plane hiccup must not cost telemetry — the rate limiter **fails open** when Valkey is
unreachable, on the reasoning that "losing a customer's telemetry costs more than briefly losing the
protection". A `last_used_at` write is bookkeeping. Blocking an export on it, even one in a
thousand, contradicts a decision this codebase has already made deliberately. The async version also
lets a write failure be dropped without touching the request at all.

**A throttle of its own — a `prefix → next-eligible` map with its own interval.** Rejected as
redundant state. The cache already refreshes at exactly the cadence wanted, so a second map would be
a second thing to bound, a second thing to expire, and a second answer to "how stale may our view of
this key be" that could drift from the first.

**Default to the published 7–30 day overlap.** Rejected for the reason in decision 3: that range is
sized for providers who cannot observe usage. Quoting it here would tell a customer to wait a week
while the screen is already showing them the answer.

**`field(repr=False)` on the leaking dataclass field.** The one-line fix, and it works — for that
one declaration. It does not travel: assign the field to a local and print the local and the key is
back. `SecretStr` makes the value itself unprintable and makes disclosure a method call you can grep
for.

## Consequences

**The throttle is structural, and it was measured rather than reasoned.** Against the running dev
stack under continuous demo traffic — thousands of exports over 80 seconds — `last_used_at` took
exactly four distinct values:

```
06:18:52.591399   06:19:22.594065   06:19:52.596718   06:20:22.607955
```

**Exactly 30.0 s apart**, matching `RECEIVER_API_KEY_CACHE_TTL=30s`. One write per key per TTL per
receiver, observed, not estimated.

**The reading is coarse, and the screen says so.** A key in constant use reads as "seconds ago" and
never as "now". The console carries that sentence rather than leaving someone to discover it: "Last
use is recorded when a receiver re-reads the key, so it lags live traffic by up to a minute."

**The receiver now writes to a table the API owns.** It previously only read `api_keys`. This is a
second writer to control-plane Postgres from the ingest path, confined to one column that no other
writer touches, on a statement that can only move the value forward. Named here because "the API
owns Postgres" is a rule in `apps/api/CLAUDE.md` and this is the exception to it.

**Timestamps are abandoned at shutdown, not flushed.** `Run` returns on context cancellation with
whatever is queued. At most a few seconds of imprecision on an already-coarse value, and holding a
drain open against a database that may be the reason we are shutting down is the worse trade.

**Drops are silent.** A full queue means the writer has stalled, in which case the write would not
have landed anyway. There is no counter, because a counter nothing reads is dead code — if this ever
needs to be visible it belongs on the existing traffic heartbeat.

**Minting is shared with signup rather than duplicated.** `allocate_unused_key` — generate, check
the globally-unique prefix, retry, bound the retries — now lives in `apikeys/service.py` and signup
calls it. Two implementations of "is this prefix free?" would be two answers to a question the
unique constraint only answers one way. `signup/service._unused_api_key`, `signup.MAX_KEY_ATTEMPTS`
and `signup/queries.key_prefix_is_taken` were deleted in the same change.

**A copied SQL statement is now checked against its source.** The live suite asserted revocation
behaviour by running a hand-copied version of the receiver's lookup. That copy could drift, and
every assertion would keep passing against a statement nothing runs. Both borrowed statements are
now compared against the `const` blocks parsed out of `postgres.go`, normalising `$1` against
`:name`. This was a **pre-existing** gap that the new write would have doubled.

**Not verified.** Three things. The receiver's behaviour under a **real multi-receiver deployment**
is reasoned from the forward-only guard, not observed — only one receiver runs here, so out-of-order
writes were exercised through the statement in the live suite rather than by two processes racing.
The `MarkUsed` path against a **revoked** key was likewise proven through the copied statement, not
against the live receiver. And the console's four-state reading has been seen with **Never used** and
**In use** only; **Going quiet** and **No traffic** are unit-tested at their boundaries but have not
been watched against a key that actually fell silent for a day.
