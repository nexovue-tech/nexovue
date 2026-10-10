# Change: deletion-finishes — a deleted organization's data is always erased, and erasing it cannot take the store down

## Classification

Bug (data protection) and Operations. Readiness group 4: blocker 22, with the purge strategy
ADR-014 decided (`docs/operations/production-readiness.md`).

## Current behavior and problem

Read from the code (paths under `apps/api/src/nexovue_api/`):

1. **An erasure that does not finish is never retried (blocker 22).** `DELETE /organization` marks
   the organization deleted in one transaction (`organizations/service.request_deletion`, :97-128),
   then starts `erase_after_response` as a FastAPI background task in the API process
   (`organizations/router.py:48-53`). That task gives up after 120 s
   (`BACKGROUND_WAIT_SECONDS`, :79) and logs. The list of erasures still owed exists —
   `erasures_still_owed` (:183) — and nothing outside the tests calls it. So an API restart
   mid-erasure, a ClickHouse mutation that outlasts 120 s, or rows the pipeline lands after the
   mutation passed (which `purge/service.py` itself says need a second purge) leave a deleted
   customer's telemetry and files in place for ever.
2. **Every deletion rewrites the store, whenever it happens (ADR-014).** Each request submits 14
   `ALTER TABLE … DELETE WHERE tenant_id = …` mutations (`purge/queries.py:42-65`). A mutation
   rewrites every part holding that tenant, which for an active tenant is most of every table, and
   ClickHouse holds the superseded parts' metadata in memory for `old_parts_lifetime` (480 s).
   Measured in development: 12,900 superseded parts, 3.3 GiB, and ordinary queries failing
   `MEMORY_LIMIT_EXCEEDED`. A few deletions close together are an outage.
3. **The API holds a ClickHouse account that can delete everything** for this one purpose
   (`settings.clickhouse_writer_dsn`, :188; `lifespan.py:256`; `organizations/dependencies.py:41`).
   Every other API read goes through the read-only account group 2a introduced.
4. **The memory spike happens when the customer chooses.** It is not tied to a quiet hour.

## Intended behavior

As ADR-014 decided, with the founder decision below:

1. **The request records the obligation and returns 202.** It still stops the charges (group 3),
   scrubs the organization row and audits. It starts no erasure.
2. **The scheduler erases every owed tenant together, once a night.** At `ERASURE_HOUR_UTC`
   (default 2), one job reads every deleted organization without an `organization.erased`
   certificate and, table by table, submits **one** mutation naming all of them —
   `ALTER TABLE <t> DELETE WHERE tenant_id IN {tenant_ids:Array(String)}` — and waits for it before
   starting the next. Only one table's superseded parts are held at a time.
3. **A large queue is split.** At most `ERASURE_BATCH_SIZE` tenants (default 100) per group, with
   `old_parts_lifetime` (480 s) between groups.
4. **Proof per tenant, as now.** After the mutations, a tenant whose rows are zero in every table
   gets its certificate; one that is not stays owed and is taken again the next night — the retry
   that does not exist today. The objects under its two prefixes are deleted in the same run.
5. **An erasure owed for more than 3 days is an error the operators see.** For now an `ERROR` log
   line each night naming how many and the oldest; group 7 turns it into the health signal.
6. **The API loses its ClickHouse writer account.** Only the scheduler holds one, under a name that
   says what it is for (`CLICKHOUSE_ERASER_DSN`). `erase_after_response`, `BACKGROUND_WAIT_SECONDS`,
   `TenantErasure` and the API's `clickhouse_writer` are deleted.
7. **The many-tenant statement is guarded.** `TenantScopedPurger` binds one tenant. The new
   `OwedErasurePurger` takes its tenant list only from the owed-erasures query it runs itself
   (organizations already marked deleted), never from a caller, and the chokepoint structure test is
   extended to prove that no other module can construct a many-tenant statement.

**Founder decision (2026-10-11): Postgres rows are deleted in the request**, in the same
transaction as the scrub; only ClickHouse and the files wait for the night. ADR-014 point 7 put the
Postgres rows in the nightly run too, and is amended. Why:

- `users.email` is unique across nexovue (`models.py:211`). Deferred, a person who closes an
  account and signs up again the same day is told their email is taken until 02:00.
- Names, emails and audit rows would sit in Postgres for up to a day for no gain: these deletes are
  ordinary row deletes, not ClickHouse mutations, and carry none of the memory risk ADR-014 exists
  for.


## Affected specifications and architecture

- ADR-014: status → built; point 7 amended (Postgres rows in the request).
- `docs/architecture/current-system-reference.md` (deletion paragraph, §6.7, the `purge` row).
- `docs/operations/production-readiness.md` (blocker 22, group 4, the stale `purge/__init__.py`).
- Compose files and `.env.example`: `CLICKHOUSE_WRITER_DSN` leaves the API; the scheduler gains
  `CLICKHOUSE_ERASER_DSN`, `ERASURE_HOUR_UTC`, `ERASURE_BATCH_SIZE`.
- No product spec changes: the customer already sees their data stop at the request.

## Risks and rollback

- **The many-tenant statement is the riskiest code here.** If the list ever held a live tenant, the
  night would erase a paying customer. The guard is structural (only the owed query feeds it), the
  list is re-read inside the job, and a live test seeds a live tenant beside two deleted ones and
  proves it untouched.
- Physical erasure now lags the request by up to a day (longer after a failed night). The customer
  sees no difference; their data stopped being readable at the request.
- A long night: 14 tables × (mutation + wait) can run for hours on a large store. The job's hard
  time limit is set for that, and a killed run is simply the next night's work.
- Rollback: pre-deployment, `git revert`.

## Plan and validation

`plan.md`.

## Status

`status.md`.
