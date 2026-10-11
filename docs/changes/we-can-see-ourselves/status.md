# Status: we-can-see-ourselves

## Lifecycle status

Verified (development stack); not committed

## Current task

Founder review of the diff.

## Completed

- `change.md` approved 2026-10-11 (pull; console errors to the API's log); `plan.md` approved and
  complete, with its Outcome.
- Scheduler health endpoints with a loop-pulse liveness, `python -m nexovue_scheduler health`, and
  healthchecks on every long-running service in both deployment files and the dev file's schedulers.
- `/metrics` on every service: worker lag and dead-letter depth from the broker, every readiness check;
  receiver totals; scheduler loop, leadership, queues, alerts, erasures; API requests by route group.
- The Go `-health` self-check reads the configured address.
- The console's warnings and errors reach the API's log.
- ADR-021; `monitoring.md` written; `alerting.md` corrected; decision 0053 annotated.

## Next action

Founder reviews and says "commit". Then readiness group 9 (the remaining recommendations); group 8
connects a monitor to these endpoints.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Nothing reads these endpoints yet.** Blocker 7 is not closed until group 8 connects a monitor.
- **The thresholds in `monitoring.md` are first guesses.**
- **The client-logs route accepts signed-out writes**, bounded by the rate limit, the API's body cap,
  a batch of 20 and field truncation; its only effect is a log line.
- **A stopped worker cannot report its own lag** — the missing scrape is the signal, and
  `monitoring.md` says so.
- Not exercised: the replica through the edge for this change (the edge's routing is held by a test).

## Validation

- Each new test seen red first: `test_health.py` (import), the Go `selfcheck_test.go` in both modules
  (build), `traffic_metrics_test.go` (no collector), `selfmetrics_test.go` (build),
  `test_scheduler_metrics_live.py` (module), `test_self_metrics.py` (module; then red again when the
  routers were nested as `main.py` nests them), `test_client_logs.py` (module), `log-beacon.test.ts`
  (module), `test_deployment_files.py` healthcheck case (two files listed) and API-routes case (red
  with a `/metrics` route added to the Caddyfile).
- Live on the dev stack: see the plan's Outcome.
- `just check`: exit 0 — Python 4301 passed, 3 skipped; web 1357; browser SDK 120; 36 Go packages ok,
  the new `selfmetrics`, `health` and `config` among them. The first run failed only on formatting
  two new test files.

## Specification impact

None.

## Documentation impact

Updated: ADR-021 (new) and the decisions index, `decision-log.md` (0053), `monitoring.md`,
`alerting.md`, `current-system-reference.md` (the worker's metrics, the scheduler's health),
`production-readiness.md` (checklist rows, blocker 7, two recommendations, group 7, `alerting.md`
stale note), `.env.example` (`SCHEDULER_HEALTH_ADDR`).

## Last updated

2026-10-11
