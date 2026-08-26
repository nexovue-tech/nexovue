# 15. Deployment = all services as containers, on-premise docker-compose for now

- **Status:** Accepted
- **Date:** 2026-08

## Context

The platform is a dozen services plus Kafka, ClickHouse, Postgres, Redis and Ollama. The choice is
between managed cloud services, an orchestrator, or running it ourselves on our own hardware.

## Decision

Everything is a container — receiver, workers, API, Celery, Kafka, ClickHouse, Postgres, Redis,
Ollama — defined in docker-compose on our own hardware.

## Consequences

Trade-off accepted: we own ops — backups, upgrades, disk sizing for Kafka and ClickHouse. In return
there is no cloud bill and no managed-service lock-in during Phase 1. Container-first design keeps
the later move to a multi-host or orchestrated setup, or to managed services, a deployment change
rather than a rewrite. What actually fits on the dev machine is worked out in
[0046](0046-dev-machine-m4-pro-24gb.md).
