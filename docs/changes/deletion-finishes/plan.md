# Plan: deletion-finishes (readiness group 4)

> **Status:** Complete 2026-10-11
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- `change.md` written; founder decision 2026-10-11: Postgres rows are deleted in the request.
- ADR-014 accepted (the nightly batch); point 7 amended by this change.

## Current system

Read from the code (API paths under `apps/api/src/nexovue_api/`):

1. **The request starts the erasure in the API process.** `organizations/router.py:48-53` adds
   `erase_after_response` as a background task; it waits 120 s (`organizations/service.py:79`) and
   logs on failure (:202-221). `erasures_still_owed` (:183-199) has no caller outside tests and asks
   once per tombstoned organization (an N+1).
2. **`erase` does three stores in one pass** (:131-180): submit the 14 ClickHouse mutations, delete the
   object prefixes, delete the 12 `DELETED_DIRECTLY` tables (`organizations/tables.py:30-50`), wait,
   and write the certificate (`audit.record_organization_erased`, `org_id` NULL).
3. **The purge binds exactly one tenant.** `purge/queries.py` holds 14 `DELETE_TENANT_ROWS`, one
   `COUNT_REMAINING_ROWS` and a `system.mutations` progress query keyed on the stored text
   `tenant_id = '<id>'` (:109-114) — that text match would not find a mutation naming many tenants.
   The runtime guard `binds_tenant` (`nexovue_platform/tenant_statements.py:21-80`) and the structure
   test (`tests/test_tenant_chokepoint_structure.py:252-259`) both require `tenant_id = {tenant_id:String}`
   in every telemetry statement, so a many-tenant statement fails both today — the deliberate
   exception ADR-014 names.
4. **The scheduler already has most of what the night needs.** Postgres (`process_engine.with_engine`),
   a ClickHouse client confined to `alerts/telemetry.py` (read-only DSN, `settings.py:110`), S3 through
   boto3 (`probes/files.py:32-57`, delete by key only), beat sweep entries with a fixed interval
   (`beat.py:330-350`) and per-job soft/hard limits (`nexovue_platform/jobs.py:27-28`). Beat has no
   time-of-day entry; the `light` worker has 8 slots (`docker-compose.yaml:436`).
5. **The API's ClickHouse writer exists only for this** (`settings.py:188`, `lifespan.py:115-116,256,308`,
   `organizations/dependencies.py:31-45`), and the structure test allows the raw driver in
   `organizations.dependencies` for it alone (`test_tenant_chokepoint_structure.py:54-58`).
6. **Object key roots are private to the API** (`sourcemaps/service.py:44`, `checkfiles/service.py:38`);
   the scheduler re-states the check-file layout in `probes/files.py`.
7. **`users.email` is unique across nexovue** (`data/postgres/models.py:211`) — why the founder chose to
   delete Postgres rows in the request.

## Change required

`change.md` §Intended behavior.

## Impact

| Area | What changes |
|---|---|
| Files | API: `organizations/{service,router,dependencies,queries}.py`, `purge/` (moves out), `settings.py`, `lifespan.py`, `health` if it pings the writer. Scheduler: new `erasure/` package, `beat.py`, `jobs/registry.py`, `settings.py`. py-platform: object key roots. Compose ×3, `.env.example` |
| Services | API (request does Postgres; no ClickHouse writer), scheduler (nightly erasure) |
| Schema / migrations | None. The obligation is still `deleted_at` without a certificate |
| UI | None. The 202 already says the erasure follows |
| Tests | `test_deletion_live.py` reworked; `test_purge_{live,guards,inventory}.py` move to `apps/scheduler/tests/`; `test_erasure_uses_the_writer.py` deleted; chokepoint structure test extended; new scheduler live test for the night |

## Reuse

- `purge/tables.py` inventory and its two inventory tests, unchanged in substance.
- `organizations/tables.py` + `queries.DELETE_TENANT_ROWS` — now called by the request.
- `audit.erasure_certificate_target` format for the certificate the scheduler writes.
- `tenant_statements.executable_text` for the new guard; `READ_SETTINGS` for the counts.
- The beat sweep-entry pattern and the job registry (group 2b), as group 3's grace sweep did.

## Gaps

- A guard and statements for "every owed tenant at once".
- A time-of-day run in beat.
- `delete_prefix` for the scheduler's S3 client.
- An owed-erasures query that is one statement, not N+1.

## Steps and tasks

### Step 1 — the request deletes the Postgres rows; the API stops erasing

*Judgeable:* after `DELETE /organization`, the users, projects and billing rows are gone and the email
can sign up again at once; ClickHouse and files are untouched; the API boots with no ClickHouse writer.

- [x] **T1** — `organizations/service.request_deletion` · deletes `DELETED_DIRECTLY` in the same
  transaction, after stopping the charges. The "deletion requested" audit entry becomes tenantless
  like the certificate (no actor, no address — nothing would ever erase an org-scoped one now).
  `erase`, `erase_after_response`,
  `BACKGROUND_WAIT_SECONDS`, `ErasureOutcome` deleted · `test_deletion_live.py`: the rows are gone in
  the response's transaction (red: they remain until the background task) and the same email signs up
  again (red: 409).
- [x] **T2** — `organizations/router.py`, `organizations/dependencies.py` · no background task, no
  `TenantErasure`; `dependencies.py` leaves the raw-client allow-list ·
  `test_tenant_chokepoint_structure.py`.
- [x] **T3** — `settings.py`, `lifespan.py`, compose ×3, `tests/environment.py` · `clickhouse_writer_dsn`
  and `Resources.clickhouse_writer` deleted · `test_settings.py`; `test_erasure_uses_the_writer.py`
  deleted.

### Step 2 — the night erases every owed tenant together

*Judgeable:* with two deleted organizations and one live one seeded in ClickHouse and S3, one run of
the task empties both deleted tenants in all 14 tables and both prefixes, writes two certificates, and
leaves the live tenant's rows and files exactly as they were.

- [x] **T4** — move `purge/{tables,exceptions}.py` and the inventory to `nexovue_scheduler/erasure/`;
  `purge/` deleted from the API · inventory and live inventory tests move with it.
- [x] **T5** — `erasure/statements.py` · `DELETE_OWED_ROWS[table]` =
  `ALTER TABLE <t> DELETE WHERE tenant_id IN {owed_tenants:Array(String)}`, and
  `COUNT_OWED_ROWS` (per tenant, per table, `GROUP BY tenant_id`) · module constants, as now.
- [x] **T6** — `erasure/store.py` · `OwedErasureStore`, the one module allowed the raw driver for writing:
  it is constructed only with the result of `owed_tenants()` (a frozen type only `erasure/owed.py` can
  make), refuses an empty list, refuses a statement without `tenant_id IN {owed_tenants:Array(String)}`,
  and submits with `mutations_sync = 0` · `test_erasure_guards.py` (scheduler): wrong statement, empty
  list, a list built by hand all refused.
- [x] **T7** — `test_tenant_chokepoint_structure.py` · the many-tenant binding is allowed only in
  `erasure.statements`, and `OwedTenants` is constructed only in `erasure.owed` · red: a copy of the
  statement in another module fails.
- [x] **T8** — `erasure/owed.py` · one statement: organizations with `deleted_at` and no certificate,
  oldest first, `LIMIT ERASURE_BATCH_SIZE` · scheduler test.
- [x] **T9** — `erasure/run.py` · for each table: submit, then poll `system.mutations` for that table's
  mutation until done (killed → stop the run, tenants stay owed); then counts per tenant; for each
  tenant at zero everywhere: delete both object prefixes, then write the certificate (Postgres). A
  tenant not at zero stays owed. Batches beyond the cap wait 480 s · `test_erasure_live.py` (scheduler):
  the judgeable above, plus a tenant with rows landed after the mutation stays owed (red: certified).
- [x] **T10** — py-platform `objectkeys.py` · the two tenant prefixes, used by the API's uploads and the
  scheduler; `probes/files.py` gains `delete_prefix` · existing upload tests still pass.

### Step 3 — at the hour, and loud when stuck

*Judgeable:* beat enqueues the erasure only during `ERASURE_HOUR_UTC`; a seeded erasure owed for four
days logs one `ERROR` naming the count and the oldest.

- [x] **T11** — scheduler `settings.py` · `CLICKHOUSE_ERASER_DSN` (required for the task to run; absent →
  the task logs that erasure is not configured, every hour, at `ERROR`), `ERASURE_HOUR_UTC` (0-23,
  default 2), `ERASURE_BATCH_SIZE` (default 100) · `test_settings.py` (scheduler).
- [x] **T12** — `beat.py` · `ERASURE_ENTRY` hourly; the task itself returns at once outside the hour, so a
  leader change cannot run it twice in one night; its own limits (soft 4 h, hard 4 h 5 min) on the
  `light` queue — one of its 8 slots · `test_beat.py`.
- [x] **T13** — `erasure/run.py` · after the run, any owed erasure older than 3 days → one `ERROR` log
  line · live test.
- [x] **T14** — compose ×3, `.env.example` · the scheduler's eraser DSN (the existing admin user until
  group 6 gives it its own ClickHouse user with only `ALTER DELETE`), hour and batch size.

### Step 4 — documents

- [x] **T15** — ADR-014 (built; point 7 amended), `current-system-reference.md` (deletion, §6.7, the
  `purge` row), readiness (blocker 22, group 4, the stale `purge/__init__.py` note), `status.md`.

## New risks

- **The many-tenant statement.** Mitigated by T6-T8: one constructor, fed only by a query that can
  only return deleted organizations, and the live test's untouched third tenant.
- **The erasure holds one `light` slot for hours.** Seven remain; a dedicated queue is group 6's call
  if measurement says so.
- **Without `CLICKHOUSE_ERASER_DSN` nothing is erased.** It logs an error every hour, not silently.
- **A night can be missed** (beat down across the hour). The obligation stays; the next night takes it,
  and day 3 is an error.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | A dedicated ClickHouse user that can only `ALTER DELETE` the 14 tables. Assumed group 6 (deployment configuration), using the admin user meanwhile as the API does today. | No | readiness group 6 |

## Gates

- [x] Specification approval — not applicable
- [x] Design approval — ADR-014 and the founder's 2026-10-11 decision
- [x] **Plan approval — before the first edited file** — 2026-10-11
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Documentation and status update

## Validation plan

Every regression test seen red first. `just check` with counts read. Live on the dev stack: delete a
seeded organization through the API, confirm Postgres is clean and the email re-signs, run the erasure
task by hand, then confirm ClickHouse, S3 and the certificate, and that the seeded dev organization's
telemetry is untouched.

## Rollback

Pre-deployment; `git revert`.

## Outcome

Built as planned in four steps; evidence in `status.md`.

What changed from the plan, and why:

- **The store is `erasure.store.ErasureStore`**, not `OwedErasureStore`: the name says what it holds,
  and `OwedTenants` is the type that carries the guarantee.
- **The shared contract is `nexovue_platform/erasure.py`**, not `objectkeys.py`: besides the two
  upload roots it holds the two audit actions and the target format, which the API writes and the
  scheduler reads. The API's `tenant_prefix` helpers had no caller left and were deleted.
- **The live test's late record is injected after the last table's mutation**, through a store
  subclass, rather than raced against the pipeline.
- **No live negative control for an omitted table.** The inventory test holds the delete and count
  statements to the same tables, and the late-record case proves `remaining` names a table with
  rows left; a second set of 14 mutations per run would cost the dev store memory for no new proof.
- **`ERASURE_HOUR_UTC` and `ERASURE_BATCH_SIZE` are in `.env.example` only**; the compose files take
  the defaults.
- **`docs/features/data-lifecycle-controls/spec.md` and ADR-009** named the deleted purger and were
  updated.
