# Monitoring

How nexovue reports its own health (ADR-021), and the monitor that is ready to read it: Prometheus with
nexovue's alert rules (`deploy/monitoring/`), run beside an install by `NEXOVUE_MONITORING=yes`.

## Current verified behavior

Verified on the development stack 2026-10-11 (`docs/changes/we-can-see-ourselves/`).

Every service answers three paths on an internal port. No edge routes any of them.

| Service | Port | `/healthz` fails when | `/readyz` fails when |
|---|---|---|---|
| receiver | `RECEIVER_HEALTH_ADDR` (`:8081`) | never (the process answers) | Kafka or Postgres does not answer |
| worker | `WORKERS_HEALTH_ADDR` (`:8081`) | a pipeline has stopped | that, or Kafka, ClickHouse, Postgres or a usage meter |
| api | `:8000`, at the root | never | Postgres or ClickHouse does not answer |
| scheduler (each of beat, light, deliveries) | `SCHEDULER_HEALTH_ADDR` (`:8081`) | its loop has been silent for 30 s | it has not reached Postgres for 30 s |

Every long-running service has a compose healthcheck in both deployment files. A frozen beat went
`unhealthy` after 75 s with its container still `running`, and `healthy` again 15 s after it resumed.
**Docker marks a container unhealthy and does not restart it**: acting on it is the monitor's job, or
an orchestrator's.

## What `/metrics` reports

The Prometheus text format. Labels never carry a tenant, project or path.

### Worker

| Metric | Meaning | Worth an alert when (first guess) |
|---|---|---|
| `nexovue_worker_consumer_lag_records{group,topic}` | Records not yet committed by each consumer group. Spans normally sit ~2,000 behind: the sampling buffer holds traces before committing | rising for 10 min, or spans > 50,000 |
| `nexovue_worker_dead_letter_records{topic}` | Records parked and still retained (7 days) | any increase |
| `nexovue_worker_kafka_admin_up` | Whether the scrape could read offsets; when 0 the lag series are absent, not zero | 0 for 5 min |
| `nexovue_worker_check_up{check}` | Every readiness check: each pipeline, meter and store | any 0 for 2 min |

**A stopped worker cannot report its own lag**: its `/metrics` stops answering. Alert on the missing
scrape (`up == 0` in Prometheus terms) for every service.

### Receiver

| Metric | Meaning | Worth an alert when |
|---|---|---|
| `nexovue_receiver_requests_total{outcome}` | Export requests accepted and refused | refused / all > 5% for 10 min |
| `nexovue_receiver_items_total{signal,outcome}` | Spans, log records, data points and samples | accepted for a signal flat at 0 for 30 min while customers send |
| `nexovue_receiver_events_total{reason}` | Refusals by reason; `unavailable` is Kafka refusing | `unavailable` rising at all |

### Scheduler

| Metric | Meaning | Worth an alert when |
|---|---|---|
| `nexovue_scheduler_loop_silent_seconds` | Seconds since this process's loop beat | > 30 |
| `nexovue_scheduler_beat_leading` | 1 on the beat that leads | sum across beats ≠ 1 for 2 min |
| `nexovue_scheduler_jobs_ready{queue}`, `…_oldest_ready_job_seconds{queue}` | Due jobs nobody has claimed, and the oldest's wait (leader only) | oldest > 120 s |
| `nexovue_scheduler_alerts_waiting`, `…_oldest_alert_waiting_seconds` | Alert notifications due and undelivered (leader only) | oldest > 300 s |
| `nexovue_scheduler_erasures_overdue` | Deleted organizations past their three days | > 0 |
| `nexovue_scheduler_jobs_added_total`, `…_enqueue_failures_total`, `…_jobs_ended_total{outcome}` | What beat added, could not add, and how workers' jobs ended | enqueue failures rising; `killed` rising |

Stopping the light worker on the dev stack raised `jobs_ready` on `light` and `light.local` within
20 s, and both cleared once it restarted.

### API

| Metric | Meaning | Worth an alert when |
|---|---|---|
| `nexovue_api_requests_total{group,status}` | Requests by route group (`projects`, `traces`, … or `other`) and status class | 5xx / all > 1% for 10 min |
| `nexovue_api_request_seconds{group}` | Duration histogram | p95 of a group > 5 s for 10 min |

The Go services also export their runtime (`go_*`) and process (`process_*`) metrics.

## The console's errors

Production console builds send warnings and errors to `POST /api/v1/client-logs`, batched; the API
writes each as a log line with `source=browser`, the page, the error and its stack. A record from a
failed API call carries that call's correlation id. Verified with a production build: a thrown error
appeared in the API's log at `error`.

## Reading them by hand

```bash
docker exec nexovue-worker-1 wget -qO- http://127.0.0.1:8081/metrics | grep ^nexovue_
docker exec nexovue-scheduler-beat-1 /workspace/.venv/bin/python -c \
  "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8081/metrics').read().decode())"
curl -s localhost:18000/metrics | grep ^nexovue_
```

## The monitor

`deploy/monitoring/`, in every install kit (docs/changes/ready-to-deploy, T24):

- `prometheus.yml` scrapes every service above by DNS, so each replica of the API and the receiver is
  its own target;
- `rules/nexovue.yml` — 16 alerts on the thresholds in the tables above, plus **a service that is
  missing altogether**: a stopped container leaves DNS, so it is never `up == 0`, only absent;
- `tests/nexovue_test.yml` — `promtool test rules`, run by `just lint-deploy` in `just check`;
- `alertmanager.yml` — routes to nowhere until a receiver is named.

Neither publishes a port; read them with `docker compose exec prometheus wget -qO- …` or an SSH tunnel.

Rehearsed 2026-10-28 on the quickstart: all six jobs up; the worker stopped, `NexovueServiceMissing`
fired after two minutes and reached Alertmanager. The first version of the rules had only
`NexovueServiceDown`, which stayed silent through that — the reason the missing-service rule exists.

## Not yet established

- No receiver is configured, so nothing pages anyone until the first deployment names one
  ([first-deploy](first-deploy.md) step 7).
- The thresholds above are first guesses, to be set from production traffic.
- No dashboards, SLOs or log-retention policy for nexovue itself.
- nexovue's telemetry is not sent into a nexovue organization (decision 0053 rule 3, still deferred).
