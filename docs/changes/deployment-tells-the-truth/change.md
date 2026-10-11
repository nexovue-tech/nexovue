# Change: deployment-tells-the-truth — the deployment files start a working, protected install

## Classification

Infrastructure and Security. Readiness group 6: blockers 4, 11, 16, 18 and 19, plus the
request-body cap, security headers on the hosted path and `index.html` caching from *Recommended*,
the ClickHouse user that can only delete (moved here from group 4), and `API_INSTANCES` /
`RECEIVER_INSTANCES` set by the deployment (moved here from group 2b)
(`docs/operations/production-readiness.md`).

## Current behavior and problem

Read from the code on 2026-10-11:

1. **The API cannot start on any deployed install (blocker 4).** Outside development the settings
   validator refuses a localhost `OTLP_ENDPOINT` (`apps/api/src/nexovue_api/settings.py:298-323`),
   and neither `docker-compose.app.yaml` nor `docker-compose.onprem.yaml` passes `OTLP_ENDPOINT` to
   the API. Whatever the operator sets, the API exits at boot.
2. **Production accepts the `console` mail provider (blocker 11).** `console` writes each message to
   the log and reports success (`nexovue_platform/email/settings.py:20-23,82-89`). Nothing in either
   service's settings refuses it outside development, so a deployed install can be configured to send
   nobody a verification, password reset or alert, with nothing going red.
3. **Behind the edge, every customer shares one login throttle (blocker 16).** `TRUSTED_PROXY_COUNT`
   defaults to 0 (`settings.py:261`) and the hosted file never sets it (only on-prem does, `:58`).
   Every request then appears to come from the proxy's address, so the eleventh login, signup or
   reset attempt *across all customers* in a minute gets a 429.
4. **The hosted file has no edge (blocker 18).** It publishes the receiver (4317, 4318), the API
   (8000) and the console (8080) directly as plain HTTP. The console's nginx (`deploy/nginx/web.conf`)
   has no `/api` route — `try_files … /index.html` answers every API call with the HTML page and a 200
   — and no TLS or security header exists on this path. All of that lives only in the on-prem
   Caddyfile, which in turn routes the console to `web:8080`, a service the on-prem file does not
   declare (its own `TODO(caddy)` at `:137-139`).
5. **The content-security policy blocks checkout (blocker 19).** The billing screen loads
   `https://checkout.razorpay.com/v1/checkout.js` (`billing/razorpay.py:63`,
   `checkout-handoff.ts:55`), and Razorpay's checkout opens in a frame from its own origin. The CSP
   allows scripts from `'self'` plus one hash and frames from nowhere (`deploy/caddy/Caddyfile:77`), and
   `Permissions-Policy` sets `payment=()` (`:64`). The first hosted edge built from it makes every
   payment fail.
6. **No request-body cap on the API.** Starlette 1.6.0 has none of its own. Login, signup and reset
   read the whole JSON body; the rate limiter peeks only 8 KB (`middleware/ratelimit.py:58`). Three
   routes stream with their own caps (deploy webhook and billing webhook 64 KiB, check files 3 MiB);
   source maps arrive as a multipart upload that is spooled whole before its 64 MiB check runs
   (`sourcemaps/service.py:35,226`). An unauthenticated caller can make the API read gigabytes.
7. **`index.html` is cached heuristically.** `web.conf` sets `Cache-Control` on `/assets/` only, so a
   browser can keep an `index.html` that names hashed assets a deploy removed — a blank console.
8. **The nightly erasure connects as the ClickHouse administrator.** Both deployment files give
   `CLICKHOUSE_ERASER_DSN` the account the server was created with (`app.yaml:314`,
   `onprem.yaml:294`), which holds every grant (DROP, FILE, URL, S3). The erasure needs only
   `SELECT` and `ALTER DELETE` (`nexovue_scheduler/erasure/statements.py`, `store.py:57-80`). Dev does
   the same (`docker-compose.yaml:451`).
9. **The instance counts are a default no deployment sets.** Each API process enforces its rate limits
   divided by `API_INSTANCES` and each receiver its ingest limit divided by `RECEIVER_INSTANCES`
   (ADR-020). Both default to 1 and neither file sets them, so a second replica silently doubles every
   limit.
10. **Stale comments** in the files this change touches: `app.yaml:221-222` ("the on-prem file, where
    signup stays open" — it is closed), and the `TODO(hosting)` read-only-user note on
    `scheduler-light` in both files (done by group 2a).

## Intended behavior

**One edge, the same on both deployment files.** The on-prem Caddy edge becomes the edge of the
hosted replica too, so blockers 18 and 19, the security headers and the caching are fixed once, in one
Caddyfile, and tested once.

1. **Caddy serves the console's files itself, and both files use it** (ADR-019 point 3: "the separate
   nginx image goes; the console's files are copied at deploy time to the edge proxy"). A
   `web-assets-init` container copies the built files from the web image into a volume; Caddy serves
   them, with `/index.html` sent `Cache-Control: no-cache` and `/assets/*` immutable for a year, and
   every unknown path falling back to `index.html`. `/api/*` goes to the API. The ingest name goes to
   the receiver. **No other container publishes a port** — the replica publishes 80 and 443 only.
   This also fixes the on-prem console route (part of blocker 10). The nginx config goes; the web
   image becomes a carrier of files until group 8 moves them into the `nexovue` image.
2. **The edge's names decide the URLs (blocker 4).** Both files take `NEXOVUE_APP_HOST` and
   `NEXOVUE_INGEST_HOST` once and derive from them: `OTLP_ENDPOINT=https://<ingest host>`,
   `APP_BASE_URL=https://<app host>`, and the CORS and MCP host settings where they need it. One
   name, set once, cannot disagree with itself. The replica uses `app.localhost` and
   `ingest.localhost`, for which Caddy issues certificates from its own local authority.
3. **`TRUSTED_PROXY_COUNT=1` on the API in both files (blocker 16)**, because Caddy is the one hop.
   A cloud load balancer in front of Caddy makes it 2; that is recorded for group 8's hosting choice.
4. **Checkout works under the CSP (blocker 19).** The Razorpay origins are added to `script-src`,
   `frame-src` and `connect-src`, read from one Caddy variable that the hosted file sets and the
   on-prem file leaves empty — a self-hosted install loads no payment script. The exact origins are
   confirmed by paying through the edge with a test key in a real browser and reading the console
   for violations, not from memory (Razorpay publishes no CSP list).
5. **Production refuses `console` mail (blocker 11)**, in the API and the scheduler, naming
   `EMAIL_PROVIDER`. Mail left *unconfigured* stays allowed: every route that needs mail already
   refuses by variable name, and an air-gapped on-prem install has no provider to reach. That a real
   provider domain is verified is a fact about an account, not the repository; it is recorded for
   group 8.
6. **A request-body cap on the API, and one at the edge.** A pure ASGI middleware counts bytes as
   they arrive — not trusting `Content-Length` — and answers 413 past the route's cap: 1 MiB by
   default, with the source-map upload allowed its 64 MiB plus multipart overhead. The edge caps
   `/api/*` at the largest of these, so an oversized body is refused before it reaches Python at all.
7. **The eraser connects as a user that can only read and delete.** `deploy/clickhouse/users.d/
   eraser.xml`: `GRANT SELECT, ALTER DELETE ON nexovue.*`, its own password, used by the scheduler in
   all three compose files.
8. **The instance count is one setting.** In both files `API_INSTANCES` sets the API's
   `deploy.replicas` *and* its environment, and `RECEIVER_INSTANCES` the same for the receiver, so the
   number of copies and the share each enforces cannot drift apart.

**Founder decisions, 2026-10-11 — both recommendations approved:**

- **The edge serves the console's files, on both files** *(recommended)* — rather than keeping the
  nginx web container behind Caddy on the hosted replica only. It is ADR-019's end state, fixes the
  on-prem console route on the way, and leaves one edge configuration instead of two.
- **Unconfigured mail stays allowed in production; only `console` is refused** *(recommended)* —
  rather than demanding a provider. Demanding one would stop the replica from starting on a laptop
  without a real mail account, and stop an air-gapped install entirely.

## Affected specifications and architecture

- ADR-019 point 3 is executed early for the edge; its image change stays in group 8.
- `docs/operations/deployment.md`, `current-system-reference.md` (deployment, API settings, the
  eraser), `.env.example`, the readiness document (blockers 4, 11, 16, 18, 19; group 6).
- `deploy/`: both deployment compose files, dev compose (the eraser), the Caddyfile, a new
  `users.d/eraser.xml`, `web.conf` deleted, `apps/web/Dockerfile`.

## Risks and rollback

- **Docker's round-robin DNS behind Caddy.** With `deploy.replicas` above 1, Caddy must spread
  requests across every copy, not dial the first address forever. Verified with two API replicas on
  the replica stack, not assumed.
- **A body cap set too low refuses a legitimate request.** Every route that takes a body is listed in
  the plan with its largest legitimate size.
- **The CSP can still block something the checkout loads late** (a bank's 3-D Secure page, a UPI
  app). The browser test covers card and UPI test payments; anything else that fires is a violation
  visible in the console, not a silent failure.
- **`just dev-app` has to keep running on a laptop.** It needs `app.localhost`/`ingest.localhost`
  and ports 80 and 443 free.
- Rollback: pre-deployment, `git revert`.

## Plan and validation

`plan.md`, after this record is approved. Validation: the replica stack brought up through the edge
— sign up, export a span to `https://ingest.localhost`, see it in the console, pay with a test key,
a body over the cap refused at the edge and at the API, the eraser's grants proved by a refused
`DROP`, and `just check`.

## Status

`status.md`.
