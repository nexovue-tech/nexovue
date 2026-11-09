# Plan: Finding explanation, with optional AI wording (F3)

> **Status:** Approved by the founder, 2026-11-02. Ready to implement once F6 merges. No file named below has been edited yet.
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks name the file that changes and the
> test that proves it, read from the code. **The founder approves this plan before any file is edited.**
>
> **Revised after eight persona reviews on 2026-11-02** (`status.md`, *Reviews*): rebuilt against all 105 acceptance criteria of that revision (106 now) and the design.
>
> **Nothing was run to write this.** Files marked *new* do not exist. Line numbers are for the working tree of `feature/anomaly-detection` on
> 2026-11-02, and will drift once F6 and F28 merge. Re-read them before each step.

## Preconditions

- **Specification and design approved** ([`spec.md`](spec.md), [`design.md`](design.md)), including the differences from the foundation that the design lists.
- **F6 merged.** Findings, the outbox's finding subject and migrations 0041 to 0050 exist only on this unmerged branch (foundation, section 9).
- **F28's readers exist, a hard precondition (A71):** `DeployHistory` and `coverage_of` in `nexovue_platform/releases.py`, and `deploys_in_window`, `releases_seen_in_window` and `release_comparison` in `nexovue_scheduler/releases/facts.py` (F28 design, *Signatures, for F3*), with F28's D6 replay fix and its `door` column on `spans` and `logs`. There is no fallback shipping deploy facts as an absence. Step 1 is not reported complete until AC7, AC8, AC12 and AC20 pass, and T2.7 waits for it. It must not copy the API's code to get them.
- **`phase-2-foundation.md` approved**, with the amendments in the design.

## Current system

Read from the code. Where it changes a task, the task says so.

| Area | Today | Where |
|---|---|---|
| Opening a finding | `_open` on the job's own connection, then `notify.finding_opened`, in one transaction | `detection/project_job.py:1113-1118`, project finding `:905-925` |
| After-commit enqueue | Only baselines and deliveries; `Outcome` holds counts, no ids | `project_job.py:435-447,1808-1810` |
| Adoption | `notify.finding_adopted` in `_adopt` | `project_job.py:1372` |
| Claim order | Capped queues first, the uncapped lane last with what is left | `jobs/worker.py:261-269` |
| Deploy webhook | Body-only signature, delivery id in a plain header; F28 rewrites it as a batch | `deploys/signatures.py:62-78`, `deploys/service.py:95-101` |
| Outbox | `_TAKEN`, `_subject`, `compose_batch` counting any kind but `resolved` as open | `alerts/outbox.py:272-290,698-705`, `alerts/messages.py:204-243` |
| Job results | Kinds `check_run`, `channel_test` | `models.py:2034,2190`, `jobs/results.py:12-21` |
| Advisory locks | Beat leadership; detection; any account can take them | `leader.py:26,55`, `detection/projects.py:86-89` |
| Change and close | `_notify_change`; `notify.finding_closed` | `project_job.py:1379,945,1139` |
| In-transaction enqueue | Exists: "on a connection already in a transaction" | `jobs/queue.py:110`; API form `findings/queries.py:208-214` |
| Job limits | Default 30 and 60 s; each request may name its own | `nexovue_platform/jobs.py:27-28` |
| Task modules | One list, all imported by the forkserver, which is set once at import | `jobs/registry.py:19-30`, `jobs/preload.py`, `jobs/worker.py:83` |
| Worker wiring | `Worker` and `Slot` already take `task_modules`; `__main__` passes none | `jobs/worker.py:102,116`, `__main__.py:_worker` |
| Queues and caps | Default queues; `detection` capped at 2 of 8; F28 adds `releases` | `__main__.py:78-85`, `jobs/worker.py:72` |
| Roles | No insight role; test holds the commands | `apps/api/src/nexovue_api/roles.py`, `tests/test_roles.py` |
| Settings | `kafka_brokers` required; production demands `check_secret_key` | `scheduler/settings.py:53,173` |
| Compose | `control-plane-env` holds the check key, object store and mail; no named network | `nexovue.yaml:84-115`, `scheduler-light` `:349` |
| Dead dev service | `insight`, running a package that does not exist, and `LLM_BASE_URL` | `docker-compose.yaml:589-627` |
| Postgres accounts | One. `POSTGRES_USER`, created as a superuser by the image; no `CREATE ROLE` anywhere | `nexovue.yaml:91`, `deploy/postgres/init/01-extensions.sql` |
| Secrets written | `ACCOUNT_PASSWORDS`, `write_secrets`, `add_missing_secrets` | `deploy/nexovue:115,117,140` |
| Restore | `--no-owner --no-privileges` | `deploy/backup/restore-postgres.sh:43` |
| Setup step | Migrates, then object store | `apps/api/src/nexovue_api/setup.py:67-74` |
| Sealing | `SealedBox`; `Keys` with two rings; `SEALED_COLUMNS` | `nexovue_platform/checks/sealing.py:95-137`, `keys.py:60,93` |
| HTTP | `perform`, `ConnectGuard`. Redirects default on, user agent says prober | `probes/http_check.py:37,65-66,99`, `probes/addresses.py:69-104,139` |
| Telemetry reads | `alerts/telemetry.py:58-96` (project-wide error groups); `detection/store.py` (project-bound pattern, `lowered_read_settings`) | |
| Structure test | Four raw-client modules; one project-bound package | `apps/api/tests/test_tenant_chokepoint_structure.py:110,116,540` |
| AI-free test | Fixed list; walks `detection` only | `apps/scheduler/tests/test_detection_has_no_ai.py:37` |
| Alert kinds | Vocabulary, frozen copy, composer's `else` | `alerts/model.py:47`, `0041:117`, `alerts/messages.py:275,310-312`, `payloads.py:163` |
| Finding API | No explanation; reads and actions | `findings/schemas.py:115-123`, `findings/router.py`, `findings/service.py:159-161` |
| Finding page | Fixed order; charts pass no markers; `TrendChart` takes them | `FindingScreen.tsx:107-116,535-544`, `TrendChart.tsx:97` |
| Window | Stored; one hook reads it, in 19 files | `shared/selection.ts:73,112,342` |
| Routes | Traces, errors and logs validate their own search, and each screen rewrites it whole. `/services/$name` has no `validateSearch`. `/infrastructure` validates only its filters | `app/router.tsx:275,294-348`, `TraceListScreen.tsx:75`, `ErrorsScreen.tsx:69`, `LogsScreen.tsx:94-99` |
| Settings rail | `SETTINGS_GROUPS` | `shared/settings/settings-nav.ts` |
| Deletion | Lists | `organizations/tables.py:32,58` |
| Expiry | `_EXPIRED` includes `retention.EXPIRED` | `jobs/expiry.py:32`, `detection/retention.py` |
| Fault tools | Fault bed has `check`, `run`, `score`, and `run` waits out a 7-day reference week first. It scores caught and time, not cause. Every fault is a flag, not a deploy. `detection-seed` writes only rollups | `tools/fault-bed/fault_bed.py:318-320,487-501`, `fault_model.py:69-95`, `tools/detection-seed/seed.py:1-8` |
| Layouts in tests | CI and `just stack` start only `docker-compose.yaml`; `dev-app` starts the merged layout under its own project | `justfile:39-48`, `.github/workflows/ci.yml:75-78` |
| Restore | Stop list omits the new role; `start` runs `setup` | `deploy/nexovue:601,619` |
| Fixtures | A recording HTTP site on a private address | `apps/scheduler/tests/recording_site.py` |

### Corrections found while writing this

Each is the code disagreeing with a document, and is carried in `design.md`, *Where this differs from the foundation*.

- **A check has no service** (`models.py:492-531`). Research's rule 3 cannot be built as written. Task T1.4 treats it as a project-wide fact.
- **Browser spans reach the exact counts** (`signals.go:176`, `stages.go:39,64`). Research left this open. It also means an F6 risk, recorded in `status.md`.
- **A restore drops grants** (`restore-postgres.sh:43`). `setup` re-applies them, since `start` runs it; T3.4 changes only the printed next step and the stop list.
- **`AI_PRIVATE_ENDPOINTS` cannot hold hostnames** (`addresses.py:139`). T4.2 uses addresses.
- **The window fix is smaller than the research feared on the reading side:** one hook. The writing side is not small: each screen must keep the window. T2.3 does both.
- **Struck: moving `DeployHistory`.** F28 owns it (foundation, section 8).

## Change required

See [`spec.md`](spec.md) and [`design.md`](design.md). Acceptance criteria are cited by number below.

## Impact

| Area | What changes |
|---|---|
| Files | New: `nexovue_platform/evidence/`, `nexovue_platform/ai/`, `nexovue_scheduler/evidence/`, `nexovue_scheduler/insight/`, `nexovue_api/ai/`, `apps/web/src/features/aisettings/`, `tools/ai-eval/`. Changed: detection's opening path, deploy webhook, worker's claim loop, outbox, findings API and page, channels, settings, Compose, install script, setup |
| Services | New role `scheduler-insight`, with its opt-out. `scheduler-light` takes queue `evidence` and a floor for probes. Dev `insight` removed |
| Schema / migrations | Postgres: `finding_explanations`, `finding_explanation_ratings`, `evidence_error_group_reads`, `explanation_stopped_rules`, `ai_*`, `explained`, `sends_explained`, `ai_check`, four job functions, account `nexovue_insight`. ClickHouse: `door` on `service_edges_1m` |
| UI | Finding page section, card line, incident page's "Look next", `/findings/sample`, `/settings/explanations`, `/settings/ai`, URL window on six screens. Charts' markers come from F28 |
| Tests | About 25 new test files, including the gate's canary and adversarial output suites |

## Reuse

- `queue.enqueue`, `JobRequest`, `Retry`, `results.store` and the send-test pattern for job results.
- `SealedBox`, `ConfiguredKeys`, `SEALED_COLUMNS`, `keys.py`'s re-seal.
- `http_check.perform`, `ConnectGuard`, `refusal_reason_for`.
- `detection/store.py`'s `for_project` pattern, `lowered_read_settings`, `binds_project`.
- `nexovue_platform.readings.MESSAGE_TEMPLATE_SQL`, `GROUP_ID_SQL`.
- `lifecycle.percent`, `times`, `local_clock` for code-written sentences.
- `_compose_finding`, `one_line`, channel escaping; `notify._INSERT_FOR_FINDING`, its unique key.
- `require_readable_project`, `CurrentReader`, `require(Action)`.
- `SkeletonRows`, `InlineFailure`, `Button`, `ConfirmPanel`, `SelectField`, `TrendChart.markers`, `deployMarkers`, `formatZonedTime`, `useCan`.
- `tools/fault-bed`, `tools/detection-seed`, `tools/otel-demo`; `recording_site.py`, `render-harness.test-util.ts`.

## Gaps

Correlation; evidence record; provider interface; validator; limits; AI settings; role, account and network; URL window; `explained`; injection fixtures; `tools/ai-eval`. The scheduler `org_id` structural test is F28's (A72).

## Steps and tasks

### Before Step 1, on approval of the specification

- [ ] **T0.1** `docs/product/feature-catalog.md:66,252-253`, `vision.md:135,140`, `phase-2-plan.md:14`: F3's name and wording, F2's, the example, and the selling reason once approved (A16). Proved by review.
- [ ] **T0.2** `tools/spec-check/ac_cited.py` *new*, `justfile`: fails if an acceptance criterion in `spec.md` is cited by no task here. `pyproject.toml`: `tools/spec-check` joins `testpaths` and `pythonpath`, or `just check` never collects the test. Proved by `tools/spec-check/test_ac_cited.py` *new*, seen in the `just check` run's output.

### Step 1: Facts on the finding page, with no AI at all

*What ships:* every new finding gets a frozen explanation, and its page shows where to look first. No new container, no provider, no setting. In production the likely-cause sentence stays held (FR50); the founder sees it through the non-production override.

*Judgeable:* run `just dev`, then `just explain-demo --rule 1` (each rule has its number). Open the finding within a minute. Ask: does the first screen tell you where to look? Is "not enough evidence" honest when it appears? Is the sentence one you would trust at 3 a.m.? Read the abstention rate on the seeded cases. **This is the point to redirect the feature, before any AI work.**

- [ ] **T1.0** `apps/workers/internal/pipeline/traceassembly/stages.go:64`, `serviceedges`, `deploy/clickhouse/migrations/<next>_service_edges_door.up.sql` *new*: the door on the edge rollup (A63), and `server_peer_attr` beside it (`serviceedges/serviceedges.go:76,113`, `traceassembly/flatten.go:289`, `serviceedges/accumulator.go:80`; A76). First confirm the cost by reading the workers. If it is more than two columns and a sort-key decision, the developer chooses the shape and records it in `design.md` and `status.md` (A84); only dropping or delaying the door returns to the founder (foundation item 7). Proved by the Go package tests, and `test_evidence_store_live.py` (AC14, a live test through the real receiver with a browser key).
- [ ] **T1.1** `apps/api/migrations/versions/<next>_finding_explanations.py` *new*, `data/postgres/models.py`, `organizations/tables.py`: `finding_explanations` (with `wording_generation`), `evidence_error_group_reads`. `jobs/expiry.py:32` and `detection/retention.py`: the hour's expiry. Proved by `test_deletion_live.py`, `test_migration_rules.py`.
- [ ] **T1.2** `packages/py-platform/src/nexovue_platform/evidence/__init__.py` *new*: queue, task names, `gather_evidence_request`, item types, `CAUSE_BAR_RECORDED`, `display_name`; `model_facts.py` with `ModelFacts`, `Alias`; `aliases.py`. `nexovue_platform/deployment.py`: `ai_available`. `apps/api/src/nexovue_api/settings.py`, `apps/scheduler/src/nexovue_scheduler/settings.py`: the non-production override, refused in production by a model validator, as `_check_key_is_set_in_production` does (`scheduler/settings.py:172`). Proved by `packages/py-platform/tests/test_model_facts.py` *new* (AC50: a free string is refused), `test_settings.py` and `test_scheduler_settings.py` (AC92's override case).
- [ ] **T1.3** `apps/scheduler/src/nexovue_scheduler/evidence/store.py` *new*: edge, traffic, error-group and host statements, project-bound, with `MAX_EDGES_READ` in the statement. `test_tenant_chokepoint_structure.py`: allow the module, and add `evidence` to F28's `PROJECT_BOUND_PACKAGE` tuple. Proved by that test and `apps/scheduler/tests/test_evidence_store_live.py` *new*: a read hitting its bound, AC64, AC100, and **the error-group read measured at a stated volume on the 16 GB install; a timeout there fails this task.**
- [ ] **T1.4** `evidence/reads.py` *new*: findings, incidents, checks (name and state only, never `reason`), other findings, F28's `deploys_in_window`, `releases_seen_in_window`, coverage. Calls F28's `release_before` and `releases_near` (built in F28's T3.8, D56) for the last release before the start and the 2-hour list (F28's FR32, FR52), and adds no reader of its own; marks called services from `service_edges_1m`. Self-hosted intake fact. Proved by `test_evidence_reads_live.py` *new* (AC10, AC12, AC91, AC93, tenant bind).
- [ ] **T1.5** `evidence/rules.py`, `evidence/sentences.py` *new*, pure. Proved by `test_evidence_rules.py` *new*, table-driven (AC6 to AC11, AC13, AC15, AC16's table, AC74's rule cases, AC98), with a test that no sentence holds a banned word.
- [ ] **T1.6** `evidence/gather.py` *new*: the task, fingerprint, versions, partial reads, alias building and `model_input`, bounds, type check, lock retry. `jobs/registry.py`: register it. Proved by `test_evidence_gather_live.py` *new* (AC1, AC3, AC4, AC94's row sizes, AC95, and a holder at the full hard limit must not lose the waiting job). `test_detection_memory_live.py`: a gather at its row bounds stays under `MAX_CHILD_PRIVATE_BYTES` (AC47's memory part, which must pass whether or not Step 3 is ever built). Also `test_model_input_built.py` *new*: a real finding's `model_input` is non-null and holds aliases only.
- [ ] **T1.7** `detection/project_job.py:905-925,945,1113,1139,1372,1379` and `deploys/service.py` (per item of F28's batch): enqueue on the open connection. `__main__.py:78-85`, `jobs/worker.py:72,261-269`, `roles.py`: queue, cap, `uncapped_floor`, lanes taking turns (if F19's T1.5 built the floor first, this task uses it; F19's SLO queues share it, F19 D66). Proved by `test_detection_project_job_live.py` (AC2, AC79), `test_compose_contract.py`, and `test_jobs_live.py` (AC47's saturation case, with F28's real `releases` queue).
- [ ] **T1.8** `test_detection_has_no_ai.py`: walk `evidence` too, and add `nexovue_platform.ai`, `nexovue_scheduler.insight` to the list ahead of their existing. Proved by itself (AC5).
- [ ] **T1.9** Confirm that `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` (F6's, extended by F28's T1.13) walks `evidence/`, adding named exemptions for cross-tenant sweeps and the intake read. Proved by that test.
- [ ] **T1.10** `findings/schemas.py`, `findings/queries.py`, `findings/service.py`, `findings/router.py`, `auth/dependencies.py` (`Reader.is_person`): `GET /findings/{id}/explanation`, `POST .../read-again`, `raw_kept_until` on `GET /findings/{id}`, the held cause; `likely_cause_sentence` on the feed. `test_authorization_coverage.py`'s `POLICY_OF`. Proved by `test_findings_api_live.py` (a token in another project gets 404; AC23, AC80, AC92), `test_findings.py`, `test_ratelimit_coverage.py`.
- [ ] **T1.11** `features/findings/api.ts`, `explanation.ts` *new*, `ExplanationSection.tsx` *new*, `FindingScreen.tsx:107-116`: header line, facts only, polling, "Look next" with links not yet window-aware (labelled so in the code), read-again, setup step, shared-cause count, held data on a failed refetch. Proved by `explanation.test.ts` *new*, `FindingScreen.test.ts` (AC17, AC18, AC66, AC67's page cases, AC68, AC69, AC70, AC84's page cases, AC86, AC87, AC88), via `render-harness.test-util.ts`.
- [ ] **T1.12** `tools/detection-seed/explain_demo.py` *new*, `justfile`: `just explain-demo --rule N`, dev only. It inserts a signed deploy through the real webhook and edges at now minus a few minutes, opens a finding directly, and enqueues the gather, so each rule can be seen within minutes. **It says plainly that detection did not judge this finding.** Proved by `tools/detection-seed/test_explain_demo.py` *new*.
- [ ] **T1.13** `apps/scheduler/tests/test_detection_unmoved_live.py` *new*: AC48's Step 1 arms, with fixed readings, and a failed `jobs` insert reporting loudly. Must pass before Step 1 is reported complete, and must stand if the feature stops at Step 2.
- [ ] **T1.14** `apps/api/src/nexovue_api/auth/permissions.py`, `auth/policy.py`: `REQUEST_EXPLANATION`. All five copies (F6's C14): `test_policy.py`, web `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, `team-and-api-access/spec.md`. Proved by `test_policy.py`, `test_authorization_coverage.py`, and AC23's role cases.
- [ ] **T1.15** `FindingScreen.tsx`, `finding-page.ts`: fold F28's *Release before this* section into F3's, keeping FR32, FR33, FR52's facts. Proved by F28's AC40, re-run through the new section, and `FindingScreen.test.ts` (AC91).

### Step 2: Links, the card, and the follow-up alert

*What ships:* the page's facts lead somewhere, and a finding's follow-up message arrives once.

*Judgeable:* follow each link from a demo finding. Copy one into a private window and see the same service and minutes. Check your other screens' window has not moved. Send a test finding to Slack or email and read the follow-up. Ask: does it add something, or is it noise? (A55 can be overturned here.)

- [ ] **T2.1** Re-run F28's test for markers on the finding's charts; F28's T3.6 already passes them. Proved by that test (AC20).
- [ ] **T2.2** `shared/url-window.ts` *new*: parse and build `from`, `to`, `project`, `finding`. Proved by `shared/url-window.test.ts` *new*.
- [ ] **T2.3** `shared/selection.ts:342`, `app/router.tsx:275,294-348` (`/traces`, `/logs`, `/errors`, `/services/$name`, `/infrastructure`, `/releases`), `TraceListScreen.tsx:75`, `ErrorsScreen.tsx:69`, `LogsScreen.tsx:94-99`, the infrastructure screen, `app/layout/WindowControl.tsx`: prefer the URL window; never write the stored one; the window line. Proved by a component test through `render-harness.test-util.ts:203-219`'s `RouterProvider`, and a Playwright flow in `tools/e2e` (AC21). `selection.test.ts` has no router test, so it cannot prove this.
- [ ] **T2.4** `features/findings/evidence-links.ts` *new*: "Look next" from `FindingDetail`, facts' links by kind; the aged-out line. `ErrorsScreen.tsx:71`, and `/errors`' `validateSearch` in `app/router.tsx:321`, which drops unknown keys: a `group` search value. Proved by `evidence-links.test.ts` *new* (AC22, AC66's window part, AC105), and `apps/api/tests/test_evidence_link_traces_live.py` *new*, which runs each callee shape's filters against `GET /traces` (AC105's live cases).
- [ ] **T2.5** `features/findings/FindingsScreen.tsx:332`: the card line, its short form, and remove the comment. Proved by `FindingsView.test.ts` (AC67's card cases, AC75's card case).
- [ ] **T2.6** `nexovue_platform/alerts/model.py:47`, `apps/api/migrations/versions/<next>_explained.py` *new*: the kind, `sends_explained`. `features/alerts/ChannelsScreen.tsx`: the setting. Proved by `test_alert_vocabulary.py` (AC29), `ChannelsScreen.test.ts`, and AC84's channel case.
- [ ] **T2.7** `evidence/explained.py` *new*, `evidence/gather.py`: the check ahead of the fingerprint stop, on any read after the settled time; told, people-read channels; no row at any sequence; per-channel lock. Proved by `test_evidence_explained_live.py` *new* (AC25, including the known-at-opening case and both races; AC26, AC28).
- [ ] **T2.8** `alerts/messages.py:204-243,275-313`: a branch before the `else`, and `compose_batch`. `alerts/outbox.py:272-290,698-705`: `_TAKEN`, `_subject`, the row dropped with a reason. `alerts/payloads.py`: `slack()` unfurl off, and a guard test. Proved by `test_alert_messages.py`, `test_alert_payloads.py`, `test_outbox_golden_live.py` *new* (AC27, AC78).
- [ ] **T2.9** `docs/product/setup/alerts/webhooks.md`, `docs/features/alerts/spec.md`, and setup pages: `explained`, frequent deployers, pages with no finding. Proved by review.
- [ ] **T2.12** `findings/service.py`, `findings/router.py`, migration: `finding_explanation_ratings`, `explanation_stopped_rules`, `POST .../feedback`, rule counts, stopping a rule. `evidence/reads.py`, `evidence/gather.py`: read the stopped rules and pass them to `rules.decide`, since this table does not exist when T1.5 and T1.6 run. `features/explanationsettings/` *new*, `app/router.tsx`, `settings-nav.ts`, `ExplanationSection.tsx`: `/settings/explanations`, the rating control. Proved by `test_findings_actions_live.py`, `ExplanationSettingsScreen.test.ts` *new* (AC75, AC83's carry-over, AC99), and `test_evidence_gather_live.py` (AC99: a stopped rule names no cause and its facts stay, in that organization only).
- [ ] **T2.13** `features/findings/sample-explanation.ts` *new*, `app/router.tsx`, `route-manifest.test.ts`, `FindingsScreen.tsx:205-240`: `/findings/sample`, the link, and real dates. Proved by `FindingScreen.test.ts`, `FindingsView.test.ts` (AC84's sample case, AC104).
- [ ] **T2.14** `features/alerts/IncidentScreen.tsx`: "Look next" for an incident with no finding, reusing `evidence-links.ts`. Proved by `IncidentScreen.test.ts` (AC101).

### Step 2b: The stop-or-go, a founder gate

**Founder, 2026-11-09 (foundation item 30): no longer a build gate.** Steps 3 to 7 are built now; T2.10, T2.11 and the written go or stop are run by the founder after Phase 2, and a stop turns the customer-facing AI wording off before release. *Originally:* Steps 3 to 6 run only on the founder's written go. This is the cheap question before the costly work (A49). No money is spent: model calls use free tiers or a local model, on synthetic data only (foundation item 4, changed 2026-11-02).

- [ ] **T2.10** Five to eight target users on the facts-only page, watched: what do they do first, and do they trust it? Written up in `status.md`. Founder's time, no code.
- [ ] **T2.11** `tools/ai-eval/spike/` *new*, thrown away afterwards and not shipped: a script sending `model_input` from seeded findings to one provider, about 20 calls. Model and template sentences shown blind to 5 people. **The rule was approved on 2026-11-02 (A61; foundation item 13), before any call.** Seeded data only, on a free tier (for example Gemini's) under the founder's own account. A free tier may keep and read what is sent, so nothing else goes. Written up in `status.md`.
- [ ] **Founder's decision, in writing.** Go: Step 3. Stop: no customer-facing AI wording in F3. Steps 1, 2 and 2c stand, and the fence and provider work wait for F4's own decision. A term glossary on the facts-only page is worth adding either way, for the new developer the AI was meant to serve.

### Step 2c: Scoring the rules, with no model

*What ships:* the first cause accuracy, known before any release can state a cause, and the change that releases causes once the bar is met. **It runs on either decision at Step 2b,** since FR50 holds causes until it finishes. It needs no AI and takes weeks (about 3 weeks of warm-up, 1 week of live demo, then about 11.5 days of run; `tools/fault-bed/README.md`). **No test server is rented (foundation item 9, 2026-11-02), so the runs wait for Oracle after Phase 2 is deployed.** T2.15 and T2.16 are built straight after Step 2; T2.17's runs and report, and T2.18's check, wait. Until then FR50 holds every cause and F3 ships facts only. It does not hold up T2.11.

- [ ] **T2.15** `tools/ai-eval/` *new*, `justfile`, `pyproject.toml` (`tools/ai-eval` joins `testpaths` and `pythonpath`, or `just check` never collects its tests): runs on `tools/fault-bed`, stated-cause scoring, written answer keys, faults grouped by whether the rules could see the cause, ties and frequency bands, stored facts for offline replay, tune and report rounds named first. A deploy scheduler posting signed records through the real webhook for at least 7 days, with varied rates and decoys. Proved by `tools/ai-eval/test_score.py` *new* (AC61, AC62, AC97's bed metric).
- [ ] **T2.16** `tools/ai-eval`: the healthy run's sampled windows (AC16), and `--run forged` through the real intake, reporting findings opened and false causes (AC76). A live test opening 100 findings at once (AC97). Proved by `tools/ai-eval/test_healthy.py`, `test_forged.py` *new*.
- [ ] **T2.17** Tune the named constants in `evidence/rules.py` by replaying stored facts on the tune rounds, then write a rules-only report under `docs/research/` from the held-out rounds, labelled demo, always with the count per fault, with the healthy windows re-checked. A re-run costs about 4.4 days, so the replay stands in for it. Proved by `test_evidence_rules.py` (AC62).
- [ ] **T2.18** **Founder's bar.** The bar in counts was approved on 2026-11-02, before any tune round (A83; `spec.md`, *Founder decisions needed*, 2; foundation item 11), and is recorded in `status.md`. Forged-cause counts from T2.16 count as wrong causes (A82). The founder then reads T2.17's report against it. A short list of released rules sits beside `CAUSE_BAR_RECORDED` (A85), in `evidence/__init__.py` (T1.2), proved by the same test with one released and one unreleased rule. With it recorded and met, a reviewed change sets `CAUSE_BAR_RECORDED` true, and only then may the claim rows in `spec.md` be used. Proved by AC92's test, then inverted. On a go, wording in production then follows this same constant (A60), and Step 6's report decides whether to offer it.

### Step 3: The fence: an idle AI container that can reach almost nothing

*What ships:* `scheduler-insight` runs idle in every layout, with its own account and network. No provider code yet. **This is the riskiest design claim, tried before anything is built on it.** Steps 1 and 2 do not rest on it, so a failure here leaves them standing.

*Judgeable:* a short terminal transcript the founder can read, from `just insight-fence`: from inside the container, Kafka and ClickHouse time out, an outside address answers, and the database refuses `SELECT * FROM findings`, `SELECT * FROM jobs`, `LOCK TABLE jobs` and the beat lock. If the spike fails, this returns to the founder before more work.

- [ ] **T3.1** **Spike.** `apps/scheduler/tests/test_insight_account_live.py` *new*: create the account and the four functions in a test. Run the parity table against `_CLAIM`, `_RETRY`, `_FINISH` (`jobs/queue.py:42-77`) and `_STORE_RESULT` (`jobs/results.py:12-21`), with `insight` jobs and others. **Check whether a column-level `UPDATE` permits `LOCK TABLE`,** and that a revoke from `PUBLIC` holds. Proved by itself (AC44, and AC43's lock and advisory cases). A failure stops the step.
- [ ] **T3.2** `apps/api/migrations/versions/<next>_ai_tables.py` *new*, `models.py`, `organizations/tables.py`: `ai_*` tables with their `CHECK`s, the four functions, `JOB_RESULT_KINDS` and the `job_results` check gaining `ai_check`. Proved by `test_deletion_live.py`, `test_migration_rules.py`, and AC96's `CHECK` cases.
- [ ] **T3.3** `apps/api/src/nexovue_api/setup.py:67-74`, `INSIGHT_GRANTS`: role, limit, timeouts, password, grants, revokes, the warning; `nexovue setup grants`. `justfile:335` and `deploy/compose/nexovue.yaml:121-125`: the password, with a dev value. Proved by `apps/api/tests/test_insight_grants_live.py` *new*: privileges from the catalog must equal the allowed list, column by column (AC43, AC45), and AC71's setup cases.
- [ ] **T3.4** `deploy/backup/restore-postgres.sh`: only the printed next step, no grants (A34). `deploy/nexovue:601`: `scheduler-insight` joins the stop list. `docs/operations/backup-and-restore.md`. Proved by a live test through the real `nexovue restore` on an empty Postgres volume, on T3.11's installed kit (AC46).
- [ ] **T3.5** `scheduler/settings.py`: `SCHEDULER_ROLE`; `kafka_brokers`, `check_secret_key` follow it; `ai_secret_key`, `ai_private_endpoints`. Proved by `test_scheduler_settings.py`: boots on exactly the permitted variables, and a typo stops it (AC41).
- [ ] **T3.6** `jobs/registry.py`, `jobs/preload_insight.py` *new*, `jobs/worker.py:83`, `__main__.py`: `--tasks`; `jobs/queue.py`, `jobs/results.py`: statements by role, the functions for insight. Proved by `test_jobs_live.py` (no `clickhouse_connect`, `confluent_kafka`, `boto3` in the child), `test_detection_memory_live.py` (the insight child's own memory, NFR3; AC47's gather case is T1.6's).
- [ ] **T3.7** `apps/api/src/nexovue_api/roles.py`: `scheduler-insight`. Proved by `test_roles.py`.
- [ ] **T3.8** `deploy/compose/nexovue.yaml`: the anchor, service, network, `SCHEDULER_INSIGHT_REPLICAS`. `compose.infra.yaml`, `compose.infra.deploy.yaml`, `compose.infra.dev.yaml`: `postgres` and Prometheus on both networks, where they are defined. `nexovue.quickstart.yaml`, `nexovue.kafka3.yaml` as needed. `docker-compose.yaml:589-627`: replace the dead service. Proved by `test_compose_contract.py`, with an exact allow-list for all layouts (AC41) and `test_an_overlay_adds_no_list_to_a_service_it_shares`.
- [ ] **T3.9** `deploy/nexovue:115,117,140`: `POSTGRES_INSIGHT_PASSWORD`, `AI_SECRET_KEY`, upgrades add them. `kit.sh` if it names settings. Proved by `test_install_script.py`.
- [ ] **T3.10** `apps/scheduler/tests/test_insight_network_live.py` *new*: from inside the container (AC42). `docs/self-hosting/sizing.md`: row, memory read from a real run at 4 and 6 children. `child_memory_limit` passed for this role.
- [ ] **T3.11** `justfile`, `.github/workflows/ci.yml`: `just insight-fence` and its own CI job. It builds both images from this checkout, tagged as `deploy/compose/nexovue.yaml:44,48` expects, and a kit with `deploy/release/kit.sh`; installs that kit into a temporary `NEXOVUE_HOME` with `nexovue install --settings`, under its own `NEXOVUE_PROJECT`, with `NEXOVUE_PULL_POLICY=never` and `NEXOVUE_SKIP_PORT_CHECK=1`; takes a snapshot with `nexovue backup`; empties the Postgres volume; and runs `nexovue restore <snapshot> --yes`. For AC46's older snapshot it first installs images built at the merge base with `main`. **This replaces the `dev-app` pattern (`justfile:48`), which installs nothing, so `restore` would refuse to run (A78).** No such harness exists today, so its first task is to get this working by hand. Check that `nexovue install`'s `compose pull` (`deploy/nexovue:372`) passes with locally tagged images, and if not, use the kit's offline path. The job needs Docker with 12 GiB of memory (`deploy/nexovue:166`). Its tests carry a marker, stay out of `just check` and `pytest -n 6`, and the job fails if any skips (AC42, AC46, AC71, AC103).

### Step 4: Connect a provider, test it, set a limit, see the bytes, consent

*What ships:* `/settings/ai` end to end, except a run. The founder can connect a real account or a local model, and read exactly what would be sent.

*Judgeable:* as an owner, connect an account, or Ollama on a private address. Try `localhost` and read the error. Read the preview for a real finding: is there anything in it you would not want sent? Sign in as an admin, and see consent refused. Turn on the switch. **Nothing is sent to a provider except the test.** Each test is a real, metered call, under the approved cap.

- [ ] **T4.1** `nexovue_platform/ai/` *new*: `AIRequest`, `AIResponse`, three wire formats, build and parse, `explain_prompt.py`, `PROMPT_VERSION`. Proved by `packages/py-platform/tests/test_ai_wire_formats.py` *new*, with recorded response shapes per outcome, including a 200 refusal, each fixture marked as AC77 asks.
- [ ] **T4.2** `probes/addresses.py`, `probes/http_check.py`: `PrivateEndpoints`, `user_agent`, control characters refused in a header. Proved by `test_connect_guard.py` (AC37: loopback and link-local stay refused even when listed; hostname to private refused unless listed), `test_http_check.py` (AC38).
- [ ] **T4.3** `nexovue_scheduler/insight/transport.py`, `check.py` *new*; `registry.py`'s insight set. `test_detection_has_no_ai.py`: confirm they are listed. Proved by `test_insight_check_live.py` *new*, against `recording_site.py` (AC35, AC53's null-limit case).
- [ ] **T4.4** `apps/api/src/nexovue_api/auth/permissions.py`, `auth/policy.py`: `MANAGE_AI`, `CONSENT_AI_DISCLOSURE`, in all five copies. Proved by `test_policy.py`, `test_authorization_coverage.py` (AC32).
- [ ] **T4.5** `apps/api/src/nexovue_api/ai/` *new*: router, service, queries, schemas; `main.py:143`. Connection, check, preview, consent, switch, opt-outs, usage, runs. Audit names in `audit/service.py`, with hosts. Owner email through the API's mail path. Mute and project-delete routes set pending rows (T5.11). Proved by `apps/api/tests/test_ai_settings_live.py` *new* (AC30 to AC34, AC36's preview against `WireFormat.build` over stored `model_input`, AC39's preview case, AC40, AC72, AC102).
- [ ] **T4.6** `keys.py:60,93`, `settings.py` (API): the `ai` ring, sealed column. `docs/operations/key-rotation.md`. Proved by `test_keys_live.py` (AC34), and `test_sealed_columns_listed.py` *new*: every sealed column in the catalog is listed.
- [ ] **T4.7** `features/aisettings/` *new*, `app/router.tsx`, `shared/settings/settings-nav.ts`, `route-manifest.test.ts`: the screen, for each role, in each state. Proved by `AISettingsScreen.test.ts` *new*, `route-manifest.test.ts`, and a Playwright check at 390 and 1920 pixels (AC85, AC90, AC103's screen).
- [ ] **T4.8** `docs/product/setup/ai.md` *new*: what is sent, Ollama's binding, what the switch does not cover, the host firewall rules, running with no AI container, the provider table. Review.
- [ ] **T4.9** **Spike.** `apps/scheduler/tests/test_bedrock_signing_spike.py` *new*: AWS signing through `perform` with a scoped key. Pass adds signing, and Bedrock's key field; fail leaves the format hidden (A59). Written up in `status.md`.
- [ ] **T4.10** `tools/ai-eval/real_call.py` *new*, `features/aisettings/`: one real call per wire format, its dated record, and the screen's format list following that record. Proved by `AISettingsScreen.test.ts` (AC77).

### Step 5: Wording, checked by code, with a limit that stops new runs

*What ships:* the whole chain, in a non-production build while FR50 holds. A finding's facts get plainer words, labelled, on a real provider, or facts alone when anything goes wrong.

*Judgeable:* with your key, inject a fault. Read the words beside the facts. Do they add anything to the template sentence? Then pull the key, stop the container, set a tiny limit, and watch the finding stay whole each time. The canary test's output is the thing to read for safety.

- [ ] **T5.1** `nexovue_platform/ai/validate.py`, `untrusted.py` *new*: Proved by `test_ai_validate.py` *new*, one case per FR37 rule, plus the adversarial table (AC51, AC52).
- [ ] **T5.2** `evidence/gather.py`: decide `off`, `held`, `abstained` or `pending`, the generation; enqueue. Must not import `nexovue_platform.ai`. Proved by `test_evidence_gather_live.py` (AC39, AC57, AC74's run cases), `test_detection_has_no_ai.py`.
- [ ] **T5.3** `nexovue_platform/ai/limits.py`, `insight/store.py` *new*: reservations and run rows. Proved by `test_insight_limits_live.py` *new*, running more parallel runs than the limit admits (AC53, AC55).
- [ ] **T5.4** `insight/word.py` *new*: the task, row lock, input limit, hash, generation. Proved by `test_insight_word_live.py` *new*, against a fake provider (AC36's bytes, AC54, AC56, AC58, AC60, AC82, AC94's spending, AC96).
- [ ] **T5.5** `insight/sweep.py` *new*, `beat.py:412`: died and expired runs, matching generation. `jobs/expiry.py:32`: `ai_runs`, `ai_usage_monthly` at 13 months. Proved by `test_beat_schedule.py`, `test_insight_word_live.py` (AC81, with the sweep's statements run as `nexovue_insight`, so a missing column grant fails here and not in production).
- [ ] **T5.6** **The gate's canary:** `apps/scheduler/tests/test_insight_canary.py` *new*. Markers in every attacker-reachable field, through gather and prompt, to a fake provider's recorded bytes, with its controls (AC49). Also searches rows and logs for wording (AC59). Runs in `just check`.
- [ ] **T5.7** `findings/service.py`: restore aliases to segments, re-validate on read, map closed lists, words withheld from tokens; `POST .../refresh`. `ai/service.py`: `GET /ai/runs/{id}/sent` with its hash statement. Proved by `test_findings_actions_live.py` (AC23's token marker, AC36's mismatch, AC58, AC65, AC73, AC106).
- [ ] **T5.8** `ExplanationSection.tsx`: words as segments, labels, picks, refresh, all states. Proved by `FindingScreen.test.ts` (AC17, AC18, AC19, AC75's wording case, AC83, AC89).
- [ ] **T5.9** `apps/scheduler/tests/test_ai_off_changes_nothing_live.py` *new*, in `just insight-fence`: detection and alerts with the real role stopped, compared with AI on (AC24, AC48's Step 5 arms). Separately, `tools/e2e/tests/non-ai-screens.spec.ts` *new* signs up its own organization and opens every path in `apps/web/src/app/routes/route-manifest.ts` and each settings screen except `/settings/ai`, failing on an `InlineFailure` or an empty page. `just e2e-insight-stopped` *new*, in `justfile`: on the dev stack, stops `scheduler-insight`, runs that spec with `alerts`, `errors`, `first-span`, `logs`, `members`, `profiles`, `rum`, `service-map`, `services`, `sign-in`, `synthetics` and `top-bar`, then starts the role again (AC48, A77). **No CI workflow runs Playwright today, so this is run by hand and its result written in `status.md`.**
- [ ] **T5.10** `metrics.py`, `deploy/monitoring/rules`, `prometheus.yml`, `deploy/monitoring/tests/nexovue_test.yml`: metrics, the scrape job, alerts, including findings over 15 minutes old with no explanation. `docs/operations/ai.md` *new*. Proved by `test_scheduler_metrics_live.py`, and the monitoring rules' tests.
- [ ] **T5.11** The mute route, project deletion and opt-out in the API: set pending rows in the same transaction. `word`'s re-check. Proved by `test_findings_actions_live.py` (AC39, AC81).

### Step 6: The bed with models, and the first honest scores

*What ships:* one report under `docs/research/`, with injection, cost and wording results on models, joining Step 2c's rules-only report. No paid calls: the cloud option is a free tier, and the local model runs on the founder's Mac (foundation item 4, changed 2026-11-02). Replays and short runs go ahead on the Mac; runs that need the demo up for days wait for Oracle after deployment (item 9). The report names each model, says paid providers are not yet tested, and says free tiers may keep what is sent, so only synthetic data went.

*Judgeable:* read the report. Decide whether the AI layer earns its place over the template sentence, and whether to publish. The accuracy bar was decided in Step 2c (T2.18).

- [ ] **T6.1** `tools/ai-eval/`: model runs on top of T2.15, recording `model_input` once and replaying it across providers offline. Proved by `tools/ai-eval/test_score.py`.
- [ ] **T6.2** `tools/ai-eval/fixtures/` *new*: LogJack's public payloads, adapted to nexovue's fields, AIOpsDoom-style false causes, attempts to leave through output, ordinary fixtures as controls. Proved by `test_fixtures.py` *new* (shapes, 42 present).
- [ ] **T6.3** Runs: faults, replay, injection, cost, on both options, with repeated trials. The local model runs on the founder's Mac through Ollama, replaying T6.1's recorded `model_input` with the dev stack stopped, models up to about 14B (A81); the report names the model and the machine. All 42 payloads through the real pipeline (AC76). Report under `docs/research/`, labelled demo, repeating *What passing these tests will not prove* (AC61, AC63, and AC62's checks on any claim's link to data). Replaces every guess in `spec.md`, `design.md`, `sizing.md`.
- [ ] **T6.4** Moved to T2.17, so it runs on a stop too (A79). The number is kept so older references stay clear.
- [ ] **T6.5** The supported list, in `features/aisettings/` and `docs/product/setup/ai.md`. Only what passed AC63.
- [ ] **T6.6** Moved to T2.18 (A79).

### Step 7: Readiness and record

*Judgeable:* the readiness check's written result.

- [ ] **T7.1** Accepted ADRs into `docs/decisions/`, and their index; the differences into `phase-2-foundation.md`; the other features' changes in `design.md`, *Specification impact*.
- [ ] **T7.2** `/review-diff`, security review, `/readiness-check`, which must read the recorded bar.

## New risks

| Risk | Why it matters | What limits it |
|---|---|---|
| The rule set is often silent or wrong on real traffic | Only demo data exists, and a wrong "likely" costs more than silence | Step 1 is judged first. Healthy run must return none. Thumbs-down rating |
| AI wording may add too little to justify offering it | Would undercut the case for it, and the budget | The stop-or-go after Step 2, by a rule written first (A61) |
| The job functions drift from the queue's own statements, or an account limit does not hold | First use of each here | T3.1 first; parity test; a failure returns to the founder |
| The `ai` network breaks an overlay or the quickstart | First named network in the stack | T3.8 tests all five layouts |
| Forged spans give believable false causes, or open a finding | Cannot be stopped outright | The door on rollups, "from telemetry only", basis and base rate shown, and AC76 reports it |
| A browser key opens a finding, before F6's change lands | F6 risk, surfaced here, and F6 ships first | Approved 2026-11-02 to be taken before Stage 1 reaches a customer (foundation item 7); F28 owns it (item 22) |
| Evidence jobs slow in a wide outage | Probes must keep their slots, so facts wait | A floor (A56), proved by AC47 from T1.7; lowered read bounds; AC97 measures the queue |
| An owner's account is taken over | Can send facts to an attacker's provider | Audit rows name the old and new host, and owners get an email. Aliases limit what goes. Not prevented |
| The rules cannot be scored honestly on staged deploys | The harness plants the deploy, so a right score is circular | AC62 reports firing and decoys only, and says so in the report |
| The merged-layout tests rot outside `just check` | They run in their own job | The job fails if any test skips |

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | The test server's price. **Answered 2026-11-02:** none rented; long runs on Oracle after deployment | Step 2c's runs, Step 6's long runs | Foundation item 9 |
| 2 | The founder's written go or stop. **Rule approved 2026-11-02;** the go or stop itself is read against it after T2.11 | Step 3 onward | Step 2b, then `status.md` (`spec.md`, *Founder decisions needed*, 1) |
| 3 | The accuracy bar, in counts. **Approved 2026-11-02;** met only after Step 2c's runs on Oracle | Any production release stating a likely cause | T2.18, then `status.md` (`spec.md`, *Founder decisions needed*, 2) |

Settled 2026-11-02 and removed from this list: model calls, now free tiers or a local model only (foundation item 4, changed) and the browser-door change (foundation item 7). The local-model machine is decided (A81).

## Gates

- [x] Specification approval: 2026-11-02
- [x] Design approval: 2026-11-02
- [x] **Plan approval:** 2026-11-02
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA: the built screens against the approved design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- **Per the founder's rule, build the whole change first, then verify once at the end of each step**, reading test output and counts. Focused tests are used while a task is unfinished only when needed to know it works.
- `just check` on every step: the canary, type and adversarial-output suites and the structural tests run here.
- Live suites (`*_live.py`) need the stack, and must fail loudly when it is absent. Tests that need the merged install layout run under `just insight-fence`, in their own CI job, and fail if skipped.
- `just e2e` for T2.3, and `just e2e-insight-stopped` for T5.9, on the dev stack. `tools/ai-eval` for Steps 2c and 6. Local models are run on the founder's Mac only (A81).
- Screens are checked in a browser against the design, at desktop and phone widths, before reporting them.

## Rollout and rollback

**The real risk is the first step shipping and nobody judging it,** or the AI being built before the facts have been judged. Each step ends in the founder judging it.

Nexovue is pre-deployment, so nothing needs migrating. On any install later: AI stays off until an owner chooses it, so rollout changes nothing for a customer but the facts section, and a likely cause stays held until the bar is met (FR50). Rollback of the AI layer is the switch, and it leaves the facts. A bad rule can be stopped per organization, and its numbers are a constant change and a release. `scheduler-insight` can be stopped at any time with no effect on detection or alerts (AC48).

## Outcome

To be filled in when the work completes.
