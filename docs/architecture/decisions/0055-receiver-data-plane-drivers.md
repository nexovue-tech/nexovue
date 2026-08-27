# 0055 — Receiver data-plane drivers, and why key lookups cache in process

- **Status:** Accepted
- **Date:** 2026-08-27

## Context

M1 gives the receiver its first two dependencies beyond Kafka. It must resolve an API key to a
tenant and project on every request ([P1-13](../../delivery/phase-1-plan.md)), and count requests
per key to enforce a limit ([P1-14](../../delivery/phase-1-plan.md)). Both sit on the hot path,
ahead of decode, where the budget is 10,000 spans/sec sustained at p99 < 50 ms.

No Go driver had been chosen for either store. The control plane's choices do not transfer: it runs
SQLAlchemy over asyncpg and `redis.asyncio`, neither of which has a bearing on Go.

## Decision

**Postgres: [pgx v5](https://pkg.go.dev/github.com/jackc/pgx/v5) with `pgxpool`.** `lib/pq` is in
maintenance mode; pgx is the default and speaks the binary protocol. The pool is configured with
explicit sizes rather than library defaults, matching what the worker already does for ClickHouse.

**Valkey: [valkey-io/valkey-go](https://github.com/valkey-io/valkey-go).** A maintained fork of
rueidis by the Valkey project, with auto-pipelining; benchmarks put it far ahead of `go-redis` under
parallelism. Consistent with [0048](0048-valkey-instead-of-redis.md).

**Rate-limit counters are fixed one-second windows via `INCR` + `EXPIRE`** — one round trip, no Lua.
The control plane's sliding-window log is deliberately *not* ported: it is the right algorithm for
login attempts and the wrong one for a per-span-batch gate.

**API key lookups cache in process, not in Valkey.** A prefix-keyed TTL cache inside each receiver,
holding negative results as well as positive ones, with a TTL short enough that revocation takes
effect within about a minute.

## Consequences

The hot path costs one in-process map read for a warm key. Putting the cache in Valkey would have
added a network round trip to save a Postgres query that already only runs on a cache miss — paying
per request to avoid a cost incurred per TTL. Caching negative results matters more than it looks:
without it, an invalid key retried in a loop becomes a Postgres query per span batch, which is a
denial-of-service vector wearing the costume of a typo.

Two costs accepted. Revocation is eventual, bounded by the TTL — a leaked key stays usable for up to
a minute after deletion, which is the wrong trade for a password and the right one for an ingest
credential. And each receiver holds its own cache, so *n* replicas mean *n* independent first-misses
per key and *n* independent expiries; harmless at the key counts we expect, and worth revisiting
only if the key set per tenant ever grows large.

The rate limiter's fixed window admits the standard burst at a window boundary — up to twice the
limit across two adjacent seconds. Accepted for an ingest gate, whose job is preventing runaway
cost rather than precise fairness.
