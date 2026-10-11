# Status: deployment-tells-the-truth

## Lifecycle status

Verified (production replica and dev stack); not committed

## Current task

Founder review of the diff.

## Completed

- `change.md` approved 2026-10-11 with both recommended decisions; `plan.md` approved and complete.
- One Caddy edge on both deployment files: console files served by it (`no-cache` page, immutable
  hashed assets), `/api/*` to every API replica, ingest to every receiver, TLS and the security
  headers, body cap; nginx config deleted, the web image carries files only.
- `OTLP_ENDPOINT` and `APP_BASE_URL` built from the edge's two names; `TRUSTED_PROXY_COUNT=1`;
  `API_INSTANCES`/`RECEIVER_INSTANCES` set replicas and limits together.
- Razorpay's four checkout origins in the hosted CSP, none on-prem.
- `console` mail refused in production in both services; a blank provider means unset.
- Request-body cap middleware with a `content-too-large` 413 problem.
- `nexovue_eraser` ClickHouse account in all three compose files.
- Found and fixed on the way: the web image did not build (setup docs not copied); a blank
  `EMAIL_PROVIDER` stopped both services starting; the eraser needs `SELECT ON system.mutations`.

## Next action

Founder reviews and says "commit". Then readiness group 7 (we can see ourselves).

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **No test payment was completed through the edge.** The checkout opened with nothing blocked by
  the policy, but Razorpay's contact field refused automated input. A later step of a real payment
  (a bank's 3-D Secure page, a UPI app) could still load something new; it would show as a
  console violation.
- **`Permissions-Policy: payment=()`** stays; it did not stop the checkout opening, and was not
  exercised past it.
- **Nothing ran against a real hostname or certificate authority**; the replica uses Caddy's local
  authority on `*.localhost`.
- **The edge's body cap** limits what is forwarded; a declared oversize is refused by the API
  before it reads the body, not by Caddy.
- **The billing screen quotes USD when the account offers only INR** — found here, recorded in the
  readiness document, not fixed.
- Leftovers: the replica's volumes (`nexovue-replica_*`, and an older `nexovue-app_*` set), the local
  `deploy/compose/.env.replica` (gitignored, throwaway values), and Razorpay test-mode subscriptions
  created by the checkout runs.

## Validation

- New file tests seen red first: `test_deployment_files.py` 20 failing before the files changed;
  `test_caddyfile_headers.py` 5 failing against the old Caddyfile; `test_body_limit.py` import-red,
  and its FastAPI case 400 instead of 413 with a plain exception; settings tests 3 failing in the two
  services; `test_erasure_live.py` erroring before the account existed and refused
  `system.mutations` before the grant.
- `docker compose config` on both files; `caddy validate` and `caddy adapt` with and without
  checkout origins.
- Live on the replica and the dev stack: see the plan's Outcome.
- `just check`: exit 0 — Python 4273 passed, 2 skipped; web 1352; browser SDK 120. The run before
  it failed only `test_dbstats_live::test_a_statement_timed_twice_does_not_weight_the_trend_it_is_in`
  (700 ms reported against an expected 500, under the parallel run), which passes alone and which
  this change does not touch. It also failed once in group 5: a flaky test, not chased here.

## Specification impact

None.

## Documentation impact

Updated: `docs/operations/deployment.md` (the replica procedure), `current-system-reference.md`
(edge, body cap, eraser, mail rule; the stale nginx gap removed), `production-readiness.md`
(checklist rows, blockers 4, 11, 16, 18, 19, three recommendations, group 6, the billing finding),
ADR-019 point 3, both `.env.example` files.

## Last updated

2026-10-11
