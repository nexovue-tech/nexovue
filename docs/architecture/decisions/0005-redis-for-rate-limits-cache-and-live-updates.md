# 5. Redis as the utility layer

- **Status:** Accepted
- **Date:** 2026-08

## Context

Three small needs with no natural home: rate-limit counters at the gateway, a hot cache for
queries, and a pub/sub channel for live UI updates such as "first span arrived".

## Decision

Run Redis for all three.

## Consequences

One shared dependency covers three jobs, and it is later reused as the Celery broker
([0007](0007-celery-for-probes-and-pollers.md)) and as the live-tail fan-out
([0017](0017-nine-architecture-additions-from-feature-audit.md)). Its availability now matters to
the ingest path, since rate limiting depends on it.
