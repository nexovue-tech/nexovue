# Plan: we-can-see-ourselves (readiness group 7)

> **Status:** Complete 2026-10-11
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` approved 2026-10-11 with both recommendations: pull (`/metrics`), and console errors to
  the API's logs. Which monitor reads them is group 8's.

## Current system

Read from the code on 2026-10-11:

1. **Scheduler entry** — `nexovue_scheduler/__main__.py:32-57`: `beat` or `worker [--queues]
   [--concurrency]`, each `asyncio.run` of one loop with SIGTERM/SIGINT setting an event (`:87-92`).
   No health server; `apps/scheduler/Dockerfile` has no `EXPOSE` or `HEALTHCHECK`.
2. **Beat's loop** — `beat.py:169-209`: `tick()` every 1 s as leader (it touches Postgres through
   `leader.keep()` and the enqueue), every 5 s as follower. Its heartbeat is a log line, leader only
   (`:221-246`), with cumulative counters already kept on the instance (`_ticks`, `_enqueued`,
   `_enqueue_failures`, `_refreshes`, `_refresh_failures`).
3. **The worker's loop** — `jobs/worker.py:119-146`: claims every `POLL_SECONDS` (1 s) or on NOTIFY;
   jobs run in child processes (`Slot`); `_record` already distinguishes retried, killed and finished
   (`:172-190`). Nothing records when it last polled.
4. **Queue state lives in two tables**: `jobs` (`queue`, `run_after`, `lease_until`, `attempts`,
   `max_attempts`, `expires_at` — `jobs/queue.py:42-64`) and `notifications`, the alert outbox
   (`state` `pending`/`delivering`/…, `deliver_after`, `lease_until` — `alerts/outbox.py:64-65`).
   Erasures overdue: `erasure/owed.overdue` (`owed.py:91-102`).
5. **Go admin servers** — one `http.Server` per service on `RECEIVER_HEALTH_ADDR`/`WORKERS_HEALTH_ADDR`
   (`:8081`), built from `health.Handler` (`internal/health/server.go:35-39`, a `ServeMux`) plus
   optional pprof. The `-health` self-check uses `config.DefaultHealthAddr`, not the configured
   address (receiver `cmd/receiver/main.go:40`, worker `cmd/worker/main.go:22`).
6. **The worker's Kafka** — one shared `kgo.Client` in `worker/run.go:80`; each pipeline its own
   consumer group (`consume.go:112`, groups in `config.go` and `usage/usage.go:191-246`) and
   dead-letter topic (`consume.go:170`). Parked counts are cumulative counters inside each
   pipeline's `Holding()` (`messages_parked`, `spans_parked`). `kadm` (franz-go's admin package,
   v1.19.0) is not yet a dependency; it is the same module family as the pinned `franz-go v1.21.6`.
7. **The receiver's counters** — `otlp/traffic.go:153-230`: accepted/refused requests, items per
   signal, a tally per refusal and degradation reason, all with **fixed key sets**, but each is
   *taken* (reset) by the summary log every window (`:275`), so none is cumulative.
8. **No metrics library** in any service: no `client_golang`, no `prometheus_client`
   (`go.mod`, both `pyproject.toml`s).
9. **API** — `/healthz` and `/readyz` at the root (`health/router.py`, included at `main.py:110`),
   exempt from rate limiting and the request log. The edge routes only `/api/*` there
   (`deploy/caddy/Caddyfile`), so a root `/metrics` is not reachable from outside.
10. **Console** — `shared/logger.ts`: `consoleSink` in development, `noopSink` otherwise (`:48-58`);
    `configureLogger` never called. **The error capture is already built**:
    `shared/global-handlers.ts` (window `error` and `unhandledrejection`), React 19's
    `onUncaughtError`/`onCaughtError`/`onRecoverableError` (`main.tsx:15-34`), and
    `ErrorBoundary.tsx:40`. Only the production sink is missing.
11. **Compose** — no scheduler healthcheck in any file; the on-prem file has no healthcheck on any
    service. The hosted file's receiver/worker/API healthchecks are the pattern
    (`docker-compose.app.yaml`).

**Corrections to `change.md`:** the console's `window.onerror` and unhandled-rejection capture already
exist (item 10) — the work is the sink alone. And the scheduler's `/readyz` cannot run a Postgres
query from the health thread without its own connection; it reports the loop's own last successful
Postgres contact instead (below), which is also what readiness means for a loop that only ever talks
to Postgres.

## Change required

`change.md`, *Intended behavior*.

## Impact

| Area | What changes |
|---|---|
| Files | scheduler: new `health.py`, `metrics.py`; `beat.py`, `jobs/worker.py`, `__main__.py`, `settings.py`, `pyproject.toml`, `Dockerfile`. receiver: `internal/health/{server,selfcheck}.go`, `internal/otlp/traffic.go`, `cmd/receiver/main.go`, `go.mod`. worker: `internal/health/{server,selfcheck}.go`, new `internal/selfmetrics/`, `internal/worker/run.go`, `cmd/worker/main.go`, `go.mod`. API: new `selfmetrics/` (middleware and route), new `clientlogs/` (router, schemas), `main.py`, `middleware/stack.py`, `pyproject.toml`. web: `shared/logger.ts`, `main.tsx`. Compose ×3; `.env.example` |
| Services | All five |
| Schema / migrations | None |
| UI | None visible; production errors leave the browser |
| Tests | new scheduler `test_health.py`, `test_scheduler_metrics_live.py`; Go `selfmetrics` tests, `traffic_test.go`, `selfcheck_test.go` in both; API `test_self_metrics.py`, `test_client_logs.py`; web `logger.test.ts`; `test_deployment_files.py` (healthchecks, `/metrics` not routed) |

## Reuse

- The Go `health` package's server and registry — `/metrics` is one more route on the same mux.
- The receiver's fixed reason lists (`refusalReasons`, `degradationReasons`, `units`) as the label
  values, so no label can grow.
- The worker's shared `kgo.Client` for `kadm`; each pipeline's existing consume settings for its
  group and topics.
- The API's rate-limit tiers and body cap for the new route; the problem types.
- `owed.overdue` for erasures overdue; the queue's own predicates for "ready".
- The console's existing handlers and `LogRecord` shape.

## Gaps

- A scheduler health server and a liveness that means "the loop is moving".
- A metrics library in each language, and the collectors.
- An endpoint for the console's errors.

## Steps and tasks

### Step 1 — The scheduler can be health-checked

*Judgeable:* `docker compose ps` shows all three scheduler services `healthy`; freezing beat's loop
(`docker compose pause` is not it — a test hook that blocks the loop) turns it `unhealthy` within a
minute while the container stays up.

- [x] **T1** — `apps/scheduler/tests/test_health.py` (new, red first) · `/healthz` 200 while the loop
  beats and 503 once it has been silent past `LOOP_SILENCE_LIMIT` (30 s); `/readyz` 503 when the
  last Postgres contact is older than the same; `/metrics` serves text format; unknown path 404.
- [x] **T2** — `nexovue_scheduler/health.py` (new) · a `Pulse` the loop stamps (`beat()` after each
  tick or poll; `reached_postgres()` after each successful query) and a stdlib `ThreadingHTTPServer`
  on a daemon thread serving `/healthz`, `/readyz` and `/metrics` from it — reading only timestamps,
  so a hung loop shows as stale rather than hanging the probe; `settings.py` gains
  `health_addr` (`SCHEDULER_HEALTH_ADDR`, `:8081`) · proved by T1.
- [x] **T3** — `beat.py`, `jobs/worker.py`, `__main__.py` · each stamps the pulse; `__main__` starts
  the server for either role and adds `python -m nexovue_scheduler health` (GET the configured
  `/healthz`, exit 0/1) · proved by T1 and a `__main__` test of the `health` exit codes.
- [x] **T4** — compose ×3 · a healthcheck on the three scheduler services running that command; the
  on-prem file gains the receiver, worker and API healthchecks the hosted file has · proved by
  `test_deployment_files.py` (new case: every long-running service in both deployment files has a
  healthcheck) and the live run.

### Step 2 — The Go services export what they count, and the worker its lag

*Judgeable:* on the dev stack, `curl localhost:<worker health port>/metrics` shows lag per topic near
0; stopping the worker while the load generator runs and restarting it shows lag climb and drain;
parking a malformed message moves the dead-letter depth by one.

- [x] **T5** — `internal/health/selfcheck.go` (both modules) · the `-health` flag reads the configured
  address · proved by `selfcheck_test.go` (configured `:9090` is the one probed; red first).
- [x] **T6** — `internal/health/server.go` (both) · `Handler` takes a `prometheus.Gatherer` and serves
  `GET /metrics` with `promhttp`; `go.mod` adds `client_golang` at its current release · proved by
  a server test (the route answers in text format; the probes are unchanged).
- [x] **T7** — `receiver/internal/otlp/traffic.go` · cumulative Prometheus counters beside the
  windowed ones — requests accepted/refused, items by signal, events by reason — every label from
  the fixed lists · proved by `traffic_test.go` (counts survive a summary window; an unknown reason
  does not create a series).
- [x] **T8** — `workers/internal/selfmetrics/` (new) · a collector, run on scrape with a 2 s budget:
  consumer lag per group and topic from `kadm.Lag`; dead-letter depth per `*-dlq` topic as end minus
  start offset; oldest uncommitted batch age and parked messages per pipeline from the consume loops;
  each pipeline running from the liveness registry. On a Kafka error the lag series are absent, not
  zero, and `nexovue_worker_kafka_admin_up` is 0 · proved by `selfmetrics_test.go` (lag arithmetic,
  absent-on-error) and the live run; `go.mod` adds `kadm`.
- [x] **T9** — `cmd/receiver/main.go`, `worker/run.go` · registries wired into the admin handler ·
  proved by the live run.

### Step 3 — The scheduler and the API export theirs

*Judgeable:* the scheduler's `/metrics` shows jobs ready per queue and the alert outbox; pausing the
light worker makes `jobs ready` and `oldest ready seconds` climb; the API's `/metrics` shows requests
by route group and status, and `https://…/metrics` through the edge is a 404.

- [x] **T10** — `nexovue_scheduler/metrics.py` (new) · beat's leader, every 15 s: jobs ready per queue
  and the oldest's age (the claim's own predicate), alert notifications pending and the oldest's age,
  erasures overdue; every process: leading (0/1), seconds since the loop beat, jobs finished /
  retried / killed; `prometheus_client` added · proved by `test_scheduler_metrics_live.py` (real
  Postgres: a due job and a pending notification move the gauges).
- [x] **T11** — API `selfmetrics/` (new) · pure ASGI middleware counting requests by route group (the
  first segment under `/api/v1`, from the router list, else `other`) and status class, with a
  duration histogram; `GET /metrics` at the root, exempt like the probes; `prometheus_client` added ·
  proved by `test_self_metrics.py` (a project id in the path never becomes a label).
- [x] **T12** — `test_deployment_files.py` · the edge routes nothing but `/api/*` to the API, so
  `/metrics`, `/healthz` and `/readyz` stay internal · seen red against a Caddyfile with a catch-all.

### Step 4 — The console's errors reach the API's log

*Judgeable:* in a production build, throw from a screen; the API's log shows one
`service=nexovue-web` line with the message, the stack and the page.

- [x] **T13** — API `clientlogs/` (new) · `POST /api/v1/client-logs`: a batch of at most 20 records,
  `warn` or `error` only, each field truncated (message 1 KiB, stack 8 KiB), body cap 64 KiB; signed
  in or not (a sign-in screen can fail too) — the default tier counts a signed-out caller by address;
  each record logged at its own level with `service=nexovue-web`, `source=browser`, the caller's
  organization when signed in; `204` · proved by `test_client_logs.py` (logged fields, truncation,
  `debug` refused, oversized batch refused, signed-out accepted).
- [x] **T14** — `apps/web/src/shared/logger.ts`, `main.tsx` · a `beaconSink`: buffers warn and error,
  sends with `fetch(..., {keepalive: true})` after 2 s or on `pagehide`, never throws, never logs its
  own failure (no loop); configured in production from `main.tsx` · proved by `logger.test.ts`
  (batching, levels, a failed send is silent).

### Step 5 — Documents and the decision

- [x] **T15** — new ADR-021 (how nexovue exposes its own health: pull endpoints on internal ports;
  decision 0053 rules 1-2 met, rule 3 still deferred); `monitoring.md` (every endpoint, every
  metric, the thresholds worth alerting on, "nothing watches yet"); `alerting.md` corrected;
  `current-system-reference.md` (each service's health and metrics; the "no exporter" line); the
  readiness document (blocker 7, checklist rows, group 7, the stale `health.py` pointer); this
  plan's Outcome and `status.md`.

## New risks

- **A scrape that reaches Kafka or Postgres adds load.** Worker lag is computed per scrape under a 2 s
  budget; the scheduler's queue queries run on beat's leader every 15 s whether scraped or not, and are
  each one indexed count.
- **The client-logs route accepts signed-out writes.** Bounded by the rate limit, the body cap, the
  field truncation and the level filter; it writes nothing but a log line.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | The thresholds `monitoring.md` names (lag, oldest job, outbox age) are first guesses until production traffic exists | No | `monitoring.md`, revised in group 8 |

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

- Each new test seen red first.
- `just check`, counts read.
- Live on the dev stack: the three scheduler services healthy, then beat's loop blocked and seen
  unhealthy; the worker stopped under `just load` and lag seen climbing and draining on `/metrics`;
  a malformed record parked and dead-letter depth moving; the light worker stopped and the oldest
  ready job's age climbing; a production console build throwing and the line found in the API log.
- On the replica: `/metrics` through the edge is a 404.

## Rollout and rollback

Pre-deployment, `git revert`. The real risk is a liveness probe that is wrong in the other direction
— a healthy service killed by its own healthcheck. Each threshold is several times the loop's
period (30 s against 1-5 s), and the live run watches all three services stay healthy for ten minutes
under load.

## Outcome

Built as planned, with these differences:

- **The worker reports less, from a better source.** Parked messages per pipeline and the oldest
  uncommitted batch's age were dropped: dead-letter depth read from the `*-dlq` topics covers every
  process that parks, and consumer lag answers "falling behind". Instead of a separate pipeline gauge,
  every readiness check is exported (`nexovue_worker_check_up`) — pipelines, meters and stores alike.
- **The `-health` fix is `config.SelfCheckAddr()`**, tested in `internal/config/selfcheck_test.go`:
  the self-check runs before the settings load, so it reads the one variable it needs.
- **`/metrics` on the Go services is a wrapper** (`health.WithMetrics`) beside `WithProfiling`, rather
  than a new `Handler` parameter, so its callers did not change.
- **The API's pieces live with their neighbours**: `/metrics` in `health/router.py`, the counting
  middleware in `middleware/metrics.py`, rather than a new `selfmetrics/` package.
- **The route group comes from the matched route's template, relative to its router.** The first
  version read groups from `app.routes`; this FastAPI keeps included routers as one nested private
  entry, so it found none, and every request was `other`. The template that routing leaves in the
  scope is relative to the innermost router (`/projects`), which the test now reproduces by nesting.
- **The client-logs route resolves no identity.** The session dependency extends a support view as a
  side effect; reporting an error must not. A record from a failed API call carries that call's
  correlation id, which ties it to the API's own line, organization included. The 64 KiB route cap
  was not added: the batch limit and field truncation bound it below the API's 2 MiB default.
- **The console's capture already existed** (`global-handlers.ts`, React 19's root hooks); only the
  sink, `shared/log-beacon.ts`, was new.
- `kadm` brought `kmsg` from v1.13.1 to v1.14.0. `client_golang` is v1.24.1, not the three-day-old
  v1.25.0. `prometheus_client`'s `_created` series are switched off.
- The proxy (Caddy) gained a healthcheck on its loopback admin API, and the on-prem file healthchecks
  on every service, because the new structural test asks for every long-running service.

**Verified live on the dev stack:** all four scheduler containers healthy; beat frozen with SIGSTOP →
`unhealthy` after 75 s with the container `running`, `healthy` 15 s after SIGCONT. The worker's lag
agreed with Kafka's own tool (2,136 against 2,148), rose to ~3,800 with the worker stopped a minute
and drained after; one malformed log record moved `logs-dlq` from 2 to 3. With the light worker
stopped, `jobs_ready` rose on `light` and `light.local` and cleared on restart. The API counted
requests under `projects`, `auth` and `other`. A production build of the console threw, sent a batch
(`204`), and the line appeared in the API's log at `error` with its stack.

**Not verified:** the replica through the edge (the structural test holds that the edge sends the API
`/api/*` only, seen red with a `/metrics` route added); any monitor reading these endpoints (group 8).
