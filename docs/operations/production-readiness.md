# Production readiness status

## Current status

**NOT READY.** Assessed 2026-09-16 against a release of both hosted SaaS **and** a self-hosted install.
There is no release date: hosted deployment follows the first paying cloud customer
([ADR-008](../decisions/ADR-008-the-website-takes-the-first-payment.md)), and this list is the deployment
track of Phase 1 (`feature-catalog.md` §5). Scope: the whole repository.

There is no deployment target, so nothing below was checked against one. Every verdict is against
the repository and the local stack, and says so. A green `docker compose` on a laptop is not evidence
about production.

## Checklist, item by item

`✓ verified` · `✗ missing` · `? unknown` — each with what was read or run.

| Item | Verdict | Evidence |
|---|---|---|
| Hosting target | ✗ | `docs/operations/deployment.md`: "no approved production hosting target" |
| Configuration / secrets | ✗ | App and on-prem compose demand secrets with `${VAR:?}` (13 and 18 uses); dev defaults (`nexovue_dev`) live only in `compose.infra.yaml`. **No external secrets manager** anywhere in `deploy/` or `apps/*/src` |
| Migrations as a deploy step | ✗ | Neither `docker-compose.app.yaml` nor `docker-compose.onprem.yaml` runs alembic or the 31 ClickHouse migrations; only the manual `just migrate` |
| Capacity | ? | Every limit is the laptop budget (`TODO(sizing)` in the on-prem compose; `memory.xml`). No load test against a target, because there is no target |
| Health checks | ✓ | `docker-compose.app.yaml`: receiver `-health`, worker `-health`, API `/healthz`, web `wget` (lines 62, 108, 142, 209). `/healthz` is liveness only, by design (`health/router.py`) |
| Logging / monitoring / alerting | ✗ | Services log structurally, but nothing watches nexovue itself. `monitoring.md` and `alerting.md` are stubs. F21 (2026-09-19) alerts customers about *their* checks and services; nothing is configured to alert us about nexovue, and no scheduler service has a healthcheck, so a dead `scheduler-deliveries` worker is visible only as notifications left `pending` |
| Backup and restore evidence | ✗ | Tooling exists and is good (`just backup`, `verify.sh` compares row counts, schema revisions and ingest watermarks). **No restore has ever been performed**; no schedule, no off-host copy, no RPO/RTO |
| Rollback | ✗ | Every ClickHouse migration has a `.down.sql`; `rollback.md` is a stub; never rehearsed |
| TLS / public endpoints | ✗ | `deploy/caddy/Caddyfile` covers on-prem only. `otlp_endpoint` defaults to `http://localhost:14318`, which `settings.py` refuses at start-up — so the API cannot start in production until a real ingest hostname exists |
| Security review | ✗ | None performed. **Clean:** git-history secret scan and transitive licence audit (2026-09-15). F18 (2026-09-17) removed browser checks and their plaintext `fill` text; check credentials are sealed, write-only secrets (ADR-010) |
| Release process | ✗ | No `.github/` directory; no registry (`TODO(registry)`); no version file, changelog or release tag |
| Smoke tests | ✗ | Eight e2e specs run against the local stack only; nothing runs after a deploy |
| Operational ownership | ✗ | One person; `incident-response.md` is seven lines; no on-call path |
| Invite-only access | ✗ | Signup is open to anyone; `auth/policy.py:5`: "there is no invite flow"; no setting closes signup |
| Email in production | ? | Provider is `resend` or `console` (`settings.py:50`); whether a Resend account and a verified sending domain exist was not checked |
| Self-hosted mode | ✗ | No deployment-mode concept in the code; billing and the receiver's allowance valve are unconditional |
| On-prem compose | ✗ | Declares itself "SKELETON, NOT YET RUNNABLE" with ten `TODO`s; never run |
| Check secret key | ✗ | `CHECK_SECRET_KEY` seals check secrets in the API and opens them in the probe workers (ADR-010). The app and on-prem compose files demand it; no key exists for any deployment, and no rotation procedure exists — rotating it today would make every stored check secret unreadable |

## Blockers

Each names the failure it prevents.

1. **No hosting target.** There is nothing to deploy to, and no item above can be verified until there is.
2. **Images are built on a laptop.** With no CI, registry or tag, nobody can say which commit is running on the server, and a bad deploy has no known-good image to return to.
3. **Signup is public.** Anyone who finds the signup page creates an organization and sends telemetry to our servers at our cost, capped only by the free allowance. An invite-only beta cannot be enforced.
4. **No ingest hostname with TLS.** The API refuses to start with the default `otlp_endpoint`; without a public TLS host, no partner can send data.
5. **Migrations are not part of a deploy.** A new image against an old schema starts services whose queries fail, and workers write against columns that do not exist yet.
6. **No backup has ever been restored.** A lost volume takes every partner's accounts and telemetry with it, with no rehearsed way back.
7. **Nothing tells us nexovue is down.** A dead receiver drops partners' telemetry and the first signal is a partner's message.
8. **No check secret key and no way to rotate one.** Without `CHECK_SECRET_KEY` no check can use a secret; with one and no re-sealing procedure, a leaked key cannot be replaced without losing every stored secret.
9. **Self-hosted installs believe they are hosted.** They show a Razorpay billing screen and cap ingestion on the operator's own disk.
10. **The on-prem compose has never run.**
11. **Email delivery is unverified.** If production sends through `console`, partners cannot verify an address or reset a password.

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

Recommended for F21:

- A read-only ClickHouse user for `scheduler-light` (`TODO(hosting)` in both compose files) — today it reads
  telemetry with the API's account, which can write.
- A healthcheck for the three scheduler services, and something watching the age of the oldest `pending`
  notification.

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
| Capacity | ✗ | NFR3 met as restated (D6): ~73 MB per busy core per day — a 4-core host stores ~4 GB over 14 days, and no host has been sized for it. No per-query memory cap on any API read (`READ_SETTINGS` bounds rows and time only) |
| Backups | ✓ | `backup.sh` and `verify.sh` list `profiles-raw`; `profile_samples` is picked up with every table |
| Monitoring | ✗ | Platform blocker 7: nothing watches the profiles worker or its lag |

**Recommended for F17:**

- A per-query `max_memory_usage` in `READ_SETTINGS` for every API read — a heavy read of any signal can still
  exhaust a shared ClickHouse; the profile reads are bounded by their shape alone (196 MiB for a busy day).
- A profile flood spread across many keys can still crash-loop `worker` — and since 2026-09-20 that stops every signal, not profiles alone: fetches are
  bounded compressed, and profile messages decompress by orders of magnitude. An uncompressed `profiles-raw`
  or a fleet-wide budget would close it.
- A request whose produce partly fails is retried whole; the parts the broker kept count twice in the graph.

## Recommended

- Load-test against the chosen host, and replace the laptop limits with measured ones.
- Rehearse one rollback to the previous tag.
- Set beta plan limits deliberately. The free plan is 7-day retention and 2 GiB per service, and `0005_plans_table.py` says these rows "are expected to be edited before launch".
- An external secrets manager instead of an environment file on the host.
- Write runbooks only from procedures actually executed.
- If the repository goes public for the self-hosted release: the CLA text (ADR-005) and `CONTRIBUTING.md`.
