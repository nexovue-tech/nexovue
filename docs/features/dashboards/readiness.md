# Readiness: Dashboards (F20)

> **Assessed 2026-11-28, as one feature (plan Step 11, gate *Production-readiness assessment*), on `feature/phase-2` at `c22a3698`.**
> **Verdict: NOT READY for a production rollout.** The code is built, its tests pass, and the 500s at 20 readers are
> gone on the founder's Mac; but the measurement gate is not passed, nothing has been measured on a deployed server, and
> three of the plan's own release conditions (the screen-reader pass, the signed demo gate, design QA) are not done.
> `docs/operations/production-readiness.md` is kept outside the repository (founder, 2026-10-29); this page does not
> update it, and the platform's own blockers recorded there apply on top of F20's. Nothing here approves a rollout:
> that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass. **The local Mac stack is not evidence about a
> deployed server**: every pass below that rests on it says so.

## What F20 adds to production, in one paragraph

Saved dashboards: two Postgres tables (`dashboards`, `dashboard_versions`, migration 0056), routes under `/dashboards`
and `/dashboard-panels`, and a batch read that turns each panel into one bounded ClickHouse read, charged against the
organization's telemetry quota. Every API screen now reads ClickHouse through a new account, `nexovue_api_reader`,
capped at 2.4 GiB and 12 queries at once (`deploy/clickhouse/users.d/api-reader.xml`, ADR-045). That last point is why
F20's capacity matters beyond dashboards: it shares one ClickHouse server with ingest, detection and alerts.

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | Measurement gate (T5.7, T11.6) | **open: the deployed server** | `docs/research/f20-measurement-2026-11-27.md`, one API process, founder's Mac, demo rollups only. Marks 1, 2, 4, 5, 6, 7 pass; **mark 3 (worker lag) inconclusive**: the lag rose by itself from 12.4k to 30.8k across the run, so whether dashboards slow ingest cannot be judged; **marks 8 (scheduler) and 9 (NFR12: 30 people, 10 zooming) not run**. Also not run: metric series and catch-up reads, templates (AC129), raw readers at 7/30/90 days, ClickHouse stopped under load, two API processes after the fix. The report says itself it "does not replace the rerun on the deployed server before any cap is published" |
| 2 | The 2026-11-22 failed margin (7 x HTTP 500 at 20 readers, Postgres pool exhausted) | **pass on the Mac; open on the server** | Fixed by ADR-102 (commit `3e7de987`). The 2026-11-27 rerun: no 500, no 503, at most 1 connection `idle in transaction` (was 15), 20-dashboard p95 717 ms (was 11,662). The 20 x 429 are the organization's 600-a-minute quota (`ratelimit/policy.py:98`), as designed |
| 3 | Automated tests | **pass** | Run here, 2026-11-28: `uv run pytest apps/api/tests -q -n 4 --dist loadfile -k dashboard`: **547 passed, 1 skipped** (6 min 51 s, live suites included, sharing the stack with other agents' runs); `pnpm exec vitest run src/features/dashboards`: **23 files, 286 tests passed**; `test_migration_rules.py`: **72 passed**. The `-k dashboard` filter does not select the shared gates (rate-limit coverage, tenancy chokepoint, regression suites the plan names); they were last recorded green in `status.md` (Step 10, 3,866 passed) and by `just check` in CI on pull request #25, not re-run here |
| 4 | Browser suite (Playwright) | **pass, on the Mac dev stack, 2026-11-27** | `docs/changes/phase-2-e2e-run/bug.md`: whole `just e2e`, **77 passed, 8 skipped, 0 failed**. All 16 dashboard tests in nine `dashboards-*.spec.ts` files are in it; none is marked `skip`, `fail` or `fixme` (read here). The drag spec needed a 150 ms wait for the library's own click guard, not a product defect. Not re-run here. **CI does not run Playwright** (plan, *Validation plan*), so a later change can break these unseen |
| 5 | Screen-reader pass (T11.7) | **open: the founder** | The plan calls it **a release condition** (*What these tests cannot prove*). Not done (`status.md`, *Current task*). The traceability script half passed: 230 requirements, 0 missing |
| 6 | First demo gate (T11.4) | **open: the founder** | `tools/dashboard-demo/CHECKLIST.md`: "Not signed", every box empty. The plan: nobody says "has dashboards" until it is signed |
| 7 | Design QA, and the founder's judging steps for Steps 1 to 10 | **open: the founder** | Plan *Gates*: Design QA unticked. `status.md` *Next action* lists a judging step per step; none is recorded as done |
| 8 | Real phones and phone Safari (AC74 hostile names as visible escapes; T4.10, T6.8 touch) | **open: the founder** | Playwright runs Desktop Chrome only; `dashboards-phone.spec.ts` passed at 375 px in Chromium, which is not Safari or a finger |
| 9 | Hostile names in every legend (AC35) | **fail, small** | Open decision 41, confirmed in code: `ChartLegend.tsx` shows no line name when a chart has one line (`lines <= 1`), so a top-N chart over one service names it only under "View as table" |
| 10 | Security and tenancy | **pass for what the code does** | See *Security review* below |
| 11 | Migration 0056 | **pass; roll-forward only** | `downgrade()` raises "roll forward only ... Going back past 0056 is a restore" (founder, 2026-11-09). Rules test 72 passed. Going back is a Postgres restore, which is the platform's and has never been rehearsed on an install (`docs/operations/backup-and-restore.md` is a claim until executed) |
| 12 | Configuration and secrets | **pass in files; not seen on an install** | `CLICKHOUSE_API_READER_PASSWORD` has a dev default only in `compose.infra.yaml`; the deploy file demands it with `:?` (`compose.infra.deploy.yaml:35`). Startup refuses a tuned limit set that breaks the quota's arithmetic, and refuses any tunable on hosted (`dashboards/limits.py`, `from_settings`, `check`). Never started on a real install |
| 13 | Capacity and memory | **open: deploy** | The ClickHouse account's bounds (2.4 GiB, 12 at once) were "never approached ... they remain guesses, untested at their edge" (report, finding 3). API peak 328.5 MiB under the 832 MiB mark, on the Mac. `docs/self-hosting/sizing.md`'s dashboard numbers are the first measurement's (T11.3 unticked). No customer-scale data, by the plan's own table |
| 14 | Logs, metrics, alerts | **pass for logs; metrics not built; no alert by design** | One `dashboard batch read` log line per batch with counts and no names (`dashboardpanels/service.py:1026`). The design's histogram of scans per read and counter by panel state were **not built**; `monitoring.md` says "Dashboards add no metric of their own", so the design and the build differ. No new alert, as designed (F6's stale-minute alert covers the harm). Never seen on a real install |
| 15 | Rollback | **pass for switching reads off; open for data** | `DASHBOARD_SCANS_ENABLED=false` pauses every panel read while the rest of the API works (`monitoring.md`, `limits.py`). Data rollback is a restore (gate 11). Once a customer saves a dashboard, `schema_version` 1 is permanent (plan, *Rollout and rollback*) |
| 16 | Customer and operator documents | **pass, unreviewed; two stale lines** | `docs/product/setup/dashboards.md`, `monitoring.md` *Dashboards*, `sizing.md` *Dashboards*. `monitoring.md` still says the pool finding "is open" though ADR-102 fixed it; `docs/changes/request-holds-one-connection/status.md` and `docs/changes/dashboard-chart-scale/bug.md` still say "not committed" though both are. Competitor facts (T11.5) incomplete: nine vendors not re-read, so no comparison may be said in public |
| 17 | Deploy | **open: the founder** | Nothing is deployed. TLS, the public edge, backups and operational ownership are the platform's |

## Security review

Read, not assumed: `dashboards/router.py`, `service.py` (every read takes `org_id`), `limits.py`, migration 0056,
`deploy/clickhouse/users.d/api-reader.xml`, the compose files' reader password, the design's threat table, and the
names of the structural tests. Not re-read line by line: `dashboardpanels/`'s statements and `compile.py`.

**Held, with where:**

- *Tenancy.* Every dashboard query in `service.py` is bound by `org_id`; a foreign id is the same 404 (AC6, two-tenant
  live case in `test_dashboards_live.py`). Panel statements bind `project_id`, held by `test_tenant_chokepoint_structure.py`.
- *Authorization.* Reads take `CurrentIdentity`; writes `MayEditDashboards`; purge `MayPurgeDashboards`
  (`router.py`). API tokens reach no dashboard route (`test_dashboards_real_tokens_live.py`), and
  `test_authorization_coverage.py` covers the routes.
- *Injection.* A definition names only a fixed panel kind and checked settings; values reach SQL bound; imports go
  through the same validator at 64 KiB with unknown fields refused (`test_dashboard_definition.py`).
- *No AI, nothing leaves.* `test_dashboards_have_no_ai.py` and `test_dashboard_outbound_imports.py` (in the run).
- *Load as an attack.* Panel and scan caps, each scan charged to the reader, per-organization shares in the process caps.
- *The ClickHouse account.* `GRANT SELECT ON nexovue.*` only, password from the environment.

**Findings:**

| # | Severity | Finding | State |
|---|---|---|---|
| S1 | LOW | `nexovue_api_reader` accepts connections from any address (`<ip>::/0</ip>`). Safe only while ClickHouse's port is unreachable from outside the container network | Open. Not checked on a deployed host; the platform's network rules decide it |
| S2 | LOW | The account can `SELECT` every table in `nexovue`, every tenant's rows; tenancy rests on the API's bound parameters, not the database | By design (ADR-045); the structural tests are the guard |
| S3 | LOW | AC35 (gate 9): a one-line chart names no service in its legend | Open, decision 41 |

No tenancy hole, authorization gap or outbound path was found in what was read.

## Blockers

Each names what goes wrong without it.

1. **The measurement gate on the deployed server (gate 1).** The caps (30 panels, the scan cap, ClickHouse's 2.4 GiB and
   12 at once) are guesses. Dashboards share one ClickHouse server with ingest; mark 3 could not show whether 20 people
   reading slows the ingest worker, and if it does, findings and alerts arrive late for every tenant. The 30-person
   case (NFR12) and the scheduler's measures were never run. The founder's decision 4 already says no cap is published
   before this rerun.
2. **The screen-reader pass (gate 5).** The plan makes it a release condition; without it, nobody knows whether a
   person using a screen reader can read a dashboard or build one.
3. **The signed demo gate (gate 6) and design QA (gate 7).** The plan's own rule: nothing is said as "has dashboards"
   until the checklist is signed, and the built screens have not been judged against the approved design.
4. **The platform's blockers** (private gap list): nothing is deployed, and restore, which is F20's only data rollback,
   has never been performed on an install.

## Recommended

- Fix decision 41 (one-line legend) so AC35 holds; a one-condition change.
- Decide the open decisions that change behaviour: 9 (a tuned install's builder offers saves the API refuses), 17
  (viewers cannot reach version history, unlike the design), 28 (`too_many_series` not counted as not reading), 25
  (name the counter's bucket), 43 and 44 (wording), 2, 36 to 38 (drag and the reliability-target panel).
- Build the design's two metrics, or amend the design to say logs only.
- Correct the three stale lines in gate 16.
- Real-phone checks (gate 8) and the wall display's week.
- Run `just e2e` before release and after any later console change, since CI does not.
- Finish T11.5 before any competitor comparison is said in public.

## What could not be checked here

- Anything on a deployed server: capacity, memory, network exposure of ClickHouse, secrets on a real install, alerts,
  backups and restore.
- The Playwright suite was not re-run (its 2026-11-27 result is used); no real phone, Safari or screen reader.
- The shared gates outside `-k dashboard` were not re-run.
- The measurement was not re-run; its numbers are the report's.
