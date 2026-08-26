# 0051 — Beat and worker separate in dev, combined with the API in deployment

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [0007](0007-celery-for-probes-and-pollers.md) in part — the container layout, not the choice of Celery

## Context

[0007](0007-celery-for-probes-and-pollers.md) put probes and pollers on Celery with browser checks isolated on their own queue, and M0 built that as three containers: `scheduler-beat`, `scheduler-light`, `scheduler-browser`. Together with `api`, the control plane was four containers before a single customer existed.

Same objection as [0050](0050-one-worker-process-pipelines-by-config.md): the split is a production answer applied to a system with no load.

## Decision

**Development** keeps `api`, `scheduler-beat` and `scheduler-worker` as three containers. Separate processes are easier to read, restart and reason about while building them, and one worker drains both queues.

**Deployment** — the replica and on-prem targets — runs **one `app` container**: FastAPI, Beat and the worker under `supervisord`.

**Browser checks are not isolated.** They run in the same worker as HTTP checks, on the `browser` queue, at concurrency 2 with a 1 GB `/dev/shm`.

## Consequences

- The deployed control plane is one container instead of four.
- **The `app` container can never run more than one replica.** Beat is a singleton — a second instance fires every customer's scheduled checks twice, which surfaces as duplicated results and looks like a data bug rather than a deployment one. `replicas: 1` is recorded in the on-prem file with the reason. **Splitting Beat back out is the first move when the API needs to scale, before adding replicas.**
- The image carries Chromium, so it is large and the container needs ~2.5 GB and a raised `shm_size`. A browser check that leaks memory can now take the API down with it.
- Those two consequences are the signal to split, and they are cheap to act on: the queue names and the supervisord programs already describe the boundary.
- `supervisord` is PID 1 rather than one process per container. Accepted deliberately: three processes that must scale together are one deployable unit, and supervisord restarts a dead child instead of leaving it silently absent.
