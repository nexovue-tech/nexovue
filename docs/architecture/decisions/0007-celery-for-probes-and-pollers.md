# 7. Doors 2 and 4b (probes and pollers) run on Celery

- **Status:** Accepted
- **Date:** 2026-08

## Context

Synthetic probes (uptime, latency breakdown, SSL expiry, API checks, Playwright browser flows) and
cloud/database pollers are both scheduled work that runs on our side. Customers edit the schedules
in the UI, so schedules cannot live in code. Alternatives considered: cron, APScheduler, Temporal,
Kubernetes CronJobs.

## Decision

Celery. Celery Beat as the scheduler with database-backed schedules in Postgres, so a config change
in the UI needs no redeploy. Redis — already in the stack — as the broker. No result backend:
workers execute the task, normalize the result, and publish to Kafka themselves.

Separate queues by weight: Playwright browser checks (low concurrency, large containers) isolated
from lightweight HTTP checks and polls (high concurrency). Every task gets a hard timeout and a
retry policy that respects cloud API rate limits. Probes start single-region; when regions are
added, an alert rule of "must fail in 2+ regions, 2+ consecutive times" comes with them.

Rejected: cron and APScheduler are too primitive for customer-edited schedules; Temporal and
Kubernetes CronJobs are too heavy for the current deployment.

## Consequences

All-Python consistency on the control-plane side, reuse of Redis, and a battle-tested scheduler.
Celery is swappable later precisely because tasks only publish to Kafka — nothing downstream knows
they came from Celery. The database-backed scheduler itself turned out to need writing; see
[0036](0036-write-our-own-celery-beat-scheduler.md).
