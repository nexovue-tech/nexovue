# Design: remaining-recommendations (readiness group 9)

## Status

Approved 2026-10-11. **Founder decisions:** every ⚑ recommendation taken (A1 no email on a lock;
A3 operator password set once; B3 masking stays in the worker; B4 three brokers decided in group 8;
C2 export digests for resends; D2 export and self-delete as written, specified first; D4 sampling
default 1). **D6 plan prices: later**, by the founder — nothing built for it.

## Context and goal

Group 9 is every item in `production-readiness.md` *Recommended* that no other group carries — the
founder chose to fix them all in this round (2026-10-10). Four read-only surveys read each one against
the code on 2026-10-11. This design says, per item, what is wrong, the fix, what it touches and how it
is proved. It also corrects the readiness document where the code disagreed with it.

**Findings the surveys added** (not in the readiness document):

- **API tokens and people share one in-flight counter.** The limiter's concurrency key omits the tier
  (`ratelimit/limiter.py:126`), so an organization's tokens and its people's telemetry reads share one
  ceiling of 32: a script can starve the console, and the reverse.
- **A support-view ticket is written to the uvicorn access log** — `GET /support/view-as?ticket=` puts
  it in the query string, and redaction matches keys, not free text.
- **A sender's resend is stored twice** whenever the receiver's own retry gives up — not only on the
  5 s timeout. `current-system-reference.md:192` says the opposite.
- **The API's 15 s ClickHouse limit never applies**: `READ_SETTINGS` (30 s) is sent per query and wins
  (`data/clickhouse/base.py:84`).
- **Deploy-webhook replays race**: two identical deliveries at once hit an uncaught `IntegrityError`
  (500); the ORM model is missing the migration's unique index.
- **Removing a member keeps their email in `audit_log.detail`.**

**Corrections to the readiness document:** heartbeat tokens are already redacted from every log line
we write (`telemetry/redaction.py:36`, tested by `test_logging.py:109`), so that item narrows to the
duplicate operation id; `style-src 'unsafe-inline'` stays by decision (Razorpay's checkout, group 6);
API tokens outliving their creator is decided (ADR-017, spec BR9/D3); tenant deletion cost was
weighed and accepted in ADR-014.

## Proposed design

Four parts, each a step of the plan. **⚑** marks a founder decision, with the recommendation.

### Part A — Security

**A1. Login lockout per account.** Today: 10 attempts a minute per address and per email, every attempt
counted, nothing per account — ~14,400 guesses a day against one account from rotating addresses.
*Fix:* count **failed** sign-ins per account in Postgres (`users.failed_logins`,
`users.locked_until`, one migration). After 5 consecutive failures the account refuses sign-in for
1 minute, doubling per further failure to a 15-minute cap (the AWS Cognito shape OWASP cites); a
success resets it. Temporary by design, so an attacker can delay a person but never lock them out for
good. The refusal reads the same as a wrong password, so it reveals nothing. Unknown emails are
counted against nothing and stay indistinguishable. Touches `auth/service.authenticate`, the second
factor's failure path, a migration. Proved by `test_auth_lockout_live.py`.
⚑ *Whether a lock emails the account's owner.* Recommended: **no** — it would let anyone send mail to
any address; the audit row `session.denied` already records each attempt.

**A2. Cross-site requests.** No middleware checks `Origin` or `Sec-Fetch-Site`; the session cookie is
`SameSite=Lax`, which a sibling `*.nexovue.com` host can still ride. *Fix:* the Go 1.25
`CrossOriginProtection` rule, ported as a pure ASGI middleware: an unsafe method (`POST`, `PUT`,
`PATCH`, `DELETE`) **that carries the session cookie** is refused with 403 when `Sec-Fetch-Site` is
`cross-site`/`same-site`, or, without it, when `Origin`'s host differs from `Host`. Requests with
neither header pass (non-browser callers). Requests without the cookie pass — webhooks, heartbeats,
API tokens and the MCP endpoint authenticate otherwise. The processor's cross-site
`POST /billing/checkout/return` carries no state change and is exempted by name. Touches
`middleware/` and `stack.py`. Proved by `test_cross_origin.py`, one case per row above.

**A3. Operator admin mode and password.** Admin mode accepts a password *or* a code even when the
operator has two-factor enrolled (`superadmin/adminmode.py:110-125`), and the operator's password is
rewritten from `SUPER_ADMIN_PASSWORD` every boot (`superadmin/seed.py:119-131`) — undoing a password
changed in the console, and un-removing a removed operator. *Fix:* with two-factor enrolled, only a
code enters admin mode. ⚑ *The boot rewrite.* Recommended: **set the password only when the operator
is created**, as the first owner already is; afterwards it changes in the console or by reset, and
the seed no longer clears `removed_at`. Touches `adminmode.py`, `seed.py`. Proved by new cases in
`test_admin_mode_live.py`; `test_super_admin_seed_live.py:136` changes its assertion.

**A4. Deploy-webhook replays.** The delivery id is optional, so a captured signed request without it
records a new deploy every time; the comment says a replay returns the stored deploy while the code
raises 409; and two identical deliveries at once hit an uncaught `IntegrityError`. *Fix:* the billing
webhook's shape (`billing/service.py:96-99, 167-173`): a delivery without `X-GitHub-Delivery` is
refused; a repeat answers 200 with the deploy already stored; the insert catches the unique violation
and answers the same; the model gains the index. Touches `deploys/`, `models.py`. Proved by
`test_deploys.py` (its replay test changes from 409 to 200) and a concurrent case in
`test_deploys_live.py`.

**A5. The database poller names our network.** A refused poll stores "db.internal resolved to
10.x.y.z…" in the customer-visible `last_error` (`pollers/postgres_connect.py:203,212`), and any
connect error's text goes there too. *Fix:* the customer reads a fixed reason — the probes' own
`REFUSAL_REASON`, and for other failures a classified sentence (refused, timed out, authentication
failed) with no address; the full text stays in our log. Touches `pollers/postgres_connect.py`,
`pollers/tasks.py`, `pollers/health.py`. Proved by `test_pollers_auth.py`, whose assertions flip from
"names 10.0.0.1" to "names no address".

**A6. Smaller security items.** The heartbeat route becomes a `GET` and a `POST` handler (the billing
router's pattern), ending the duplicate operation id. The support-view ticket is redacted from the
access line by adding `ticket=` to `redaction.py`. The token in-flight counter is keyed per tier (see
D5).

### Part B — Data safety

**B1. Credentials in URLs.** No masking rule reads a URL; `url.full` with
`https://user:secret@host/?X-Amz-Signature=…` is stored as sent. *Fix:* a new default value family,
`url_credentials`, in `mask/rules.go`: in any string value that parses as a URL, the userinfo
password and the values of a fixed list of signing parameters (`X-Amz-Signature`,
`X-Amz-Credential`, `X-Amz-Security-Token`, `X-Goog-Signature`, `sig`, plus `access_token`,
`token`, `api_key`, `apikey`) become `[masked]`. It meets ADR 0059's bar — no developer debugs with a
signature — and the parameter list is the one the scheduler and the browser SDK already share, now
joined by Go in `test_cross_service_contracts.py`. The API's family list gains the name. Proved by
`url_test.go` (masked, and ordinary URLs untouched) and the contract test.

**B2. CSV formula injection.** Three exports write customer text raw (`logs/search.py:305`,
`traces/service.py:732`, `billing/service.py:658`), and the console builds the same log file itself
(`log-export.ts:30`). *Fix:* OWASP's rule — a cell beginning with `=`, `+`, `-`, `@`, tab, CR or LF
is prefixed with `'` — in one Python helper the three writers call and in `log-export.ts`, which
must open identically. Proved by a case in each export's test.

**B3. Unmasked telemetry in Kafka.** Masking runs in the worker, so `*-raw` and `*-dlq` hold unmasked
telemetry for 7 days. ⚑ Recommended: **keep masking in the worker** and state the bound: the copies
age out within 7 days, are never backed up (`backup.sh` copies Postgres and ClickHouse only), and the
broker's disk is the host's to encrypt (group 8). ADR-014 gains that sentence for erasure. Moving
masking into the receiver means sharing the mask package across modules, a rules cache in the
receiver, and decoding and re-encoding logs and metrics on the request path — rejected below.

**B4. One copy of every record in Kafka.** `topics.sh` defaults to replication 1 and no
`min.insync.replicas`; the broker is one node. *Fix now:* `topics.sh` sets `min.insync.replicas` to
replication − 1 whenever replication is above 1, and says why. ⚑ *Running three brokers* is the
hosting target's (group 8) — recommended to decide it there, since it triples the disk.

### Part C — Reliability

**C1. A short Postgres outage stops ingest.** The key cache never serves an expired entry
(`cache/cache.go:80`), so 30 s into an outage every key 503s; overflow past 10,000 entries clears
everything, hits included, so a flood of invented keys also evicts real ones. *Fix:* on a lookup
**error** (never a miss), serve the expired hit for up to `RECEIVER_API_KEY_MAX_STALE` (default
10 min), counted on `/metrics`; misses get their own ceiling, so invented keys can only evict other
misses. The allowance gate already fails open the same way (`gates.go:150-160`). A revoked key then
works for up to the max-stale window *during an outage only*. Proved by `cache_test.go` and
`auth_test.go` cases.

**C2. A resend is stored twice.** ⚑ The largest item. Recommended: **the receiver stamps each record
with a digest of the export it came from** (tenant and body hash), and the worker keeps the digests it
stored in the ClickHouse KeeperMap it already uses for watermarks, for 15 minutes; a record whose
export digest is already stored is acknowledged and skipped. It holds across receiver replicas and
worker restarts, and costs one lookup per batch. Counters and usage fall out of the same skip. The
alternative — `ReplacingMergeTree` on `spans` and `logs` — changes the engines of the two largest
tables and still double-counts every derived rollup. `current-system-reference.md:192` is corrected
either way.

**C3. AWS pollers have no timeouts.** botocore's 60 s defaults outlive the task's 30 s soft limit, so
a hung call reports "passed its soft time limit" rather than the AWS cause. *Fix:* the STS and
CloudWatch clients take `Config(connect_timeout=5, read_timeout=10, retries=standard, 2)`, as the
check-file client already does. Proved by a `test_pollers.py` case on the client's config.

**C4. Read timeouts.** The API's `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` (15 s) is overridden by
`READ_SETTINGS` (30 s) on every read. *Fix:* **one value**: the setting is deleted, and 30 s —
which the reader profile already enforces server-side — is stated as the bound. Proved by
`test_settings.py` (the variable is gone) and the existing reader-profile tests.

**C5. Dead letters have no tool.** *Fix:* a `just dlq` command (Go, in the workers module, using the
`kadm` client group 7 added): `list` shows each parked record's reason, tenant, source offset and
age; `replay --topic … --from … --to …` re-produces the original bytes to their source topic. A
runbook, `docs/operations/dead-letters.md`, written from a replay actually run on dev. A replayed
record goes through masking as any other.

**C6. Migrations on a grown table.** No migration sets `lock_timeout`, and none builds an index
concurrently. *Fix:* `env.py` sets `lock_timeout = '5s'` per migration (a migration waiting on a lock
fails instead of stalling every write), and `docs/engineering/` gains the rule that an index on an
existing table is built `CONCURRENTLY` in its own non-transactional migration. The downgrades of
`0020`, `0022`, `0026` raise with "roll forward only: this drops billing records". Proved by a
migration-lint test reading every revision.

### Part D — Product and console

**D1. Billing currency.** The screen quotes USD when the account takes only INR. *Fix:* the quoted
currency is the first **offerable** one — requested, then the subscription's, then the default if
offerable, then the first offerable. Proved by a `test_billing.py` case (INR-only account → INR).

**D2. Personal data — export and self-deletion.** Nothing lets a person export or delete their own
data, and removing a member keeps their email in the audit log. ⚑ *Scope.* Recommended:
- **Export:** `GET /profile/export` returns one JSON file of everything held about the person —
  profile, sessions (client and network), their audit entries, tokens they made, invitations.
- **Delete my account:** a person leaves the organization themselves: the member removal that exists,
  plus their name replaced by "Former member" and the email in their audit details replaced by their
  user id. The organization's last owner is refused and pointed at deleting the organization.
- Both on the profile screen. This is customer-facing, so its two screens get a short spec and
  wireframe of their own before build (`/feature-work`), inside this group.

**D3. The console is one 1.3 MB file.** All ~55 screens import eagerly (`router.tsx:9-69`). *Fix:*
each screen becomes `lazyRouteComponent` (TanStack Router's own), the shell and sign-in stay eager.
Proved by a build-size check in CI (`just check`): no chunk over 500 kB.

**D4. The browser SDK cannot sample.** *Fix:* `sampleRate` (0-1, default 1) decided **per session**,
so a sampled session keeps all its page views and vitals together; errors are always sent, because
they are what a customer most needs and least has. ⚑ The default. Recommended: **1** — every customer
keeps today's behaviour until they choose.

**D5. Tokens and people share one in-flight counter.** *Fix:* the concurrency key gains the quota name,
so tokens and people each have their own organization ceiling. Proved by a `test_ratelimit_callers.py`
case: a full set of token requests in flight leaves the console's reads admitted.

**D6. Plan prices.** ⚑ Yours alone: the seeded prices are provisional by their own migration's words
(`0015`: "priced against what the market charges rather than against cost"). Nothing is built until
you set them; the design records the question.

## Data and migration impact

One Alembic migration (A1: `users.failed_logins`, `users.locked_until`); C2 adds a KeeperMap table in
ClickHouse; C6 edits three downgrades. Nexovue is pre-deployment, so each is written as the correct
end state.

## Performance and scale

C2 adds one KeeperMap read per worker batch. D3 cuts the first load. A1 adds one row update per failed
sign-in. A2 is header comparison.

## Failure handling

C1 serves stale hits only on a lookup error, bounded and counted. C2 fails open: if the digest store
cannot answer, the batch is stored (a duplicate is better than a loss). A1's lock is always temporary.

## Security, tenancy, and privacy

Parts A and B are this section. D2 is the privacy item. Nothing here adds a cross-tenant read.

## Observability and operations

C1's stale serves and C2's skips are counted on `/metrics` (ADR-021). C5 adds the dead-letter runbook.

## Alternatives and tradeoffs

- **Hard account lockout** (locked until an admin acts) — turns every account into a target for a
  denial of service; rejected for the temporary, doubling lock.
- **CSRF tokens** — a token in every form and fetch, when the browser already says where a request
  came from; the header check is what Go's standard library now ships.
- **Masking in the receiver** (B3) — a second copy of the mask package, a rules cache in the
  receiver, and decode/re-encode on the request path, to shorten an exposure that already ends in 7
  days and never reaches a backup.
- **`ReplacingMergeTree` for resends** (C2) — engine changes on the two largest tables, and every
  derived counter still doubles.
- **A personal-data deletion that erases the person from the audit log entirely** — the audit log is
  the organization's record of who did what; replacing the identifying fields keeps the record and
  removes the person.

## Specification impact

D2 needs its own short spec (profile export and self-deletion). A3 amends the super-admin
specification's "rewritten every boot". Everything else is internal.
