# Status: deletion-finishes

## Lifecycle status

Implemented and verified on the development stack. Not committed.

## Current task

None.

## Completed

- Code read; `change.md` and `plan.md` written. Founder decision 2026-10-11: Postgres rows are
  deleted in the request (ADR-014 point 7 amended).
- **Step 1 — the request deletes the Postgres rows; the API stops erasing.** `request_deletion`
  deletes every `DELETED_DIRECTLY` table in its one transaction; the "deletion requested" entry is
  tenantless and names no person. `erase`, `erase_after_response`, the background task, `purge/`,
  `TenantScopedPurger`, `organizations/dependencies.py` and the API's ClickHouse writer
  (`CLICKHOUSE_WRITER_DSN`) are deleted. Red then green: the rows remained after the request (red:
  41 tables still full); the same email could not sign up again (red: `ConflictError: An account
  already exists`); the request's entry carried the organization (red: `org_id` set).
- **Step 2 — the night erases every owed tenant together.** `nexovue_scheduler.erasure`: `owed`
  (the one query that makes an `OwedTenants`), `statements` (one `ALTER TABLE … DELETE WHERE
  tenant_id IN {owed_tenants:Array(String)}` per table, and the per-tenant count), `store` (the only
  client that can delete; refuses an empty list or a statement without the predicate), `run` (submit
  and wait per table, then per tenant: zero rows → delete both prefixes → certificate; rows left →
  owed). `probes/files.py` gains `delete_prefix`; the upload roots and audit actions are shared in
  `nexovue_platform/erasure.py`. The structure test allows the many-tenant binding in
  `erasure.statements` alone and `OwedTenants(...)` in `erasure.owed.take_owed` alone. Red then
  green: a record landing after the mutation was certified (red: `KeyError`, not owed); a live tenant
  let into the list lost its rows (red: `{'spans': 0, …} == {'spans': 3, …}`); an empty list was sent;
  a stray copy of the statement and a hand-built list both passed the structure test (red: 3 failed).
- **Step 3 — at the hour, and loud when stuck.** `CLICKHOUSE_ERASER_DSN` (empty → ERROR every
  hour), `ERASURE_HOUR_UTC` (default 2), `ERASURE_BATCH_SIZE` (default 100, groups 480 s apart).
  Beat asks hourly with limits of 4 h / 4 h 5 min on `light` (red: 50 s). An erasure owed past 3 days
  is logged at ERROR (red: the certificate was ignored). The eraser credential is on
  `scheduler-light` in all three compose files; the API holds no ClickHouse credential that writes.
- **Step 4 — documents.** ADR-014 (built, point 7 amended, as built), ADR-009, the system
  reference, the readiness document (blocker 22, group 4), the data-lifecycle spec.
- **`just check` exit 0** — Python 4193 passed, 3 skipped; web 1352; browser SDK 120; lint and
  types clean. The Python count fell by 46: the tenancy structure test runs once per API query
  function, and 35 such functions were deleted with the API's erasure; the rest are the moved and
  replaced erasure tests.
- **Live on the dev stack:** a throwaway organization signed up through the API and sent 5 spans;
  `DELETE /api/v1/organization` → 202; its users, projects and keys were gone at once and the same
  email signed up again (201). The erasure run by hand in `scheduler-light` (12.8 s): `erased 1`;
  its spans and trace summaries 5 → 0; the certificate written; nothing left owed; the seeded `acme`
  organization's 173,832 spans untouched.

## Residual risks

- The eraser uses the ClickHouse admin account until group 6 provisions one that can only
  `ALTER DELETE` the 14 tables.
- The night's run holds one of `scheduler-light`'s 8 slots for as long as it runs.
- A night missed (beat down across the hour) is a day's delay; day 3 is an ERROR.
- The live check left one throwaway organization, "Erase E2E again", in the dev database.

## Next action

Founder reviews the diff; on approval, commit. Then readiness group 5 (keys can be rotated).
