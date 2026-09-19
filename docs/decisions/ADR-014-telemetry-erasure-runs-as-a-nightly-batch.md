# ADR-014: Telemetry erasure runs as a nightly batch, not per deletion

## Status

Accepted 2026-09-19 by the founder. **Not built** — it shapes the rest of P1-77 (account and
organization deletion). Found by `docs/changes/gate-found-defects/`.

## Context

Deleting an organization has two phases (`organizations/service.py`). The first — marking
`organizations.deleted_at` — is immediate and is the whole customer-visible stop: sessions are
refused, ingest is refused, nothing can read the tenant's data. The second erases what is stored: an
`ALTER TABLE … DELETE` mutation on each of the 13 telemetry tables (`purge/queries.py`), plus
Postgres rows and object-store files.

Today the second phase starts straight after each request (`erase_after_response`, a background
task). A mutation re-writes every part of its table — a tenant is not a partition — and ClickHouse
holds each superseded part's metadata in memory for `old_parts_lifetime` (480 s), outside its own
memory tracker. Measured on development: 12,900 superseded parts held 3.3 GiB, and ordinary queries
failed `MEMORY_LIMIT_EXCEEDED`. So one erasure is a transient spike, and several close together —
a cleanup of free accounts, a bad day — take the store down with them.

The heavy mutation itself is right and stays: it is ClickHouse's recommended delete when the bytes
must leave the disk for compliance ([Delete overview](https://clickhouse.com/docs/deletes/overview)).
What changes is *when* it runs and *how many tenants* one run covers.

Two things this decision rests on already exist: an organization with `deleted_at` set and no
`ORGANIZATION_ERASED` certificate *is* the durable record of an owed erasure, and
`erasures_still_owed()` lists them. Nothing calls it yet, so an erasure that fails today is never
retried.

## Decision

1. **The deletion request only records the obligation.** It marks `deleted_at` and returns 202, as
   now. It no longer starts the telemetry erasure.
2. **A nightly job in the scheduler discharges every owed erasure together.** It reads
   `erasures_still_owed()` and, for each of the 14 tables in turn, submits **one** mutation naming all
   of them — `ALTER TABLE <table> DELETE WHERE tenant_id IN (…)` — and waits for it to finish before
   starting the next table, so only one table's superseded parts are ever held at once. Ten
   deletions in a day, or a hundred, are one run.
3. **The hour is configurable**, defaulting to 02:00 UTC, and is moved to the hour with the least
   ingest once production traffic shows which that is. The service is not down; the job runs
   alongside normal use.
4. **A large queue is split.** Past a cap of tenants per run, the job erases in groups, waiting at
   least `old_parts_lifetime` between them so one group's parts have gone before the next adds more.
5. **Proof is per tenant, as now.** After the mutations, each tenant's `remaining()` must be zero in
   every table before its `ORGANIZATION_ERASED` certificate is written. A tenant that is not proved
   stays owed and is taken again the next night — the retry that does not exist today.
6. **An erasure owed for more than three days raises an alert to the operators.** Privacy law asks
   for erasure without undue delay; a nightly run meets it, and a stuck one must not go unseen.
7. Postgres rows and object-store files are erased by the same run, for the same tenants.

## Rejected

- **Per request, as built.** Correct for one deletion; several close together are the outage above.
- **Lightweight `DELETE FROM`.** It only masks rows; the bytes stay until a later merge, which is not
  erasure ([Handling updates and deletes](https://clickhouse.com/blog/handling-updates-and-deletes-in-clickhouse)).
  `purge/queries.py` already records why.
- **A partition per tenant, so a purge is `DROP PARTITION`.** Instant to delete, but a partition key
  with one value per customer multiplies the number of parts in every table, which ClickHouse handles
  badly on reads, merges and memory — the problem this decision exists to avoid, all day instead of
  at night.
- **Lowering `old_parts_lifetime`.** Frees the memory sooner but shortens the window ClickHouse keeps
  superseded parts for recovery after a crash, for every write, to protect one rare operation.

## Consequences

- **The tenancy guard needs a deliberate exception.** `TenantScopedPurger` binds one tenant per
  statement, which is part of the tenant-isolation chokepoint. A statement naming many tenants must
  be built so it can only name tenants taken from `erasures_still_owed()` — organizations already
  marked deleted — and the chokepoint's structure test must be extended to prove it. This is the
  riskiest part of building this and belongs in its design.
- Physical erasure lags the request by up to a day (longer if a run fails). The customer sees no
  difference: their data stopped being readable at the request.
- The memory spike happens once a night at a chosen hour, bounded by one table's parts, and not at a
  time a customer's deletion happened to pick.
- Records already on the broker when an organization is deleted can land after a run; the next night
  finds them through `remaining()` and erases them again, as the purge's design already expects.
