# 0048 — Valkey instead of Redis

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [0005](0005-redis-for-rate-limits-cache-and-live-updates.md) in part — the implementation, not the role

## Context

Redis 8 is tri-licensed AGPLv3 / RSALv2 / SSPLv1. We had already rejected MinIO partly for AGPLv3 ([0037](0037-seaweedfs-for-local-dev-object-storage.md)) and Redpanda for BSL ([0026](0026-apache-kafka-4-in-kraft-mode.md)), so keeping Redis meant applying a licence standard to two components and not to a third.

Surfaced while building the compose stack, when nothing yet depended on it.

## Decision

Use **Valkey** — the BSD-3 fork created when Redis relicensed, now the default in most distributions — for every role [0005](0005-redis-for-rate-limits-cache-and-live-updates.md) assigns to Redis: rate-limit counters, hot cache, sessions, live-update pub/sub, and the Celery broker.

The compose service keeps the name `redis`, and the connection variable stays `REDIS_URL`. The wire protocol, the URL scheme and every client library are still Redis; only the server implementation changed.

## Consequences

- The licence question is settled the same way for all three components.
- No client code changes. `redis-py`, `go-redis` and Celery all speak to it unchanged.
- The data volume is not portable between the two: Valkey 8.1 cannot read an RDB written by Redis 8.10 (`Can't handle RDB format version 15`). Irrelevant for a cache, and it cost one `docker volume rm` in dev — but a production migration would need the cache to be cold-started deliberately.
- Healthchecks call `valkey-cli`; the Redis-named binaries are not all present in the alpine image.
