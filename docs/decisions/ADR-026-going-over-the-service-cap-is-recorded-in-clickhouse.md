# ADR-026: Going over the service cap is recorded in ClickHouse, and the cap keeps the busiest

## Status

Accepted 2026-11-07: the founder answered the browser-door change's open decisions 1 and 2 as recommended
([`docs/changes/browser-door/status.md`](../changes/browser-door/status.md), *Founder answers*). Built the same
day.

## Context

The worker counts at most 2,000 services per organization per minute into `service_stats_1m`
(`derive.MaxServicesPerTenant`). Two facts in the code made that cap a weakness rather than a bound:

- **It kept the first 2,000 in name order.** Anyone holding a key could send one-request services named
  `aaa-1`, `aaa-2`, … and push a busy real service out of the minute detection judges.
- **Going over it left nothing durable.** One warning naming one tenant per drain, and a counter that died
  with the process. No screen could say which minutes of which organization were missing services, which
  F19's design asks for (`slo-tracking/design.md`, *Forged spans*).

## Decision

1. **The cap keeps the busiest**: requests in the drained tenant-minute, descending, then service name, then
   the rest of the row's key (`derive/services.go`, `busiest`). It is applied at drain to that drain's rows, so
   it is a pure function of them and a replay drains the same block, which its deduplication token relies on.
   It is per worker and per drain, as the cap already was, accepted knowingly.
2. **Each tenant-minute the cap left services out of is a row of `services_over_cap_1m`** (ClickHouse migration
   `000039`): tenant, minute, and how many services were left out, summed across drains. It is written in the
   same settlement as `service_stats_1m`, with a deduplication token over the rows (`derive/capwriter.go`). A
   refusal is counted and holds no progress, because detection reads `service_stats_1m`, which a refused record
   leaves whole. It names no project because the cap is per organization, and it is in the erasure inventory.
3. **The warning stays** as the operator's pointer, once per drain, now totalled from the rows.

## Consequences

- A screen can read, beside the rollups, which minutes are missing services and roughly how many.
- The count is summed across drains and workers, so one service can be counted twice or be left out by one
  drain and kept by another. A reader treats any row as "this minute was capped", not as an exact census.
- One more small table, at most one row per capped tenant-minute per drain, and none in normal operation.

## Rejected

- **A Prometheus metric instead of a table:** visible to the operator, but no screen could tell a tenant which
  of its minutes were capped.
- **A Postgres table:** the ingest path writes Postgres only through a short named list of exceptions
  (`AGENTS.md`, *Repository invariants*); widening it is what that invariant resists, for no gain.
- **A cross-drain measure of "busiest"** (a running total per service): global, but it makes a drain depend on
  history, which breaks the replay determinism the deduplication token relies on.
