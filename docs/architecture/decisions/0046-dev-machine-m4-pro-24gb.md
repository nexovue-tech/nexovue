# 46. Recorded — dev machine is an Apple M4 Pro, 24 GB

- **Status:** Accepted (recorded)
- **Date:** 2026-08-26

## Context

[0015](0015-containers-with-on-premise-docker-compose.md) puts the entire stack in compose. Whether
it fits on the machine it has to run on was an assumption until measured.

## Decision

It fits. The full Phase 1 stack occupies roughly 10 GB of a ~13 GB Docker VM allocation, with
headroom for the demo app, a load generator and one Playwright browser — but only with:

- a hard `mem_limit` on every service,
- compose profiles, so the full stack is not always up,
- ClickHouse capped and running a 3-day dev TTL,
- Playwright on the arm64 image with `--ipc=host` and a 1 GB `/dev/shm`.

See `../../delivery/dev-environment.md`.

## Consequences

**Consequence for `../system-architecture.md` §4:** on one laptop the performance budgets are a
**regression gate, not a capacity model**. They catch a change that made something slower; they say
nothing about production capacity. Production sizing waits for a production host.
