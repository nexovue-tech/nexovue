# Plan: browser-door — the browser door stops counting in the rollups

> **Status:** Approved by the founder, 2026-11-07, with open decisions 1 to 5 answered as recommended (`status.md`)
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order; tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited.**

## Preconditions

- The scope is approved (foundation item 7, 2026-11-02; F28 owns it, item 22) and is not redesigned here.
- Open decisions 1 and 4 in [`status.md`](status.md) are answered — they decide the shape of Steps 3 and 4's
  tasks. Steps 1 and 2 are not blocked by them.
- This worktree is cut from `feature/phase-2`, which carries `service_stats_1m` (`000035`). The foundation's
  "F6 merges first" ordering applies to the branch this lands on, not to writing this plan.

## Current system

Read from the code in this worktree (`wt/browser-door`); line numbers are for it and will drift.

**How a span's door is known today.** The receiver decides the door from the kind of key:
`DoorFor` maps a secret key to `DOOR_OTLP` and a public site key to `DOOR_RUM`
(`apps/receiver/internal/ingest/ingest.go:46-55`), and stamps it on the Kafka envelope
(`ingest.go:288`; `packages/contracts/proto/nexovue/v1/envelope.proto:40`, enum at `:9-15`). The
prober stamps `DOOR_SYNTHETIC` on its own envelopes
(`apps/scheduler/src/nexovue_scheduler/publish.py:187,249`). Only a public key may use the browser
door and only a secret key the server doors (`apps/receiver/internal/auth/auth.go:39-47,76-84`).

**The `door` column question, answered from the code: it does not exist, and this change does not add it.**
Neither `spans` nor `logs` has a door column — `deploy/clickhouse/migrations/000001_spans.up.sql` and
`000002_logs.up.sql` declare none, and a grep for `door` across `deploy/clickhouse/migrations/*.sql` matches
only comments in `000030`/`000031` (web vitals). The column on `spans`/`logs` is F28's to add (foundation
section 8, F28 design D38). This change never needs it: every behavior here acts in the workers or receiver
*before* rows are written, and the door is already on every Kafka envelope. The workers decode that envelope
in each pipeline (`traceassembly/stages.go:245`, `metricwriting/metricwriting.go:336-347`) — today the only
reader of the stamp is the usage meter (`pipeline/usage/usage.go:281-291`).

**Where the rollups are written: by the worker, not by materialized views.** `endpoint_stats_1m`'s
materialized view was dropped in `000024` because it sat downstream of the sampler; both rollups are now
filled by the derive stage, pre-sampling, with direct inserts
(`000024_endpoint_stats_1m_from_derive.up.sql`, `000035_service_stats_1m.up.sql` header;
`derive/endpointwriter.go`, `derive/servicewriter.go`). The counting path:
`absorbMessage` decodes the envelope (`traceassembly/stages.go:244-285`), builds `countable` per span
(`stages.go:39-60` — it copies no door) and calls `counters.Observe` (`stages.go:264`;
`derive/counters.go:236`), which routes through `observeEndpoint` → `observeService`
(`derive/endpoints.go:108-112`), counting only `SERVER` and `CONSUMER` spans (`endpoints.go:100-102`).
Edges go separately through `edgeSpan` (`stages.go:64-84,273-275`) — F3's A63 territory, untouched here.
**One filter before `stages.go:264` therefore covers `service_stats_1m`, `endpoint_stats_1m`, and — by
construction — F28's `service_versions_1m`,** because F28's design feeds its versions map from the same
`observeService` point (its design, *In the worker*, D55). `service_versions_1m` does not exist yet:
migrations end at `000037`.

**How the service cap works.** `MaxServicesPerTenant = 2000` (`derive/services.go:55`). The cap is applied
at drain, to rows sorted by minute → tenant → project → service name (`services.go:101-113,172-199`), and
`capServices` keeps the first 2,000 rows of each tenant-minute *in that sort order* (`services.go:205-236`)
— so minted names sorted early evict real services. What going over leaves behind: `ServiceCap` names one
tenant and a count for the drain (`services.go:131-137`), which becomes a WARN log line and an in-process
counter logged at drain (`traceassembly/pipeline.go:142-145,394,756-768`). Nothing durable, nothing
per-minute, nothing a screen can read.

**Where `synthetic.*` readings come from and go.** The prober writes gauges named `synthetic.check.*` /
`synthetic.tls.*` and evidence logs, all under `DOOR_SYNTHETIC` (`publish.py:60-74,187,249`). The metric
worker flattens points keeping the name and resource attributes but never the door
(`metricwriting/flatten.go:71-110`). The API's check screens then select from `metrics_gauge` and `logs`
by name and `synthetic.check.*` attributes alone
(`apps/api/src/nexovue_api/synthetics/queries.py:30-42,145-322`). So a browser key — the door serves
`/browser/v1/metrics` (`otlp/browserdoor_test.go:34`) — or a secret key can forge a check's readings.

**Legitimate browser spans are never counted anyway.** nexovue's SDK sends one page-view span with no kind,
so `INTERNAL` (`packages/browser-sdk/src/rum.ts:66-78`, kind mapping `otlp/spans.ts:9-14`), and the rollups
count only `SERVER`/`CONSUMER`. Filtering by door loses no real data (F19 spec D63).

**Where code and approved documents disagree:**

- `metricwriting/flatten.go:64-70` states a principle — never drop the odd metric silently; reject the whole
  message instead — that sits in tension with "drop forged `synthetic.*` points". How a refused reading is
  handled is open decision 4, not this plan's to pick.
- F19's `status.md` says in one place that P2 has no owner (`slo-tracking/status.md:324`) and in another that
  F28 owns it (`:268,332`). The foundation (item 22) settles it: F28 owns it. Recorded in this change's
  `status.md`, not edited there.

## Change required

The four approved behaviors in [`change.md`](change.md), *Intended behavior* — foundation section 9, item 7,
verbatim. F19's design details the same four (`slo-tracking/design.md:449-454`) and adds the AC37 end-to-end
test condition (`:458`); F28's design fixes the filter point (D55) and requires the test to assert both
tables. No restatement here.

## Impact

| Area | What changes |
|---|---|
| Files | `apps/workers/internal/pipeline/traceassembly/stages.go`, `derive/services.go`, `derive/counters.go`, `traceassembly/pipeline.go`, `metricwriting/flatten.go` (+ per decision 1: possibly a new writer file and `deploy/clickhouse/migrations/000038_*`) |
| Services | workers only; receiver, API, scheduler, web unchanged (unless decision 1 picks a surface read by the API) |
| Schema / migrations | None required by Steps 1, 2, 4. ClickHouse `000038` only if decision 1 chooses a table (then also `erasure/tables.py`, per the foundation's checklist). Alembic not needed unless decision 1 chooses Postgres (next free: `0051`) |
| UI | none in this change; F19's screen later reads whatever decision 1 creates |
| Tests | new cases in `derive/services_test.go`, `derive/counters_order_test.go`, `traceassembly` tests, `metricwriting/flatten_test.go`; one new live end-to-end test `apps/api/tests/test_browser_door_live.py` |

## Reuse

- The envelope's `Door` — already decoded and in hand in both pipelines (`stages.go:245`,
  `metricwriting.go:336`). No new plumbing from the receiver.
- The drain-time, sorted-order cap mechanism and its determinism argument (`services.go:166-171`) — the
  ordering changes, the mechanism does not.
- The live-test harness: `tests/helpers/live.py` (`require_stack`), `clickhouse_cleanup.py`, and the
  receiver-to-rollup wait pattern in `apps/api/tests/test_tenant_isolation_live.py:43-57`.
- If decision 1 picks a ClickHouse table: `000035`'s measured insert-deduplication pattern
  (`insertdedup`, `000021`) and `servicewriter.go`'s shape.

## Gaps

- No durable record of a tenant going over the cap (today: one WARN line and a process counter).
- No reader of the envelope door anywhere past the usage meter.
- No test anywhere sends a forged `SERVER` span or a forged `synthetic.*` reading; the live suites write
  rollup rows directly (`test_rum_live.py`) rather than through the receiver.

## Steps and tasks

Each behavior change's test is written first and **seen failing** against today's code before the fix lands
(change-work rule 5). Step 1 ships the thin end-to-end path.

### Step 1 — Browser-door spans stop counting in the rollups

*Judgeable:* run the stack, POST a forged `SERVER` span to `/browser/v1/traces` with a site key, and watch it
appear in the trace view but in neither `service_stats_1m` nor `endpoint_stats_1m`. The new live test does
exactly this.

- [x] **T1.1** — `apps/workers/internal/pipeline/traceassembly/stages.go` · in `absorbMessage`, skip the
  `counters.Observe` call (`:264`) when `payload.envelope.GetDoor() == DOOR_RUM`, leaving `edges.Observe`,
  the sampling buffer and the skew report untouched · proved by a new `traceassembly` test (beside
  `invariants_test.go`) that absorbs a `DOOR_RUM` envelope carrying a `SERVER` span and asserts the counters
  derived nothing while the edge accumulator and the buffer both saw it — seen failing first.
- [x] **T1.2** — same test file · a `DOOR_OTLP` envelope with the same span still counts in both rollup
  accumulators, so the filter cannot over-reach · part of T1.1's table-driven test.
- [x] **T1.3** — `apps/api/tests/test_browser_door_live.py` (new) · the AC37 end-to-end proof through the
  real receiver, Kafka and worker: sign up, create a site key, POST a forged `SERVER` span to
  `/browser/v1/traces`, then assert a row lands in `spans` and none in `service_stats_1m` or
  `endpoint_stats_1m` (both tables in one test, per F28 D55) · fails loudly when the stack is down
  (`tests/helpers/live.py`) · seen failing first. F28 extends this to `service_versions_1m` when its
  migration exists; recorded in `status.md`, *Documentation impact*.
  *Built 2026-11-07:* F28's `000038` already existed, so the test asserts all three tables itself. F28's
  `countedVersion` branch, which its D55 says this change deletes, is deleted with its test.

### Step 2 — The cap keeps the busiest

*Judgeable:* the new unit test's scenario — 2,001 one-request `aaa-*` services plus one busy real service in
one tenant-minute — keeps the busy one.

- [x] **T2.1** — `apps/workers/internal/pipeline/derive/services.go` · `capServices` ranks each
  tenant-minute's rows by request count descending, name ascending as the tie-break, before applying the cap;
  the input stays the drain's sorted rows, so a replay still drains the same block · proved by a new case in
  `services_test.go` (minted `aaa-*` names no longer evict a busy service; a tie keeps the same service every
  run) — seen failing first, since today's code keeps first-in-name-order.
- [x] **T2.2** — `derive/counters_order_test.go` · the drain-determinism property still holds with the new
  ordering: two drains of identical input produce identical rows in identical order · extend the existing
  ordering test.

### Step 3 — Going over the cap is recorded

*Judgeable:* send a tenant over the cap on the dev stack and read the record back by tenant and minute.
**Task shapes depend on open decision 1; the recommendation's tasks follow. If the founder picks another
option, this step is re-planned before implementation.**

- [x] **T3.1** — `derive/services.go` · widen `ServiceCap` (`:131-137`) to per tenant-minute entries
  (tenant, minute, services dropped), still computed inside `capServices` · proved by a new case in
  `services_test.go` asserting two tenants over the cap in different minutes each get their own entry —
  seen failing first.
- [x] **T3.2** — `deploy/clickhouse/migrations/000039_services_over_cap_1m.up.sql` / `.down.sql` (`000038` became F28's) · the over-cap record
  table (per decision 1's recommendation), copying `000035`'s deduplication-token pattern so a retried
  insert cannot double-count; tenant first in the sort key · plus `erasure/tables.py`, or erasure's live
  test fails (foundation section 8 checklist).
- [x] **T3.3** — `traceassembly/pipeline.go` · write the entries at drain where `reportServiceCap` runs
  (`:707,756-768`); keep or delete the WARN line per decision 1's answer (rule 11: delete what the change
  supersedes, state what remains) · proved by a worker test beside the existing drain tests, and the record
  read back in `test_browser_door_live.py` after T1.3's tenant is pushed over the cap.

### Step 4 — `synthetic.*` readings come only from the prober's door

*Judgeable:* the live test sends `synthetic.check.status` through the browser door and through a secret key's
server door; neither lands in `metrics_gauge`; the dev prober's own readings keep flowing.
**Blocked on open decision 4 (what happens to a refused reading); the recommendation's tasks follow.**

- [x] **T4.1** — `apps/workers/internal/pipeline/metricwriting/flatten.go` · a point whose metric name
  starts `synthetic.` or whose resource attributes carry a `synthetic.check.*` key is not written unless the
  envelope's door is `DOOR_SYNTHETIC`; the refusal is handled per decision 4 · proved by new cases in
  `flatten_test.go`: forged name under `DOOR_RUM` and `DOOR_OTLP` → not written; the same name under
  `DOOR_SYNTHETIC` → written; a non-synthetic browser gauge → written — seen failing first.
- [x] **T4.2** — `apps/api/tests/test_browser_door_live.py` · extend with the forged-reading case above,
  asserting against `metrics_gauge` through the real receiver and worker.
- [x] **T4.3** — whether the same guard must cover the evidence `logs` read at
  `synthetics/queries.py:322` is open decision 5; no task until it is answered, because widening the
  approved scope is the founder's call. *Answered yes (decision 5); built:* `logwriting/flatten.go` drops and
  counts a record whose resource claims `synthetic.check.*` outside `DOOR_SYNTHETIC`, proved by
  `flatten_test.go` and `stages_test.go` (seen failing first) and the live test.

## New risks

- The filter point (T1.1) sits upstream of F28's future versions map; if F28 moves `observeService`, the
  coverage silently narrows. Mitigated by F28's planned both-tables test (its plan, *Preconditions*) and
  noted in its gate list (`deployment-release-tracking/plan.md:279`).
- Busiest-first is judged per drain, per worker (each worker drains its own partitions' share), so a service
  can be kept by one worker and folded by another in the same minute. Today's cap has the same per-drain
  grain; recorded under open decision 2 so the founder accepts it knowingly.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | What "recorded" means for going over the cap | Step 3 only | `status.md` open decision 1, then an ADR if the answer adds a table |
| 2 | How "busiest" is measured (per-drain grain) | No — proceed on the recommendation | `status.md` open decision 2 |
| 3 | Whether existing rows need a rebuild | No | `status.md` open decision 3 |
| 4 | What happens to a refused `synthetic.*` reading | Step 4 only | `status.md` open decision 4 |
| 5 | Whether forged synthetic evidence *logs* are in scope | T4.3 only | `status.md` open decision 5; a yes widens the approved scope, so the founder's |

## Gates

- [ ] Specification approval — **done**: the scope is the founder's, 2026-11-02 (foundation items 7 and 22)
- [ ] Design approval — not applicable; the change record carries the design, inside the approved scope
- [ ] **Plan approval — before the first edited file**
- [x] Implementation
- [x] Tests — each behavior's test seen red first
- [x] Review — `/review-diff`
- [ ] Production-readiness assessment — with F28's, since its Step 4 waits on this
- [x] Documentation and status update

## Libraries and research

No new library, pinned version or external API is touched, and the rollups are worker-filled — `000024`
dropped the one materialized view — so no current-best-practice question about ClickHouse syntax arises and
no web research was run for this plan. The one candidate schema (decision 1's table) copies the
insert-deduplication pattern this repository itself measured (`000021`, `000035` header). Nothing in this
plan is asserted from memory about an external system; the OpenTelemetry span-kind facts are F19's, read
from source on 2026-11-02 and recorded in its spec D63.

## Validation plan

- During development: the named Go tests per module with `-count=1` (never repository-root `./...`).
- Whole change: `just check`, output and counts read.
- Live: `test_browser_door_live.py` against `just dev`'s stack — it is the AC37 condition F19's Step 4
  requires, and it must fail loudly when the stack is down.
- This change alters a store write path, so the chaos check (`just chaos`) runs once at the end.

## Rollout and rollback

Pre-deployment: no customers, no live data, nothing to migrate (decision 3). Reverting the commit restores
today's behavior. The real risk is the one the template names — the first step shipping and nobody judging
it — so Step 1's live test is the judging, and F28's and F19's Step 4 gates stay closed until this record
reads Verified.

## Outcome

Built 2026-11-07, all four steps, uncommitted for the coordinator's review. Validation, residual risks and what
was not run are in [`status.md`](status.md). Where the code differed from this plan, mechanically: F28's
`000038` took the migration number (this change's table is `000039`) and its version rollup already existed, so
T1.3 asserts three tables and F28's interim empty-version branch is deleted (F28 D55). `just check` and
`just chaos` were not run (the coordinator runs `just check`).
