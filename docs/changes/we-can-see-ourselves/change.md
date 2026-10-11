# Change: we-can-see-ourselves — every service reports its own health in a form a monitor can read

## Classification

Operations. Readiness group 7: blocker 7, with the consumer-lag and dead-letter metrics moved here from
group 1 and the console's production error sink (`docs/operations/production-readiness.md`). What
*watches* these signals waits for the hosting choice (group 8, founder decision 2026-10-10).

## Current behavior and problem

Read from the code on 2026-10-11:

1. **No scheduler service can be health-checked (blocker 7).** `scheduler-beat`, `scheduler-light`
   and `scheduler-deliveries` have no health endpoint and no compose healthcheck in any file. The old
   `health.serve_probes` was never called and was deleted with Redis (`8bf0f2f`); the readiness
   document's checklist row still points at it. A beat whose loop has hung keeps its container
   "running" while every customer's checks stop; a delivery worker that hangs leaves every alert in
   the outbox. Beat's only sign of life is an INFO log line every 60 s, from the leader alone
   (`beat.py:221-246`); the workers log nothing periodic (`jobs/worker.py`).
2. **Nothing exports a number a monitor can read.** No service has a metrics endpoint
   (`current-system-reference.md:271`: "all self-observation is structured log lines"). The Go
   services' counters exist, but only inside log lines: the worker's `ingest heartbeat` every 30 s
   (`consume/consume.go:552-561` — uncommitted records, parked messages per pipeline) and the
   receiver's `ingest summary` every 60 s (`otlp/traffic.go:140` — accepted and refused by reason).
3. **Consumer lag and dead-letter depth are not measured at all.** Nothing compares the worker's
   committed offsets with the topics' end offsets, so "the worker is falling behind" is invisible
   until a customer notices stale data. Nothing counts what sits in the `*-dlq` topics
   (`pipeline/dlq/dlq.go:8-10`: "Nothing drains this topic automatically").
4. **The console drops every error in production.** `apps/web/src/shared/logger.ts` sends to
   `noopSink` outside development, and `configureLogger` is never called. A screen that crashes for a
   customer leaves no trace anywhere.
5. **The Go `-health` flag probes the default port**, not the configured one
   (`RunSelfCheckIfAsked(os.Args, config.DefaultHealthAddr)`, receiver `main.go:40`, worker
   `cmd/worker/main.go:22`): set `RECEIVER_HEALTH_ADDR` to anything else and the container reports
   unhealthy forever.
6. **The on-prem file has no healthcheck on any service.**
7. **`monitoring.md` and `alerting.md` are stubs**, and `alerting.md` still calls customer alerting
   "separately planned" (F21 is built).

## Intended behavior

The standard shape: **each service answers `/healthz` (am I alive), `/readyz` (can I do my job) and
`/metrics` (Prometheus text) on its own internal port, which no edge routes.** Anything that watches
— Docker, a cloud monitor, an external uptime service — reads those. Nothing here chooses or runs the
watcher; group 8 connects one.

1. **The scheduler gets health endpoints, one per process** (`SCHEDULER_HEALTH_ADDR`, default
   `:8081`), with a liveness that means something: beat's `/healthz` fails when its loop has not
   ticked for 30 s, a worker's when it has not polled the queue for 30 s — a hung loop, not just a
   dead process. `/readyz` checks Postgres. A `python -m nexovue_scheduler health` self-check backs a
   compose healthcheck on all three services, in every compose file.
2. **`/metrics` on every service, Prometheus text format** (`prometheus/client_golang`,
   `prometheus_client`) — the format every cloud monitor and agent reads, with no exporter to run.
   The set is small and each one answers an operator question:

   | Service | Metric | The question it answers |
   |---|---|---|
   | worker | consumer lag per topic (records), oldest uncommitted batch age | Is ingest falling behind? |
   | worker | dead-letter depth per `*-dlq` topic; parked messages per pipeline | Is data being set aside? |
   | worker | each pipeline running (0/1) | Has a pipeline stopped? |
   | receiver | exports accepted and refused, by signal and reason; Kafka publish failures | Are customers being refused, and why? |
   | scheduler | beat leading (0/1), seconds since last tick; seconds since a worker last polled | Is anything scheduling? |
   | scheduler | jobs ready per queue, age of the oldest ready job | Are checks and alerts waiting? |
   | scheduler | alert deliveries waiting; erasures overdue | Are alerts and deletions stuck? |
   | api | requests by route group and status class, duration | Is the console failing? |

   Lag is read with franz-go's own `kadm` (end offsets minus the group's committed offsets),
   computed when scraped, not on the ingest path.
3. **The console sends its errors to the API**, which writes them as structured log lines
   (`service=nexovue-web`) next to every other service's: `POST /api/v1/client-logs`, warnings and
   errors only, batched, small, rate-limited, plus `window.onerror` and unhandled rejections. Logs to
   stdout are the one channel that works when Kafka or ClickHouse is the broken part (decision 0053,
   rule 2).
4. **The `-health` flag reads the configured address.**
5. **`monitoring.md` written from what exists** — every endpoint, every metric, and the thresholds
   worth alerting on — and `alerting.md` corrected. Both say plainly that nothing watches yet.

**Founder decisions, 2026-10-11 — both recommendations approved:**

- **Pull, not push** *(recommended)*: expose `/metrics` for a monitor to read, rather than sending
  nexovue's own telemetry into a nexovue organization (decision 0053 rule 3, deferred). A monitor
  that lives inside the thing it monitors goes dark exactly when it is needed; 0053's own rule 1 says
  the "nexovue is down" alert must come from outside. Self-telemetry into a nexovue organization can
  be added later on top, by a collector reading the same endpoints.
- **Console errors to the API's logs** *(recommended)*, rather than the browser SDK sending them into a
  nexovue organization — same reason: it works when the telemetry pipeline is what broke, and it
  needs no organization nexovue does not have.

**Which outside tool watches** is group 8's, decided with the hosting target. Nothing here depends
on the answer: anything that reads Prometheus text can read these endpoints.

## Affected specifications and architecture

- A new ADR: how nexovue exposes its own health (pull endpoints; amends decision 0053's rule 3 as
  still deferred and records rules 1 and 2 as met).
- `current-system-reference.md` (each service's health and metrics), `monitoring.md`, `alerting.md`,
  the readiness document (blocker 7, group 7, checklist rows).
- Compose ×3: scheduler healthchecks, every on-prem healthcheck, `SCHEDULER_HEALTH_ADDR`.

## Risks and rollback

- **`/metrics` must never be public.** It names topics and queues, not tenants, but it is still
  internal. The edge routes only `/api/*` and the ingest prefixes; a test holds the Caddyfile to that.
- **A scrape that asks Kafka for offsets costs a round trip.** Lag is computed per scrape with a
  timeout and reported as absent, not zero, when Kafka cannot answer.
- **Labels must stay bounded** — no tenant, project or path label, so a customer cannot grow the
  series count.
- Rollback: pre-deployment, `git revert`.

## Plan and validation

`plan.md`, after this record is approved. Validation: on the dev stack — kill the beat's loop and see
its healthcheck fail; stop the worker and watch lag rise on `/metrics` while the receiver accepts;
park a message and see dead-letter depth move; throw in the console and find the line in the API's
log; and `just check`.

## Status

`status.md`.
