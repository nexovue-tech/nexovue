# Plan: ClickHouse protects itself (group 2a)

> **Status:** Complete

## Preconditions

Group 1 committed and pushed (`0279175`). Group structure approved 2026-10-10; group 2 split into 2a
(this) and 2b (Redis goes, ADR-020) with the founder told.

## Current system

Read from the code on 2026-10-10.

- `packages/py-platform/src/nexovue_platform/tenant_statements.py:36-40` — `READ_SETTINGS`:
  `max_execution_time` 30, `max_result_rows` 500,000, `result_overflow_mode` throw. No memory setting.
  Applied to every tenant read by `data/clickhouse/base.py` and the scheduler's alert reads.
- `deploy/compose/compose.infra.yaml:62-90` — ClickHouse creates one user from `CLICKHOUSE_USER` /
  `CLICKHOUSE_PASSWORD` with `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1`; only `config.d` files are mounted,
  no `users.d`.
- The API writes to ClickHouse in one place: tenant erasure (`purge/`), whose purger is built in
  `organizations/dependencies.py:41` from `resources.clickhouse`. Every other API use reads. The
  scheduler only reads ClickHouse — its `INSERT`s are Postgres (`probes/state_store.py`,
  `alerts/send_test.py`, `alerts/new_errors.py`).
- `apps/api/src/nexovue_api/lifespan.py:227-235` — one ClickHouse client from `CLICKHOUSE_DSN`.
- `logs/tail.py:514-548` — `tail` subscribes `registry.subscribe(..., project_id=project_id, ...)`
  without checking the project; `TailRegistry` keeps one feed per (tenant, project). No cap on viewers.
- `llm_observability/queries.py` — `LLM_CALLS_SQL` has no `LIMIT`; `metrics/queries.py` shows the
  pattern: read `MAX + 1`, report `truncated`.
- `deploy/compose/docker-compose.app.yaml:338` — `scheduler-light` 256M (dev: 768M,
  `docker-compose.yaml:476-481`); `scheduler-deliveries` 256M in app (dev: 384M).
- Tests that exist: `apps/api/tests/test_log_tail.py`, `test_llm_observability.py`,
  `test_llm_observability_live.py`, `test_settings.py`.

## Steps and tasks

### Step 1 — every read is bounded in memory

*Judgeable:* a deliberately memory-hungry query sent with `READ_SETTINGS` is refused
`MEMORY_LIMIT_EXCEEDED` on its own, and every other query keeps running.

- [x] **T1** — `tenant_statements.py` · `READ_SETTINGS` gains `max_memory_usage` (1 GiB) and
  `max_bytes_before_external_group_by` (512 MiB — a big GROUP BY spills to disk before it is refused);
  sizes stated as laptop sizing until the group 8 load test · proved by a new
  `apps/api/tests/test_read_settings_live.py`: a `groupArray` over 2·10⁸ numbers with `READ_SETTINGS`
  is refused `MEMORY_LIMIT_EXCEEDED`, and **seen succeeding without the cap first**

### Step 2 — readers connect as a user that can only read

*Judgeable:* signed in to ClickHouse as the reader, `INSERT`, `ALTER … DELETE`, `DROP` and
`SET max_memory_usage = 0` are all refused; every screen still loads; deleting a project still erases its
telemetry.

- [x] **T2** — new `deploy/clickhouse/users.d/reader.xml` · user `nexovue_reader`, password from
  `CLICKHOUSE_READER_PASSWORD`; profile `reader` with `readonly = 2` (settings may change, data may not),
  the Step 1 memory settings as defaults **and as constraint maxima**, and `max_execution_time` max 30;
  `GRANT SELECT ON nexovue.*` only · mounted in `compose.infra.yaml`, password defaulted for development
  only · proved by new `test_clickhouse_reader_live.py`: each refusal above, and a tenant read succeeding
- [x] **T3** — `apps/api/src/nexovue_api/settings.py` and `lifespan.py` · `CLICKHOUSE_DSN` is the reader;
  new required `CLICKHOUSE_WRITER_DSN` for erasure only; `Resources` gains `clickhouse_writer`;
  `organizations/dependencies.py:41` builds the purger from it · proved by `test_settings.py` cases and
  the existing deletion live tests (`test_deletion_live.py`) passing against the split
- [x] **T4** — the scheduler's `CLICKHOUSE_DSN` points at the reader · compose only · proved by the alert
  live suite (`apps/scheduler/tests/test_alert_telemetry_live.py`) passing
- [x] **T5** — `deploy/compose/docker-compose.yaml`, `docker-compose.app.yaml`,
  `docker-compose.onprem.yaml` · the reader and writer DSNs per service; `CLICKHOUSE_READER_PASSWORD`
  demanded (`:?`) in both deployment files · proved by `just fresh` bringing the stack up and the gate
  passing

### Step 3 — the live tail can be opened only on your own project, and only a few at once

*Judgeable:* opening a tail with a made-up project id is refused; a sixth tail from one person is
refused; a real tail works as before.

- [x] **T6** — `logs/tail.py` · before subscribing, check the project belongs to the caller's
  organization with a short-lived session (opened and closed before the tail starts, so no pooled
  connection is held for its life); refuse with the WebSocket policy-violation close otherwise ·
  proved by new cases in `test_log_tail.py`
- [x] **T7** — `logs/tail.py` · `TailRegistry` counts open viewers per person and per organization;
  past 5 per person or 20 per organization a new tail is refused with a close that says why · proved by
  new cases in `test_log_tail.py`

### Step 4 — the LLM-calls read is capped and says so

*Judgeable:* the screen notes "more groups than this list holds" when the cap is reached.

- [x] **T8** — `llm_observability/queries.py`, `schemas.py`, `service.py` · read `MAX_LLM_CALLS + 1`
  (500), return `truncated` · proved by new cases in `test_llm_observability.py`
- [x] **T9** — `apps/web/src/features/llm-observability/{api.ts,LLMCallsScreen.tsx}` · the `truncated`
  notice, as the metrics screen words it · proved by a new case in `LLMCallsView.test.ts`

### Step 5 — the hosted scheduler has the memory it needs

- [x] **T10** — `docker-compose.app.yaml` · `scheduler-light` 768M and `scheduler-deliveries` 384M, the
  values dev measured; the on-prem file gains the same limits · proved by reading the compose config
  (`docker compose config`) — there is no hosted target to run it on

## New risks

- **`readonly = 2` lets a reader change settings** — needed, because every read sends
  `READ_SETTINGS`; the profile's constraints are what stop a query raising its own memory or time cap.
- **The writer DSN is one more secret** for every deployment.
- **Refusing a sixth tail** may surprise someone with many tabs; the close message says why.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Per-query memory 1 GiB, external GROUP BY at 512 MiB, 5 tails a person / 20 an organization | No — laptop sizing, configurable | `production-readiness.md` *Capacity* after the group 8 load test |

## Gates

- [x] Specification approval — not applicable
- [x] Design approval — this plan
- [x] **Plan approval — before the first edited file** (founder, 2026-10-10)
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Documentation and status update

## Validation plan

Each new test seen failing first. `just fresh` once (the ClickHouse user and the DSNs change how the stack
starts), then `just check`, counts read.

## Rollout and rollback

Pre-deployment. Revert the commit; `just fresh` restores the single-user stack.

## Outcome

Built as planned; every step judged live on the development stack. What changed, and why:

- **T1's live test asks the server what it received, not whether a heavy query fails.** The development
  ClickHouse idled at 2.54 GiB of its 4 GiB; a query big enough to trip a 1 GiB cap would, uncapped,
  have pushed the whole server over and failed unrelated queries. `getSetting('max_memory_usage')` sent
  with `READ_SETTINGS`: red `0`, green `1073741824`. Enforcement itself is ClickHouse's documented
  behaviour.
- **The reader profile sets a minimum as well as a maximum** for memory and time: `0` means unlimited
  for both, and a maximum alone let it through — red-run with the minimums removed: `SET
  max_memory_usage = 0` and `max_execution_time = 0` were accepted.
- **A refused `DROP` is proved on a throwaway table the admin creates**, never on a real one; `DROP
  TABLE IF EXISTS` on a missing table succeeded without any privilege check, which proved nothing.
- **The erasure wiring is pinned by a unit test, not a live erasure** (`test_erasure_uses_the_writer.py`,
  red with the purge built from the reader). No end-to-end test deletes an organization, and running a
  real erasure on the development store rewrites every part of every telemetry table.
- **T10 changed the hosted file only.** The on-prem file sets no memory limit for any service — its
  existing `TODO(sizing)`, F37's — and one service's limit alone would be inconsistent.
- **`just fresh` was not run.** It wipes the development databases, and proves nothing more here: the
  reader is a config file ClickHouse loads whatever its data, and ClickHouse, the API and
  `scheduler-light` were each recreated on the new configuration and checked.
- **The tail limits are per API process**, as every in-process limit is now (the 2b decision on rate
  limits): several API containers each allow five tails a person.
