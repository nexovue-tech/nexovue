# Plan: Deployment and release tracking (F28)

> **Status:** Approved by the founder, 2026-11-02, with the specification and design. Ready to implement once F6 merges.
> Revised 2026-11-02 after eight persona reviews (`status.md`, *Reviews*).
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under each step
> name the file that changes and the test that proves it, read from the code on 2026-11-02. New files are marked *new*.

## Preconditions

- **F6 is merged.** Findings, `service_stats_1m`, `detection_sweep`, the settled minute and migrations 0041 to 0050 exist
  only on `feature/anomaly-detection` (`anomaly-detection/status.md`). No F28 branch starts before that.
- **The specification and design are approved.** D1 no longer differs from the foundation (`phase-2-foundation.md:547`).
- ~~**The one round of customer conversations has been held before Step 1b,** as the founder approved on 2026-11-02
  (foundation, *Founder decisions*, 8).~~ **Withdrawn by the founder, 2026-11-08 (foundation item 27):** the buyer round
  is a validation milestone, not a gate; Step 1b is built without it, and its answers are read afterwards.
- **Before Step 4:** the one browser-door change (foundation section 9, shared with F3 and F19's P2) has filtered browser-door SERVER and CONSUMER spans out of `service_stats_1m` and `service_versions_1m` at one point, with a test that sends such a span and finds it in neither (AC17, D55). No labelled-exposure path: the founder
  approved the change first (foundation, *Founder decisions*, 7; D58).
- **Other features' documents agree** as of the reconciliation on 2026-11-02 (`status.md`).
- **Migration numbers are re-read at the start.** At this reading the next are ClickHouse `000038` and Postgres `0051`.

## Current system

Read from the code. Where a line number is given it is for this working tree.

### Corrections to earlier documents

Found while writing this plan.

| # | A document says | The code says | Effect |
|---|---|---|---|
| C1 | Research: "Not checked" whether a browser key can make a rollup-counted span | Nothing in the receiver reads span kind (search of `apps/receiver/internal` for `SpanKind` and `GetKind` finds none). The worker takes kind from the payload (`flatten.go:185`), and uses the door for billing only (`usage/usage.go:281,291`). **Expected:** it can | T1.3 excludes `DOOR_RUM` from versions. F6's exposure goes to `status.md` |
| C2 | Research: a null link "is shown as an empty cell" | It shows the `UNKNOWN` mark (`ErrorsScreen.tsx:364`, `format/number.ts:14`) | Short words in the cell (D23) |
| C3 | Research D7: sealed and re-readable | Notification channels are shown once (`setup/alerts/webhooks.md`) | Shown once; removes the "viewed" audit |
| C4 | Foundation: put `deploys_in_window` beside `DeployHistory` in `nexovue_platform` | `test_cross_service_contracts.py:265` refuses `sqlalchemy` and `clickhouse` imports there | Pure code and fragments are shared; each process runs its own (T5.1) |
| C5 | Foundation: comparison by time, as rollups hold no version | True today; not a real limit | D1 |
| C6 | `000035` comment: "nothing else reads this table" | Stops being true in T4.1 | `database.md` corrected. The migration stays as applied |
| C7 | Foundation: F3 builds the scheduler Postgres test | It exists: `apps/api/tests/test_scheduler_postgres_tenancy_structure.py`, from F6 | F28 extends it first (T1.13) |
| C8 | F28's first draft: `TenantScopedRepository` in the scheduler | It does not exist there. Scheduler access is a fixed module list (`test_tenant_chokepoint_structure.py:110-116`) | T1.11 edits that test |
| C9 | F28's first draft: `tools/detection-seed` could show a release before a finding | `seed.py` writes only `service_stats_1m` (`:78`) | T3.0 |
| C10 | F28's first draft: MCP "keeps truncated" | It does not pass it today (`assistant/tools.py:280-281`, `bounds.py:23,39`) | T5.4 adds it |

### What exists, and is used as it stands

| What | Where |
|---|---|
| Webhook: raw body, signature, parse, write | `deploys/router.py:43-62,127-140`, `deploys/service.py:73-137`. The cap changes (T2.2) |
| Signature, constant-time, SHA-256 only | `deploys/signatures.py:56-92` |
| Secrets model and rotation order | `models.py:829-862`, `deploys/service.py:269-306` |
| `DeployHistory`, with "before, not because" | `deploys/service.py:147-236`, used at `errorgroups/router.py:67,116` |
| Rollup pattern | `derive/services.go`, `derive/servicewriter.go`, `000035` |
| `Disown`, drains, deduplication token | `derive/counters.go:293-400`, `servicewriter.go:84-94` |
| Project enumeration, fair order, session lock | `scheduler/detection/projects.py:105,121,154` |
| Queue caps and defaults | `jobs/worker.py:72`, `__main__.py:80-84` |
| Beat entries | `scheduler/beat.py:385-415`, tested by `test_beat_schedule.py` |
| Expiry | `jobs/expiry.py:27-44`, `detection/retention.py:41` |
| Sealing and rotation | `packages/py-platform/src/nexovue_platform/checks/sealing.py:95-137`, `keys.py:93` |
| Markers drawn | `TrendChart.tsx:251-272` |
| Marker filter | `shared/deploy-markers.ts:37-58` |
| Secret-section pattern | `features/projects/CheckSecretsSection.tsx`, `DetectionSection.tsx` |
| Read surface, by dependency | `readsurface/spec.py:9-13` |
| Service list | `ServiceLastSeen`, `models.py:1831` |

## Change required

[`spec.md`](spec.md) and [`design.md`](design.md). Not restated here.

## Impact

| Area | What changes |
|---|---|
| Files | Worker `derive`, `traceassembly`, `consume`; API `deploys`, new `releases`, `keys.py`, `findings`, `errorgroups`, `assistant/tools.py`, `ratelimit`; scheduler new `releases`, `beat.py`, `jobs`, `alerts`; `nexovue_platform/releases.py` (new); web new `features/releases`, settings, charts; setup docs |
| Services | Worker, API, `scheduler-light`, beat. No new container |
| Schema / migrations | ClickHouse `000038`, `000039`. Postgres `0051`, `0052`, `0053` |
| UI | `/releases`, `/releases/$releaseId`, *Deploys* settings, markers, finding section, errors list |
| Tests | Listed per task. Unit, live against dev stores, and structure tests |

## Reuse

- `service_stats_1m`'s schema, writer and token. Do not design a second rollup shape.
- `bucket_p95` and `LOG_EDGES`: moved, not copied.
- `SealedBox`, `SEALED_COLUMNS`, and their rotation.
- `active_projects`, `fair_order`, `session_lock`.
- `TrendChart` with its `markers` and series. No new chart, and one new mode.
- `_OCCURRENCES` in `errorgroups/queries.py`, for errors by version.
- `require_readable_project`, `require_project`, `TenantScopedRepository`, `resolve_window`.
- `EmptyState`, `SkeletonRows`, `InlineFailure`, `Button`, `TextField`, `Tabs`, `CopyButton`, `UnsavedChanges`, `format/time`.
- `DEPLOY_IS_NOT_A_CAUSE` wording, extended, not rewritten.
- `tools/detection-measure/scale.py` for disk at volume.

## Gaps

- A rollup by version, its writer and caps.
- `releases`, `release_periods`, `release_sync_state`, `deploy_intake_state`, and a sync.
- The comparison.
- A release screen and API.
- A demo tool sending versions through the real receiver: `tools/release-demo` (*new*). `tools/otel-demo` runs one
  version per service, and `tools/detection-seed` writes history directly, so neither exercises the path.

## Steps and tasks

Each step ends in something the founder can judge. Tests run once at the end of each step, not after every edit.

### Step 1a — The rollup, with its cost measured

**This step goes first because it holds the largest unproven assumption,** and it stops at the gate. Measuring needs only the rollup and the
demo tool. Nothing built on it has been written yet.

*Judgeable:* Read the committed cost report: its pass or fail against each threshold, the absolute sizes, how often a small team would reach `side_by_side`, and the list of what
disappears if the gate fails. Run a query and see the printed counts equal to `release-demo`'s.

- [x] **T1.1** — `deploy/clickhouse/migrations/000038_service_versions_1m.up.sql`, `.down.sql` (*new*) · table per design
  · with `apps/scheduler/src/nexovue_scheduler/erasure/tables.py` `DERIVED_TABLES` and `erasure/statements.py`, in this
  same task, since the inventory fails otherwise · proved by `apps/scheduler/tests/test_erasure_inventory.py`,
  `test_erasure_live.py` (AC20)
- [x] **T1.2** — `apps/workers/internal/pipeline/derive/versions.go` (*new*), `counters.go`, `services.go` · `Span.Version`,
  `versions` map, all three caps, fold to `__other__`, `Derived.Versions`, `Disown`, `PendingVersions`, the Go side of the name check · proved by
  `derive/versions_test.go` (*new*: sum equals `service_stats_1m`'s for any mix; two `Counters` instances that fold differently; a junk burst before the real version; 25 versions in one
  drain; long and control-character versions; AC15, AC18). The real version's count landing in `__other__` is asserted here, and "Versions were combined" in T4.1's AC67, `disown_test.go`, `counters_order_test.go`
- [x] **T1.3** — `traceassembly/stages.go`, `flatten.go` · `countable` carries the version, empty for `DOOR_RUM` until the F6 change removes that branch · proved
  by `flatten_test.go` (AC17's before-the-change case)
- [x] **T1.4** — `derive/versionwriter.go` (*new*), `traceassembly/pipeline.go`, `consume/retry.go` · writer, token, insert last in `insertDerived`, with its own 30-second budget, step-over and 5-minute skip, no hold, never an error, log fields at `:289,:383` · proved by
  `derive/versionwriter_test.go` (*new*), `traceassembly/refusal_test.go` (AC19; *new case with a fake writer that always returns a non-permanent error*: AC54), `restart_test.go`, `counterhandover_test.go` (AC16)
- [x] **T1.5** — *Built 2026-11-07 without `--deploys N`, which the task gives from T2.2 on, when the new deploy body exists.* `tools/release-demo/send.py`, `test_send.py` (*new*), `justfile`, `pyproject.toml` (`tools/release-demo` added to both `testpaths` and `pythonpath`, or `just test-py` never collects this folder) · sends OTLP through the receiver. Named modes: `rolling` (share, failure rate), `swap`, `no-version`, `rollback`, `exceptions`, `cold-start`, `churn`, `many-services`, and `--deploys N` for a webhook sender (from T2.2 on) · proved by `test_send.py`, and by the printed counts matching the store
- [x] **T1.6** — `tools/release-demo/release_measure.py` (*new*; `tools/detection-measure/measure.py` already holds that module name on `pythonpath`), `tools/detection-measure/scale.py` · the fixed run: baseline at the commit before T1.2, same traffic, 30 minutes, 3 repeats, a rollout an hour on every service, memory sampled, spread and peak · proved by `tools/release-demo/test_release_demo_measure.py` (*new*)
- [x] **T1.7** — *Built 2026-11-07 as [`docs/research/release-rollup-cost-2026-11-07.md`](../../research/release-rollup-cost-2026-11-07.md): passed on demo data; the alert-delay row cannot be measured before Step 1b.* `docs/research/release-rollup-cost-<date>.md` (*new*) · **the gate:** rows, disk, worker memory, on the
  demo and with churn, against the design's thresholds, absolute figures per million requests, how often `side_by_side` is reached at small-team traffic, and an arithmetic estimate for a per-endpoint table · labelled demo throughout · proved by the report (AC21). **Stop here if it fails:** tighten the caps once, then bring the numbers to the founder, with the design's list of what disappears

### Step 1b — A release compared by version, on real traffic

Only after the gate. The buyer round no longer gates it (founder, 2026-11-08, foundation item 27).

*Judgeable:* Run `just dev` and `tools/release-demo rolling`. Open `/releases`. Without any webhook, `release-demo` appears with two versions. Open the new one and read
"Side by side over N minutes: failure share X% on v2 against Y% on v1", with the counts matching what the tool printed it sent. Run `swap` and read a before-and-after, with a block for since the rollout finished.

- [x] **T1.8** — *Built 2026-11-08 as `0054_releases.py`, the next free number (F22 and F19 took 0051 to 0053). `erasure/`
  needs no change: the scheduler's erasure covers ClickHouse only, and Postgres deletion is `organizations/tables.py`.*
  `apps/api/migrations/versions/0051_releases.py` (*new*), `data/postgres/models.py`, `organizations/tables.py`,
  `erasure/` · `releases`, `release_periods`, `release_sync_state` · proved by `test_deletion_live.py`,
  `test_postgres_tenancy_structure.py`
- [x] **T1.9** — *Built 2026-11-08. `LOG_EDGES` had already moved to `nexovue_platform/slo.py` with F19, so only
  `bucket_p95` moves here, reading those edges; `method.py` re-exports it.* `packages/py-platform/src/nexovue_platform/releases.py` (*new*), `scheduler/detection/method.py` · moves
  `LOG_EDGES`, `bucket_p95`; adds `choose_method` as the design's ordered list, `compare_failure_share`, `compare_p95`, the four words, warm-up, and name check · proved by
  `packages/py-platform` tests (*new*: `test_platform_releases.py`, every row, both sides of z = 3, 0 of 20 against 5 of 20; AC32, AC33), `test_detection_method.py` unchanged and passing,
  `test_cross_service_contracts.py`
- [x] **T1.10** — *Built 2026-11-08. The founder's 2026-11-08 answer added the gate's alert evaluation start delay to this
  task: measured, and passed (cost report, *Addendum, Step 1b*). The sync reads the hour before its new minutes again,
  not five minutes, since the 60-minute rule needs it (status, *Step 1b*).* `apps/scheduler/src/nexovue_scheduler/releases/__init__.py`, `sync.py`, `store.py` (*new*),
  `jobs/registry.py`, `jobs/worker.py` (`QUEUE_CAPS`), `__main__.py` (`worker_queues`), `deploy/compose/nexovue.yaml`, `beat.py` · `sweep:releases`, `observe_project`, own queue, expiry, lock, skip, per-minute walk, periods, first-version flag, per-row skip, `synced_through` rule, per-run bounds, cap and eviction · proved by
  `apps/scheduler/tests/test_release_sync.py`, `test_release_sync_live.py` (*new*: AC24, AC25, AC55, AC56, AC58, AC60, AC72, a late partition, a return during an outage, sparse two-version traffic, a quiet service's swap ending its period, one stray request opening no return, opposite-order writes, rollback's previous version), `test_beat_schedule.py`
- [x] **T1.11** — *Built 2026-11-08 without adding `releases.store` to the raw-client list: like F19's `slos/store.py`, it reads
  through `alerts.telemetry`'s reader and holds no driver, so the list stays as it was.* `apps/api/tests/test_tenant_chokepoint_structure.py` · add `releases.store` to `SCHEDULER_MODULES_ALLOWED_THE_RAW_CLIENT`, and make `PROJECT_BOUND_PACKAGE` a tuple including `releases` · proved by that test, and `apps/scheduler/tests/test_release_store.py` (*new*) with `tenant_statements`
- [x] **T1.12** — `apps/scheduler/tests/test_detection_isolation.py` (*new*) · walks imports transitively, at module level and inside functions, from every `detection` module and from `alerts/` `sweeps`, `thresholds`, `new_errors`, `silence`, `check_state`, `incidents`, `rules`, `outbox` and `messages`. None may reach `nexovue_scheduler.releases` or read its tables. `alerts/delivery.py` and `alerts/tasks.py` are outside the roots, by name (AC42's structure half, D51) · proved by that test
- [x] **T1.13** — `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` (exists, from F6; a second file of this name would stop collection, as these folders have no `__init__.py`) · `releases/` statements join what it reads, each cross-tenant one named with its reason · proved by that test, and `test_release_sync_live.py` with two organizations sharing a service and version (AC77)
- [x] **T1.14** — *Built 2026-11-08. AC28's F6 half is recorded: F6 opened no finding on the 0.8% mixed figure (its minimum
  change at normal sensitivity is 2 points), and a 30% control opened a critical one.* `apps/api/src/nexovue_api/releases/router.py`, `service.py`, `queries.py`, `schemas.py` (*new*), `main.py`,
  `ratelimit/policy.py` · `GET /releases` with its version filter and overlap window, `GET /releases/{id}`, `GET /releases/{id}/comparison` for `side_by_side` and
  `version_before_after`, with two blocks and `require_readable_project`, reading to the settled minute. Fixed paths are declared before `/{id}` · proved by
  `apps/api/tests/test_releases.py`, `test_releases_live.py` (*new*: AC26, AC28, AC31, AC35, AC62, AC63; AC28's mixed figure is computed directly from `service_stats_1m`, and it records F6's behaviour by running the project job), `test_ratelimit_coverage.py`,
  `test_read_surface_structure.py`
- [x] **T1.15** — `apps/web/src/features/releases/api.ts`, `ReleasesScreen.tsx`, `ReleaseScreen.tsx`, `comparison.ts` (*new*), `apps/web/src/shared/format/version.ts` (*new*),
  `app/router.tsx`, `app/routes/route-manifest.ts` · list and page: sentence, table, states, phone layout, zone, formatter · proved by
  `features/releases/comparison.test.ts` (AC66), `ReleasesScreen.test.ts` (*new*: AC27), `route-manifest.test.ts`
- [x] **T1.16** — *Done 2026-11-08: about 60 seconds from the settled minute to the release row (cost report, *Addendum,
  Step 1b*).* timed check with `release-demo`, in the cost report's addendum · first settled span to visible release (AC22). One demo measurement, not a unit test

### Step 2 — A person can set it up

The target is five minutes, unmeasured until here.

*Judgeable:* Follow only the console and the new setup page, as someone new to it. Mint a secret, copy the GitHub Actions
or `curl` recipe, run it with `dry_run`, watch "last check received" change. Remove `dry_run`, and see the deploy on
`/releases` beside what telemetry saw. Try it signed wrong and read the words. Send a bad field, and see it as the last refusal. Withdraw the secret and remove what it sent. Read the time recorded for a new person.

- [ ] **T2.1** — `apps/api/migrations/versions/0052_deploy_envelope_and_sealed_secrets.py` (*new*), `models.py`, `organizations/tables.py` · new
  `deploy_events` columns with both `ON DELETE SET NULL` foreign keys, `ix_deploy_events_created_at`, `delivery_id NOT NULL` at 200, `sealed_secret NOT NULL`, `secret_hint`, `last_used_at`, `deploy_intake_state`, per design ·
  proved by `test_deploys_live.py`
- [ ] **T2.2** — `deploys/schemas.py`, `deploys/service.py`, `deploys/router.py` (`MAX_BODY_BYTES` to 512 KiB and its message), `deploys/queries.py`, `nexovue_platform` name check and masking port, `ratelimit/limiter.py` (`cost` on `consume`, exact path, with a conditional update so a refused batch charges nothing), `ratelimit/policy.py` (exact quota, window 3600), `main.py` (limiter on `app.state`) · batch body, signed id, time bounds, field checks, sorted writes, `ON CONFLICT DO NOTHING`, `dry_run`, limit after signature, counting new items only, release and period upsert by the start rule, per-item answers, signed refusals recorded, `deploy_intake_state` · proved
  by `test_deploys.py`, `test_deploys_live.py` (AC6 to AC14, AC51, AC52, AC53, AC57, AC59, including replaying one body 2,000 times, and a 100-item body at the limit), `test_ratelimit.py`. The missing-header test is removed on purpose, and replaced by one for a missing signed `delivery_id`. A measurement of HMAC cost at 512 KiB is reported
- [ ] **T2.3** — `deploys/signatures.py`, `deploys/service.py`, `deploys/schemas.py` (`WebhookSecret.secret` removed, times and last refusal added), `deploys/queries.py`, `keys.py`, `packages/py-platform/src/nexovue_platform/checks/sealing.py` · reads `uuidv7()` first and sets the id, seal under a new purpose,
  cap of 5, shown once, `last_used_at` · proved by `test_keys_live.py`, a new assertion that fails when the column
  leaves `SEALED_COLUMNS`, `test_deploys.py` (AC3, AC4, AC5, AC50)
- [ ] **T2.4** — `audit/service.py`, `test_audit.py` · removes `deploy.webhook_secret_viewed`; `deploy.batch_recorded` with secret id, count and first deploy id; `deploy.removed` · proved by `test_audit.py`
- [ ] **T2.5** — `ratelimit/policy.py` · corrects the comment · proved by `test_ratelimit.py`
- [ ] **T2.6** — `apps/web/src/features/projects/DeploysSection.tsx`, `deploys-api.ts` (*new*), `ProjectSettings.tsx` · the
  section: rotation steps, polling, `role=status`, last refusal, current versions, `UnsavedChanges`, sentence for others · proved by `DeploysSection.test.ts` (*new*: hidden for others, shown once), `test_deploys.py` (AC2's route refusals)
- [ ] **T2.7** — `docs/product/setup/deploys.md` (*new*), `setup/index.md`, `setup/api.md`, `setup/troubleshooting.md`, `setup/platforms/kubernetes.md`, `setup/platforms/aws-ec2-and-bare-metal.md`, every language page, onboarding snippets, the `apps/web/src/features/onboarding` strings · recipes by FR14's list, `service.version` per platform, reachability, one project per environment, stored in clear text, pre-send sampling, the 7-day limit, a post-deploy pipeline recipe · proved by `apps/api/tests/test_deploy_recipes.py` (*new*: AC48, AC49: extracts each signing step and runs it against the API test client; secret by environment, none in arguments; exit 0; new id on a re-run), and a run against the dev stack. A hosted runner is tried once from a reachable address when one exists
- [ ] **T2.8** — `tools/otel-demo/doors.py` · new body · proved by running `announce_deploys` on the dev stack
- [ ] **T2.9** — `deploys/router.py`, `deploys/service.py`, `releases/service.py` · `DELETE /deploys/{id}`, secret removal since a time, recalculating releases and periods, `POLICY_OF` · proved by `test_deploys_live.py` (AC61), `test_authorization_coverage.py`
- [ ] **T2.10** — timed observation · at least one real new person, up to three, on GitHub Actions, reported as observations in `status.md` (AC1). If it fails five minutes, ship a small first-party script instead of adding more text. If nobody can be found, record "not timed" and withdraw the five-minute claim, which AC1 allows, and Step 3 may proceed

### Step 3 — Markers that can be read, on every chart, including the finding

*Judgeable:* Open a seeded finding with a release before it. See its markers and the sentence. Hover a
marker, click to pin, follow the link. Tab to the list. On a service with 60 deploys, move the window to last week and see last week's markers. Set a service
with no versions and no deploys and read "We receive no deploy information". Open a phone-width window. Trigger a seeded finding and read the line in its notice.

- [ ] **T3.0** — `tools/detection-seed/seed.py`, `test_seed.py` · also writes matching `service_versions_1m` rows, `releases`, `release_periods` and `deploy_events`, one release before its seeded finding, a project with 60 deploys, and a release older than the project's limit, which has Postgres rows only since the rollup's TTL removes older rows · proved by `test_seed.py`, asserting the seeded rows sum to `service_stats_1m`
- [ ] **T3.1** — `releases/router.py`, `service.py`, `queries.py` · `markers_in_window`, Postgres only, behind `GET /releases/markers`, for F20 to call too; `GET /releases/coverage` with `at`; `coverage_of` in `nexovue_platform/releases.py`; the `none` list from `service_last_seen` · proved by `test_releases_live.py` (AC38, AC73)
- [ ] **T3.2** — `apps/web/src/shared/deploy-markers.ts` · `service`, `source`, `state`, `release_id`, cluster function ·
  proved by `deploy-markers.test.ts`
- [ ] **T3.3** — `shared/components/TrendChart.tsx`, `shared/components/ChartReleases.tsx`, `ReleaseHoverBox.tsx` (*new*) · one neutral dash, HTML strip by `valToPos`, hover as a React portal, click to pin, drag still selects, one list per chart group, always-visible line, failure line · proved by `ChartReleases.test.ts` (*new*; AC39), `ReleaseHoverBox.test.ts` (*new*: hostile strings; AC43), and Design QA, as canvas drawing has no unit test
  here
- [ ] **T3.4** — `features/overview/api.ts`, `OverviewScreen.tsx`, `features/servicedetail/api.ts`, `ServiceDetailScreen.tsx` ·
  windowed read, `truncated` kept, cap sentence · proved by `OverviewView.test.ts`, `service-detail.test.ts`
- [ ] **T3.5** — `findings/schemas.py`, `findings/service.py`, `findings/queries.py`, `releases/queries.py` · `FindingDetail` gains
  `release_context`: preceding period, coverage as of the start, the last report's age, and releases elsewhere, with those it calls from `service_edges_1m` · proved by `test_findings_api_live.py`, `test_findings.py` (AC40, AC71, AC73), `test_tenant_chokepoint_structure.py`
- [ ] **T3.6** — `features/findings/FindingScreen.tsx`, `api.ts`, `finding-page.ts` · section by the wording table, `markers`, link carrying the finding · proved by
  `FindingScreen.test.ts`, `finding-page.test.ts` (AC40, AC64, one instant reading the same on both pages)
- [ ] **T3.7** — `features/errors/error-groups.ts`, `ErrorsScreen.tsx`, `ErrorGroupDetail.tsx`, `errorgroups/router.py`,
  `errorgroups/schemas.py` · words in the cell, and the panel's sentence at every coverage value · proved by `error-groups.test.ts`, `ErrorsView.test.ts`, `test_errorgroups.py` (AC73)
- [ ] **T3.8** — `apps/scheduler/src/nexovue_scheduler/releases/facts.py` (*new*, the coverage reads, `release_before` and `releases_near`, D56, for F3's T1.4 to call), `alerts/messages.py`, `alerts/delivery.py` · FR44's line, in `Subject.detail`, read after the claim commits on its own connection, one read per single-notice `opened` finding batch, 500 ms including the wait for a connection, left out on failure, in a grouped message and for a project finding (D51) · proved by `test_alert_messages.py`, `test_detection_isolation.py`, a live test (AC70), including a read that raises and a read that hangs, with the batch still claimed and sent; and `apps/scheduler/tests/test_release_facts_live.py` (*new*): `release_before` at any age, `releases_near` same service first and bounded to its lookback, both bound to one org and project (D56)
- [ ] **T3.9** — `apps/scheduler/tests/test_detection_release_neutrality_live.py` (*new*) · runs the project job over the same seeded minutes with and without release and deploy rows, and compares findings, `detection_readings` and outbox rows, which must be equal, since FR44's line is never stored (AC42)

### Step 4 — The whole release page

Needs the F6 change listed in *Preconditions*.

*Judgeable:* With `tools/release-demo`, run `rolling`, `swap`, `no-version` plus a webhook, `rollback` and `cold-start`. Each must read differently and honestly. See new errors in the new version with the caution. See a seeded finding
listed as "Started after this release". Read an old release whose numbers have expired. Open a release from a finding 5 hours later and read the window it chose.

- [ ] **T4.1** — `releases/queries.py`, `service.py` · `time_before_after`, last week, per-minute completeness, `versions_combined`, the window chosen from a finding, every FR21 reason, environment check, FR46's read on `GET /releases` · proved by `test_releases.py`, `test_releases_live.py` (AC29 including last week, AC30, AC32, AC36, AC64, AC65, AC67, AC78)
- [ ] **T4.2** — `deploy/clickhouse/migrations/000039_door_on_spans_and_logs.up.sql`, `.down.sql` (*new*), `traceassembly/flatten.go`, the log pipeline's row builder · `door` on raw rows · proved by `flatten_test.go`, and a log pipeline test
- [ ] **T4.3** — `releases/router.py`, `service.py`, `queries.py`, `errorgroups/queries.py` · `/errors`, `/rollout`, with two per-table version predicates in the pattern of `_ONE_SERVICE_PLACEHOLDER`, a door predicate, and timeout words. `/releases/{id}/sender-details`, with its role check · proved by `test_errorgroups.py`, `test_releases_live.py` (AC68, AC69, AC76), `test_tenant_chokepoint_structure.py`. The errors read's cost is measured at 24 hours on seeded data and reported, with the 1-hour default taken if it is poor. The 2-second target is measured once (AC67)
- [ ] **T4.4** — `releases/service.py`, `queries.py` · later findings, activity, states, events on `GET /releases/{id}` · proved by `test_releases_live.py`
  (AC23, AC41)
- [ ] **T4.5** — `features/releases/ReleaseScreen.tsx`, `ReleasesScreen.tsx`, `comparison.ts`, `api.ts`, `shared/components/TrendChart.tsx` (categorical mode) · every section and
  state, per-section failure, phone blocks, refetch, table of shares, FR46's words, FR49's and FR50's sentences · proved by `ReleaseScreen.test.ts` (*new*; AC43, AC44, AC65, AC68, AC75, AC79, AC80, AC84), `ReleasesScreen.test.ts` (AC78)
- [ ] **T4.6** — `apps/scheduler/src/nexovue_scheduler/releases/retention.py` (*new*), `jobs/expiry.py` · the project's derived retention, the shortest-plan guard, the deleted-project clause · proved by
  `test_release_sync_live.py` (AC74)
- [ ] **T4.7** — `apps/api/tests/test_release_words.py` (*new*) · searches F28's source, web strings and setup page for the
  forbidden words (AC37), and proves only the two named statements touch `spans` (AC34). A search cannot prove there is no implied cause, so Review checks that too

### Step 5 — What other features need, and telling people

*Judgeable:* Read one test output: the F3 reader returns facts with ids and coverage, and no free text, in two separate reads. Read the MCP
`list_deploys` answer and see no sender text. Turn on the summary for a project, and read one notice after a demo rollout. See "failure share higher" in the list.

- [ ] **T5.1** — `nexovue_platform/releases.py`, `deploys/service.py`, `errorgroups/router.py`, `errorgroups/service.py`, `apps/api/tests/test_deploys.py` · moves
  `DeployHistory`; plain `DeployFact`; shared fragments · proved by the same behaviours at `test_deploys.py:550-633`, re-pointed at the new module and type, and `test_errorgroups.py`
- [ ] **T5.2** — `apps/scheduler/src/nexovue_scheduler/releases/facts.py`, `store.py` · `deploys_in_window`, `releases_seen_in_window`, `release_comparison`, coverage, with ClickHouse read through the store · proved by `apps/scheduler/tests/test_release_facts.py`, `test_release_facts_live.py` (*new*; AC45's exact allow list, AC77)
- [ ] **T5.3** — `apps/scheduler/tests/test_detection_has_no_ai.py` · F28's modules join what is checked (AC47)
- [ ] **T5.4** — `apps/api/src/nexovue_api/assistant/tools.py:271-281`, `assistant/bounds.py`, `deploys/schemas.py` · an explicit field list; adds `partial`, since `truncated` is ignored today; `GET /deploys` loses sender text · proved by
  `test_mcp_tools.py` (AC46), `test_releases_live.py` (AC76)
- [ ] **T5.5** — `apps/scheduler/tests/fixtures/hostile_release_names.py` (*new*) · version, `metadata`, `delivery_id` and `source`, for F3's injection suite, tested here by
  showing the reader leaves free text out · proved by `test_release_facts.py`
- [ ] **T5.6** — `apps/scheduler/src/nexovue_scheduler/releases/summary.py` (*new*), `apps/api/migrations/versions/0053_release_summary.py` (*new*), `apps/api/src/nexovue_api/data/postgres/models.py`, `packages/py-platform/src/nexovue_platform/alerts/model.py`, `alerts/outbox.py` (`_IN_ORDER`, `_TAKEN`, `_subject`, `SubjectKind`), `alerts/messages.py`, `alerts/payloads.py`, `apps/api/src/nexovue_api/projects/router.py`, `projects/service.py`, `projects/schemas.py`, `audit/service.py`, `ReleasesScreen.tsx`, `DeploysSection.tsx` · stored reading and list flag; `notifications.release_period_id`, its check and unique key, the partial index's new condition, `sequence` always 0; `projects.release_summary_channel_id`; `PUT /projects/{project_id}/release-summary` under `CONFIGURE_PROJECTS`, in `POLICY_OF`; the summary (design section 6, D52, D53) · proved by `test_alert_vocabulary.py`, `apps/api/tests/test_authorization_coverage.py`, `test_alert_messages.py`, `test_release_summary_live.py` (*new*: AC81, AC82, including two releases in a row on one channel), `test_projects.py` (AC83), `ReleasesScreen.test.ts` (AC81), `DeploysSection.test.ts`
- [ ] **T5.7** — `docs/architecture/database.md`, `docs/features/integrations-deploy-events/spec.md`,
  `docs/features/console-redesign/design.md`'s IA3 note, `docs/product/feature-catalog.md:91,104,371-372`, `docs/self-hosting/sizing.md` if the gate requires it · proved by review

### Step 6 — Launch: say only what is true

*Judgeable:* Read the allowed and banned sentences, the refreshed competitor table with dates, and watch a demo recording labelled as demo data.

- [ ] **T6.1** — `docs/features/deployment-release-tracking/research.md` · refresh every competitor row within two weeks of announcing. Settle in a SigNoz install or repository where markers appear and which edition holds them, and install Coroot Community Edition once to settle its edition · proved by dated rows
- [ ] **T6.2** — `docs/product/phase-2-foundation.md` section 12 · exact allowed and banned sentences, for example "Releases appear from `service.version`; a webhook adds failed states and same-version redeploys". Keep "seen is not a deploy" · proved by review, against `status.md`'s *What may be said*
- [ ] **T6.3** — changelog entry, the setup page, a demo recording from `tools/release-demo` labelled as demo data · proved by review

## New risks

- **The rollup's memory on a real cardinality.** The caps' values are guesses, and many hostile tenants add up.
- **Folding hides a real version,** first come at arrival and per drain at writing. The screen says so whenever `__other__` has requests.
- **Rollout minutes may be too thin for the floors on small services,** so many releases may read "before and after" or "not clearly different".
  Expect this on low traffic. It is a labelled weaker method, not a wrong one, and small services are the first buyer. T1.7 measures it.
- **The period rule's thresholds** (90%, 20%, 10 minutes) can misread blue-green flips and long canaries. Guesses until seen.
- **`__other__` as a sentinel** could collide with a real version of that name in telemetry. It is folded and never made a release, and the webhook refuses it.
- **Changing `bucket_p95`'s home touches F6.** The test suite must prove it unchanged.
- **A new column on two raw tables** costs disk on every row (`LowCardinality`, *expected small*), unmeasured.
- **The Python masking port can drift from the Go rules.** Shared fixtures catch what they cover.
- **FR44 and the summary add work in the delivery path.** Both are bounded, and tests cover failure.
- **The scheduler Postgres test will hold F28's new statements,** and each cross-tenant one must be named there.

## Questions

None open. The customer conversations were settled by the foundation on 2026-11-02 (*Founder decisions*, 8): one round,
before Step 1b. The conditional gate after Step 1a was approved in advance on 2026-11-02 (foundation item 24), and F28 owns
the browser-door change (item 22). D56 to D58 were decided on 2026-11-02. **Where Step 1a runs:** on the Mac's Docker, as written;
no test server is rented (item 9). The whole-server fit on 16 GB waits for Oracle after deployment.

## Gates

- [x] Specification approval: 2026-11-02
- [x] Design approval: 2026-11-02
- [x] **Plan approval:** 2026-11-02
- [ ] F6 merged
- [x] Step 1a cost gate: passed on demo data, 2026-11-07 ([report](../../research/release-rollup-cost-2026-11-07.md)). Its alert evaluation start delay row was added to T1.10 by the founder's answer of 2026-11-08, measured there, and passed
- [x] ~~The one round of customer conversations held, before Step 1b (foundation item 8)~~ No longer a gate (foundation item 27, 2026-11-08)
- [ ] F6 browser-door change, before Step 4. F28 owns it (foundation item 22, approved 2026-11-02); its tasks are added when this plan is next revised
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, including 375px on `/releases`, the release page, the finding section and Deploys
- [ ] Production-readiness assessment
- [ ] Claims check — every competitor row refreshed within two weeks of announcing, and the foundation's section 12 updated
- [ ] Documentation and status update

## Validation plan

Build each step whole, and then run its checks once.

- **Go:** `just test-go`, `just lint-go`. Read package counts.
- **Python:** `just test-py`, `just lint-py`, including live suites against the dev stores.
- **Web:** `just test-web`, `just lint-web`.
- **Contract:** `just check` before each step's handoff.
- **In the browser, on the dev stack:** each step's *Judgeable* line, as the founder would.
- **Not provable here:**
  - Real customer version habits, real rollouts, and memory on a real production mix. The gate's numbers are from a demo, and must say so.
  - Recipes on real hosted runners, until tried once from a reachable address. The contract test covers signing, and not GitHub's environment.
  - Usability and five minutes, until a real new person tries it. One observation is one observation.
  - That no text implies cause. A search catches words, and a person must read for the rest.
  - That `side_by_side` is fair on a customer's routing. We state the limit, and cannot measure it.
  - Sampling before sending. Nothing in the data shows it.

## Rollout and rollback

Pre-deployment, there is no live data to protect. The real risks are Step 1a shipping and nobody judging it, and the gate
being treated as a formality.

- **Rollback of F28 before release:** drop the migrations and revert. Nothing else depends on it until F3.
- **Once F3 builds on it,** the API and facts contract are what must be kept.
- **Order inside an upgrade:** the ClickHouse migration runs before the worker that writes to it, as with `000035`.

## Outcome

Not started.
