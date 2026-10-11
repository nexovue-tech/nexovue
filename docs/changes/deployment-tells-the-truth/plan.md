# Plan: deployment-tells-the-truth (readiness group 6)

> **Status:** Complete 2026-10-11
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` approved 2026-10-11, with both recommended decisions: the edge serves the console's files
  on both deployment files; production refuses `console` mail and allows mail left unconfigured.

## Current system

Read from the code on 2026-10-11, adding to what `change.md` already records:

1. **The replica has never had an env file.** `just dev-app` (`justfile:35-36`) runs
   `docker compose -f deploy/compose/docker-compose.app.yaml up -d --wait --build` with no `--env-file`,
   and `deploy/compose/.env` does not exist, so every `:?` variable stops it. Its env example,
   `deploy/compose/.env.example`, still lists `OTLP_ENDPOINT` as an on-prem variable (`:59-64`), lacks
   `CLICKHOUSE_READER_PASSWORD`, both encryption keys, `APP_BASE_URL` and `NEXOVUE_TLS_EMAIL` for the
   replica.
2. **The replica's stores start empty, with no schema and no topics.** Nothing in either deployment
   file runs migrations or `topics.sh` (blocker 5, group 8). The live check here runs them by hand,
   and says so.
3. **The infrastructure module publishes no host port** (`compose.infra.yaml`); only the dev file adds
   them. So the replica can run beside the dev stack, apart from host ports 80 and 443.
4. **The API is reached under `/api/v1` on the console's own host** (`settings.API_PREFIX`,
   `settings.py:70`), so behind one edge the console and the API share an origin: no CORS setting is
   needed, and `API_PUBLIC_URL` can stay empty (it falls back to the app's host, `settings.py:420-426`).
5. **Caddy already handles the forwarded address the way the API expects.** Without
   `trusted_proxies` it ignores an incoming `X-Forwarded-For` and sets the client's own address (Caddy
   `reverse_proxy` docs), so with Caddy as the one hop the API's `TRUSTED_PROXY_COUNT=1` reads the
   real caller.
6. **Caddy with a plain `api:8000` upstream does not spread across replicas** — the documented way is
   a `dynamic a` upstream, re-resolved on a `refresh` interval; the default policy is `random`.
7. **The middleware stack is one function**, `middleware/stack.install_middleware`
   (`stack.py:21-38`): rate limit, request log, correlation id, exception boundary (outermost last).
   The rate limiter peeks 8 KiB of auth bodies (`ratelimit.py:58`).
8. **Body sizes the API legitimately takes**, read from the schemas:

   | Route | Largest legitimate body | Source |
   |---|---|---|
   | `POST /api/v1/source-maps` (multipart) | 64 MiB map + form fields | `sourcemaps/service.py:35` |
   | `POST /api/v1/check-imports/*` | 5 M characters of text, so up to ~20 MiB UTF-8 in JSON | `checkimports/schemas.py:27` |
   | `POST /api/v1/check-files` (streamed, own cap) | 3 MiB | `checkfiles/schemas.py:12` |
   | a check with an inline body | 256 K characters, so up to ~1 MiB | `nexovue_platform/checks/config.py:37` |
   | billing and deploy webhooks (streamed, own cap) | 64 KiB | `billing/router.py:51`, `deploys/router.py:38` |
   | everything else | well under 64 KiB (the assistant's 32 K characters is the next largest) | |

   So a default of **2 MiB** with two named exceptions (source maps, check imports) refuses nothing
   legitimate.
9. **The scheduler reads the same `EMAIL_PROVIDER` literal** (`nexovue_scheduler/settings.py:103`)
   through the shared `email_settings` (`nexovue_platform/email/settings.py`); each service has a
   production validator to sit beside (`settings.py:349` API, `:148` scheduler).
10. **The erasure's ClickHouse calls** are `ALTER TABLE … DELETE` with `mutations_sync` as a query
    setting, `SELECT` counts, and a `SELECT` on `system.mutations` (`erasure/store.py:57-80`). So the
    eraser needs `SELECT, ALTER DELETE` and must not be `readonly` (a readonly user cannot send
    `mutations_sync`); system tables are readable without a grant by default.
11. **The reader account is the pattern to copy** — `deploy/clickhouse/users.d/reader.xml`, mounted
    by `compose.infra.yaml:94`, password from `CLICKHOUSE_READER_PASSWORD` with a dev default
    (`:72`).
12. **Tests already read these files.** `apps/api/tests/test_caddyfile_headers.py` ties the CSP hash
    to `apps/web/index.html`; `test_compose_dependencies.py` and `test_compose_worker_memory.py` parse
    every compose file. The new tests follow them: files only, no Docker, so they run in `just check`.
13. **The web image is nginx** (`apps/web/Dockerfile:16-21`), used by the replica as a running service
    and by on-prem's `web-assets-init` as a carrier (`onprem.yaml:262-267`, copying
    `/usr/share/nginx/html`).

**One finding against `change.md`:** checkout cannot be exercised on the replica as it stands, because
the replica runs `ENV=production` and group 3 refuses a test key there. The browser check of the CSP
therefore runs the replica's API with `ENV=staging` through a scratch override file — the edge, the CSP
and the console are the production ones. Nothing is changed in the repository for it.

## Change required

`change.md`, *Intended behavior*.

## Impact

| Area | What changes |
|---|---|
| Files | `deploy/caddy/Caddyfile`; `deploy/compose/docker-compose.app.yaml`, `docker-compose.onprem.yaml`, `docker-compose.yaml`, `compose.infra.yaml`, `.env.example` (compose); new `deploy/clickhouse/users.d/eraser.xml`; `deploy/nginx/web.conf` deleted; `apps/web/Dockerfile`; API `middleware/body_limit.py` (new), `middleware/stack.py`, `settings.py`; scheduler `settings.py`; py-platform `email/settings.py`; root `.env.example`, `justfile` |
| Services | Edge (Caddy, now on both files), API, scheduler; ClickHouse users |
| Schema / migrations | None |
| UI | None — the console is served from a different place, unchanged |
| Tests | new `apps/api/tests/test_deployment_files.py`; `test_caddyfile_headers.py` extended; new `apps/api/tests/test_body_limit.py`; settings tests in both services and py-platform's email tests; `apps/scheduler/tests/test_erasure_live.py` runs as the eraser; `test_compose_dependencies.py`'s decided list |

## Reuse

- The on-prem Caddyfile's header snippets, unchanged in substance.
- `reader.xml`'s user shape and its compose wiring for the eraser.
- `email_settings` and `EmailProvider` — one rule both services call.
- The streamed caps in the webhook and check-file routes stay as they are; the new middleware is the
  floor under them, not a replacement.
- The compose-file test helpers in `test_compose_dependencies.py`.

## Gaps

- A body-limit middleware (none exists; Starlette 1.6.0 has none).
- An eraser ClickHouse user.
- A test that reads both deployment files for what a deployed install must have.

## Steps and tasks

### Step 1 — The replica runs behind its edge

*Judgeable:* with a filled env file, `just dev-app` brings the replica up; `https://app.localhost`
shows the console, sign-in works, a span exported to `https://ingest.localhost` appears in it, and
`docker compose ps` shows only the proxy publishing ports.

- [x] **T1** — `apps/api/tests/test_deployment_files.py` (new, written first and seen red) · for both
  deployment files: only `proxy` publishes ports; the API's `OTLP_ENDPOINT` is built from
  `NEXOVUE_INGEST_HOST` and `APP_BASE_URL` from `NEXOVUE_APP_HOST`; `TRUSTED_PROXY_COUNT` is 1;
  `API_INSTANCES` sets both the API's environment and its `deploy.replicas`, `RECEIVER_INSTANCES` the
  receiver's; every Caddy upstream names a service and port the file declares; no `web` service.
- [x] **T2** — `deploy/caddy/Caddyfile` · the console's files served from `/srv/web` with an
  `index.html` fallback; `Cache-Control: no-cache` on everything outside `/assets/`, a year and
  `immutable` on `/assets/*`; `/api/*` and the two ingest prefixes through `dynamic a` upstreams; the
  stale `TODO(caddy)` route to `web:8080` goes · proved by T1 and `test_caddyfile_headers.py` (new
  case: the caching headers).
- [x] **T3** — `apps/web/Dockerfile` · the final stage carries the built files only (a small pinned
  base with `sh` and `cp`, files at `/srv/web`), no nginx; `deploy/nginx/web.conf` deleted · proved by
  the image building and T5's `web-assets-init` copying from it.
- [x] **T4** — `deploy/compose/docker-compose.app.yaml` · a `proxy` service and `web-assets-init` as
  on-prem has them; the `web` service and every other published port go; the API gets
  `OTLP_ENDPOINT`, `APP_BASE_URL` (moved out of the shared block into its derived form for the
  scheduler too), `TRUSTED_PROXY_COUNT: 1`, `API_INSTANCES` with `deploy.replicas`; the receiver gets
  `RECEIVER_INSTANCES` with `deploy.replicas`; the stale comments (`:221-222`, `TODO(hosting)`) go ·
  proved by T1.
- [x] **T5** — `deploy/compose/docker-compose.onprem.yaml` · the same derivations and counts; the
  proxy's `TODO(caddy)` and the console route resolved; `web-assets-init` copies from `/srv/web`;
  `TRUSTED_PROXY_COUNT` moves from the shared block onto the API, which alone reads it · proved by T1.
- [x] **T6** — `deploy/compose/.env.example` · the replica's full list (hosts, TLS email, reader and
  eraser passwords, both keys, instance counts), `OTLP_ENDPOINT` removed as derived; `justfile`
  `dev-app` takes `--env-file deploy/compose/.env` explicitly · proved by the live run.

### Step 2 — Checkout works under the policy, and bodies are capped

*Judgeable:* through the edge, a test-mode card payment and a UPI payment complete with no CSP
violation in the browser console; a 3 MiB login body is refused 413 at the edge and, sent to the API
directly, 413 by the API.

- [x] **T7** — `deploy/caddy/Caddyfile` · `{$NEXOVUE_CHECKOUT_ORIGINS}` added to `script-src`,
  `frame-src` and `connect-src`; `payment=()` lifted to `payment=(self <origin>)` only where checkout
  needs it, decided by the browser run; `request_body` `max_size` on `/api/*` at the API's largest cap
  · proved by `test_caddyfile_headers.py` (new cases: the variable is in all three directives; the edge
  cap is not below the API's largest).
- [x] **T8** — both deployment files · `NEXOVUE_CHECKOUT_ORIGINS` set on the hosted proxy, empty
  on-prem · proved by T1 (new case).
- [x] **T9** — `apps/api/src/nexovue_api/middleware/body_limit.py` (new) · pure ASGI: HTTP only,
  `Content-Length` rejected early when declared too large, bytes counted as received otherwise, 413
  problem+json in the shape `errors.py` sends; caps: 2 MiB default, source maps
  `MAX_MAP_BYTES + 1 MiB`, check imports `4 × MAX_IMPORT_CHARACTERS + 1 MiB`, each imported from its
  module rather than restated; `stack.py` installs it outside the rate limiter · proved by
  `apps/api/tests/test_body_limit.py` (new): a declared oversize, a chunked oversize with no length,
  a lying `Content-Length`, each exception route at its cap, and a WebSocket untouched.

### Step 3 — Production refuses console mail; the eraser can only delete

*Judgeable:* `ENV=production EMAIL_PROVIDER=console` stops both the API and the scheduler at start,
naming the variable; on the dev stack the erasure runs as `nexovue_eraser`, and that account is refused
a `DROP TABLE` and an `INSERT`.

- [x] **T10** — `packages/py-platform/src/nexovue_platform/email/settings.py` · one
  `refuse_console_outside_development(env, provider)` both services call; API `settings.py` and
  scheduler `settings.py` validators · proved by py-platform's email tests and both services' settings
  tests (production + console refused; production + unset allowed; development + console allowed).
- [x] **T11** — `deploy/clickhouse/users.d/eraser.xml` (new) · `nexovue_eraser`, password from
  `CLICKHOUSE_ERASER_PASSWORD`, `GRANT SELECT, ALTER DELETE ON nexovue.*`; `compose.infra.yaml`
  mounts it with a dev default password; dev, hosted and on-prem files point
  `CLICKHOUSE_ERASER_DSN` at it (the deployment files demand the password) · proved by
  `test_erasure_live.py` running as the eraser and a new case there: `DROP TABLE` and `INSERT`
  refused; T1 (new case: no deployment file gives the eraser the administrator account).

### Step 4 — Documents

- [x] **T12** — `docs/operations/deployment.md` (the edge, the env file, the two names),
  `current-system-reference.md` (edge, body cap, eraser, mail rule), root `.env.example`
  (`CLICKHOUSE_ERASER_DSN` example, the mail rule), the readiness document (blockers 4, 11, 16, 18, 19
  and group 6; *Recommended* items done), ADR-019 (point 3 executed for the edge), this plan's
  Outcome and `status.md`.

## New risks

- **Docker Desktop must bind 80 and 443** for the replica. If either is taken on the laptop, the live
  run uses override ports through a scratch file and says so.
- **A running replica beside the dev stack doubles the infrastructure's memory.** The live run may
  stop the dev stack first (`just down`, volumes kept).

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Which Razorpay origins the checkout needs | No — answered by the browser run in Step 2 | The Caddyfile comment and `NEXOVUE_CHECKOUT_ORIGINS` in the hosted file |

## Gates

- [x] Specification approval — `change.md`, 2026-10-11
- [x] Design approval — `change.md`
- [x] **Plan approval — before the first edited file** — 2026-10-11
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Design QA — not customer-facing UI
- [x] Production-readiness assessment — the readiness document updated
- [x] Documentation and status update

## Validation plan

- The new file tests (T1, T7, T8, T11) and `test_body_limit.py` seen red before their fix.
- `just check`, counts read.
- Live, on the replica: up through the edge, migrations and topics by hand; sign-in, a span exported
  through `https://ingest.localhost`, the response headers read with `curl`, two API replicas taking
  turns (request log), 413 at the edge and the API, the payment in a browser with the console read for
  violations.
- Live, on the dev stack: `test_erasure_live.py` as the eraser.

## Rollout and rollback

Pre-deployment, `git revert`. The real risk is the replica: it has never run, so Step 1 is likely to
find something neither file shows. Each finding is fixed in the file it belongs to and recorded in the
Outcome.

## Outcome

Built as planned, with these differences, each found by running the thing rather than reading it:

- **The console's production image did not build.** The console build reads `docs/product/setup`
  (since `f8ae63b`, 2026-09-14) and the Dockerfile never copied it, so `just dev-app` could not have
  run for four weeks. `apps/web/Dockerfile` copies it now.
- **A blank `EMAIL_PROVIDER` stopped both services from starting.** Both deployment files pass
  `${EMAIL_PROVIDER:-}`, and the literal refused `''` — so an install without mail could not start at
  all. Blank now means unset in the API and the scheduler (tests in both).
- **The eraser needs `SELECT ON system.mutations`.** The plan assumed system tables need no grant;
  the live erasure suite, run as the new account, was refused. Granted, and the suite is that proof.
- **Checkout needs two origins nobody documents** — `cdn.razorpay.com` (a risk script) and
  `lumberjack.razorpay.com` (tracking). Read from the browser run; the policy now allows four.
  `payment=()` in `Permissions-Policy` did not block the checkout opening; it was left as it was.
  Razorpay's script also injects inline styles, so the report-only `style-src 'self'` experiment
  ends: `'unsafe-inline'` stays while the console takes payments.
- **The replica's settings file is `deploy/compose/.env.replica`, not `.env`.** Compose reads
  `deploy/compose/.env` by itself for every file in that directory; the first run's file leaked into
  the dev stack, whose Kafka refused the foreign cluster id (nothing was written with the wrong
  values; the dev stores were checked on their own credentials afterwards).
- **`frame-src` keeps `'self'`**, so a self-hosted install's empty checkout list does not forbid the
  console's own frames.
- No py-platform email test file exists; the shared rule is proved through both services' settings
  tests.

**Verified live on the replica** (`-p nexovue-replica`, fresh volumes; schema, topics and bucket by
hand — blocker 5): only the proxy publishes ports; the page `no-cache`, hashed assets a year, a
missing asset 404 without it, deep links served the page, all with HSTS, CSP and the other headers;
sign-in, an ingest key, a span exported to `https://ingest.localhost` and read back through the
console's API; two API replicas both answering behind `dynamic a`; 413 problem+json for a 3 MiB
login and a 66 MiB source map; two clients on fixed addresses — one exhausting its ten logins left
the other admitted with `TRUSTED_PROXY_COUNT=1`, and refused with 0; the billing screen and
Razorpay's checkout through the edge with no enforced violation.

**Not verified:** a completed test payment (Razorpay's contact field refused automated input; the
checkout itself opened clean); anything against a real hostname or certificate authority.

**Found, not fixed (recorded in the readiness document):** the billing screen quotes USD when the
account offers only INR, so the upgrade fails at the last button.
