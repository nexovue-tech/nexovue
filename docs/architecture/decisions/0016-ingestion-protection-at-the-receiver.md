# 16. Ingestion protection lives in the receiver

- **Status:** Accepted
- **Date:** 2026-08

## Context

Ingest is the one endpoint the outside world can reach, and every byte past it costs money.

## Decision

The receiver does per-key validation and rate limiting, using the Redis counters from
[0005](0005-redis-for-rate-limits-cache-and-live-updates.md). This was part of the receiver
decision ([0001](0001-ingestion-edge-custom-receiver.md)) and is confirmed here.

## Consequences

Invalid payloads and over-limit tenants are rejected before any downstream cost. Redis is now on
the ingest path. The same counters were later extended into usage metering
([0017](0017-nine-architecture-additions-from-feature-audit.md)).
