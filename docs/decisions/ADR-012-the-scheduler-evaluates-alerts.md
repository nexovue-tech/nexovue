# ADR-012: The scheduler evaluates alerts, reading ClickHouse through shared, tenant-bound statements

## Status

Accepted 2026-09-19, with the F21 design.

## Context

F21 evaluates four conditions: a check's state, a new error group, a service's error rate or p95, and a service
going silent. Two start from a moment the scheduler already owns — a confirmed check state change, written in
`probes/state_store.py` — and all four need a periodic evaluator that runs **once**, not once per replica.
Delivery needs an outbound HTTP client that refuses private addresses after DNS resolution.

The scheduler has the leader (`leader.py`, beat), the queue, and that client (`probes/addresses.py`,
`ConnectGuard`). It has **no ClickHouse access**: no setting, no client. The API has ClickHouse access and the
tenant chokepoint (`data/clickhouse/base.py`) but runs several replicas with no leader and no guarded outbound
client.

## Decision

1. Alerts are evaluated in the scheduler: check-state conditions inline in the state-change transaction, the
   others by beat-scheduled sweeps on the `light` queue.
2. The scheduler gains a **read-only** ClickHouse client and `CLICKHOUSE_DSN`.
3. The statements it runs live in `packages/py-platform`, shared with the API where the API reads the same thing
   (service error rate and p95; the error-group fingerprint), so an alert and the screen it links to cannot
   disagree.
4. Every such statement carries `tenant_id = {tenant_id:String}` as a real predicate, enforced by a structural test
   mirroring `test_tenant_chokepoint_structure.py`.

## Rejected

- **Evaluate in the API.** Needs its own leader election across replicas and puts outbound requests to
  customer-chosen addresses in the control plane — the rejection ADR-011 already made for Run now.
- **A separate alerting service.** Duplicates the leader, queue and outbound guard to run four sweeps. The alerts
  module is written as one package with no imports from `probes/` beyond the state-change hook, so it can move out
  when its load justifies a process of its own.
- **Push the silence and threshold decisions into the Go workers.** The workers see a partition of the stream, not
  a service's whole minute; a threshold needs the aggregate.

## Consequences

- The scheduler's blast radius grows: it now reads telemetry. It reads through a read-only ClickHouse user, and
  its statements are held to the same tenant rule as the API's.
- `docs/changes/compose-dependencies/` removed `CLICKHOUSE_DSN` from the schedulers on 2026-09-18 because nothing
  read it. F21 restores it with a reader, and the compose rule test then requires the scheduler to wait for
  ClickHouse.
- A ClickHouse outage stops two of the four conditions; the check-state and silence conditions keep working,
  because they read Postgres only.

## As built — 2026-09-19

Decision 3 is carried out one level down. What is shared through `packages/py-platform` is what the statements
are *built from*: the rollup's quantile arguments and index, `failure_ratio`, and the error-group fingerprint
(`nexovue_platform.readings`), plus the tenant check itself (`nexovue_platform.tenant_statements`). Each service
owns its statements, under its own chokepoint: the API's `TenantScopedRepository`, and the scheduler's
`alerts/telemetry.py`, whose `TelemetryStore` hands out only readers bound to one tenant and project. Two reasons:
the shared package reaches no store (`test_the_shared_package_reaches_neither_service_nor_a_database`), and moving
the API's chokepoint would have rewritten the file its whole tenancy guarantee rests on for no gain.

Decision 4 is enforced as written: `test_tenant_chokepoint_structure.py` walks the scheduler's source as well as
the API's, and allows the raw ClickHouse driver in `alerts.telemetry` alone. Its first run found three modules
passing the raw client around; they now receive a `TelemetryStore`.

The scheduler uses the API's ClickHouse account until the deployment provisions a read-only one
(`TODO(hosting)` in the production-replica and on-prem compose files).
