# Production readiness status

## Current status

**NOT READY.** Re-assessed in full on **2026-10-10**, against a release of both hosted SaaS **and** a
self-hosted install. Scope: the whole repository — the platform checklist, every open blocker
re-verified against the code rather than this document, and every built feature reviewed in seven
areas (platform, ingest, API security, reads and storage, scheduler and integrations, billing and
data, web console and browser SDK).

There is no deployment target, so nothing below was checked against one. Every verdict is against the
repository and the development stack, and says so. A green `docker compose` on a laptop is not
evidence about production.

**This list is the plan for Phase 1 Track 3** (`feature-catalog.md` §5): the blockers below are fixed
before F37 is specified, then F37 closes the ones that belong to it.

What changed since 2026-09-21: every then-open blocker is still open; the `deploy/` tree has not moved
since that day. Twelve new blockers were found (12-23) — in what the deployment files never set, in
ingest, in the shared stores, in billing and in deletion. Two rows of the previous assessment were
wrong (health checks, restore evidence) and are corrected below.

## Checklist, item by item

`✓ verified` · `✗ missing` · `? unknown` — each with what was read or run.

| Item | Verdict | Evidence |
|---|---|---|
| Hosting target | ✗ | `docs/operations/deployment.md`: "no approved production hosting target" |
| Configuration / secrets | ✗ | `${VAR:?}` demands in the app and on-prem compose; no external secrets manager. Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): both files derive `OTLP_ENDPOINT` and `APP_BASE_URL` from the edge's two names, set `TRUSTED_PROXY_COUNT=1` and require both encryption keys — blockers 4, 16, 17 |
| Migrations as a deploy step | ✗ | No alembic or ClickHouse migrate call in any compose file or Dockerfile — only `justfile:248-254`. 35 alembic + 32 ClickHouse migrations. `topics.sh` is not run by any target either, and broker auto-create is off (`compose.infra.yaml:40`) |
| Capacity | ? | Laptop limits (`TODO(sizing)`, `docker-compose.onprem.yaml:88`); no load test, no target. `scheduler-light` at 256M in the hosted file is a limit dev already measured OOM-killing it (`docker-compose.yaml:476-481`) — blocker 22 |
| Health checks | ✗ *(was ✓)* | receiver, worker, API, web are checked (`docker-compose.app.yaml:123,165,212,265`). **None of the three scheduler services is**: `health.serve_probes` (`apps/scheduler/.../health.py:65`) has no caller anywhere |
| Logging / monitoring / alerting | ✗ | Structured logs only. Neither Go service exports metrics (no consumer lag, no DLQ depth); the console's production log sink is a no-op (`apps/web/src/shared/logger.ts`, `configureLogger` never called). `monitoring.md`/`alerting.md` are stubs, and `alerting.md` still calls F21 "separately planned" |
| Backup and restore evidence | ✗ | **Corrected:** two full local restore cycles *were* run on 2026-09-04 (`84832a3`) — this row said none had been. That drill predates about twenty migrations and has not been repeated; never against a deployed target; no schedule, off-host copy, RPO or RTO |
| Rollback | ✗ | `rollback.md` is a stub; never rehearsed. Most ClickHouse `.down.sql` files drop tables or columns (`000001`-`000007`, `000032`, and seven column drops), and `000024`'s down would double-count endpoint stats against the current worker — so a ClickHouse rollback means *restore the backup and roll the images back together*, never `migrate down` |
| TLS / public endpoints | ✓ (replica) | Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): one Caddy edge on both files serves the console's files, the API and ingest over TLS on two names, and nothing else publishes a port. Run on the replica with `app.localhost`/`ingest.localhost`; never against a real hostname |
| Security review | ✗ | This review is the first since the 2026-09-15 secret scan and licence audit (both clean) |
| Release process | ✗ | No `.github/` or other CI; no registry (`TODO(registry)`); no version file or changelog; the only tag is `backup-trailers-20260915` |
| Smoke tests | ✗ | Twelve e2e specs, local only; nothing runs after a deploy |
| Operational ownership | ✗ | One person; `incident-response.md` is seven lines; no on-call path |
| Invite-only access | ✓ | `SIGNUP_ENABLED` defaults closed in both deployment files (`app:223`, `onprem:248`). The stale comment at `docker-compose.app.yaml:221-222` was removed 2026-10-11 |
| Email in production | ? | Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): production refuses `console` in the API and the scheduler, and a blank `EMAIL_PROVIDER` (which both files pass when unset) means unconfigured rather than failing to start. Still open: no bounce handling; whether a Resend domain exists is not checked (group 8) |
| Self-hosted mode | ✗ | No deployment-mode concept anywhere in `apps/` or `packages/` |
| On-prem compose | ✗ | "SKELETON, NOT YET RUNNABLE"; twelve `TODO`s; never run |
| Secret keys and rotation | ✓ (dev) | Fixed 2026-10-11 ([keys-can-be-rotated](../changes/keys-can-be-rotated/status.md)): a keyring per key and a re-seal command. Before: `CHECK_SECRET_KEY` (check and channel secrets) and `CREDENTIAL_ENCRYPTION_KEY` (TOTP) each seal with one key and no key id (`probes/secrets.py:36-47`, `auth/encryption.py`) — rotating either loses everything it sealed |

## Blockers

Each names the failure it prevents, and which target it stops: **H** hosted, **S** self-hosted.
Numbers 1-11 keep the numbers other documents already cite.

### Still open from 2026-09-21

1. **No hosting target.** *(H)* There is nothing to deploy to, and no item above can be verified until there is.
2. **Images are built on a laptop.** *(H, S)* With no CI, registry or tag, nobody can say which commit is running, and a bad deploy has no known-good image to return to. A self-hosted operator has nothing to pull.
3. ~~**Signup is public.**~~ **Cleared 2026-09-21** (`docs/changes/signup-closed-by-default/`); re-verified 2026-10-10.
4. **Fixed on the replica 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): the API's `OTLP_ENDPOINT` is `https://<NEXOVUE_INGEST_HOST>` in both files, and a span exported to `https://ingest.localhost` on the replica was read back through the console's API. ~~**No ingest hostname with TLS — and the API cannot start on any deployed target.** *(H, S)* The settings validator refuses the localhost `OTLP_ENDPOINT` default (`settings.py:313-320`, reproduced), and **neither deployment file passes `OTLP_ENDPOINT` to the container at all**, so the API exits at boot whatever the operator sets.~~
5. **Migrations and Kafka topics are not part of a deploy.** *(H, S)* A new image against an old schema starts services whose queries fail; a fresh install's receiver has no topics to publish to and answers 503 to every export.
6. **No restore has been performed against anything but a laptop, and the one drill is stale.** *(H, S)* A lost volume takes every customer's accounts and telemetry with it, with no rehearsed way back — the 2026-09-04 drill predates about twenty migrations.
7. **Nothing tells us nexovue is down.** *(H, S)* A dead receiver drops customers' telemetry and the first signal is a customer's message. The scheduler is worse: none of its three services has a liveness probe, so a dead beat silently stops every customer's checks and a dead delivery worker leaves every alert in the outbox.
8. **Fixed on the development stack 2026-10-11** ([keys-can-be-rotated](../changes/keys-can-be-rotated/status.md)): every sealed value names its key; current and previous keys; `just keys-status`/`keys-reseal`; rotated and reversed on dev with sign-in, Run now and a webhook test passing at every step ([key-rotation](key-rotation.md)). ~~**Neither encryption key can be rotated.** *(H, S)* `CHECK_SECRET_KEY` seals check *and* notification-channel secrets; `CREDENTIAL_ENCRYPTION_KEY` seals TOTP secrets. Each is one key with no key id, so a leaked key cannot be replaced without making every check, Slack/PagerDuty/webhook channel and 2FA enrolment unreadable at once.~~
9. **Self-hosted installs believe they are hosted.** *(S)* They show a Razorpay billing screen and cap ingestion on the operator's own disk.
10. **The on-prem compose has never run — and its console could not answer.** *(S)* Beyond the twelve `TODO`s, the Caddyfile routes the console to `web:8080` and the on-prem file declares no `web` service (only `web-assets-init`; its own `TODO(caddy)` at `:138`), so every console request would answer 502.
11. **Half fixed 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): production refuses `console` in both services. **Still open: email delivery is unverified** — no provider domain has been verified, which needs the hosting decision (group 8). ~~Nothing refuses `console` outside development; with it, no one can verify an address, reset a password, or receive an alert.~~

### New on 2026-10-10

12. **Fixed on the development stack 2026-10-10** ([ingest-cannot-be-knocked-over](../changes/ingest-cannot-be-knocked-over/status.md)): every writer now treats a refusal as permanent; logs, metrics and profiles park the refused chunk, spans narrow it to the refused row, derived rows are counted and stepped over, and attribute nesting is capped below what ClickHouse refuses. Verified live, red then green. ~~**One row ClickHouse refuses stops all ingest for every customer.** *(H, S)* Only the log and vitals writers treat a refused row as permanent (`storefailure.RefusedTheData`, `logwriting/writer.go:43`, `vitals/writer.go:96`). The span, summary, metric, profile, service-edge and derive writers return a plain error; `Loop.Retry` retries two minutes and exits (`consume/retry.go:74-79`), and the restarted worker re-reads the same batch. One worker runs every pipeline, so every signal stops — the exact one-hour outage `permanent.go` records, fixed then for logs alone.~~
13. **Fixed on the development stack 2026-10-10** (same record): a tap handle refuses missing, malformed and known-bad keys before the body is read, and one in-flight budget bounds the exports held across both doors. The live flood that took the receiver from 262 to 629 MiB now moves it 20 → 22 MiB. ~~**The public gRPC port can be exhausted without an API key.** *(H, S)* grpc-go receives, decompresses (to 16 MiB) and unmarshals the whole message before `runExport` authenticates (`otlp/grpc.go:98-104`, `grpcserver.go:62`), and no concurrent-stream or in-flight limit exists. Enough parallel gzip bodies kill the receiver, dropping every tenant's telemetry. The HTTP door authenticates before it reads.~~
14. **Fixed on the development stack 2026-10-10** ([clickhouse-protects-itself](../changes/clickhouse-protects-itself/status.md)): every read sends a 1 GiB memory cap, and readers connect as an account whose server-side profile caps memory and time whatever a query sends. ~~**No read is bounded in memory, so one heavy read fails every query on the shared ClickHouse — ingest included.** *(H, S)* `READ_SETTINGS` caps time and rows only (`tenant_statements.py:36-40`); the server reports `max_memory_usage = 0`, and nothing caps the window a caller asks for (`window.py`). At the server's 4 GiB ceiling every query fails, the workers' inserts with them.~~
15. **Fixed on the development stack 2026-10-10** ([redis-goes](../changes/redis-goes/status.md)): Redis is removed from every compose file. Sessions, one-time links, second-factor state, the job queue that replaced Celery, job results and alert evaluation status are logged Postgres tables (migration `0037`), and beat leads through a Postgres advisory lock — nothing a customer depends on sits in a store that evicts. ~~**Redis evicts state that cannot be rebuilt.** *(H, S)* The one Valkey runs `allkeys-lru` at 384 MB (`compose.infra.yaml:158-162`) on the assumption it holds only counters and cache. It also holds sessions, password-reset and verification tokens, the Celery broker (probe runs, alert deliveries) and the beat leader lock. Under memory pressure it silently signs customers out, voids reset links, drops queued check runs, and can briefly let two beats schedule.~~
16. **Fixed on the replica 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): `TRUSTED_PROXY_COUNT=1` on the API in both files. Proven with two clients through the edge: one exhausting its ten logins leaves the other admitted; with the old value 0 the other is refused. A load balancer in front of the edge makes it 2 (group 8). ~~**Behind any proxy, every customer shares one login throttle.** *(H)* `TRUSTED_PROXY_COUNT` defaults to 0 (`settings.py:258`) and the hosted file never sets it, so all customers appear as the proxy's address. The auth tier is 10 a minute (`ratelimit/policy.py:60`): the eleventh login, signup or reset attempt *across all customers* in a minute gets a 429, and the per-organization tiers collapse the same way.~~
17. **Fixed 2026-10-11** (same record): both deployed compose files require `CREDENTIAL_ENCRYPTION_KEY`, and production refuses to start without either key. ~~**Two-factor sign-in cannot be enabled on any deployed install.** *(H, S)* `CREDENTIAL_ENCRYPTION_KEY` is not passed by either deployment file, so enrolment answers 503 (`auth/dependencies.py:314`). (Its rotation is blocker 8.)~~
18. **Fixed on the replica 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): the hosted file runs the same Caddy edge as on-prem, which serves the console's files, the API and ingest, with TLS and the security headers; the nginx config is deleted. ~~**The hosted target has no edge in front of the console.** *(H)* `docker-compose.app.yaml` publishes web and API directly. The console's nginx (`deploy/nginx/web.conf`) has no `/api` route — `try_files … /index.html` answers every API call with the HTML page and 200, which the client cannot parse — and there is no TLS and no security header on this path; all of that lives only in the on-prem Caddyfile.~~
19. **Fixed on the replica 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): the hosted edge allows Razorpay's checkout, script, frame and reporting origins, read from a test checkout in a browser through the edge — which also found two the documentation does not name (`cdn`, `lumberjack`); the checkout opened with nothing blocked. A payment was not completed end to end. ~~**The documented content-security policy blocks checkout.** *(H)* The billing screen loads `https://checkout.razorpay.com/v1/checkout.js` (`billing/razorpay.py:63`, `checkout-handoff.ts:55`); the Caddyfile's CSP allows scripts from `'self'` and one hash and frames from `'self'` only. The first hosted edge built from it makes every payment fail with `SCRIPT_BLOCKED`.~~
20. **Fixed on the development stack 2026-10-10** ([money-is-right](../changes/money-is-right/status.md)): closing an organization cancels every live arrangement at the processor immediately, before anything is scrubbed, and is refused — nothing scrubbed — when the processor cannot be reached. ~~**Closing an organization keeps charging its card.** *(H)* `organizations/service.request_deletion` and `erase` delete the `billing_subscriptions` rows (`organizations/queries.py:37-44,98-99`) and never cancel the subscription at the processor; nothing in `organizations/` references billing beyond those deletes. The mandate keeps debiting for up to twelve cycles, and each later `subscription.charged` names a subscription we no longer hold, so it is logged and ignored — money taken with no record left to refund from.~~
21. **Fixed on the development stack 2026-10-10** ([money-is-right](../changes/money-is-right/status.md)): a checkout locks the organization and supersedes any earlier one at the processor, so one is ever live; ending a subscription ends every live arrangement. Proven with two real concurrent checkouts. ~~**A customer who checks out twice is debited twice.** *(H)* `begin_plan_change` refuses only when the *latest* row is `active` (`billing/service.py:795-804`); any number of `pending` checkouts can exist, two authorised tabs make two live mandates, and `end_subscription` ends only the latest. *(Rated recommended by the area review; raised here — charging a customer twice is not a recommendation.)*~~
22. **Fixed on the development stack 2026-10-11** ([deletion-finishes](../changes/deletion-finishes/status.md)): the request deletes the Postgres rows; the scheduler erases every owed organization's telemetry and files together once a night (ADR-014), certifies only what it proved gone, and takes the rest again the next night; an erasure owed past 3 days is logged at ERROR. ~~**Erasures owed to a deleted organization are never retried.** *(H, S)* `erasures_still_owed` (`organizations/service.py:170`) is the deletion guarantee the router cites, and its only callers are tests; no scheduler job runs it. An API restart mid-erasure, or rows that land after the purge mutation (which `purge/service.py` itself says need a re-purge), leave a deleted customer's telemetry and objects in place.~~
23. **Fixed 2026-10-10** (same record): 768M and 384M in the hosted file. ~~**The hosted scheduler's memory limit is one dev already saw kill it.** *(H)* `scheduler-light` is capped at 256M (`docker-compose.app.yaml:338`); the dev file records 256M "OOM-killed [children] during pool startup and the queue never drained" and runs 768M.~~

## Recommended

Grouped by area; each names the failure. Items that are a business choice are marked **decision**.

**Ingest**
- Export consumer lag and DLQ depth from both Go services — without them blocker 7 has nothing to watch.
- DLQ records disappear silently after the topic's 7-day retention (`topics.sh:24,62-65`); no replay or inspection tool, no runbook.
- A Postgres outage refuses all ingest within ~30 s: the key cache never serves a stale entry (`cache/cache.go:80`), and exceeding 10,000 entries drops the whole cache at once (`:127`).
- A batch resent after the 5 s delivery timeout is stored twice; `spans` is a plain `MergeTree` and derived counters double (`cmd/receiver/main.go:243`). *(Already recorded; confirmed.)*
- ~~The tail-sampling buffer caps traces and spans but not bytes~~ — **done 2026-10-10**, `WORKERS_SAMPLING_MAX_BYTES` (ingest-cannot-be-knocked-over).
- Unmasked telemetry sits in `*-raw`/`*-dlq` topics for 7 days and goes into backups; masking happens only in the worker.
- `topics.sh` defaults `REPLICATION=1` and sets no `min.insync.replicas` — an acknowledged write lives on one broker's disk.

**API security**
- ~~No request-body cap on unauthenticated routes (login, signup, reset read the whole JSON; the limiter peeks 8 KB; no edge `max_size`).~~ Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): the API counts every body as it arrives (2 MiB, more on two named routes) and the edge caps `/api/*`.
- Heartbeat tokens are in the URL path, which the request and error logs record in full — anyone with log access can forge "job ran" pings (`heartbeats/router.py:19`, `middleware/request_log.py:55`). The same route's GET+POST `api_route` produces a duplicate OpenAPI operation id.
- The platform operator enters admin mode with a password *or* a code, even when enrolled in 2FA (`superadmin/adminmode.py:107-120`), and that password is rewritten from an env var every boot.
- No per-account login lockout — ~14,400 guesses a day against one account from rotating addresses.
- No `Origin`/`Sec-Fetch-Site` check on unsafe methods; `SameSite=Lax` does not separate sibling `*.nexovue.com` hosts such as the website.
- ~~Security headers exist only on the on-prem Caddy path (see blocker 18).~~ Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): both files use the edge.
- **Decision:** API tokens outlive the member who minted them (`apitokens/service.py:132-151`; nothing in `members/` revokes them). Intended or not, it should be stated.

**Reads and storage**
- **Done 2026-10-10** (clickhouse-protects-itself) — a ClickHouse read-only user with its own settings profile: today every service uses one account holding every grant, including DROP, FILE, URL and S3 — tenant separation rests entirely on the code-level check. This is also where blocker 14's memory cap belongs, and where `scheduler-light`'s read-only account (F21's open item) comes from.
- **Done 2026-10-10** — the log live tail bypasses every rate limit and concurrency cap (`middleware/ratelimit.py:219,225` skips WebSockets; `logs/tail.py:518-547` opens one poller per unchecked `project_id`).
- Deleting a tenant rewrites every part of all 14 telemetry tables (`ALTER DELETE`; a tenant is not a partition) — a whole-store rewrite competing with ingest for memory.
- **Done 2026-10-10** — the LLM-calls read has no `LIMIT` or truncation flag (`llm_observability/queries.py`) — a span name carrying an id grows the response until it hits the 500,000-row ceiling and fails as an error.
- API tokens have no organization-wide concurrency ceiling (`TOKEN_TIER`: 8 per token, unlimited tokens).
- The client's 15 s timeout never applies to tenant reads — `READ_SETTINGS`' 30 s wins (`lifespan.py:238`).
- `deploy/clickhouse/backfill-trace-summaries.sh` runs `docker exec` against a container named for the laptop, with the dev password as default.

**Scheduler and integrations**
- The database poller writes resolved internal addresses into the customer-visible integration health (`pollers/postgres_connect.py:212-215` → `pollers/health.py:42`) — a customer can map our network. The probes deliberately omit them (`probes/addresses.py:115-117`).
- Deploy-webhook replay protection depends on an optional delivery-id header (`deploys/service.py:92-95`); a captured signed request without it records a new deploy every time. The comment at `:89-92` says a replay returns the stored deploy; the code raises `ConflictError`.
- AWS pollers set no client timeouts — a hung STS/CloudWatch call holds a worker slot to the task's 60 s hard limit.
- ~~Dev keeps probe queues on Redis DB 1; both deployment files put everything on DB 0.~~ Gone with Redis, 2026-10-10 ([redis-goes](../changes/redis-goes/status.md)).

**Billing and data**
- ~~Out-of-order webhooks can re-grant a paid plan: `SUBSCRIPTION_ACTIVATED` sets `active` even on a `cancelled` row (`billing/service.py:197-203`).~~ Fixed 2026-10-10 ([money-is-right](../changes/money-is-right/status.md)): a cancelled arrangement stays cancelled.
- ~~**Decision:** `subscription.halted` (payment permanently failed) changes nothing, so the customer keeps the paid tier indefinitely (`razorpay.py:120-121`).~~ Fixed 2026-10-10 ([money-is-right](../changes/money-is-right/status.md)): founder chose (b): the plan holds 7 days, the owners are emailed the date, then it lapses to free; a late payment restores it.
- ~~A missing `x-razorpay-event-id` is accepted as `""` (`razorpay.py:370`), so after the first, every id-less event — a cancellation included — is dropped as a duplicate.~~ Fixed 2026-10-10 ([money-is-right](../changes/money-is-right/status.md)): a delivery without an event id is refused, and the column refuses `''`.
- ~~Nothing refuses sandbox payments in production: the hosted file defaults `PAYMENTS_DOMESTIC_ENV` to `sandbox` (`docker-compose.app.yaml:80`).~~ Fixed 2026-10-10 ([money-is-right](../changes/money-is-right/status.md)): `PAYMENTS_DOMESTIC_ENV` (a no-op — Razorpay has one host) is deleted; the key decides, and production refuses a key that is not live, and nothing else may hold one.
- **Found 2026-10-11** (deployment-tells-the-truth): the billing screen quotes the default currency (USD) even when `PAYMENTS_DOMESTIC_CURRENCIES` offers only INR, so the upgrade fails at the last button with "cannot be charged in USD yet" — `billing/service.overview` picks `DEFAULT_BILLING_CURRENCY` without checking it is offerable, and the currency switch is hidden at one currency.
- Credentials inside URLs are not masked — nothing scans `url.full`/`http.url` query strings (`mask/rules.go:24-37`).
- CSV exports are open to formula injection: customer text starting `=`, `+`, `-`, `@` is written raw (`logs/search.py:341-359`, `traces/service.py:768`).
- **Decision:** the seeded plans are still placeholders (`0005_plans_table.py:31-37`); the prices in `0015`/`0023` appear in no decision document.
- No GDPR self-service: no personal data export, no person-level account deletion.
- No alembic migration uses `CREATE INDEX CONCURRENTLY` or a `lock_timeout` — harmless today, a write outage on the first index added to a grown table.
- Billing downgrades `0020`, `0022`, `0026` drop billing data — mark them roll-forward only.

**Web console and browser SDK**
- Production drops every browser error: the logger's production sink is a no-op and `configureLogger` is never called.
- ~~`index.html` gets heuristic caching (`web.conf` sets `Cache-Control` on `/assets/` only), so a cached page can point at hashed assets a deploy removed — a blank app.~~ Fixed 2026-10-11 ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)): the edge sends the page `no-cache` and only existing hashed assets a year.
- Every route ships in one 1.34 MB chunk (400 kB gzipped).
- `style-src 'unsafe-inline'` sits beside a report-only strict policy no browser has ever loaded the console under.
- The browser SDK has no client-side sampling; all volume control falls on the receiver's allowance.
- The SDK is `"private": true` at 0.1.0; publishing needs the release work in blocker 2.

**Platform, carried over**
- Load-test against the chosen host and replace the laptop limits with measured ones.
- Rehearse one rollback to the previous tag — as image-plus-restore for ClickHouse (see *Rollback*).
- An external secrets manager instead of an environment file on the host.
- Write runbooks only from procedures actually executed.
- If the repository goes public for the self-hosted release: the CLA text (ADR-005) and `CONTRIBUTING.md`.

**Stale documents found by this review** — `alerting.md` (F21 "separately planned"), the comment at `docker-compose.app.yaml:221-222` (removed 2026-10-11), `purge/__init__.py:3` ("Nothing calls this yet" — fixed: the package moved to `nexovue_scheduler.erasure`), and the feature catalog's F15 ("unpublishable") and F29 ("no screen") rows.

## Fix order

The founder's plan for now: fix the blockers, with the recommendations beside them where they share
the work, before F37 is specified. Grouped so each package is one change record:

1. **Ingest cannot be knocked over** — 12, 13 (+ sampling-buffer bytes). **Built and verified on the development stack 2026-10-10**; consumer-lag and dead-letter metrics moved to group 7.
2. **The shared stores protect themselves** — 14, 15, 23. **Split in two, 2026-10-10:** 2a (14, 23, the read-only user, live tail, LLM-calls cap) **built and verified on the development stack**; 2b (15, Redis goes, + the token organization ceiling) **built and verified on the development stack 2026-10-10** ([redis-goes](../changes/redis-goes/status.md)). **Blocker 15 is closed by removing Redis**, not by splitting it: what Redis holds moves to Postgres and Celery is replaced by a Postgres task queue ([ADR-020](../decisions/ADR-020-postgres-carries-what-redis-held.md)).
3. **Money is right** — 20, 21 (+ out-of-order webhooks, empty delivery id, sandbox-in-production guard, the `halted` decision). **Built and verified on the development stack 2026-10-10** ([money-is-right](../changes/money-is-right/status.md)).
4. **Deletion finishes** — 22 (+ the purge strategy that stops rewriting the whole store). **Built and verified on the development stack 2026-10-11** ([deletion-finishes](../changes/deletion-finishes/status.md)); the API no longer holds a ClickHouse credential that writes. A ClickHouse user that can only delete moves to group 6.
5. **Keys can be rotated** — 8, 17. **Built and verified on the development stack 2026-10-11** ([keys-can-be-rotated](../changes/keys-can-be-rotated/status.md)).
6. **Deployment configuration tells the truth** — 4, 11, 16, 18, 19 (+ request-body cap, security headers on the hosted path, `index.html` caching). **Built and verified on the production replica 2026-10-11** ([deployment-tells-the-truth](../changes/deployment-tells-the-truth/status.md)), with the ClickHouse user that can only delete and the instance counts that set the replicas. Still open from it: a verified mail domain (11) and a completed test payment through the edge.
7. **We can see ourselves** — 7 (+ browser error sink).
8. **F37 and hosting** — 1, 2, 5, 6, 9, 10: images and CI (as the two images of
   [ADR-019](../decisions/ADR-019-two-images-each-role-its-own-container.md)), migrations as a deploy
   step, the deployment-mode concept, the runnable on-prem install, a restore against a real target,
   and connecting a cloud monitor to what group 7 reports. **Deferred by the founder (2026-10-10)
   until groups 1-7 and 9 are done.**
9. **The remaining recommendations** — everything above that no group carries (personal data export
   and deletion, console code splitting, SDK sampling, and the rest of *Recommended*). The founder
   chose to fix these now, after group 7.

**Founder decisions taken for this plan (2026-10-10):** one change record per group; every
recommendation fixed in this round; a permanently failed payment gets a grace period and then drops to
the free plan (group 3); telemetry erasure is ADR-014's nightly batch, built in group 4; API tokens
stay the organization's when their creator leaves, as ADR-017 already decided; group 7 makes nexovue
report its own health, and what watches it waits for the hosting choice; the hosting target is chosen
in group 8; nexovue ships as two images (ADR-019); Redis is removed and object storage gets a
local-disk option, leaving ClickHouse, Kafka and Postgres as the only required services, with
Postgres kept required rather than replaced by SQLite (ADR-020).

## F21 — alerts, assessed as one change (2026-09-19)

Scope: is F21 safe to roll out, given the platform above? It adds no blocker of its own beyond the list
above, and it depends on three of them:

- **Email delivery (11).** The default rule emails the organization's owners; with `console` in production,
  no alert reaches a person.
- **Check secret key (8).** Channel secrets are sealed under the same key (ADR-010, amended); without it no
  Slack, Teams, Discord, PagerDuty or webhook channel can be saved or delivered to, and rotating it loses
  every channel secret as well.
- **Migrations (5).** `0028_alerts` and `0029_alert_telemetry_state` must run before the scheduler starts, or
  every sweep and every check state change fails. Both upgrade, downgrade and upgrade again in
  `test_alerts_migration_live.py`.

Recommended for F21 (both still open 2026-10-10, now tracked above):

- A read-only ClickHouse user for `scheduler-light` (`TODO(hosting)` in both compose files) — today it reads
  telemetry with the API's account, which can write.
- A healthcheck for the three scheduler services, and something watching the age of the oldest `pending`
  notification — now part of blocker 7.

## F17 — continuous profiling, assessed as one change (2026-09-19)

Scope: is F17 safe to roll out, given the platform above? **NOT READY.** Every platform blocker applies; F17's
own storage blocker was closed by the founder's NFR3 decision (D6), leaving it under *Capacity*. Verdicts are against the repository and the development stack; there is no target.

| Item | Verdict | Evidence |
|---|---|---|
| Migrations have a way back | ✓ | `0030_usage_profile_samples.py` drops its column; `000032_profile_samples.down.sql` drops the table. Neither downgrade has been *rehearsed* |
| Topics exist where it deploys | ✗ | `profiles-raw` and `profiles-dlq` come from `deploy/kafka/topics.sh` only; no deploy target runs it — blocker 5's class. The receiver's publish to a missing topic answers 503 and the Collector retries |
| Health checks | ✓ | `worker` carries the `-health` check in the app compose; a killed worker restarts on its own. Since 2026-09-20 it carries every pipeline, so a flood-killed worker takes every signal with it until it is back (`docs/changes/one-worker-again/`) |
| Isolation from other signals | ✓ | Own rate bucket, per-key sample budget, own topic and worker process: spans unaffected by a 400,000-samples-a-second flood (`docs/features/profiling/status.md`) |
| Tenancy | ✓ | `test_tenant_isolation_live.py` sends a real profile under one key and reads it from another organization: 404 or nothing, on every route |
| Hostile input | ✓ | Frame bound (413), shared attribute resolution, value overflow, index bounds — `internal/profiles/convert_test.go`, review findings H1/H2 |
| Capacity | ✗ | NFR3 met as restated (D6): ~73 MB per busy core per day — a 4-core host stores ~4 GB over 14 days, and no host has been sized for it. No per-query memory cap on any API read — now blocker 14 |
| Backups | ✓ | `backup.sh` and `verify.sh` list `profiles-raw`; `profile_samples` is picked up with every table |
| Monitoring | ✗ | Platform blocker 7: nothing watches the profiles worker or its lag |

**Recommended for F17:**

- A profile flood spread across many keys can still crash-loop `worker` — and since 2026-09-20 that stops every signal, not profiles alone: fetches are
  bounded compressed, and profile messages decompress by orders of magnitude. An uncompressed `profiles-raw`
  or a fleet-wide budget would close it.
- A request whose produce partly fails is retried whole; the parts the broker kept count twice in the graph.
