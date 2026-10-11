# Plan: remaining-recommendations (readiness group 9)

> **Status:** In progress — every task built but T21, which waits for its spec (T20)
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `design.md` approved 2026-10-11, every ⚑ recommendation taken; D6 (plan prices) later, by the
  founder, and not part of this plan.
- D2 (personal data) is customer-facing: its step starts with a short spec and wireframe, approved
  before its code (`/feature-work`).

## Current system

The four surveys of 2026-10-11 read each item against the code; `design.md` records each "today" with
`file:line`. Read again for this plan:

1. **The envelope** (`packages/contracts/proto/nexovue/v1/envelope.proto:30-60`) carries a random
   UUIDv7 `ingest_id` per record, built in `apps/receiver/internal/ingest/ingest.go:276-288`. A resend
   gets a new one, so nothing that exists identifies a resend. The payload is the record's own OTLP
   bytes (`proto.Marshal(batch.Request)`, `:271`) — one trace per record for spans (`tracesplit`).
2. **The worker's durable state** is KeeperMap watermarks per partition
   (`pipeline/ingeststate/state.go`), keyed by partition. **KeeperMap has no TTL**, so it cannot hold
   a short-lived set of digests — a correction to `design.md` C2 (below).
3. **Migrations**: Alembic head `0038_money_is_right`; ClickHouse head `000032_profile_samples`.
4. **The workers module has one command** (`apps/workers/cmd/worker`); the dead-letter tool is a
   second (`cmd/dlq`).
5. Everything else as `design.md` records it.

**Correction to `design.md` C2:** the digests live in a plain ClickHouse table with a 15-minute `TTL`
(`stored_payload_digests`), not KeeperMap; and the digest is **per record** — a hash of tenant,
project, signal and payload — not per export, because a trace export is split into one record per
trace and a resend re-splits identically. Same skip, finer grain, nothing to expire by hand.

## Change required

`design.md`, *Proposed design*.

## Impact

| Area | What changes |
|---|---|
| Files | API: `auth/service.py`, `middleware/` (new `cross_origin.py`), `superadmin/{adminmode,seed}.py`, `deploys/`, `heartbeats/router.py`, `ratelimit/limiter.py`, `billing/service.py`, `logs/search.py`, `traces/service.py`, new `exports/csv_cells.py`, `settings.py`, `lifespan.py`, `profile/` (D2), `members/service.py`, `migrations/env.py`, three billing downgrades. py-platform: `telemetry/redaction.py`. Scheduler: `pollers/{postgres_connect,tasks,aws}.py`. Receiver: `cache/cache.go`, `auth/auth.go`, `auth/site.go`, `config/config.go`, `ingest/ingest.go`. Workers: `pipeline/mask/` (new family), the writers' skip, new `cmd/dlq`. Contracts: `envelope.proto`. Web: `app/router.tsx`, `features/logs/log-export.ts`, profile screens (D2). SDK: `config.ts`, `rum.ts`. `deploy/kafka/topics.sh`. |
| Services | All |
| Schema / migrations | Alembic `0039` (`users.failed_logins`, `users.locked_until`); ClickHouse `000033_stored_payload_digests`; downgrades of `0020`, `0022`, `0026` |
| UI | D2's two profile screens; D3 changes nothing visible |
| Tests | Named per task |

## Reuse

- The billing webhook's delivery-id shape (A4); the billing router's GET/POST split (A6).
- The probes' `REFUSAL_REASON` (A5); the check-file client's botocore `Config` (C3).
- The scheduler/SDK sensitive-parameter list and `test_cross_service_contracts.py` (B1).
- `kadm` from group 7 (C5); `/metrics` for C1's and C2's counts (ADR-021).
- `members/service.remove` for D2's self-deletion; `organizations/tables.py` for D2's export inventory.

## Gaps

A per-account failure counter; a cross-origin middleware; a URL mask family; a CSV cell helper; a
payload digest; a dead-letter command; a migration lint; personal-data export and self-deletion.

## Steps and tasks

### Step 1 — Part A, security

*Judgeable:* the sixth wrong password in a row is refused for a minute; a cross-site `POST` carrying the
session is refused 403 while a webhook passes; the operator with 2FA cannot enter admin mode by password;
a replayed deploy answers 200 with the first deploy; a refused poll shows no address.

- [x] **T1** — migration `0039` + `auth/service.py` + the second-factor failure path · failed sign-ins
  per account, a lock of 1 min doubling to 15 after 5 in a row, reset on success, the same refusal
  text · proved by `test_auth_lockout_live.py` (new): fifth admitted, sixth locked, doubling, cap,
  reset, an unknown email locks nothing.
- [x] **T2** — `middleware/cross_origin.py` (new), `stack.py` · unsafe method + session cookie +
  cross-site `Sec-Fetch-Site` or foreign `Origin` → 403; the checkout return exempt by name · proved
  by `test_cross_origin.py` (new): each row of design A2.
- [x] **T3** — `superadmin/adminmode.py`, `superadmin/seed.py` · a code only when 2FA is enrolled; the
  password set only at creation; `removed_at` no longer cleared · proved by `test_admin_mode_live.py`
  (new case) and `test_super_admin_seed_live.py` (`:136` asserts the opposite now).
- [x] **T4** — `deploys/{router,service,queries}.py`, `models.py` · delivery id required; a repeat
  answers 200 with the stored deploy; the unique violation caught; the model gains the index; the
  stale comment goes · proved by `test_deploys.py` (`:212` 409 → 200, `:247` now refused) and a
  concurrent case in `test_deploys_live.py`.
- [x] **T5** — `pollers/postgres_connect.py`, `pollers/tasks.py` · a fixed customer reason, the full
  text logged · proved by `test_pollers_auth.py` (`:289-318` flip to "names no address").
- [x] **T6** — `heartbeats/router.py` (GET and POST handlers), `test_authorization_coverage.py` (the
  second name); `telemetry/redaction.py` (`ticket=`) · proved by an OpenAPI case (no duplicate
  operation id) and `test_logging.py` (a ticket never reaches a line).

### Step 2 — Part B, data safety

*Judgeable:* a span whose `url.full` is `https://u:p@h/?X-Amz-Signature=abc` is stored with both masked;
a log body `=HYPERLINK(...)` exports as `'=HYPERLINK(...)`.

- [x] **T7** — `pipeline/mask/` (`url_credentials` family: constant, `DefaultRuleNames`, `Rules`,
  `families()`, `setFamily`, `masksValues`, `maskValue`), API `projects/config.py` · proved by
  `url_test.go` (new) and `families_test.go`; `test_cross_service_contracts.py` gains the Go list.
- [x] **T8** — `exports/csv_cells.py` (new) called by `logs/search.py`, `traces/service.py`,
  `billing/service.py`; `features/logs/log-export.ts` · proved by a case in `test_log_search.py`,
  `test_traces.py`, `test_billing.py`, `log-export.test.ts`.
- [x] **T9** — `deploy/kafka/topics.sh` (`min.insync.replicas` = replication − 1 above 1); ADR-014
  (the Kafka copies age out in 7 days and are never backed up) · proved by running it against dev.

### Step 3 — Part C, reliability

*Judgeable:* with Postgres stopped, a key used in the last ten minutes still exports and `/metrics`
counts the stale serves; a resent export is stored once; `just dlq list` shows the parked records and
`replay` brings one back.

- [x] **T10** — `cache/cache.go`, `auth/auth.go`, `auth/site.go`, `config/config.go` · stale hits on a
  lookup error up to `RECEIVER_API_KEY_MAX_STALE` (10 min); a separate ceiling for misses · proved by
  `cache_test.go` and `auth_test.go` (new cases: error → stale hit; error → no stale miss; a flood of
  misses leaves hits).
- [x] **T11** — `envelope.proto` (`payload_digest = 9`), receiver `ingest.go` (deterministic marshal,
  SHA-256 of tenant, project, signal, payload); ClickHouse `000033_stored_payload_digests` (TTL 15
  min); the worker skips a record whose digest is stored, writing digests after its insert is
  acknowledged · proved by a contracts roundtrip case, a receiver case (same export → same digest),
  and `resend_live_test.go` (an export sent twice is stored once, counted once). Fails open: an
  unreadable digest table stores the batch.
- [x] **T12** — `pollers/aws.py`, `pollers/tasks.py` · botocore `Config(5 s, 10 s, standard, 2)` ·
  proved by a `test_pollers.py` case.
- [x] **T13** — `settings.py`, `lifespan.py` · `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` deleted, 30 s stated
  once · proved by `test_settings.py`.
- [x] **T14** — `apps/workers/cmd/dlq` (new) and `justfile` (`dlq list`, `dlq replay`);
  `docs/operations/dead-letters.md` written from a replay run on dev · proved by `dlq_test.go`
  (header parsing, the replay record keeps key and bytes).
- [x] **T15** — `migrations/env.py` (`lock_timeout = '5s'`); `0020`, `0022`, `0026` downgrades raise
  "roll forward only"; `docs/engineering/` the concurrent-index rule · proved by
  `test_migration_rules.py` (new): every revision parsed, the lock timeout set, the three refuse.

### Step 4 — Part D, product and console

*Judgeable:* an INR-only account sees INR and checks out; no console chunk is over 500 kB and the first
screen still loads; `sampleRate: 0.5` keeps about half the sessions whole; a script holding every token
slot leaves the console's reads admitted; a person exports their data and deletes their account.

- [x] **T16** — `billing/service.py` (`overview`: the first offerable currency) · proved by a
  `test_billing.py` case.
- [x] **T17** — `app/router.tsx` (`lazyRouteComponent` for every screen but the shell and sign-in);
  a bundle-size check in `just check` · proved by the check and the existing e2e sign-in.
- [x] **T18** — SDK `config.ts`, `rum.ts` (`sampleRate`, per session, errors always) · proved by
  `sampling.test.ts` (new).
- [x] **T19** — `ratelimit/limiter.py` (the concurrency key gains the quota name) · proved by a
  `test_ratelimit_callers.py` case.
- [x] **T20** — D2's spec and wireframe (`docs/features/personal-data/`), **approved before T21**.
- [ ] **T21** — `profile/` (`GET /profile/export`, `DELETE /profile` — self-deletion), `members/service.py`
  (name and audit email replaced on removal), the profile screens · proved by
  `test_personal_data_live.py` (export contents; self-deletion; the last owner refused; the audit
  entry keeps the user id, not the email) and the screens' tests.

### Step 5 — Documents

- [x] **T22** — the readiness document (each item done, decided or corrected; the six new findings),
  `current-system-reference.md` (§4.7's resend claim corrected; each part's behaviour), ADR-014, the
  super-admin spec (A3), this plan's Outcome and `status.md`.

## New risks

- **T11 changes the envelope.** The new field is optional, so a record from an older receiver simply
  has no digest and is stored as today; nexovue is pre-deployment, so there is no fleet to roll.
- **T2 can refuse a legitimate browser request** if a route is cross-site by design and was missed.
  Every row of design A2 is a test, and the console's e2e suite runs after it.
- **T17 can break a deep link** if a lazy screen fails to load; the e2e suite covers the main routes.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | D2's exact screens and wording | Blocks T21 only | `docs/features/personal-data/spec.md` (T20) |
| 2 | Plan prices (D6) | No — not in this plan | The founder, later; `docs/product/vision.md` |

## Gates

- [x] Specification approval — `design.md`, 2026-10-11
- [x] Design approval — `design.md`, 2026-10-11
- [x] **Plan approval — before the first edited file** — 2026-10-11
- [ ] D2 spec and wireframe approval (T20)
- [x] Implementation — all but D2
- [x] Tests — all but D2
- [x] Review — all but D2
- [ ] Design QA — D2's screens
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

Each new test seen red first. `just check` after each step, counts read. Live on dev: the lockout, the
cross-site refusal from a second origin, the resend stored once (an export posted twice through the
receiver), Postgres stopped with ingest continuing, a dead letter listed and replayed, the console's
chunks and first load, D2 end to end.

## Rollout and rollback

Pre-deployment; one commit per step, so any part can be reverted alone. The real risk is the size: five
steps across every service. Each step is reported to the founder before the next starts.

## Outcome

Built 2026-10-11, every task but **T21** (personal-data export and self-deletion), whose spec
(`docs/features/personal-data/spec.md`, T20) waits for the founder's approval. D6, plan prices, is the
founder's, later. Differences from the plan, each found by running the thing:

- **A4** — a repeated deploy answers 201 with the stored deploy, not 200: it says "recorded" either way
  and GitHub ignores the code. The repeat lookup names the organization (the tenancy structure test
  refused it otherwise), which removed an exemption rather than renaming it.
- **A6** — the heartbeat token was already redacted from every log line; only the duplicate operation id
  and the support ticket needed fixing.
- **B1** — the URL family uses the conventions' five signature parameters, the list the scheduler and
  the SDK already share, so all three stay one list (`test_cross_service_contracts.py` now reads Go's).
  `access_token`, `token` and `api_key` as query names were left out: adding them to one copy would break
  the contract, and to all three would widen what the SDK and probes redact; a project can name them.
  A cheap pre-check spares the patterns every string with no `://` and no `?`.
- **B2** — the billing export holds no customer text (dates, amounts, our plan names), so only logs and
  traces needed the rule.
- **C2** — a Kafka **header**, not an envelope field: the worker reads it without decoding and the
  contract package did not change. The digest is **claimed when consumed**, not when stored, because a
  trace waits a minute in the sampling buffer and a resend comes within seconds; a claim names its
  offset, so a replay of that same offset after a crash is still processed. The live run found three
  defects the unit tests could not: ch-go sends a `[]string` parameter unquoted and mangles one passed
  as text (digests are now validated hex, written as literals), a 5,000-record batch passed ClickHouse's
  256 KB query limit (lookups are chunked by 1,000), and the claims were failing silently through the
  default logger (they now warn through the pipeline's). `claims_storeprobe_test.go` holds both server
  cases. Proven live: one export sent twice, stored once; real resends from the demo skipped too.
- **C5** — the tool ships in the worker image as `/dlq`; reasons are cut to 200 characters, because a
  store's refusal quotes the whole value it refused.
- **C6** — the first `SET lock_timeout` ran before Alembic's transaction, which made Alembic commit
  nothing: every migration was silently rolled back. The scratch-database migration suites caught it;
  it now runs inside the transaction, session-level so the autocommit blocks keep it.
- **D3** — the five screens whose modules also exported a search parser stayed eager at first (564 kB);
  the parsers moved to `search.ts` files and the first download is 393 kB (was 1.34 MB).
- **Not in the plan, found on the way:** `deploy/clickhouse/backfill-trace-summaries.sh` defaulted to the
  dev password (a readiness item the design missed); it now has none.

**Verified:** `just check` exit 0 — Python 4355 passed, 3 skipped; web 1358; SDK 124; every console chunk
under 500 kB. E2E sign-in, services and synthetics: 8 passed. Live on dev: the cross-site refusal, the
resend stored once, a dead letter listed and replayed, the store probes against ClickHouse.
