# Change: every service waits for what it needs, and is handed nothing it ignores

## Why

The QA pass on F18 found that the API started before the object store it needed, and the fix
(`docs/changes/qa-found-defects/`) added a rule: **a service that names another in its environment must
wait for it.** Applied across all three deployment files, that rule found 17 service/file pairs — 30
single dependencies — that broke it. They were frozen in a test, not fixed, because they were not all the
same defect, and which were real was the founder's decision.

Each was then judged from the service's own startup code — what it reads, whether it connects at boot,
and what it does when the other service is missing — rather than from the variable's presence.

## What changed

**Nine dependencies declared**, where a service really does need the other at start:

| File | Service | Now waits for | Because |
|---|---|---|---|
| all three | worker | postgres | project masking and sampling rules load the moment it starts; without them spans are handled with the built-in defaults for the first 30 s |
| app, onprem | scheduler-light | kafka, redis | Redis is its task queue; Kafka carries every check result, lost after three delivery attempts |
| onprem | worker | clickhouse, kafka | its pipelines consume and write at start (the dev and app files already had these) |

**Ten variables deleted**, where the service never reads them. They arrived through two shared YAML
blocks, and a merge key can add a variable but never remove one — so the blocks were split rather than
edited:

- `x-data-plane-env` now holds what the receiver **and** the worker read; the receiver adds `REDIS_URL`,
  the worker adds `CLICKHOUSE_DSN`.
- `x-control-plane-env` now holds what the API **and** the scheduler read; the API adds `CLICKHOUSE_DSN`,
  the scheduler adds `KAFKA_BROKERS`.

Removed: `CLICKHOUSE_DSN` from the receiver and both schedulers, `REDIS_URL` from the worker, and
`KAFKA_BROKERS` from the API — in both the production-replica and on-prem files.

**Eleven left undeclared on purpose**, each now recorded with its reason in
`DELIBERATELY_UNDECLARED` (`apps/api/tests/test_compose_dependencies.py`): the console does not wait for
the API so it can explain that the API is down; beat is handed `KAFKA_BROKERS` because the shared
scheduler settings require it, though beat never produces; the probe worker's Postgres is covered because
all its work arrives from services that wait for Postgres; and `insight` does not exist yet.

## Validation

- **Resolved configuration, before and after**, for all three files, from `docker compose config` with
  every required variable supplied. The only differences are the ten removals and the nine additions
  above; every other variable of every service — including `insight`, left exactly as it was — is
  unchanged.
- **Dev cold start** (`down`, then `up -d --wait`): exit 0, every service healthy.
- `test_compose_dependencies.py`: 10 passed. The frozen list became a decided one, checked in both
  directions — a new undeclared dependency fails, and so does a recorded one that is no longer true.

The production-replica and on-prem files were verified by resolution, not by running them: neither has
images to run yet (production readiness blockers 2 and 10).

## Found and not changed

- In the on-prem file, `insight` waits for Postgres and ClickHouse but is given neither address. Left for
  when the service is written.
- The scheduler's health probes (`health.py`) are never called and neither scheduler has a health check,
  so nothing can wait for the scheduler to be *healthy*, only *started*.
