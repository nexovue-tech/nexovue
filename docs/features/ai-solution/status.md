# Status: ai-solution (F5)

## Lifecycle status

**Step 9 built 2026-11-22, awaiting commit; T6.5 still not given.** Built in `wt/f5s9`, on 4ad5d47 (`feature/phase-2`): the sweep's figures and its stalled alert (migration 0085), the admin summary with notices (AC39), NFR2's click timing, the customer's page and the written demo (both drafts for your wording approval), the rivals re-read dated 2026-11-22, the architecture documents and ADR-103 to ADR-106; and from the hardening pass, `fix-section.spec.ts` run and the Docker restore release check run and passing. **Write access stays off in code** (`write_gate.WRITE_ACCESS_GO` is None). T9.4's timing, T9.6's six unreachable rivals and OneUptime install, and T9.7's real run are the founder's (*Step 9*, below).

**Earlier:** Step 8 built 2026-11-22, committed: Run CI and F5's own pull request poller, with write access off in code.

**Earlier:** Step 7 built 2026-11-22, committed (1dec10b): decision 81's fix, then the draft pull request, with write access off in code.

**Earlier:** Step 6 (code) built 2026-11-22, committed (ef92b33): the evaluation's code, and write access off in code.

**Earlier:** Step 5 built 2026-11-21, committed (patch, verdict, and taking it all back, T5.1 to T5.5; T5.5's Docker release check written, not run).


## Current task

One change awaits the founder's review and the coordinator's commit: **Step 9** (`F5 Step 9: words, operations, launch, and the hardening pass's runs`; migration 0085, ADR-103 to ADR-106; its files are listed under *Step 9*, below). Then **the wording list** (*Wording waiting for the founder*, below), open decisions 119 to 130, and those still open from earlier steps. The real runs (T6.1, T6.3, T6.4), the go (T6.5), T7.11, T8.4, T9.4 and T9.7's real run are the founder's, after Phase 2.

## Wording waiting for the founder

**Every piece of new user-facing wording F5 introduced that needs your approval (foundation decision 2), in one list.** "New" means words the spec did not fix itself; sentences the approved spec states word for word (FR24's confirm sentences, the label "Example pattern, not your code…", the source sentence) are already yours and are not repeated. Each line names where the words live in code, so one reading can approve or change them. **Recommendation:** read them on the screens with the demo (`docs/product/fix-demo.md`) and on the customer's page, then answer per group.

| # | Words | Where they appear | Recorded as |
|---|---|---|---|
| W1 | **The customer's page, whole** | `docs/product/setup/ai-suggested-fixes.md` (rendered in the console's setup docs; marked as a draft at its top) | Step 9, T9.5 |
| W2 | **The written demo's room lines** ("A forged error can steer…", already in the spec's *What may be said*) and its scene descriptions | `docs/product/fix-demo.md` | Step 9, T9.7 |
| W3 | **The scope sentence's final words** (its rung 2b, imports and the AI-asking sentences) | spec *Goal*; the customer's page | Spec D58, D62, D72 to D74 |
| W4 | The `writes_off` sentences: "Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done. Download the patch instead." and Run CI's twin | `sentences.DRAFT_REFUSAL_SENTENCES[writes_off]`, `CI_FAILURE_SENTENCES[writes_off]`; the section, `/settings/ai`, the customer's page | Decision 90 |
| W5 | Run CI's confirmation, its action words, "Asking GitHub to run CI…", "CI was asked for…", "Asking GitHub to run CI did not finish…", its six failure sentences, the Run CI commit message, FR30's three facts-line templates | `sentences.RUN_CI_CONFIRM`, `CI_REQUESTED`, `CI_PUSHED`, `CI_DID_NOT_FINISH`, `CI_FAILURE_SENTENCES`; `pull_request_text.run_ci_commit_message`; web `fix-page.ts` `mergedFactsLine` | Decision 109 |
| W6 | **The admin summary's words**: "Suggested fixes, last 30 days", "Asked for", "Refused, by reason", "None refused.", "Drafts opened", "Drafts merged", "Tokens used", "Notices", "Files read", "Files are not shown in a support view.", "Open the finding", "Only owners and admins read the suggested fix summary.", "The suggested fix summary could not be loaded", and a refusal's label "Rule K7: added hidden or look-alike characters" | web `aisettings/FixSummaryPanel.tsx` (`FIX_SUMMARY_WORDS`); API `findings/fix_summary.py` | Step 9, decision 122 |
| W7 | **The notice**: "Rule {K} withheld a suggestion because it {what the rule found}. A planted instruction in a file of this repository may look like that." | `nexovue_platform/fixes/notices.notice_sentence` | Step 9 |
| W8 | The rules' own words for what each found (K1 to K10), shared by the requester's sentence and the notice | `sentences.WITHHELD_RULE_WORDS` | Steps 4, 9 |
| W9 | The timeline sentence "A suggested fix was written from the service's own files" | web `finding-page.ts` | Decision 50 |
| W10 | `needs_likely_cause`'s sentence ("…this service's own signed deploy…") | `sentences.DRAFT_REFUSAL_SENTENCES` | Decision 91 |
| W11 | The App panel's raising warning and its signed-commit warning ("If a branch rule requires signed commits, a draft's commit may not count as signed, and that can stop its merge."); the plan sentence is gone (item 35b) | web `settings/FixAppPanel.tsx` | Decisions 95, 96 |
| W12 | Rung 2b's line before Step 3 ("Nexovue does not know where this service's code lives.") and `nexovue_error`'s sentence | `sentences.ELIGIBILITY_SENTENCES`, `REASON_SENTENCES` | Decisions 22, 24 |
| W13 | Banned words read to include "fixes" and "fixed" (the scan every sentence and the page pass) | `test_fix_sentences.py`, `test_fix_customer_page.py` | Decision 9 |
| W14 | Every other sentence in `nexovue_platform/fixes/sentences.py` and `pull_request_text.py` that the spec does not state word for word (refusal, state and check sentences; pull request title, body and commit templates) | those two modules; `fix-page.ts` | Steps 2b to 8 |

## CI fix 2026-11-22: the AI account's job tests raced the stack's own AI worker

**What failed.** A local full CI run (`pytest -n 6` beside the dev stack) failed `test_fix_insight_grants_live.py::test_the_ai_account_claims_retries_and_finishes_an_insight_fix_job` at `assert job in claimed`: the AI account's claim returned nothing. **The product is not at fault; the test was.** It committed a due `insight-fix` job, then claimed it in a second session. The dev stack's `scheduler-insight` (`--queues assistant,insight,insight-fix`) claims every due job on those queues, whichever organization it is for, polling every second, so it could take the job in the gap between the commit and the test's claim. Under load that gap widens. The suite's sibling, `test_the_claim_still_matches_no_queue_outside_the_ai_role`, had the same flaw: it committed a due `light` job, which `scheduler-light` can take first, and then the test passes without testing anything. Its claim also committed 60-second leases on any other organization's due `insight-fix` jobs.

**Reproduced,** on this change's own Postgres with the real `scheduler-insight` worker process running against it. A scratch harness waited up to 3 s after the test's commit, to stand for a slow CI scheduler. The unchanged test failed 10 of 10 runs with CI's error: 7 times the worker held the job's lease (`vm:…`), and 3 times it had already run and removed it. With the worker stopped, the same harness passed 3 of 3. Without the harness, the unchanged test passed 15 of 15 runs beside the worker, because the gap is milliseconds when the machine is idle. That is why GitHub runs passed until now.

**The change.** Both tests now write their organization and job, act as the AI role (`SET LOCAL ROLE nexovue_insight`), and claim, retry and finish, all in one transaction that is rolled back. No other session ever sees the job. This is the pattern `test_insight_account_live.py` and the rolled-back route suites already use. The role's own grants still decide each call. Seen: leaving `insight_retry_job` out of `INSIGHT_FUNCTIONS` fails with "permission denied for function insight_retry_job". Removing `insight-fix` from the claim's queues fails the first test. Removing the claim's queue fence fails the second. The reads still sign in as the AI account itself. *After:* the same 3 s harness, with the worker live (a committed canary was taken by it in every run), passed 5 of 5 runs of both tests, and other sessions saw the job 0 times. *Rejected:* `jobs_held`, because it makes the job never due, and this test's own claim must find it; a private database, because it costs a migration and holds the server's provision lock, when nothing here has to be committed. *Not fixed (LOW, recorded by `/review-diff`):* the claim still takes the oldest 10 due AI jobs across the whole database. If more than 10 other due `insight-fix` jobs, older than this test's transaction, were waiting unclaimed, its job would be left out. Today the stack's worker drains due jobs within a second, and the suites that commit AI jobs hold them, so it was not seen. For the test's few milliseconds, its claim also row-locks those other jobs, and the stack's claim skips them.

## Step 9, built 2026-11-22

**What it ships.** The operator's view of rung 3's sweep, the admin's 30-day summary with notices, NFR2's click timing, the customer's page, the written demo, the rivals re-read, the architecture documents and the design's four ADRs, plus the hardening pass's Playwright run and Docker restore check. **Write access is still off in code** (`write_gate.WRITE_ACCESS_GO` is None); nothing in this step is near a write path, so `test_fix_write_gate.py` needed no new entry (it passed unchanged). Fake providers and the simulated GitHub only; real-model and real-GitHub runs remain the founder's, after Phase 2 (foundation items 28, 31, 33).

- **T9.1, the sweep's figures and its alert.** Migration **0086** (`0086_ai_solution_sweep_finished.py`, `down_revision "0085"`, renumbered at commit after F22 Step 8 took 0085; a downgrade drops the table, since it holds one time the next pass writes again): `fix_sweep_status`, one install-wide row, the only name `sweep_fixes`. `connections/fix_sweep.py`: the task now runs `sweep_and_record`, which writes the database's `now()` there once a whole pass finished; a pass that raises writes nothing. `metrics.py`: `nexovue_scheduler_fix_rows_recent{kind,state,reason}` (state moved in the last hour; `reason="none"` for none), `…_fix_oldest_in_flight_seconds` (gathering, gathered, proposing, approved; 0 when none) and `…_fix_sweep_age_seconds` (+Inf before any pass, 0 while connections are off, since beat then schedules no sweep). `deploy/monitoring/rules/nexovue.yml`: **`NexovueSuggestionSweepStalled`**, age over 900 s held 5 minutes (a ticket). The click-time refusal alert (`NexovueSuggestionRefusedAgain`) already existed since Step 5. Runbook entries in `docs/operations/ai.md`; `alerting.md` and `monitoring.md` corrected (the rules exist and are tested; 27 alerts, not 19). **Fixed after merge (2026-11-22):** the record was refused on `scheduler-connections`, whose own account (F22 T8.7, built in parallel) held no right on `fix_sweep_status`, and `test_fix_sweep_status_live.py` wrote it as the install account; the account now holds the three column rights the upsert needs and the suite runs as it (`docs/changes/fix-sweep-records-as-its-account/`).
- **T9.2, NFR2's timing.** `apps/api/tests/test_fix_click_timing_live.py`: signs up through the real signup route of its own real API under uvicorn on the stack's stores (`assistant_served.running`: self-hosted with AI available, held causes shown, signup open; the dev stack's API is hosted, which offers no suggested fix, so the first full gate's run was refused with `hosted_ai_not_offered`, 2026-11-22), seeds the rest in Postgres as the route suites do, and times three clicks, 10 each, each on its own finding, each followed by the read the page makes: **figures in *Validation*, no pass mark.** Every job a click adds is held as the API commits it (`held_jobs.jobs_held`), so the stack's `scheduler-insight` never takes one or reaches the fake provider's address; the test soft-deletes its organization and removes its jobs at the end.
- **T9.3, the admin summary (AC39).** `GET /ai/fix-summary` (F3's `/ai` router, `MayManageAI`; logic in `findings/fix_summary.py`, shapes in `fix_schemas.py`): requests, tokens and refusals by reason by `requested_at`, verdicts by `verdict_at`, drafts opened (a pull request number, by `approved_at`) and merged (by `pull_request_state_at`), all 30 days; a notice for each `fix.withheld` entry the sweep wrote for K2, K7, K9 or K10, newest 50, with the rule's words, the time, the finding and the files, **never the suggestion's text**; a support view reads `files: null`. **K6 and K8 notices wait on `nexovue_platform/fixes/notices.REPLAY_REPORT_READ`**, a date in code, None until the founder records that he read T6.3's replay report (decision 89, now built). The rules' words moved into `sentences.WITHHELD_RULE_WORDS`, which the requester's sentences and the notice share (the 49 reason sentences compared equal before and after). Web: `aisettings/FixSummaryPanel.tsx` in a *Suggested fixes* section beside F3's usage on `/settings/ai`: loading, failed, empty (the design's "No suggested fixes asked for in the last 30 days."), no permission (members and viewers, or a 403), an em dash for a number the API did not give, 0 only as a count.
- **T9.4, the setup timing: not done; its fixture is.** `tools/ai-eval/fix/setup_fixture.py` makes 30 services appear in two environments (`production`, `staging`) on a dev stack and prints each one's code location across three repositories. How to run the timing is under *T9.4, the setup timing*, below. The timing is a person's: the founder's to arrange.
- **T9.5, the customer's page.** `docs/product/setup/ai-suggested-fixes.md`, linked from `setup/index.md` (it renders in the console's docs): the exact scope sentence and the approved source sentence; the steps and what each sends; **write access off until the go, and drafts on every GitHub plan (D62)**; what is sent and never sent; the rules and what they do not check; protect `main` and release branches, the App's reach, the author and approval, other CI and other triggers, `pull_request_target`, Run CI's warning; the login note by exact string; merged and closed final, 72 hours, disconnect; the 90-day case; how long code is kept and backups; the outbound limit not yet shipped (decision 43); one cost example from Anthropic's prices read 2026-11-22, labelled an estimate. **A draft for the founder's wording approval.** `docs/self-hosting/backup.md` gains *Suggested fixes after a restore*.
- **T9.6, the rivals re-read: half done.** `research.md`, *Re-read 2026-11-22*, and a dated note in `docs/research/phase-2-competitors-2026-11.md`: Sentry, Datadog Bits Code, Grafana, New Relic, OneUptime's README, OpenObserve, HolmesGPT, Aurora and OpenSRE read from their own pages with `curl`. **Changed:** Sentry's $40 per contributor holds only until 8 December 2026; Grafana's sandboxes are Grafana Cloud only; New Relic's page says Autopilot never writes to GitHub; Datadog adds Azure DevOps Cloud. **Unreachable from this session** (the proxy answered `CONNECT` with 403, and the web-fetch tool could not resolve them): incident.io, docs.rootly.com, oneuptime.com (agent page and pricing), betterstack.com, docs.middleware.io, www.splunk.com; their rows stand as of 2026-11-02. **OneUptime's Community edition was not installed** (about 8 GB of disk free; its stack is many services), so whether its self-hosted fix path is free is still unknown.
- **T9.7, the written demo.** `docs/product/fix-demo.md`: four scenes (one good diff `py_stack_average`, one refusal `py_config_timeout`, one planted instruction `py_planted_notify`, the "not checked" list), labelled the authors' seeded fixture repository, with the rehearsal on the simulated GitHub with fake providers (125 passed, *Validation*) and the founder's steps for the real run.
- **T9.8, documents.** `docs/architecture/system.md`, `database.md`, `security-and-tenancy.md`, `current-system-reference.md` (§7.7) gain F5; `feature-catalog.md`'s F5 row and entry; `vision.md:136`'s line now says what F5 does; `design.md`'s *Proposed ADRs* points to the files.
- **T9.9, the ADRs.** **ADR-103** (code from a model reaches a person or a repository only through rules in a room with no model), **ADR-104** (the first commit holds back CI), **ADR-105** (consent per repository, bound to where it goes), **ADR-106** (F5's own App and code location), indexed in `docs/decisions/README.md`. ADR-102 was the last taken; 103 was next free (ADR-099 is a gap in this tree, left unused: decision 127).

**Hardening done here.**
- **`fix-section.spec.ts` was run** (Chromium at `/opt/pw-browsers/chromium`, trace off, against this step's own web and API on private ports, the finding's reads stubbed as the spec does): **23 of 28 pass; 5 fail, every one a shared component under 44 px at 375 px** (decision 120). Three faults in the spec itself were fixed: the rung 3 and draft states passed a section line where the general read belongs, so the page crashed ("Cannot read properties of undefined (reading 'languages')"); their heading locator matched both the section's and the state's "Suggested fix" heading; the draft states asserted 44 px at 1280 px, which AC37d does not ask. **One product fault fixed:** the brief's disclosure (`FixSection.tsx`'s `summary`) was 20 px tall; it is now `min-h-11` (the rung 3 proposed state failed on it at 20 px, then passed).
- **`test_fix_restore_docker.py` was run** on this step's own containers and **passed** (102 s): backup, `restore-postgres.sh`, `verify.sh`, AC32 and AC6a. It first failed: its seed put the in-flight row on a finding already holding one (`uq_finding_fixes_one_working`), and `psql`'s error was hidden. The seed now picks a finding with none, and a refused statement shows `psql`'s words.
- Not done here: Playwright states for Run CI and the facts line (Step 8; never written), browser and phone checks by hand, Design QA, tests for AC54 and AC58 (decision 103), decisions 83, 84 and 88.

**Shared files touched, and why.** F3's `ai/router.py` (one route) and `AISettingsScreen.tsx` (one section); `metrics.py`, `nexovue.yml`, `nexovue_test.yml`; `organizations/tables.py` and `models.py` (the new table); `test_scheduler_postgres_tenancy_structure.py` (two gauges and the install-wide table); `docs/operations/{ai,alerting,monitoring}.md`, `docs/self-hosting/backup.md`, `docs/product/setup/index.md`, the architecture documents, `feature-catalog.md`, `vision.md`, `docs/research/phase-2-competitors-2026-11.md`, `docs/decisions/README.md`, `docs/product/phase-2-tracker.md`. F5's own: `fix_sweep.py`, `sentences.py`, `records.py`, `FixSection.tsx`, `fix-section.spec.ts`, `test_fix_restore_docker.py`.

### T9.4, the setup timing (the founder's to arrange)

AC37c: someone new times the whole setup once, on a 30-service, two-environment fixture; time and step count reported with no pass mark, rungs 3 and 4 apart from rung 5, before any page calls setup simple. **Not run.** To run it:

1. **The person:** someone who has not set F5 up and has not read its design. The founder watches and writes down each step and the clock; he does not help unless asked, and notes each time he is asked.
2. **The fixture:** a fresh dev stack (`just fresh`), one organization and project; then `cd tools/ai-eval && uv run python -m fix.setup_fixture send --key <the project's ingest key>`, which makes 30 services appear in `production` and `staging`. `uv run python -m fix.setup_fixture locations` prints where each service's code lives: 18 in `acme-scratch/platform`, 6 in `acme-scratch/web`, 6 in `acme-scratch/data`. Create those three repositories in a scratch GitHub organization (empty folders are enough for the setup itself).
3. **Timed, part one (rungs 3 and 4):** connect AI on `/settings/ai` and an owner's agreement to send facts; create the *Nexovue suggestions* App from the console and install it on the three repositories; set 30 code locations; an owner agrees to send code for each repository; ask once for a suggested fix on one finding and download its patch. Clock and step count.
4. **Timed, part two (rung 5), only after the go (T6.5):** raise the App to write, allow drafts on one repository, link a GitHub account, open one draft. Clock and step count, apart from part one.
5. **Write the result here:** the two times, the two step counts, every place the person stopped or asked, with no pass mark.

### Tests, each seen failing

Recorded in this session's scratchpad (`f5s9-own/seen.md`); breaks by a scripted edit (`seefail.py`), the file restored and compared byte for byte, every restore equal.

| Test | How it was seen failing |
|---|---|
| `test_fix_sweep_status_live.py` (3), `test_scheduler_metrics_live.py` (2 new) | Written first: all 5 failed with the migration applied and no code (`sweep_and_record`, the two readings missing) |
| `deploy/monitoring/tests/nexovue_test.yml` (2 new groups) | Run against a copy of the rules without the new alert: failed |
| `test_fix_notices.py` (10) | Written first: failed to collect before `notices.py` and `WITHHELD_RULE_WORDS` |
| `apps/scheduler/tests/test_fix_summary_live.py` (3, the AC39 walk) | Breaks: the rule filter removed (K6 shows a notice), the 30-day bound removed, the files blanked: each failed its test |
| `test_fix_summary_routes_live.py` (6) | Breaks: the merged window removed, the verdict count zeroed, `MayManageAI` replaced by `CurrentIdentity`, the support view flag forced off: each failed |
| `FixSummaryPanel.test.ts` (8) | Written first: no module; then breaks: the permission gate, the em dash, the support view's files line, the empty sentence: each failed one test |
| `test_fix_customer_page.py` (15) | Breaks on the page: the login sentence altered, "safe" added, the scope sentence altered: each failed |
| `tools/ai-eval/fix/test_setup_fixture.py` (4) | Written first: no module |
| `test_fix_sweep_status.py` (4) | Frozen copy: passes with the code (it guards later drift, like `test_fix_follow_storage.py`) |
| `fix-section.spec.ts` | Ran: the brief's 20 px disclosure failed, then passed with the fix |
| `test_fix_restore_docker.py` | Ran: failed on the seed, then passed with the fix |

**Clocks:** the sweep-status tests use the database's clock (the row is install-wide; their stand-in pass reaches no organization); the metrics tests run in a rolled-back transaction; the AC39 walk passes the test's `now`; the route tests use the suite's `now`. **Live rows:** the AC39 walk builds and drops its own organization and narrows each sweep to it; route tests roll back; the timing test soft-deletes its organization and deletes its jobs.

### Validation, 2026-11-22

Against this step's own containers only: `f5s9-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, 127.0.0.1:35446, network `f5s9-net`, volume `f5s9-pgdata`, migrated to 0085 by `nexovue_api.setup`) and `f5s9-clickhouse` (`clickhouse:26.3.20.7-jammy`, 127.0.0.1:38137 and 39017, volume `f5s9-chdata`, migrated by the workers' setup), with this step's own API (uvicorn, 127.0.0.1:38946) and web (Vite, 127.0.0.1:35946) for the timing and Playwright runs. Port 38000 was already serving another builder's API and was not touched. A container restart cut the session once; the containers were started again with their volumes and the checks below finished after it. All were removed at the end, with their volumes and network.

- **F5's suites and every gate touched, one run (`-n 4`): 6,862 passed, 7 failed, 4 errors.** The run covered all `test_fix_*` (API and scheduler), `packages/py-platform/tests`, `tools/ai-eval`, the vocabularies, authorization coverage, compose contract, cross-service contracts, deletion, the GitHub link, insight grants, migration rules, personal data, policy, both tenancy structure tests, the read surface (live, spec, structure), beat, scheduler metrics, the fake's recordings and F22's `test_connections_*`. **The 7 failures:** `test_postgres_tenancy_structure.py[summary]`, **a real finding** (the summary named `org_id` through a variable the structural check cannot see; fixed by naming it in each statement, then 2,924 passed); `test_compose_contract.py`'s insight-password test in 5 kits, from this shell exporting `POSTGRES_INSIGHT_PASSWORD` (passes with it unset, as Step 7 recorded); `test_connections_github_live.py::test_a_repository_no_longer_reached_is_marked_unreachable` (F22's, untouched; passed alone and in the re-run, so load on a shared host). **The 4 errors:** `test_fix_click_timing_live.py`'s guard, failing loudly because that run had no API at its default origin, as a live suite must; it passed three times against this step's API. **Re-run after the fix:** the summary suites, compose contract, F22's GitHub suite and the tenancy structure test, 3,051 passed.
- **NFR2's figure (T9.2), no pass mark:** decision 124 (median *Suggest a general fix* 60 to 131 ms, slowest 852 ms; *Ask for a suggested fix* 91 to 256 ms, slowest 709 ms; the verdict 24 to 35 ms, slowest 258 ms).
- **Demo rehearsal (T9.7):** 125 passed.
- **`fix-section.spec.ts`:** 23 passed, 5 failed (decision 120). **`test_fix_restore_docker.py`:** 1 passed (102 s).
- `promtool check rules` (27 rules) and `promtool test rules`: success. `uv run mypy .`: no issues in 1,329 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,758 files formatted. Biome format and lint on `aisettings`, `FixSection.tsx` and the Playwright spec: no errors. `pnpm -r --if-present run typecheck`: passed. Web: `aisettings`, `findings`, `docs` and the setup-docs plugin, 426 tests, all passing after the page's `.md`-looking text was rephrased (the plugin test refuses link-shaped text ending in `.md`).
- **Not run:** `just check` (by instruction); the whole web suite; other features' live suites beyond those above; Playwright for other screens; any real provider or real GitHub (the founder's); T9.4's timing; T9.7's real run.

### Review (`/review-diff`), 2026-11-22

Every changed file is named above. **One MEDIUM found and fixed:** the summary's statements named `org_id` through a local variable (`in_org`), which the tenancy structure test cannot see, so a later edit could drop the tenant unnoticed; each statement now names `FindingFix.org_id == org_id` (the structural test failed on it, then passed). **Checked by hand:** every new Postgres statement binds the organization, or is a named install-wide gauge or the install-wide `fix_sweep_status`, each in the tenancy test with its reason; the route is a read under `MayManageAI`, and a support view's notices carry no paths; no new ClickHouse statement; no write path added, the write gate untouched and its 17-plus structural tests passing; no credential in code, logs or fixtures (the API's private settings live in this session's scratchpad, never in the repository); the web panel imports only `shared`, uses design tokens, and draws an em dash for a missing number. **Recorded, not fixed (LOW):** the two new gauges read `finding_fixes` by `state_changed_at` every 15 seconds without an index on that column, like Step 5's tamper gauge; the table is small (one row a suggestion), so revisit if it grows. The coordinator should check no other branch also adds migration 0085 or ADR-103 to ADR-106 (decisions 119, 127).

## Step 8, built 2026-11-22

**What it ships.** On a draft Nexovue opened, a person with write access can click **Run CI**, read FR25's warning, and have CI run on the draft, **once the founder's go is recorded**; and the draft's state on the page follows GitHub within a poll: merged (with the facts line), closed, or `untracked`. Until the go, which is how it ships, Run CI writes nothing: the click and the job refuse `writes_off` **before any request reaches GitHub** (the fake's request log stays unchanged, asserted), and the read offers no Run CI. No setting, environment variable or row can change that (ADR-097, ADR-101). **The GitHub trial may change or delete this step** (plan; design, *The GitHub trial*: "Does a draft opened with an App token run `pull_request` workflows?", "Does a no-change commit without the marker run CI?", the merge question); the trial is the founder's, after Phase 2.

- **Migration 0084** (`0084_ai_solution_follow.py`, `down_revision "0083"`, roll forward only): on `finding_fixes`, `ci_parent_sha`, `ci_commit_requested_at`, `ci_pushed_at` (Run CI tells a retry from a second click, and never refuses its own commit), `pull_request_state_at` (BR9's 7-day clock, FR30's merge time), `pull_request_merge_commit_sha` (FR30's deploy), `pull_request_polled_at` (the poller's order); `last_write_failure` gains `branch_changed` and `pull_request_not_open`; their sha check. None is stored code. The design names three migrations; this is a fourth (decision 105).
- **T8.1, Run CI.** `fix_github.py`: `FixWrites.move_branch`, the `PATCH` of `refs/heads/nexovue/suggestion-<id>` only, `force` a constant false; a 422 reads `not_fast_forward` or `commit_missing`. `connections/fix_writer.py`: `run_ci` and `connections.run_fix_ci` (ids only): the gate first; an `opened` draft whose pull request is open, for the request the row holds; the person who clicked (`ci_requested_by`) re-checked for write access on GitHub (D6), the owner's allowance and the App's write grant (`draft.ci_refusal`), BR13 on GitHub's live answer; the head must be the opening commit, the latest Run CI commit or the head that commit was built on, else `branch_changed`; one commit with **the head's own tree** and no skip marker (`pull_request_text.run_ci_commit_message`), **stored with its request and parent in its own transaction before the `PATCH`**; a retry whose commit is already the head finishes with no new commit, one whose head is still the parent moves the branch to the stored commit; a second click makes a second commit. Disconnect and a newer click are read before each write. API: `POST .../run-ci` (`MayOpenFixPullRequest`, `findings/fix_draft.request_ci`): `writes_off` first; an open draft only (`pull_request_not_open`); the request stored, any earlier Run CI failure cleared, `fix.ci_requested` committed with it, the job added with ids only. Rung 2b's placeholder route is removed: the source route refuses a general row `general_has_no_code` (decision 116).
- **T8.2, following the pull request.** `connections/fix_poller.py`, `connections.poll_fix_pull_requests` on `connections`, every 5 minutes from beat (`sweep:fix-pull-requests`, 60 s and 90 s), while connections are on. **Reads only** (decision 104): `GET /repos/{r}/pulls/{n}` (`FixGitHub.pull`, on `ROUTES`) and `?head=`, with a `pull_requests: read` token per repository. An open draft becomes `merged` (GitHub's merge time and the merge's commit), `closed` (its close time), which frees the finding (BR8, AC28b), or `untracked` on a 404 with a token that reaches the repository (decision 110); a repository the App cannot reach is skipped. A held branch (AC51) is adopted by `?head=` when a pull request's head is Nexovue's commit (`opened`, `fix.pull_request_opened` with `adopted`, the timeline's event; decision 113), `untracked` when its head is another (decision 111), or `not_opened`, naming the leftover branch. An `approved` row is left to its job. The sweep (`connections/fix_sweep.py`, decision 106) deletes stored code 7 days after a merge or close (`pull_request_done`, BR9) and marks every followed pull request in a repository whose access has been gone 72 hours `untracked` (AC49). The permissions and repositories refresh was already the sweep's, hourly, since Step 5; no plan refresh is built (decision 107).
- **T8.3, the section.** `FixCi.tsx`, drawn by `FixDraft.tsx`: **Run CI** opens FR25's confirmation by exact string, and only its own click posts; CI held (FR26's line, only while open and not yet run), requested ("Asking GitHub to run CI…"), done (the held line goes), not finished after 20 minutes (Run CI offered again), or the failure's sentence; merged, closed and `untracked` read the API's sentences; after a merge the requester reads FR30's facts line, each time stated in the order it fell, no causal word (`fix-page.ts`, `mergedFactsLine`). The read (`fix_draft_read.opened`) gains `ci_action`, `ci_confirm`, `ci_confirm_action`, `ci_status`, `merged_facts`: the first `deploy_events` row in the project, as F28 records it, whose `commit_sha` is the merge's, at or after the merge (decision 114), and the finding's close.
- **T8.4 (not run):** the real run with **Run CI** on a scratch repository is the founder's, after Phase 2 (item 28), and needs the go (T6.5).
- **T8.5, its gate half:** `test_fix_write_hostile_live.py` stores AC20's hostile proposals on a `proposed` row as a compromised AI room would, approves each with the digest a check would have reached, and drives it through the draft job with a test go: every one stops `changed_since_check` with **no repository write**; a harmless control stored the same way opens; a banned claim in `why` is replaced on screen, not refused, and never reaches GitHub (BR10). The re-run of T6.1's safety arms is a real-model run: the founder's, after Phase 2 (item 31).

**Proof nothing writes while the gate is off.** (1) `test_fix_writer_live.py::test_as_shipped_run_ci_answers_writes_off_with_no_request_to_github`: with `WRITE_ACCESS_GO`, the job stores `writes_off` and the fake's request count is unchanged; (2) `test_fix_draft_live.py::test_as_shipped_run_ci_refuses_writes_off_and_the_read_offers_no_run_ci`: 409 `writes_off`, no job, no row change, no Run CI on the read; (3) `test_fix_write_gate.py` (17): every write entry point, Run CI's click and job included, asks the gate **on a line before** anything that can reach GitHub or add a job; every go-taking function in those modules is on that list; only the writer builds `FixWrites`; the poller imports no write route, calls no write method and only read routes; and Step 7's checks (nothing builds a go, nothing assigns it, no default, the gate reads no setting) cover the new code; (4) `test_fix_pull_request_state.py::test_the_poller_only_reads`: every request the poller made was a read or a read-only token, and the repository writes in the fake's log did not change; `test_with_no_draft_the_poller_reaches_nothing`: as shipped it asks GitHub nothing.

**Shared files touched, and why (kept minimal).** F22/shared: `tracker_fake.py` (F5's part: `close_pull`, `merge_pull`, `delete_pull`, a pull request's GitHub times and merge commit, and its head following its branch), `fixtures/github_fix/read_pull_merged.json` and its scene in `test_fake_github_matches_recordings.py` (shaped from GitHub's documents, unverified), `beat.py` and `test_beat_schedule.py` (the poller's entry), `jobs/registry.py` (`fix_poller` in the connections set), `test_cross_service_contracts.py` (Run CI's job; the poller not in `TASK_MODULES`), `test_scheduler_postgres_tenancy_structure.py` (three named statements), `test_authorization_coverage.py` (the route; rung 2b's placeholder gone), `test_migration_rules.py` (0084 roll forward only), `docs/product/phase-2-tracker.md`. F5's own earlier files: `fix_takeback.py` (a disconnect's `untracked` gains its time), `fix_writer.py`'s `_OPENED`/`_NOT_OPENED` (their state time), `fix_general.py` and `general_router.py` (the placeholder and `refuse_code_action` deleted), `test_fix_general_routes*.py`, `test_fix_write_storage.py` (0083 holds its own list, `STEP_7_WRITE_FAILURES`), `test_fix_draft_live.py` (the read's new keys), Step 7's test helpers moved from `test_fix_writer_live.py` into `fix_writer_fixtures.py` so the new suites share them; `test_fix_consent_live.py`'s AC9 test reads only its own organization's consents (decision 117). F3, F4, F28: none (F28's `deploy_events` is read, not changed).

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run with `-x` (or `vitest -t`), and the file restored and compared byte for byte (`seefail.py`, this session's scratchpad, `f5s8-own`); every restore compared equal. **67 breaks, each failing its test.** Seven first did not fail and were redone, recorded honestly: Run CI's early `pull_request_not_open` check (the job's later check still refused, after GitHub reads; the test now asserts no request at all for a closed draft), the done-check break (its old string missed the formatted source), FR30's commit match (the other commit's deploy came after the right one; it now comes first), and the four web breaks (the script read a later `error: undefined` line as the summary; re-run reading vitest's `Tests` line). The recorder's check (`test_record_github.py`) failed in the full run before `read_pull_merged` joined `record_github.py`.

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_fix_writer_live.py` (11) | Run CI: the gate first · no skip marker · parent is the head · the commit stored before the `PATCH` · a retry after the move finishes with no commit · a retry before it moves to the stored commit · a head not Nexovue's refused · the person's write access · the owner's allowance · an open draft only, asking GitHub nothing · one request served once | 1 each |
| `test_fix_github_client.py`, `test_fix_writer_live.py` (2) | the `PATCH` never forces · only the suggestion's own ref | 1 each |
| `test_fix_pull_request_state.py` (17) | closed stored · GitHub's close time · merged stored · the merge's commit · a 404 `untracked` · visited · adopted by head · adoption audited · none found `not_opened` · another head `untracked` · an `approved` row left to its job · an unreachable repository skipped · a read token only · 72 hours `untracked` · under 72 hours kept · 7 days kept · code deleted after 7 days | 1 each |
| `test_fix_write_hostile_live.py` (1) | the writing room's rules re-run (both layers removed) | 1 |
| `test_fix_draft_live.py`, `test_authorization_coverage.py` (17) | the route's gate first · the request stored · audited · an open draft only · a general row's named code · a viewer refused · the route's gate in coverage · the read hides Run CI while off · waiting replaces the action · done reads done · the held line goes · lapsed after 20 minutes · FR25's confirmation · the deploy at or after the merge · the deploy names the merge's commit · the finding's close | 1 each |
| `test_fix_write_gate.py` (5) | an entry point asks before reaching out (the job; the route) · every go-taking entry point listed · only the writer builds writes · the poller imports no write | 1 each |
| `test_fix_draft_rules.py`, `test_fix_pull_request_text.py` (3) | the write grant · done names the request · Run CI's message | 1 each |
| Storage, rules, contracts (7) | 0084's frozen list · the model's sha check · roll forward only · statements bind `org_id` · Run CI's job registered · the poller every 5 minutes · the fake's merged shape | 1 each |
| Web (5) | `FixFiles.test.ts`: Run CI confirms first · CI requested shown · facts in time order · the draft draws Run CI; `fix-page.test.ts`: no causal word | 1 each |

**Clocks:** scheduler suites one `now` per test, read when it starts, passed to the job, the poller (`clock`) and the sweep (`only_org`); API suites the suite's `now`, and the 20-minute lapse set relative to it. **Bounded waits:** no test polls; every GitHub call crosses the fake through the guarded client with its timeout. **Live rows:** every scheduler test builds and drops its own organization and polls or sweeps only it; every API test runs in a transaction rolled back after it, so no job it adds is committed for the stack's workers; the scheduler tests call each job's code directly and commit no job.

### Validation, 2026-11-22

Against this step's own containers only: `f5s8-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, 127.0.0.1:35433, network `f5s8-net`, volume `f5s8-pgdata`, migrated to 0084 by `nexovue_api.setup`) and `f5s8-clickhouse` (`clickhouse:26.3.20.7-jammy`, 127.0.0.1:38124 and 39001, volume `f5s8-chdata`, migrated by the workers' setup, ClickHouse only), through a wrapper refusing any other store port. All were removed at the end, with their volumes and network.

- **F5's suites and every gate touched, one run (`-n 4`): 6,300 passed, 1 failed:** `test_record_github.py` (the recorder lacked the new recording; fixed, then 4 passed). The run covered all `test_fix_*` (API and scheduler; `test_fix_restore_docker.py` is the release check, not run), `packages/py-platform/tests`, `tools/ai-eval` (with ClickHouse migrated), `test_github_link_live.py`, `test_ai_vocabulary.py`, `test_alert_vocabulary.py`, cross-service contracts, migration rules, the tenancy structure tests, authorization coverage, the read surface tests, beat, and the fake's recordings. An earlier run of the same set had 1 failure, in Step 3's AC9 test, from another suite's committed rows (decision 117); fixed. After the review's fixes: the draft, recordings, recorder and consent suites, 96 passed.
- `uv run mypy .`: no issues in 1,287 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,700 files formatted. Biome on `apps/web/src/features/findings` and `fixtures/github_fix`: no errors. `pnpm -r --if-present run typecheck`: passed. Web findings suite: 15 files, 362 tests passed.
- **Not run:** `just check`; other features' live suites (F22's `test_connections_*` among them, though the fake changed only in F5's additive part); the Playwright states (hardening pass); `test_fix_restore_docker.py`; any real provider or real GitHub (the founder's, after Phase 2); T8.4.

### Deferred to F5's hardening pass

- Playwright at 375 and 1280 px for Run CI, its confirmation, the CI states and the facts line (not written); browser and phone checks; Design QA; the founder's *Judgeable* on a real scratch repository (T8.4).
- AC49's disconnect, reconnect and new proposal as one walk (decision 118).

### Review (`/review-diff`), 2026-11-22

Every changed file is named above. **Fixed:** a Run CI request whose job never answered (expired, or its worker died past its attempts) would have read "Asking GitHub to run CI" for ever with no way to ask again; it now reads as not finished after 20 minutes and offers Run CI (with a test seen failing); a type error on that path (`mypy`); the merged recording's JSON formatting; the recorder's missing step. Found while testing and corrected: a banned claim in `why` is replaced on screen by design, not refused, so AC20's drive asserts it never reaches GitHub rather than that nothing is written. Recorded, not fixed (LOW): `test_fix_writer_live.py`'s `_commit_writes` overlaps `fix_writer_fixtures.repository_writes` (it slices from a point). Tenancy: every new scheduler statement binds `org_id` except the three named cross-tenant ones, each in the tenancy test with its reason; every API query binds the organization. The route is in authorization coverage under `MayOpenFixPullRequest`. No token, sealed value or login is stored, logged or audited.

## Decision 81, fixed 2026-11-22 (a separate change, before Step 7)

**Cause.** The pack job notes stack frames only while choosing round 1's files (`PackReader._first_round` calls `builder.note_frame`); a later round (`_asked`) kept the earlier files with their numbers but never noted their frames, and `_store` wrote `builder.frames()` again, so rounds 2 and 3 stored and sent `frames: []`. The design says the model gets "`frames` from the pack as numbers" and that each later Send "runs this step again, with every file in the pack" (*What the model gets*; *The flow*, step 5): the frames belong in every round.

**Fix.** The pack job reads the earlier round's `frames` with its files (`_PACK`, `PackContext.pack_frames`) and, after keeping those files, notes each frame whose `ref` names a kept file, so the numbers and lines carry forward unchanged and an asked-for file adds none. The evaluation bed's later round passes the earlier frames too, so it still mirrors the product (ADR-098).

**Evidence.** `test_fix_source_pack.py::test_decision_81_a_later_round_sends_the_first_round_s_frames` failed before the fix (`[] == [{'ref': 'file_1', 'line': 5}, {'ref': 'file_2', 'line': 4}]`), and with the fix reverted by a scripted edit; `test_eval_pack.py::test_a_later_round_carries_the_stack_s_frames_as_the_product_does` failed with the bed's change reverted. Both pass; the pack, bed and run suites passed (53).

**Files, separable from Step 7:** `apps/scheduler/src/nexovue_scheduler/connections/source_pack.py` (`_PACK`, `PackContext`, `_context`, `_asked`; Step 7 also changes `check_access` in this file, so commit with `git add -p`, taking only the four decision-81 hunks), `apps/scheduler/tests/test_fix_source_pack.py`, `tools/ai-eval/fix/eval_pack.py`, `tools/ai-eval/fix/test_eval_pack.py`, and this section plus decision 81's line below.

## Step 7, built 2026-11-22

**What it ships.** A person with write access, in a repository an owner allowed, can open a suggestion as a draft pull request, **once the founder's go is recorded**. Until then, which is how it ships, nothing writes: the draft check, the click and **Delete this branch** answer 409 `writes_off` with its sentence, the check job refuses a `draft` check `writes_off`, the draft job puts an `approved` row back with `writes_off`, and the branch deletion does nothing, **each before any request reaches GitHub** (the fake's request log stays empty, asserted). The read hides **Open draft pull request** with the same sentence in its place. No setting, environment variable or row can change that: only a reviewed commit to `write_gate.py` with this file's T6.5 line (ADR-097, ADR-101).

- **Migration 0083** (`0083_ai_solution_write.py`, `down_revision "0082"`, roll forward only): on `finding_fixes`, `approved_by`, `approved_at`, `approved_digest`, `requester_note`, `requester_note_confirmed`, `ci_requested_by`, `ci_requested_at`, `pull_request_number`, `_url`, `_branch`, `_state`, `_commit_sha`, `ci_commit_sha`, `last_write_failure`, with their checks (the branch can only be `nexovue/suggestion-<id>`; commits are shas; `approved` names when and the digest; only a source row is written); the states `approved` and `opened`; **the one-draft index** `uq_finding_fixes_one_draft`, built concurrently; `fix_repository_settings`; the draft check's reasons and `writes_off`; the timeline's `fix_pull_request_opened`. `clear_stored_code` and the restore's SQL gain the note and the digest; the restore also returns an `approved` row to `proposed`, `did_not_finish`.
- **T7.1** above, with `organizations/tables.py` (settings go with their repository) and the frozen lists' tests (`test_fix_write_storage.py`; 0079's and 0082's tests now hold their own steps' lists).
- **T7.2, the write routes** (ADR-101). `fix_github.py`: `FixWrites` (blobs, tree, commit, the one ref, its deletion, the draft, and the narrowed write token), built only through `FixGitHub.writes(go)`, refused without the go; `WRITE_ROUTES` checked in; each write takes the suggestion's id and builds `refs/heads/nexovue/suggestion-<id>` itself; `POST /pulls` always `draft: true`; a 422 read as `branch_exists`, `commit_missing`, `pull_request_exists` or `draft_refused`. Reads added: the draft job's read token with `pull_requests: read`, the suggestion's branch head, its pull requests by `?head=`.
- **T7.4, the draft check and the click.** `nexovue_platform/fixes/draft.py` decides the four per-repository refusals in order (`drafts_not_allowed`, `no_write_permission`, `no_draft_support` for 24 hours, `needs_likely_cause`: only a released `signed_deploy` or `signed_deploy_weak` cause, D58), and `public_refused` (BR13). `connections/draft_rules.py` reads their facts. `fix_check.py` routes `draft` to `fix_patch.check_patch`, which, for a draft, refuses those before any GitHub call, then reads GitHub's live visibility and always checks the person's **write** access (D6), then re-reads, re-checks and stores the patch and digest on the person's own check row, as for a patch. API: `POST .../check {"for": "draft"}` needs a linked account; `findings/fix_draft.py` and `POST .../draft-pull-request` (`MayOpenFixPullRequest`): own `draft` check passed within 15 minutes, its digest, the note under FR16's prose checks (a public repository's only when confirmed), the case's root locked as detection locks it, the claim (`approved`), `draft_open` from the one-draft index, `fix.pull_request_attempted` committed with it, a case started (D42), the job added with ids only. FR9's row 13, `draft_open`, joins the eligibility list.
- **T7.5, the draft job** (ADR-100). `connections/fix_writer.py`: the gate first; then the two tokens; the person, the per-repository rules and BR13 again; **adoption first**; a branch not Nexovue's refused; a stored commit reused, a dropped one cleared; otherwise re-read and re-checked by the check run's own code (`fix_patch.read_changed`, `rechecked`), the digest equal to the approved one, blobs, one tree on the head, one commit (`[skip ci]` and `skip-checks: true` unless the owner chose CI on open), **the commit stored before the branch, the branch before `POST /pulls`**; a second pull request's 422 adopted by `?head=`; a refused draft `not_opened`, `no_draft_support`, `draft_refused_at` set, never a non-draft; then `opened`, `fix.pull_request_opened` and the timeline's event. Withdrawal and disconnect are read before every write. The sweep returns an `approved` row past 20 minutes, `did_not_finish` (AC26a). The taking-back returns an `approved` row to `proposed`, `consent_withdrawn`, and a disconnect marks a draft Nexovue can no longer follow `untracked` (AC48).
- **T7.6, the text.** `nexovue_platform/fixes/pull_request_text.py`: title, commit message and body all template; files by fixed role words, flags by fixed code and line, what was not checked; a private repository adds the signal, numbers, console link and the login as plain text, never `@login`; a public one only the requester's role, and the note only when confirmed; the note sits in a code span it cannot close.
- **T7.7, the settings.** `GET` and owner-only `PUT /connections/github/repositories/{id}/fix-settings` (`fix_settings.py`), audited `fix.repository_settings_changed` with before and after; `aisettings/FixRepositorySettings.tsx` on `/settings/ai` (owners set, others read; the `writes_off` sentence above while off); `FixAppPanel.tsx` gains the raising warning with the plan requirement, the steps, and a signed-commit warning.
- **T7.8, the section.** `FixDraft.tsx` and `FixDraftConfirm.tsx`: the hidden button with its sentence (FR10c), the link step first when unlinked, this person's own check, FR24's confirm by exact string with the note (and its public confirmation), the opened draft with its link and FR26's line, a last failure naming the branch, **Delete this branch**; `fix-page.ts` and the timeline's sentence; the Playwright states written, not run.
- **T7.9, the audit.** `test_fix_audit_live.py`: the draft job's read names the person and purpose `draft`; a support view reads the purpose, never the paths; the click's entry names the person and repository, and no entry holds a GitHub login.
- **T7.10, Delete this branch.** `delete_fix_branch` (job) and `POST .../delete-branch`: only the suggestion's own branch, only at a commit Nexovue made, only after its pull request is closed or never opened, only for a person with write access; audited `fix.branch_deleted`.
- **T7.11 (not run):** a real scratch repository end to end is the founder's, after Phase 2 (item 28), and needs the go (T6.5).

**Shared files touched, and why (kept minimal).** F22: `nexovue_platform/cases.py` and `detection.py` (the timeline's `fix_pull_request_opened`, with its fields), `findings/schemas.py` (its kind), `web/.../finding-page.ts` (its sentence), `test_finding_events.py`, `test_alert_vocabulary.py` reads 0083's list unchanged. F6/shared: `organizations/tables.py`, `test_authorization_coverage.py` (three routes), `test_scheduler_postgres_tenancy_structure.py` (the sweep's statement), `test_cross_service_contracts.py` (two jobs), `test_migration_rules.py` (0083 roll forward only), `jobs/registry.py` (`fix_writer` in the connections set), `deploy/backup/clear-stored-code.sql` (written by `restore_sql()`), `docs/decisions/README.md`, `docs/product/phase-2-tracker.md`. F5's own earlier files: rung 2b's `general_router.py` loses its draft placeholder (the source route serves both kinds, as `/check` and `/patch` do), with its two tests; `fix_patch.py`'s helpers made public for the job; `recheck.py` returns the checked contents in memory. F3, F4: none.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run with `-x` (or `vitest -t`), and the file restored and compared byte for byte (`seefail.py`, this session's scratchpad, `f5s7-own`); every restore compared equal, and the tree's checksums matched before and after. 93 breaks, each failing its test (6 first ran with an invalid `-k` and were re-run, recorded honestly).

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_fix_source_pack.py`, `test_eval_pack.py` (2) | decision 81: the product carries frames · the bed carries them | 1 each |
| `test_fix_writer_live.py` (28) | the job, the check and the deletion ask the gate first · always a draft · the skip-checks trailer · the tree on the head's · the timeline event · CI on open has no marker · the commit stored before the branch · adoption by the stored commit · the 422 adopted · a branch not ours · the digest equal · a refused draft not opened · withdrawal before each write · attempts used up named · write access at the check · drafts allowed · the App's grant · GitHub's refusal holds 24 hours · signed deploy cause · public needs allowance · the sweep's return · delete: own head, closed only, write access, deletes · a public repository's unconfirmed note | 1 each |
| `test_fix_github_client.py` (3) | writes cannot be built without the go · no write method names a branch · every write on `FixWrites` | 1 each |
| `test_fix_draft_live.py` (27) | the routes, the check route, the read and the settings while off · each refusal on the read (drafts, public, D58) · a link needed · the login warning · CI on open on the confirm · the click starts a case and is audited · own check, 15 minutes, digest · `draft_open` · the note checked, a public one confirmed, the confirmation stored · a general row · FR26's line · `did_not_finish`'s branch · withdrawal stops the draft · the note is stored code · disconnect marks `untracked` · the owner's change audited · a viewer refused | 1 each |
| `test_fix_audit_live.py` (3) | the click names the repository · a support view's stripped paths · the draft read's purpose | 1 each |
| `test_fix_write_storage.py` (3) | 0083's frozen list · the one-draft condition · the settings in the inventory | 1 each |
| `test_fix_write_gate.py` (6) | never builds a go · only the gate assigns it · only the recorded go passed · no default go · the gate imports nothing · every write path asks | 1 each |
| `test_fix_pull_request_text.py` (7) | a public body's login, figures and note · the code span · the note's address · a banned template word · CI on open's marker | 1 each |
| `test_fix_draft_rules.py` (2) | both write permissions · F3's own rule names | 1 each |
| Web (12) | `FixFiles.test.ts`: the writes-off sentence, the draft check's body, the link step, the confirm's digest, the public note, the opened link, delete; `FixRepositorySettings.test.ts`: the PUT body, read-only roles, the CI warning; `FixAppPanel.test.ts`: the raising warning; `finding-page.test.ts`: the timeline sentence | 1 each |

**Clocks:** scheduler suites one `now` per test, read when it starts, passed to the check, the job, the sweep (`only_org`) and the deletion; API suites the suite's per-test `now`; the routes read the real clock only for the 15-minute window, which the tests set relative to `now`. **Bounded waits:** no test polls; every GitHub call crosses the fake through the guarded client with its timeout. **Live rows:** every scheduler test builds and drops its own organization; every API test runs in a transaction rolled back after it, so no job it adds is committed for the stack's workers; the scheduler tests call each job's code directly and commit no job.

### Validation, 2026-11-22

Against this step's own containers only: `f5s7-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, network `f5s7-net`, volume `f5s7-pgdata`, migrated to 0083 by `nexovue_api.setup`) and `f5s7-clickhouse` (`clickhouse:26.3.20.7-jammy`, ports 38123 and 39000, volume `f5s7-chdata`, migrated by the workers' setup, ClickHouse only), through a wrapper refusing any other store port. Both were removed at the end, with their volumes and network.

- **F5's suites and every gate touched, one run (`-n 2`): 6,861 passed; 24 errors**, all `tools/ai-eval/fix/test_seed_findings_live.py`, whose ClickHouse address pointed at the native port; with the address corrected and ClickHouse migrated, **that suite: 25 passed** (its first run in F5). The run covered all `test_fix_*` (API, scheduler, platform; `test_fix_restore_docker.py` is the release check, not run), `packages/py-platform/tests`, `tools/ai-eval`, `test_github_link_live.py`, AC33's walk (`test_fix_facts_hold_no_ai.py`, `test_fix_rooms_import.py`), `test_ai_vocabulary.py`, `test_alert_vocabulary.py`, cross-service contracts, migration rules, both tenancy structure tests, authorization coverage, read surface (structure, spec, live), rate-limit coverage, keys, deletion, personal data, members, policy, insight grants, compose contract (with `POSTGRES_INSIGHT_PASSWORD` unset), audit, projects, cases, F22's simulated GitHub suites (`test_connections_*`, recordings), beat, metrics. After the review's fix (the stored note confirmation) and the documents: the Step 7 suites, 71 passed; then **the same full set again: 6,886 passed, no failure or error** (ClickHouse migrated).
- `uv run mypy .`: no issues in 1,279 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,692 files formatted. Biome on `findings`, `aisettings`, `settings` and the Playwright spec: no errors. `pnpm -r --if-present run typecheck`: passed. `verify-tokens.js` and `verify-contrast.js`: passed.
- **The whole web suite, final run: 214 files, 2,675 tests passed.** An earlier full run had 1 failure in a file this step does not touch (`synthetics/EvidenceView.test.ts`, waiting for a lazily loaded editor under load), which passed alone (7 of 7) and in the final run: a timing flake, recorded, not this step's.
- **Not run:** `just check`; the Playwright states (hardening pass); `test_fix_restore_docker.py` (release check); any real provider or real GitHub (the founder's, after Phase 2); T7.11.

### Deferred to F5's hardening pass

- Playwright at 375 and 1280 px for the draft's three states (written in `fix-section.spec.ts`, not run), browser and phone checks, performance (the draft job's 12 to 20 calls), customer pages (T9.5's draft paragraphs), Design QA, the founder's *Judgeable* on a real scratch repository (T7.11, AC22a).
- AC54 (a draft beside a detection job, no deadlock) and AC58 (a case idle past 90 days with a draft open) have no test yet (decision 103).

### Review (`/review-diff`), 2026-11-22

Every changed file is named above. **One MEDIUM found and fixed:** the job assumed any stored note had been confirmed for a public repository, so a repository private at the click and public (and allowed) by the job would have received an unconfirmed note; the confirmation is now stored (`requester_note_confirmed`) and the job passes it, with a test seen failing. Recorded, not fixed (LOW): decisions 93, 94 and 97 to 99. Tenancy: every new scheduler statement binds `org_id` (the sweep's is a named exception); every API query binds the organization; the new routes are in authorization coverage, off the read surface, under `/findings`' and `/connections`' tiers. No token, sealed value or login is stored, logged or audited.

## Step 6 (code), built 2026-11-22

**What it ships.** The evaluation's code, with no real run: a harness that reads the fixture repository through **the pack job's own reader**, builds **the AI job's own request**, and judges every answer with **the job's own code**, its one retry and its rounds; the attacks T6.1 names (A1, A3, A4, A5, A6, A19) and harmless controls in four arms; scoring for AC21's outcomes and safety gate, AC38's figures and AC89's counts; the report generator; T6.3's K6 and K8 replay over commit history; and the switch Steps 7 and 8 read, **off**. Every provider here is a fake: they test the harness and say nothing about any model. No real provider was called (foundation item 31), no real GitHub was used, only the fixture repository was read, and nothing wrote to any repository (item 33).

- **No migration.** No table, column or list changes; `test_ai_vocabulary.py` needs no entry.
- **T6.1, the harness (built; the runs are the founder's).** `tools/ai-eval/fix/eval_pack.py`: `LocalRepository` answers the six read routes a pack uses from `repository/` on disk, with git's own blob ids, and `source_pack.PackReader` chooses each pack (stack, one hop of imports from the top two, tests; a later round's asked-for names through the lists). `eval_attacks.py`: 16 moves across the six attacks, each the answer a model would give if the planted instruction won (A1 is forged frames on the input). `eval_runs.py`: each first-round input recorded once with a fingerprint of its request (the fence tag left out, as it is drawn per call), replayed across options; four fakes (`fake-answer-key`, `fake-abstaining`, `fake-garbled`, `fake-obeying`); the real seam `http_provider`, the AI role's format and transport, **not run**; at least 20 trials of each attack and 20 harmless controls in each arm, about 200 calls per option. `eval_scoring.py`: each trial reached a person, was refused by the rules, abstained, was unusable, failed, or had nothing to send; every proposal read again for ways out; evidence ids resolved; the seeded test after applying the proposal (Python only, with a timeout); AC38's and AC89's counts. `eval_report.py`: the skeleton (`docs/research/f5-evaluation-2026-11.md`, no number), the filled form from saved trials, and the founder's `run` and `render` commands.
- **T6.2 (waiting):** each guess replaced by what was seen needs T6.1's real numbers.
- **T6.3, the replay (built; the run waits).** `tools/ai-eval/fix/replay_rules.py` replays K6 and K8 through `checks.added_line_breaks` (made public with `addresses_in_file`) over a local clone's non-merge commits, in files a suggestion could change, with the file before each commit giving K6 its known addresses; counts only, never the repository's code. Skeleton: `docs/research/f5-k6-k8-replay-2026-11.md`. The run needs the founder's choice of a consenting repository. No K6 or K8 notice exists yet (T9.3), so "stay off until read" holds (decision 89).
- **T6.4 (waiting):** the founder's approval, an engineer who did not write the fixtures, and one consenting real repository, read only.
- **T6.5, the gate (recorded, not passed).** `nexovue_platform/fixes/write_gate.py` (ADR-097): `WRITE_ACCESS_GO = None`, `write_access_on()` False; a `WriteGo` must name the options that passed AC21's safety gates, the buyers' answer and the terms. *Blocked* carries the matching line; `test_fix_write_gate.py` holds the two equal.

**Shared files touched, and why (kept minimal).** `apps/scheduler/.../connections/source_pack.py` (F5's): `_Reader`, `_Context` and `_Reading` became `PackReader`, `PackContext`, `PackReading`, reading through the `RepositoryReads` protocol, so the bed's packs are the product's (ADR-098); no behaviour changed. `packages/py-platform/.../fixes/checks.py` (F5's): `added_line_breaks` and `addresses_in_file` made public for T6.3; no rule changed, so `CHECKS_VERSION` stays. `tools/ai-eval/fix/fixtures.py`: `run_seeded_test` moved here from `test_fixtures.py`, now with a timeout. `docs/decisions/README.md` (the index), `docs/product/phase-2-tracker.md` (F5's row). F3, F4, F22: none.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run with `-x`, and the file restored and compared byte for byte (`seefail.py`, this session's scratchpad, `f5s6-own`); every restore compared equal. 49 breaks, each `-x`, so 1 is the first failure.

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_eval_pack.py` (6) | git's own blob id · `__pycache__` left out · the product's order with frames as numbers · without imports drops imported files · without rounds starts at the last round · a later round lists refused asked names | 1 each |
| `test_eval_runs.py` (12) | record once · a moved input refused · the fingerprint leaves the fence tag out · trial counts reach 20 · the one retry · the next round sends the asked file · the last round cannot ask · nothing sent with no file · a failed call ends the trial · seconds from the clock given · the answer key's refusal reason · the real seam's model | 1 each |
| `test_eval_scoring.py` (15) | a later round abstains (the moves) · asked-for refusals counted · a kept-back file sent · a new address read again · a file never sent changed · an unresolved evidence id · an option with no trial has not passed · controls blocked · a seeded refusal case proposed · the edited file's role · estimated tokens flagged · the proposal applied for the seeded test · one seeded run per proposal · a timed-out seeded test · Python seeded tests only | 1 each |
| `test_fixtures.py` (1) | the seeded test's timeout | 1 |
| `test_eval_report.py` (6) | the skeleton current · the skeleton says "Not given" · saved trials read back · a fake labelled · a failed gate says leave it out · a local model's machine shown | 1 each |
| `test_replay_rules.py` (6) | known addresses from the file before · files a suggestion could not change skipped · hunks only † · `git` timeout · the skeleton current · counts per rule | 1 each |
| `test_fix_write_gate.py` (3) | off until the go · a go must name the buyers' answer · status and code move together | 1 each |

† first passed and was redone, recorded honestly: the test's look-alike line (`+++counter`) had no space after `+++`, so it never looked like a header; it now reads `+++ b/looks_like_a_header.py`, and the break fails it. `source_pack.py`'s and `checks.py`'s renames change no behaviour; their existing suites passed unchanged (below).

**Clocks:** every trial's seconds come from a clock the test passes (`clock` fixture, one second per reading, made per test); no test reads the real clock. **Bounded waits:** no test polls; each seeded test (60 s), each `git` command (30 s in the tests, 120 s in the replay) and the `git hash-object` check carry a timeout. **No live rows:** the new tests use no store.

### Validation, 2026-11-22

Against this step's own container only: `f5s6-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, network `f5s6-net`, volume `f5s6-pgdata`, migrated to head by `nexovue_api.setup`), through a wrapper refusing any other store port. No ClickHouse was started.

- **F5's suites and every gate touched, one run (`-n 2`): 5,231 passed.** It covered all `test_fix_*` (API, scheduler, platform; `test_fix_restore_docker.py` is the release check and was not run), `test_github_link_live.py`, AC33's walk (`test_fix_facts_hold_no_ai.py`, `test_fix_rooms_import.py`), `test_ai_vocabulary.py`, cross-service contracts, migration rules, both tenancy structure tests, authorization coverage, and `tools/ai-eval`. Two collection errors: `tools/ai-eval/fix/test_seed_findings_live.py` needs ClickHouse, which was not started (as at Step 5); it imports `fixtures.py`, whose change only adds `run_seeded_test`. After the review fix: `tools/ai-eval` and the platform's checks and gate tests, 459 passed (1 skipped on purpose: the ClickHouse seeder).
- `uv run mypy .`: no issues in 1,265 files (after clearing a cache a partial run had left stale). `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,676 files formatted. Web not touched.
- **Not run:** `just check`; any real provider (the founder's, after Phase 2); real GitHub; the ClickHouse seeder's live test; T6.3 on any real repository.

### Deferred to F5's hardening pass

- Seeded tests for TypeScript and Java (no Node or JDK in the gate; decision 83), and fixture cases for the `slower` and `traffic` signals (decision 84).
- The bed's retry of a failed call (decision 88).

### Review (`/review-diff`), 2026-11-22

No HIGH or MEDIUM finding left open. Fixed: `SEEDED_TEST_SECONDS` sat between `Reach` and `REACHES`, and its comment said a timeout "counts as failed" (the scorer reports `timed_out`); the real seam's 120-second timeout is now a named constant. Recorded, not fixed (LOW): the `run` and `render` command lines are not tested (they wrap tested functions, and `run` is a real call); a later round's request carries no frames, which the bed mirrors (decision 81). Unintended changes: none; every shared file is named above.

## Step 5, built 2026-11-21

**What it ships.** A member who can read a written suggestion clicks **Prepare patch**: their own check run, on `scheduler-connections` with a read-only token, re-reads each changed file from the default branch's head, re-checks the display facts against the file as it was read, runs K1 to K10 again and stores the patch on their own check row; the page then shows that very patch's diff, marking files that moved, and a **Download patch** link served from their row for 15 minutes. A refusal on the same rules, list and files ends the suggestion and alerts (decision 52). Every written suggestion, of either kind, asks **"Was this suggestion useful?"** (decision 25), changing nothing else. Stored code is taken back at every end BR9 names, plus a deleted project and a removed member (ADR-094), and the backup and restore keep the promise (ADR-095). **No call writes to a repository** (foundation item 33); no real provider is called (item 31).

- **Migration 0082** (`0082_ai_solution_patch.py`, `down_revision "0081"`, roll forward only): `finding_fixes.verdict`, `verdict_by` (composite key to `users`, `SET NULL`), `verdict_at`, with their CHECK; `project_deleted` in `reason` and `code_deleted_reason`; the out-of-date codes and `checks_refused_at_click` in `finding_fix_checks.reason`; `connection_repositories.unreachable_since`. No table, index or function; no list `test_ai_vocabulary.py` holds is widened, so it needs no revision entry. `clear_stored_code` needs no new column: none of 0082's holds code (`test_fix_patch_storage.py` holds that).
- **T5.1, the check run for a patch** (ADR-096). `connections/fix_check.py` dispatches by purpose; `check_rows.py` holds the row's end, shared with `read`; `fix_patch.py` the patch steps; the pure decision in `nexovue_platform/fixes/recheck.py`; `diff.patch_hunks` reads a stored patch back. Out-of-date comparisons that need no file (rules, the owner's list, the code location, the likely cause by rule and fact id) run before any read; a file past the client's 1 MiB is `edit_no_longer_applies`; the store is conditional and taken under the suggestion's row lock, so a withdrawal cannot be overtaken (AC29a's check half). Refused again: `failed`, `checks_refused_at_click`, code deleted, `fix.checks_refused` audited (`at: check_run`), a warning; beat reads the last hour's count (`nexovue_scheduler_fix_checks_refused_recent`) and `NexovueSuggestionRefusedAgain` opens a ticket (rule, promtool test, runbook in `docs/operations/ai.md`).
- **T5.2, the routes and section.** `POST .../check` takes `{"for": "patch"}`; `GET .../patch` (`MayReadFixCode`: a person with `REQUEST_FIX`, never a token or a support view; audited `fix.patch_downloaded`); `POST .../verdict` for both kinds (`findings/fix_verdict.py`); rung 2b's `POST .../patch` placeholder removed, AC73 now held on the `GET`. The read gains `patch_action`, `patch` and `verdict`, the rebuilt diff with `moved_note`, an out-of-date row's old diff with no action (`read_stale`), and **Code deleted** with when, why and until when backups hold it. Web: `PreparePatch` in `FixFiles.tsx`, `FixVerdict.tsx` (a radio group), the notes in `FixDiff.tsx`, rung 2b's question in `FixGeneral.tsx`, `api.ts` hooks and `patchDownloadUrl` (a plain link; no status read outside `api-client.ts`).
- **T5.3, taking it back** (ADR-094). `findings/fix_takeback.py`: one `end_and_clear` for an owner's withdrawal (`fix_consent.py` now uses it), F22's disconnect hook (registered through F22's `register_disconnect_hook`; consents withdrawn `disconnected`), a deleted project (`projects.service.delete`), a removed member (`members.service.scrub`: link, attempts and their check rows). The sweep's GitHub-access-gone pass (72 hours from `unreachable_since`, or an App not installed), which withdraws consent `access_gone`; F22's refresh keeps `unreachable_since`. The owners' one email when consent ends by itself (`fixes/notify.py`, `FIX_CONSENT_ENDED`, naming what changed). Verdicts in the personal-data export.
- **T5.4, the backup.** `POSTGRES_ROWS_NOT_KEPT` in `common.sh`; `--exclude-table-data` for each; left out of the manifest.
- **T5.5, the restore.** `restore-postgres.sh` runs `clear-stored-code.sql` after `pg_restore` (written by `statements.restore_sql()`, held equal by `test_fix_backup_scripts.py`): code deleted again, work in flight `failed`, every consent withdrawn; `nexovue restore` already stopped both F5 roles. `docs/operations/backup-and-restore.md` states the crash window. `test_fix_restore_docker.py` is the release check (`release_check` marker, root `pyproject.toml`), **written, not run**.

**Shared files touched, and why (kept minimal).** F22: `connections/service.py` (the hook registry refuses a second copy of a hook), `scheduler/connections/tasks.py` (`unreachable_since` written by the refresh). F6/F1: `projects/service.py` (one call), `members/service.py` (the inline link deletes moved into F5's `forget_person`), `test_projects.py` (its fake session answers F5's read with no rows), `test_deletion_live.py` (F5's rows in the fixture). Shared: `auth/policy.py` (`MayReadFixCode`), `profile/personal_data.py`, `profile/schemas.py`, `email/sender.py`, `email/templates.py`, `metrics.py`, `deploy/backup/*`, `deploy/monitoring/*`, `pyproject.toml` (the marker), `docs/features/personal-data/spec.md` (the export list), `docs/operations/ai.md`, `backup-and-restore.md`, the authorization, tenancy, migration-rules and general-route tests. F3, F4: none.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run, and the file restored and compared byte for byte (`seefail.py`, this session's scratchpad, `f5s5-own`); every restore compared equal. 64 breaks, each `-x`, so 1 is the first failure.

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_fix_patch_check_live.py` (15) | the patch stored on the own check · moved detected · a moved file no longer applying is stale, not refused · past the client's cap · rules, list, location stale before reading · tamper clears code · display facts against the base · the owner's requirement before any read · a withdrawal before the store · one person's check only · a consent ended by itself does not stop it | 1 each |
| `test_fix_recheck.py` (12) | the digest over the head's blob † · gone, unreadable, not a regular file · an unreadable base · the mode kept · what a read refuses is refused again | 1 each |
| `test_fix_diff.py` | the patch reads back its own text | 1 |
| `test_fix_patch_routes_live.py` (15) | Prepare is a patch check · link first · deleted code cannot be prepared · the download audited · another person's check never served · past 15 minutes · a support view refused · the moved diff · a refused check says why · out of date shows the old diff · code deleted says when, why, until · a verdict changes nothing else · a viewer cannot answer · an unwritten suggestion · a general one on the same route | 1 each |
| `test_fix_takeback_live.py` (9) | an owner's withdrawal · the hook registered · a disconnect withdraws consent · a tracker disconnect touches nothing · project deletion · member removal takes their checks · the export lists verdicts · the restore's statements withdraw consent · the audit words held equal | 1 each |
| `test_fix_backup_scripts.py` (6) | the restore SQL is the one list · the dump excludes the tables · the manifest leaves them out · the restore empties after `pg_restore` | 1 each |
| `test_fix_access_gone_live.py` (10) | access gone 72 hours · under 72 hours keeps · an App not installed · the clock keeps the first miss · it clears when the repository answers · F3's automatic off keeps code · a consent ended by a new model keeps code · stale keeps code until 30 days · each owner told, once · unchanged terms tell nobody · the changes named in order | 1 each |
| `test_scheduler_metrics_live.py`, `test_fix_patch_storage.py`, `test_deletion_live.py` | the gauge counts only the last hour's refusals · 0082's frozen words · an F5 table left out of the inventory | 1 each |
| `FixFiles.test.ts`, `FixGeneral.test.ts` | Prepare posts a patch check · the download is the person's own link · a running check offers no second Prepare † · the verdict posts its answer · rung 2b asks too | 1 each |

† first passed and was redone, recorded honestly: the digest break went unseen because an unmoved file's head and base blob are the same (the moved test now asserts the digest); the first "no second Prepare" break rendered a button with no label (redone so it shows the label). **Clocks:** pure tests use fixed values; scheduler suites one `now` per test, passed to the job, the sweep and `write_reach`, every sweep and notifier narrowed to the test's own organization (`only_org`); API suites the suite's per-test `now`. The notifier's `ended_notice_sent_at` is the database's `now()`, asserted only as set. **Bounded waits:** no test polls; the release check's subprocesses each carry a timeout.

### Validation, 2026-11-21

Against this step's own container only: `f5s5-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, network `f5s5-net`, volume `f5s5-pgdata`, migrated to 0082 by `nexovue_api.setup`), through a wrapper refusing any other store port. No ClickHouse was started.

- **F5's suites and every gate touched, one run (`-n 2`): 6,583 passed; 7 failed in `test_projects.py`** (its fake session had no answer for F5's new read at project deletion; taught it, re-run: 92 passed). It covered all `test_fix_*` (API, scheduler, platform), `test_github_link_live.py`, AC33's walk, both tenancy structure tests, authorization coverage, read surface (structure, spec), rate-limit coverage, migration rules, `test_ai_vocabulary.py`, keys, deletion, personal data, members, policy, insight grants, compose contract (with `POSTGRES_INSIGHT_PASSWORD` unset), cross-service contracts, connections (F22's simulated GitHub suites, recordings), beat, metrics, audit read, projects, `packages/py-platform/tests`, `tools/ai-eval` except its ClickHouse seeder. Then the suites calling the changed services (admin, read surface live, wording, dashboards, assistant privacy, cases, alert defaults, projects live): 253 passed; jobs and email: 26 passed; after review fixes, the patch, check, access-gone, deletion, rooms and AC33 suites: 72 passed.
- `promtool check rules` (26 rules) and `promtool test rules`: success. `shellcheck` on `deploy/nexovue` and the backup scripts: clean.
- `uv run mypy .`: no issues in 1,248 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,653 files formatted. Biome on `src/features/findings`: no errors. `pnpm -r --if-present run typecheck`: passed. `verify-tokens`: passed.
- **The whole web suite: 213 files, 2,662 tests passed.**
- **Not run:** `just check`; `test_fix_restore_docker.py` (release check, by hand); the ClickHouse seeder's live test (no ClickHouse started); any real provider (the founder's, after Phase 2); real GitHub; Playwright.

### Deferred to F5's hardening pass

- The Docker release check (AC6a, AC32) through `nexovue restore` on an installed kit.
- Browser and phone checks of the patch and verdict rows (no Playwright states were asked for at this step), performance, customer pages (`docs/self-hosting/backup.md` does not yet mention F5's tables), Design QA, the founder's *Judgeable* (`git apply` of a downloaded patch on a real scratch repository, after Phase 2).

### Review (`/review-diff`), 2026-11-21

No HIGH or MEDIUM finding left open. Fixed: a suggestion deleted with its finding between the click and the check (or between the read and the store) would have raised in the job (now ends quietly; its check row went with it); a web test's wait returned nothing to wait on (now waits on the line). Recorded, not fixed (LOW): under the owner's requirement, a passed `patch` check made before the owner switched the requirement on can unlock quoted code for its 15 minutes without an access check (decision 67); the owners' notice stamps the database's clock, not a passed one. Unintended changes: none; every shared file is named above.

## Step 4, built 2026-11-21

**What it ships.** A member clicks Send on the file list; the AI role (`scheduler-insight`, on `insight-fix`) reads the files, compares consent again, calls the provider through F3's guarded transport with each file and the hint in their own fence, and stores either a suggestion that passed K1 to K10 (with display facts Nexovue computed from the whole file) or only the rule that refused it. The model may ask for up to 3 more files in rounds 1 and 2; those go back through the pack job and the person's Send. The API reads a written suggestion back without believing it: hunks rebuilt from display facts, the rules that need no file and FR16's prose checks run again, names restored as segments. Viewers and support views read counts only; under the owner's requirement code waits for the person's own **Check my access**. The sweep records each result once (the timeline, `fix.withheld`, D18's tamper re-run) and owes the requester one email, sent from `light`. **No real provider was called** (foundation item 31: the real runs are the founder's, after Phase 2). **Nothing writes to a repository** (item 33).

- **Before Step 4, decision 34:** `/settings/connections/fix-app/return` (`FixAppReturnScreen`, F22's manifest return reused) and `/settings/github-link/return` (`GitHubLinkReturnScreen`: posts the code, follows the attempt for a bounded 30 polls, returns the person to the finding remembered in `sessionStorage`, `shared/github-link-return.ts`). Routes in `app/router.tsx` and `console-routes.json`.
- **Migration 0081** (`0081_ai_solution_suggestion.py`, `down_revision "0080"`): `finding_fixes.reason` gains `not_in_these_files`, `edit_did_not_apply`, `withheld_K1` to `withheld_K10`, `checks_refused_at_click`; `retry_code` gains `source_shape`, `source_misquote`, `source_never_changed`; `finding_fix_checks.reason` gets its CHECK; `fix_drop_pack` (ADR-091). Roll forward only. No table, column, sealed column or inventory change; `clear_stored_code` already covers every source column.
- **T4.1:** `test_fix_rls_spike_live.py` runs the job's own statements (`source_fix_store`) as `nexovue_insight`: consent, frames, pack dropped first and counted, the end; a late write and a late drop match nothing; only the function goes back to `gathering`. Passed, so no `SECURITY DEFINER` fallback beyond ADR-091 was needed.
- **T4.2:** `INSIGHT_GRANTS` gains `connection_repository_id, folder, round` (read) and `edits, withheld_refs, requested_paths` (write) on `finding_fixes`, a column read of the pack, FR8's consent columns, and `EXECUTE` on `fix_request_more_files` and `fix_drop_pack`. Applied by `setup`; Compose memory and caps were set at T2b.5.
- **T4.3:** `nexovue_platform/fixes/source_prompt.py` (instructions holding the never-changed list, schema per round, retry sentences, the trusted document of numbers); the job wraps each file and the hint in F3's existing `Untrusted` (no F3 change needed).
- **T4.4, T4.5, T4.13:** `insight/propose_fix.py`, `insight/source_fix_store.py`, `fixes/proposal.py` (judge: parse, `needs_files` with shown-line and prose checks, K1 to K10, retry by code, display facts). `suggest_general_fix.py` shares its provider-answer half (`read_answer`, `why_not_send`) and `fix_store` gains `FixOfProject` and `model_input_of`, so both jobs use F3's `reserve_month`, `insert_run` and the guarded `settle_run` once.
- **T4.6, T4.15:** `FixDiff.tsx` (one disclosure per file, gutter, hunk headers, `<ins>`/`<del>` as text, visible escapes, flags jumping to lines, the model's explanation set apart), `FixFiles.tsx` (the written suggestion, files read, not checked, tokens and age; Check my access; the asked round), `fix-page.ts`, `api.ts`, the timeline's source sentence.
- **T4.7:** `findings/fix_proposal.py` (role-shaped read, `Out of date` by `checks_version`, rules and prose at read through `fixes/stored.py`, alias restore, the owner's requirement, first code view audited once), wired into `fix_service._view`; `GET /fixes/source` carries it.
- **T4.8:** `connections/fix_sweep.py`: stuck `proposing`, results recorded once with the tamper re-run (`fix.checks_refused`), `fix.withheld` with paths resolved through `files_read`, `email_due`; the pack job marks the first listing and the model's round; `fixes/notify.py` on `light` (three fixed templates in `nexovue_platform.email`), beat's `sweep:fix-requesters`.
- **T4.9:** `test_fix_nothing_runs_by_itself_live.py`, `test_fix_detection_unaffected_live.py` extended; `apps/scheduler/tests/test_fix_source_never_leaks_live.py` new (decision 48).
- **T4.10:** `fix-section.spec.ts` Step 4 states, written, not run.
- **T4.11:** not built (decision 43).
- **T4.12:** `connections/fix_check.py` (`read` only) and `POST /findings/{id}/fixes/{fix_id}/check` (`findings/fix_checks.py`), one route for both kinds (decision 45).
- **T4.14:** Send moves to `proposing`, keeps the pack for the job, audits `fix.sent` (paths, blob ids, host, round; the hint's presence, never its words), enqueues `propose_fix` with ids only; one try per suggestion (only round 1 spends one); every round lapses after 15 minutes.

**Shared files touched, and why (kept minimal).** F3: `insight_account.py` (grants, two functions); none of F3's job code. F22: `settings/GitHubReturnScreen.tsx` (its manifest return made reusable; F22's behaviour unchanged). F4: none. Shared: `email/sender.py`, `email/templates.py` (three messages), `jobs/registry.py`, `beat.py`, `data/postgres/models.py` (one CHECK), the authorization, tenancy, cross-service, grants and beat tests, `test_fix_general_routes.py` (the `/check` path moved), the route manifest and its test.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run, and the file restored and compared byte for byte (`seefail.py`, this session's scratchpad, f5s4-own); every restore compared equal. Database behaviour (the spike) was broken in the own Postgres and the function recreated from 0081's text. Counts are what the run printed (`-x`, so 1 is the first failure).

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_fix_proposal.py` (24) | display facts from the file · K3 retried · asked line within lines sent · last round may not ask | 1 each |
| `test_fix_stored.py` (15) | K1 folder · line rules · prose · K5 | 1 each |
| `test_fix_propose.py` (13) | pack dropped first · consent terms · consent folder · the more-files function called · fields sent | 1 each |
| `test_fix_model_request.py` (3) | files fenced as untrusted · no path in the trusted part | 1 · 1 |
| `test_fix_hostile_answers.py` (20) | K7 on hidden characters | 1 |
| `test_fix_sweep_records_live.py` (5) | tamper re-run · withheld audited · stuck `proposing` · email owed | 1 each |
| `test_fix_notify.py` (4) | taken once · the box cleared | 1 · 1 |
| `test_fix_check_live.py` (5) | refusal status · refusal audited · withdrawn consent | 1 each |
| `test_fix_source_never_leaks_live.py` (1) | the hint in a log line | 1 |
| `test_fix_proposal_routes_live.py` (8) | viewer shape · out of date · refused at read · owner's requirement · first view once · check repeat | 1 each |
| `test_fix_source_routes_live.py` | `fix.sent` audited · tries at Send | 1 · 1 |
| `test_fix_source_pack.py` | the asked reason's prose re-check | 1 |
| `test_fix_insight_grants_live.py`, `test_insight_grants_live.py` | `folder` not granted · an extra write column | 1 · 1 |
| `test_fix_rls_spike_live.py` (11) | the more-files function · `fix_drop_pack` without its state guard (in the database) | 1 · 1 |
| `test_fix_suggestion_storage.py` (7) | a reason not frozen in 0081 · the model's check-run reasons | 1 · 1 |
| `test_cross_service_contracts.py`, `test_fix_nothing_runs_by_itself_live.py`, `test_fix_detection_unaffected_live.py`, `test_beat_schedule.py`, `test_scheduler_postgres_tenancy_structure.py`, `test_authorization_coverage.py` | notify importing connections · a second builder · the proposal's queue · notify's queue · `_DUE` unnamed · the check route ungated | 1 each |
| `FixFiles.test.ts` (11), `FixDiff.test.ts` (4), `GitHubReturns.test.ts` (6), `finding-page.test.ts` | `<ins>`/`<del>` · Check my access body · the asked round · the title · bidi escaped · the Fix App's route (2) · back to the finding · the timeline's source sentence | 1 each, the route 2 |

Two breaks first passed and were redone (recorded honestly): K5's first break left the line cap in force (redone by removing the refusal); the second-builder break only imported the name, which the AST walk does not count as use (redone with a use). **Clocks:** pure tests use fixed values; the check and sweep suites a fixed `now`; the proposal suites take one `now` per test, passed to the job and the sweep, and each sweep and notifier run is narrowed to the test's own organization (`only_org`). **Bounded waits:** the link page polls at most 30 times; no test waits without a bound.

### Validation, 2026-11-21

Against this step's own container only: `f5s4-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, network `f5s4-net`, volume `f5s4-pgdata`, migrated to 0081 by `nexovue_api.setup`), through a wrapper refusing any other store port. No ClickHouse was needed.

- **F5's suites and every gate touched, one run (`-n 2`): 6,321 passed after one fix** (the first run found `test_fix_general_routes.py` still listing `/check` on rung 2b's router; corrected, re-run: 9 passed). It covered all `test_fix_*` (API, scheduler, platform), `test_github_link_live.py`, AC33's walk, both tenancy structure tests, authorization coverage, read surface, rate-limit coverage, migration rules, `test_ai_vocabulary.py` (no revision entry needed: 0081 widens no list it holds), keys, deletion, personal data, members, policy, insight grants, compose contract (with `POSTGRES_INSIGHT_PASSWORD` unset), cross-service contracts, beat, F22's simulated GitHub suites, F3's wording and check, `packages/py-platform/tests`, `tools/ai-eval` (without the ClickHouse seeder). Then F3's and F4's account, budget and limit suites, `test_detection_has_no_ai.py`, `test_jobs_live.py`, email: 1,731 passed; route links: 213 passed.
- `uv run mypy .`: no issues in 1,234 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: passed. Biome on `src/features/findings`, `settings`, `app/router.tsx`, `shared/github-link-return.ts`, `fix-section.spec.ts`: no errors. `pnpm -r --if-present run typecheck`: passed. `verify-tokens`: passed.
- **The whole web suite: 213 files, 2,657 tests passed** (after `console-routes.json` gained the two routes).
- **Not run:** `just check`; any real provider (never, by instruction: the founder's after Phase 2, item 31); real GitHub; `fix-section.spec.ts`; AC53.

### Deferred to F5's hardening pass

- `fix-section.spec.ts`'s Step 4 states (AC37d, AC36), written, not run; the diff's wrap marker (decision 49).
- The real runs on a provider (T4.4's *Judgeable*): **not yet run: the founder's, after Phase 2.**
- Browser and phone checks, performance, customer pages, Design QA, the founder's *Judgeable*.

### Review (`/review-diff`), 2026-11-21

No HIGH or MEDIUM finding left open. Fixed during the build: a third-party table `DELETE` the design granted the AI account (now ADR-091's function); the `/check` route shadowed by rung 2b's router, so a source row could never be checked (one route now); a notifier and sweep in tests reaching other tests' rows (`only_org`); literal bidirectional and Cyrillic characters in test sources (now escapes). Recorded, not fixed (LOW): a call refused for F5's share at admission ends the row through F3's shared statement, leaving its pack for the sweep's next pass (within a minute) rather than dropping it first; `read_source` commits a person's first code view inside a `GET`, by design (AC57). Unintended changes: none; every shared file is named above.

## Step 3, built 2026-11-21

**What it ships.** An admin creates the *Nexovue suggestions* App (read only) and sets where each service's code lives; an owner agrees, per repository and folder, to send that code to the AI provider as it is now, and answers whether quoted code needs a linked GitHub account. A member clicks **Ask for a suggested fix**: Nexovue reads this service's stacks (or the paths they type), and `scheduler-connections` reads the matching files from GitHub, filters, caps and scans them, adds one hop of imports and test files, and **stops** at a list the person can trim, with each file's reason and a GitHub link. **Send stops at a stated guard: nothing goes to a provider in Step 3,** and no AI job is ever enqueued. All against the simulated GitHub (foundation item 28); no model was called.

- **T3.1, storage.** Migration **0079** (`0079_ai_solution_source.py`, `down_revision "0078"`): on `finding_fixes`, the source columns without pull request columns, `source` in `kind`, the states `gathering`, `gathered`, `stale` and Step 3's reasons, the CHECK that a general row holds no repository, folder, files, edits or candidates (AC75, decision 27), candidates only while gathering, `round` 1 to 3, the code-deleted reason, `explanation_version` nullable for a source row; the one-in-flight index widened to every working state and built `CONCURRENTLY` (`uq_finding_fixes_one_working`); `finding_fix_source_packs` (row security for the AI account while `proposing`), `finding_fix_checks`, `finding_fix_code_views`, `service_code_locations`, `fix_source_consents`, `github_user_links`, `github_link_attempts`; on F22's `connections`, `github_client_id`, `sealed_client_secret`, `granted_permissions`, `granted_at`, and the disconnected check grows; `brief_paths` in `job_results`; `fix_request_more_files` (D75; `EXECUTE` revoked from `PUBLIC`, granted to the AI account at T4.13). Composite keys throughout; inventory entries (`organizations/tables.py`); two sealed columns in `keys.SEALED_COLUMNS`. Roll forward only.
- **T3.2, permission.** `OPEN_FIX_PULL_REQUEST` for owner, admin, member, `MayOpenFixPullRequest`, and the five copies.
- **T3.3, the Fix App** (ADR-082). `connections/fix_app.py` and `connections/fix/router.py` (manifest, its return, refresh, the panel's read); `nexovue_scheduler/connections/fix_app.py` (conversion keeping the client id and sealing the secret; the refresh binding the installation, repositories via F22's `write_reach`, and the grant); `fix_github.py`, F5's client: one method per route, fixed hosts, no redirect, no write route (`ROUTES`).
- **T3.4, code locations** (ADR-083). `findings/fix_locations.py`, `GET`/`PUT`/`DELETE /code-locations`, audited per row, all or none naming the refused row; `features/settings/FixAppPanel.tsx` (`CodeLocationsPanel`).
- **T3.5, consent** (ADR-084). `findings/fix_consent.py`, `nexovue_platform/fixes/consent.py`, `GET .../source-consent`, `PUT`/`DELETE .../{repo}/source-consents` with exactly one of `folder=` or `scope=whole-repository`; FR7's sentences as one constant; withdrawal ends work in flight and deletes its stored code through `CLEAR_STORED_CODE`, saying how many.
- **T3.6, panels.** `features/aisettings/FixConsentPanel.tsx` (consent list, the confirm with nothing preselected, and `FixReadinessPanel` over `GET /fix-readiness`), `features/settings/FixAppPanel.tsx`; mounted on `/settings/ai` and `/settings/connections`. Loading, failed, empty and no-permission states; an em dash for unknown.
- **T3.7, eligibility.** `findings/fix_eligibility.py` (FR9 rows 1 to 12; `source_eligibility`; rung 2b only at a code-access reason, D67), `findings/fix_access.py` (Postgres-only code access), `findings/fix_state.py` (one read for rung 2b's and rung 3's routes); `fixes/sentences.py` gains every Step 3 sentence. Decision 22's interim line is removed: `no_code_location` now reads its whole sentence.
- **T3.8, candidates.** `findings/fix_paths.py`; `errorgroups.service.recent_for_service` and `GROUP_SERVICE_OCCURRENCES_SQL`, bound to the finding's service.
- **T3.9, T3.11, T3.16, the routes.** `findings/fix_service.py` on `findings/fixes/source/router.py`: `GET /fixes/source`, `POST /fixes` (the row, `fix.requested` and the pack job in one transaction; an aged-out or empty stack stores `refused`), `/files`, `/send` (digest-bound 409 with the new list; top-file confirmation; then Step 3's guard), `/cancel`, `POST /brief/files` and its result.
- **T3.10, T3.18, T3.19, the pack job.** `nexovue_scheduler/connections/source_pack.py` with the pure `fixes/matching.py`, `fixes/pack.py`, `fixes/imports.py`: consent, the owner's requirement before any blob, ancestry, the folder's subtree only, filters, caps in order, windows, secret scan, imports one hop, test files, asked-for names checked against the pack as it stands, one transaction with one `repository.read` entry naming the person.
- **T3.12, the section.** `FixFiles.tsx` (`SourceSection`), `FixSection.tsx`, `fix-page.ts`, `api.ts` (`useSourceFix` polling while reading); `document.title` while a list waits; `fix-section.spec.ts` gains rung 3's width state (written, not run).
- **T3.13, the sweep.** `nexovue_scheduler/connections/fix_sweep.py` (`fixes.sweep_fixes` on `connections`): stuck and lapsed rows, ended rows' code cleared once, AI off and 30 days, short-lived rows, the Fix App's hourly refresh; beat's `sweep:fixes` while connections are on; rung 2b's sweep reads general rows only.
- **T3.14.** `test_fix_detection_unaffected_live.py`: rungs 1 and 2 unchanged with a source row present and AI off; rung 3's queues are `scheduler-connections`' alone.
- **T3.15.** `test_fix_rooms_import.py` (AC5's two walks, AC6b); AC4 stays held by `test_compose_contract.py`.
- **T3.17, the account link.** `connections/user_link.py` (hashed state bound to the session's person, sealed code, ids-only job), `nexovue_scheduler/connections/fix_link.py` (one exchange, no token kept, the code gone in the same transaction); export in `profile/personal_data.py`, deletion in `members/service.py`; `audit/read.py` drops `paths` and `files` from these entries during a support view (AC11).

**Shared files touched, and why (kept minimal).** F22: `connections/queries.py` (`kind` parameter; the overview lists tracker kinds only), `connections/service.py` (`sealed_box`, `hash_of_state`, made public for the Fix App), `scheduler/connections/github.py` (`GitHubApp.signed_jwt`), `scheduler/connections/tasks.py` (`write_reach` made public), `tests/tracker_fake.py` (the installation's grant, the OAuth exchange and `GET /user`; `client_id` on conversion), `fixtures/github/repository.json` (`default_branch`, marked unverified), `ConnectionsScreen.test.ts` (stubs the new panel). F3: none. F4: none. Shared: `audit/read.py`, `audit/router.py`, `audit/service.py`, `keys.py`, `organizations/tables.py`, `profile/*`, `members/service.py`, `errorgroups/*`, `main.py`, `jobs/registry.py`, `beat.py`, the authorization, tenancy, migration-rules and cross-service tests, `test_keys_live.py`, web `identity.ts` and copies, `team-and-api-access/spec.md`, the finding page's render harness.

**Not built in Step 3, and why** (decisions 34, 39, 42): the console's two GitHub return screens, the separate checklist component, owner emails and `notify_requesters`, and the 72-hour lost-access withdrawal.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run, and the file restored and compared byte for byte (`seefail.py`, in this session's scratchpad); every restore compared equal. Counts are what the run printed. † marks a test that first passed and was strengthened before it failed. Three guards that no test could reach were removed rather than kept untested: an import climbing out of the folder, and `..` or an address in an asked-for name, can match nothing because only a path in the folder's own listing is ever named.

| Test | Behaviour broken | Failed |
|---|---|---|
| `test_fix_imports.py` | Relative Python import base · Go `_test.go` skipped | 1 · 2 |
| `test_fix_matching.py` | Two-segment minimum · an ambiguous suffix dropped · build folder to source | 2 · 1 · 1 |
| `test_fix_pack.py` | Secret scan · NUL byte · 12-file cap · 64 KiB cap · window around the line · an unmatched name is a count | 1 each |
| `test_fix_consent.py` | A sibling sharing a prefix · the key id compared | 1 · 1 |
| `test_fix_eligibility.py` | Row 10 (`no_consent`) · rung 2b only at a code-access reason · naming files at `cause_is_elsewhere` | 3 · 3 · 1 |
| `test_fix_paths.py` | Third-party frames dropped · the read bound to the service · Java's qualified path | 2 · 1 · 1 |
| `test_fix_source_pack.py` (26) | Consent compared · the owner's requirement before any blob · ancestry · imports one hop · a removed file kept out · the state guard † · the moved-on-default label | 1 each |
| `test_fix_sweep_live.py` (6) | The 15-minute lapse · code cleared when AI is switched off | 1 · 1 |
| `test_fix_app_live.py` (5) | The client secret kept, sealed · the link's code dropped in its transaction | 1 · 1 |
| `test_fix_github_client.py` (4) | No redirect followed | 1 |
| `test_fix_source_routes_live.py` (21) | Send bound to the digest · the top-file confirmation · a typed `..` refused · an aged-out stack stored as `refused` · a viewer reads no list · the link step · cancel neutral · a removal marked | 1 each |
| `test_fix_consent_live.py` (23) | Owner only · exactly one scope · withdrawal ends work in flight · a support view reads no owner paths · a project's override wins · the refused row named · readiness counts project findings | 1 each |
| `test_fix_source_storage_live.py` (10) | AC75's CHECK dropped in the own database | 3 |
| `test_fix_source_storage.py` (16) | A reason left out of 0079's frozen list | 1 |
| `test_github_link_live.py` (4) | Paths kept from a support view's audit · the link left out of the export · the link left at member removal | 1 each |
| `test_fix_detection_unaffected_live.py` (4) | Rung 2's brief coupled to a source row | 1 |
| `FixFiles.test.ts` (7), `FixConsentPanel.test.ts` (5), `FixAppPanel.test.ts` (2) | Send without the digest · Agree before the access answer · an unread grant shown as "none" | 1 each |

**Clocks.** Pure tests use fixed datetimes; live scheduler tests take one `now` per test, passed to the job and the sweep, and the sweep is narrowed to the test's own organization (`only_org`), so a test's sweep never reaches another test's rows; API live tests use the suite's `now` fixture; web tests fake `Date` only.

### Validation, 2026-11-21

Against this step's own container only: `f5s3-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, migrated to 0079 by `nexovue_api.setup`), through a wrapper refusing any other store port. ClickHouse was not needed (the stack read is replaced by a stand-in answering as the service-bound read does) and none was started.

- **F5's suites and every gate touched, one run: 5,222 passed.** All `test_fix_*` (API, scheduler, platform), `test_github_link_live.py`, F22's `test_fake_github_matches_recordings.py`, `test_connections_github_live.py`, `test_connections_sync_live.py`, `test_connections_have_no_ai.py`, `test_connections_live.py`, `test_connections_webhook_live.py`, `test_connection_model.py`, `test_no_tracker_calls_in_api.py`, `test_beat_schedule.py`, `test_cross_service_contracts.py` (every `queries.py` read as ClickHouse SQL), `test_authorization_coverage.py`, `test_read_surface_structure.py`, `test_read_surface_spec.py`, `test_ratelimit_coverage.py`, both tenancy structure tests, `test_migration_rules.py`, `test_deletion_live.py`, `test_keys_live.py`, `test_personal_data_live.py`, `test_members_live.py`, `test_policy.py`, `test_auth_routes.py`, `test_findings.py`, `test_alert_vocabulary.py`, `tools/ai-eval` (without the ClickHouse seeder). Then `test_audit.py`, `test_audit_read_live.py`, `test_compose_contract.py` (with `POSTGRES_INSIGHT_PASSWORD` unset), rung 2b's sweep and `packages/py-platform/tests`: 1,644 passed.
- `uv run mypy .`: no issues in 1,172 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,566 files formatted. Biome on `src/features/findings`, `settings`, `aisettings`, `members`, `src/shared` and `fix-section.spec.ts`: no errors. `pnpm -r --if-present run typecheck`: passed. `verify-tokens`: passed.
- **The whole web suite: 211 files, 2,624 tests passed.**
- **Not run:** `just check`; real GitHub and any model (never, by instruction); `fix-section.spec.ts`; the ClickHouse seeder's live test.

### Deferred to F5's hardening pass

- `fix-section.spec.ts`'s rung 3 state (AC37d), written, not run.
- Browser and phone checks, performance, customer pages, Design QA, and the founder's *Judgeable* for Step 3 (a scratch repository on real GitHub, after Phase 2: foundation item 28).

### Review (`/review-diff`), 2026-11-21

No HIGH finding left open. Fixed during the build: a stale-digest Send rolled the transaction back before reading the new list, which expired the caller's identity mid-request (MEDIUM; now read in the same transaction, `test_ac3b_…` proves it); rung 2b's sweep would have recorded and ended source rows (its passes now read general rows only); a helper named `test_file_for` was collected by pytest as a test (renamed `conventional_test_file`); the in-flight index replaced on a table with rows is built `CONCURRENTLY`. Recorded, not fixed (LOW): `GET /findings/{id}/brief/files/{request_id}` binds the organization and the request id but not the finding (request ids are random and organization-bound); F4's `members/service.py` `scrub` calls `assistant_privacy.forget_person` twice (found while adding the GitHub link's deletion; harmless, F4's to remove). Unintended changes: none; every shared file is named above.

## Step 2b, built 2026-11-21

**What it ships.** Where Nexovue cannot read a service's code, a member (owner, admin, member; `REQUEST_FIX`) picks a language and clicks **Suggest a general fix**. The AI role sends F3's `model_input` for the latest version, F5's fixed instructions and the language, as one trusted part; checks the answer against G1 to G9; and stores it, or only the rule that refused it. The API checks it again on every read and shows it under the label "Example pattern, not your code. Nexovue has not seen your code. Check before using." No copy control, patch, pull request, email or alert. With fake providers only: **no real provider was called.**

- **T2b.1, storage.** Migration **0077** (`0077_ai_solution_general.py`, `down_revision "0076"`): `finding_fixes` with rung 2b's columns; CHECKs on kind (`general` only until Step 3), state, reason, retry code, language (FR33's list, required for a general row), an answer only at `proposed`, at most 8 KiB, a reason exactly on `refused` and `failed`; the one-in-flight unique index (BR8); composite keys to the finding (cascade), the project (cascade) and the requester (`SET NULL (requested_by)`); row-level security (the AI account reads rung 2b's states and changes a row only while `proposing`); `fix_proposed` added to the timeline's frozen kinds; F3's three job functions widened to `insight-fix`. Roll forward only (`test_migration_rules.py`). `FindingFix` model; deletion inventory (`finding_fixes` → `findings`). No sealed column, so `SEALED_COLUMNS` is unchanged; no personal-data export entry, since the row holds no person's words (the click's audit entry is in the export's activity already).
- **T2b.2, permission.** `REQUEST_FIX` (`request_fix`) for owner, admin, member, in `permissions.py`, `policy.py` (`MayRequestFix`) and the five copies: `test_policy.py`, `test_auth_routes.py`, web `identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, and `team-and-api-access/spec.md`.
- **T2b.3, the rules.** `fixes/general_rules.py` (G1 to G9, pure, all or nothing; G1 and G2 retried once as `invalid_output`, G3 to G9 `general_withheld_Gn` at once) and `fixes/general_lists.py` (standard-library import lists for the ten languages, process-start and dynamic-code names per language, shell words, package managers, install words). Reused, not copied: K7's hidden and look-alike letters and K6's addresses (`patterns.py`), U4's email and markup and BR11's words (`prose.py`, made public), K8's opaque runs, K9's secrets, K2's build files.
- **T2b.4, the request.** `fixes/general_prompt.py`: instructions, schema, `PROMPT_VERSION = "fix-general-1"`, 1,500 output tokens, and `request_document` (`model_input`, `language`, and on a retry one fixed sentence by code). **It holds text only:** the job wraps it in F3's `AIRequest` (`suggest_general_fix.general_request`), because the `fixes` package is inside AC33's walk and may import no AI module (see *Open decisions*, 16).
- **T2b.5, the queue and grants.** `insight-fix` (`fixes/records.py`): `scheduler-insight` now claims `assistant,insight,insight-fix` on 8 slots, caps `assistant` 4, `insight` 3, `insight-fix` 2 (D56), 768 MB (`roles.py`, both Compose files, `worker.py`, `queue.py`). `INSIGHT_GRANTS` gains `finding_fixes` columns: SELECT ids, kind, language, version, state, retry code and `tokens_used` (each attempt adds to it); UPDATE of the end columns; never the requester or the stored answer. F3's grants test lists them.
- **T2b.6, the job.** `insight/suggest_general_fix.py` with `insight/fix_store.py`: the row must be `proposing`; connection, consent (F3's `consent_covers`), opt-out and deployment read again; the key opened for this address; F5's share reserved through F3's `reserve_month` under the row's lock, with the run row (F3's `insert_run`) in the same transaction; one call through F3's guarded transport; G1 to G9; F3's `settle_run`, which ends the run first and settles only a run still `started`, so a call finishing after F3's sweep called its run `died` moves no tokens, stores no answer and returns `stale` (F3's [wording settles once](../../changes/wording-settles-once/bug.md), carried over when this step was put on it; `test_a_call_finishing_after_f3_s_sweep_ended_its_run_changes_nothing`). `ai_runs` purpose `fix`, `fields_sent` `["model_input", "language"]`, `subject_id` the finding. A fault writes `failed`, `nexovue_error` before it is raised, with no body.
- **T2b.7, the API.** `findings/fix_eligibility.py` (FR9's order to the code-access reasons, then FR31's gate; pure), `findings/fix_general.py`, `findings/fix_queries.py`, and **a router of its own**, `findings/fixes/general_router.py`: `GET /findings/{id}/fixes/general` (line, offer, latest checked again with names restored as segments, earlier), `POST /findings/{id}/fixes/general` (409 with the reason's code; the row, the audit entry `fix.general_requested` naming the person and the provider host, and the job with ids only, in one transaction), and `/check`, `/patch`, `/draft-pull-request`, `/run-ci` refusing a general row with `general_has_no_code`. `fixes/sentences.py` holds every rung 2b sentence. `GET /findings/{id}/fixes` loses its `section` field: the line is rung 2b's read's (ADR-075).
- **T2b.8, the screen.** `FixGeneral.tsx` (label first, lead, summary and steps with names as segments, the example as text in a focusable labelled region, the facts it rests on, tokens and age; no copy control), `FixSection.tsx` (its own read, `useGeneralFix`, polling every 5 s only while `proposing`; one always-present status line; its own failure in place while the brief still renders), `fix-page.ts` words, the language list through the shared `SelectField`, and the timeline's `fix_proposed` sentence.
- **T2b.9, the sweep.** `nexovue_scheduler/fixes/sweep.py` on `light`, every minute, always (a row asked for before AI was turned off must still end): `proposing` past 15 min + 300 s + 40 s + 30 s ends `failed`, `timed_out`; each ended row is recorded once, `fix_proposed` (ids and counts) or `fix.withheld` (the rule alone). `TASK_MODULES`, beat's `sweep:general-fixes`, `test_cross_service_contracts.py`, and both statements named in the scheduler tenancy test.
- **T2b.10, the bed.** `tools/ai-eval/fix/general/` (cases, runner, test) and `docs/research/f5-general-bed-2026-11.md`. **The real run is not yet run: the founder's, after Phase 2.**
- **T2b.11.** `test_fix_nothing_runs_by_itself_live.py` (AC2 from source; AC1 live) and `test_fix_detection_unaffected_live.py` (AC33's AI arms for this rung; NFR4 structurally).

**Shared files touched, and why (F3 and F4 changes kept minimal).** F3: `ai/limits.py` gains `FIX_SHARE` (0.5) and `fix_ceiling`, and `reservation` takes an optional output limit; `insight/store.py` makes `insert_run` public and moves its settling into `settle_run`, which `finish_wording` now calls; `insight/word.py` makes `consent_covers` and `open_key` public; `findings/explanations.py` reads aliases back through the shared `restored_segments` and asks `evidence.facts_may_be_sent` (its own `_causes_allow_wording`, moved unchanged); `insight_account.py` grants; `nexovue_platform/evidence/aliases.py` gains `ALIAS_TOKEN` and `restored_segments`. F3 and F4 together: `worker.py` caps (`INSIGHT_CONCURRENCY` 4 → 3), `queue.py`, `roles.py`, Compose, and F4's capacity test `test_jobs_live.py` now proves two full queues never keep the third waiting. F22: the timeline's kinds (`detection.py`, `cases.py`, `schemas.py`, the web timeline). F5's own Step 2: `patterns.py` IPv4 (below), `prose.py` names made public.

**A bug found and fixed in Step 2's code.** K6's IPv4 pattern refused a match followed by any dot, so an address ending a sentence ("Point it at 10.0.0.1.") passed both K6 and the prose checks. A test was added first and seen failing (1), then the pattern fixed: a dot is part of the address only before another digit. F3's and Step 2's suites pass unchanged, and the rules report is still current.

### Tests, each seen failing

Each behaviour was broken by a scripted edit, its test run, and the file restored and compared byte for byte (`seefail.py`, in this session's scratchpad). Database behaviours were broken in the own Postgres (a constraint, index, policy or function dropped or replaced), the test run, and the object recreated. Counts are what the run printed. † marks a test that first passed, was strengthened, and then failed.

| Test (count) | Behaviour broken | Failed |
|---|---|---|
| `test_fix_general_rules.py` (107) | G1 exact keys · evidence ids known · language picked · abstention empty · G2 summary cap · line count · whole answer † · G3 prose controls · mixed script · G4 addresses · G5 shell words · process starts · JS template strings · G6 imports · `node:` prefix · install words · build files · G7 secrets · opaque runs · G8 "your code" · banned words · source names · G9 alias in code · unknown alias | 1 · 1 · 1 · 1 · 1 · 1 · 1 · 2 · 1 · 5 · 5 · 9 · 1 · 9 · 1 · 5 · 2 · 2 · 1 · 2 · 3 · 2 · 1 · 1 |
| `test_fix_prose.py` (31) | An IPv4 address before a full stop (the bug above) | 1 |
| `test_fix_sentences.py` (48) | A banned word in a sentence · a reason with no sentence · the label's words | 1 · 1 · 1 |
| `test_fix_facts_hold_no_ai.py` (3) | An AI import in `general_rules.py` | 1 |
| `test_fix_general_routes.py` (22) | AI off · evidence pending's window · cause elsewhere · three tries · in flight · the not-a-problem closings · the read taking `CurrentReader` | 1 each |
| `test_fix_general_storage.py` (7) | A reason left out of 0077's frozen list | 1 |
| `test_fix_general_storage_live.py` (15) | One-in-flight index dropped · answer-only-when-proposed CHECK · language CHECK · the finding's cascade | 1 · 2 · 2 · 1 |
| `test_fix_rls_spike_live.py` (8) | Update policy `USING (true)` · `requested_by` granted | 1 · 1 |
| `test_fix_insight_grants_live.py` (3) | The claim function without `insight-fix` | 1 |
| `test_insight_grants_live.py` (8) | `tokens_used` not granted | 1 |
| `test_fix_general_request.py` (3) | An extra field in the request · the job reading `evidence` instead of `model_input` | 2 · 2 |
| `test_fix_general_job.py` (11) | Consent compared · opt-out read again · F5's share · fields sent · retry reads its code · 5xx retried · refusal mapped · fault writes `failed` · a left row (state check, the lock's state and both row policies, together †) | 1 each; the last 1 |
| `test_fix_general_hostile_answers.py` (18) | G1/G2 never retried · G3 to G9 retried · refused answer's ids stored | 5 · 13 · 13 |
| `test_fix_general_sweep_live.py` (5) | Stuck window † · event recorded · withheld audited · recorded once | 1 · 1 · 1 · 3 |
| `test_fix_general_routes_live.py` (26) | Offer line · `ai_off` gated · hosted · held causes · tries count answered rows † · audit's host · enqueue · viewer shape · support view as viewer · read re-checks (AC71) · names restored · earlier reading · actions refuse · unknown fix 404 | 1 · 1 · 1 · 1 · 1 · 1 · 2 · 2 · 1 · 2 · 1 · 1 · 4 · 5 |
| `test_fix_nothing_runs_by_itself_live.py` (3) | Beat naming the task · a read inserting a row | 1 · 1 |
| `test_fix_detection_unaffected_live.py` (2) | Rung 2's brief coupled to an in-flight row · the `insight-fix` cap | 1 · 1 |
| `test_beat_schedule.py`, `test_cross_service_contracts.py`, `test_insight_role.py`, `test_jobs_live.py`, `test_roles.py`, `test_compose_contract.py`, `test_finding_events.py` | Sweep on the wrong queue · job module in `TASK_MODULES` · statements missing `insight-fix` · `insight-fix` uncapped (three-queue capacity) · old queues and slots · 576 MB · `fix_proposed` without `step_count` | 1 · 1 · 1 · 3 · 1 · 5 · 1 |
| `tools/ai-eval/fix/general/test_general_fixtures.py` (13) | Refusals not by rule · the gate ignoring a leaked name · 5 hostile runs | 1 · 1 · 1 |
| `FixGeneral.test.ts` (12) | Label after the lead · code region not focusable · viewer offered · sending with no language · refusal sentence hidden · status line empty · earlier list hidden · a banned word in a state | 1 each |
| `FixSection.test.ts` (16), `fix-page.test.ts` (7), `finding-page.test.ts` (54) | Section always shown · rung 2b's failure not shown in place · the timeline's `fix_proposed` words | 5 · 1 · 1 |

**Not proven failing, and why:** `check_general`'s size check on a parsed answer (G2 on a stored row) cannot be reached by an answer within every field's cap, so no fixture breaks it alone; it stays as a guard on rows written by hand. `test_ac74_no_chat_or_mcp_tool_reads_a_suggestion` holds because no tool exists; it fails the day one names a `/fixes` route. **Clocks:** pure tests use fixed datetimes; live tests take one `now` per test (`fix_clock`, `now` fixtures) and pass it to the job and the sweep; the API routes read the real clock, as F3's do; web tests fake `Date` only and pass `now` to the formatters.

### Validation, 2026-11-21

Against this step's own containers only: `f5s2b-postgres` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, migrated to 0077 by `nexovue_api.setup` with a temporary, never-committed 0076 stand-in that was removed after each run), through a wrapper refusing any other store port. No ClickHouse was needed and none was started.

- **F5's suites and the gates touched**, each run and read: all `test_fix_*` (API, scheduler, platform), `tools/ai-eval` (199, without the ClickHouse seeder), `test_fix_facts_hold_no_ai.py`, `test_fix_platform_holds_no_ai.py`, `test_cross_service_contracts.py`, `test_authorization_coverage.py`, `test_ratelimit_coverage.py`, `test_read_surface_structure.py`, `test_read_surface_spec.py`, `test_postgres_tenancy_structure.py`, `test_scheduler_postgres_tenancy_structure.py`, `test_migration_rules.py`, `test_alert_vocabulary.py`, `test_evidence_vocabulary.py`, `test_findings.py`, `test_deletion_live.py`, `test_personal_data_live.py`, `test_keys_live.py`, `test_insight_grants_live.py`, `test_ai_settings_live.py`, `test_findings_wording_live.py`, `test_compose_contract.py`, `test_roles.py`, `test_policy.py`, `test_auth_routes.py` (3,496 passed in one run, then compose's 99 apart, below); F3's and F4's `test_insight_word_live.py`, `test_insight_check_live.py`, `test_insight_limits_live.py`, `test_insight_account_live.py`, `test_insight_role.py`, `test_assistant_budget_live.py`, `test_assistant_account_live.py`, `test_beat_schedule.py`, `test_detection_coordinator.py`, `test_jobs_live.py`'s capacity test (233 passed); `packages/py-platform/tests` (1,521 passed).
- `test_compose_contract.py`'s password test fails when the shell exports `POSTGRES_INSIGHT_PASSWORD` (it was, in the first wrapper); with the variable unset it passes (99). An environment artefact, not a change.
- `uv run mypy .`: no issues in 1,116 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,498 files formatted. Biome on `apps/web/src/features/findings`, `src/shared`, `src/features/members` and `tools/e2e/tests/fix-section.spec.ts`: no errors. `pnpm -r --if-present run typecheck`: passed. `verify-tokens` (`packages/ui/scripts/verify-tokens.js`) and `verify-contrast.js`: passed.
- **The whole web suite: 205 files, 2,561 tests passed** (before the timeline sentence and the `SelectField` change, whose files were re-run: 54, 12 and 16 passed).
- **Not run:** `just check`; any real provider (never, by instruction); the bed's real run; `fix-section.spec.ts`; the founder's *Judgeable*.

### Deferred to F5's hardening pass

- **`fix-section.spec.ts`'s rung 2b states (AC78)**, written, not run: offered, being written, proposed and refused at 375 and 1280 px with a 120-character code line and a long host, and a poll-driven change moving neither focus nor scroll. Its 44 px check is expected to fail on the shared `SelectField` (about 34 px tall) and `CopyButton`, as T1.6's does (*Open decisions*, 3).
- **The bed's real run (T2b.10, AC76):** the founder's, after Phase 2, on a free tier or a local model.
- Browser and phone checks, performance, customer pages, Design QA of the section, and the founder's *Judgeable* on the demo stack.

### Review (`/review-diff`), 2026-11-21

No HIGH or MEDIUM finding left open. Fixed during the build: a JSON `null` written for an absent answer (SQLAlchemy's `JSONB` default), which the database's CHECKs would have refused on every click (now `JSONB(none_as_null=True)`); a bare "fix proposed" the timeline would have shown for the new kind; literal bidirectional, zero-width and Cyrillic characters that had reached two test sources, now escapes. Recorded, not fixed (LOW, decisions below): F3's "View what was sent" rebuilds only the explanation request, so a `fix` run reads "Cannot be reproduced" (19); F3's AI sweep settles a died `fix` run only while AI is on (20); a support view's `POST` is refused by the shared identity dependency, not by this route (21).

## Step 2, built 2026-11-11

**What it ships.** No model, no GitHub, no migration, no ADR, no screen. The module every later step rests on, proved against hostile fixtures first:

- **The lists** (T2.1, `nexovue_platform/fixes/policy.py`): the source-extension allow-list, the hard never-sent list (secrets and keys, CI, agent instructions), the configuration-shaped list an owner may loosen by named path, and the never-changed list, with `CHECKS_VERSION`. Matching is on the whole path, case-insensitive, at any depth.
- **The rules** (T2.2, T2.3, `fixes/checks.py`, `fixes/patterns.py`): the schema guards first (outcome, at least one edit, unchanged edits, C0 controls, NUL and lone surrogates, oversized answers, display-fact keys, unknown evidence ids, a `why` failing the prose checks), each an unusable answer with a fixed retry code; then K1 to K10 on the whole file, every refusal named by rule and detail; then FR18's six flags, each naming its file and line, and the paths whose language the flag lists do not cover.
- **The diff** (T2.3, `fixes/diff.py`): line changes worked out per region an edit touches, numbered hunks with 3 lines of context, a git-format patch with blob ids, mode, quoted paths and the no-final-newline marker; D53's display facts and the hunks derived from them alone; their shape check; and the digest (SHA-256 over each changed file's path, base blob id and new content in path order, each field length-prefixed, no head sha).
- **The secret patterns** (T2.4, `fixes/secrets.py`): private key headers, 12 token prefixes after gitleaks' default rules (read 2026-11-11), and long random values assigned to secret-named keys. A net, not a boundary.
- **The prose checks and the deletion statement** (T2.5, `fixes/prose.py`, `fixes/statements.py`): U4 (URL, email, Markdown or HTML, host names and IPs anywhere), hidden and control characters, the 300-character cap, aliases restored through F3's `restore` or refused, BR11's words replaced by the fixed sentence; `CLEAR_STORED_CODE`, one statement over Step 3's source columns and tables, binding `org_id` in each part.
- **The bed's parts** (T2.6 to T2.8, T2.11, `tools/ai-eval/fix/`): a fixture repository of 12 seeded bugs in three services (Python, TypeScript, Java), with the bug in the stack, one import away, or neither, three where a refusal is right, and two with planted instructions (asking for `.env` and a workflow edit; asking for a call to an outside address); a span seeder writing each case's stack in its language's form; the rules report, `docs/research/f5-rules-report-2026-11.md`, 26 planted changes and what stopped each, generated and checked current by its test; and the traceability check, now in the gate.
- **The no-AI, no-I/O walk** (T2.9) and **the simulated GitHub's git data and pull requests** (T2.10): F22's fake gains refs, commits, recursive trees that can be cut short, blobs, `compare`, a person's permission, a user by id, pull requests by `?head=`, drafts, the 422 on a second pull request, token permissions held on these routes, and every write droppable after it is applied. Its answers are compared with shaped recordings in `fixtures/github_fix/`; `record_github.py` will replace them with GitHub's own.

**Shared files touched, and why.** Root `pyproject.toml` (named by T2.11): `tools/ai-eval` joins `pythonpath` and `testpaths`, and the fixture repository is excluded from ruff and mypy, because it is made-up customer code with bugs seeded on purpose. F22's `apps/scheduler/tests/tracker_fake.py` (named by T2.10): F5's routes added; F22's routes, names and behaviour unchanged, except that a dropped-write fault now also covers `DELETE` (F22 has none). The git object store lives in a new `github_git_data.py` beside it, to keep the fake readable. Two base fixtures (`fixtures/github/user.json`, `pull.json`) join F22's folder, marked unverified as F22's are. F22's live suites over the fake pass unchanged (80).

**Files.** New: `packages/py-platform/src/nexovue_platform/fixes/policy.py`, `checks.py`, `patterns.py`, `diff.py`, `secrets.py`, `prose.py`, `statements.py`; `packages/py-platform/tests/test_fix_policy.py`, `test_fix_checks.py`, `test_fix_diff.py`, `test_fix_secrets.py`, `test_fix_prose.py`; `apps/api/tests/test_fix_platform_holds_no_ai.py`; `apps/scheduler/tests/github_git_data.py`, `test_fake_github_matches_recordings.py`, `fixtures/github_fix/` (22 files), `fixtures/github/user.json`, `pull.json`; `tools/ai-eval/fix/` (`__init__.py`, `conftest.py`, `fixtures.py`, `cases.json`, `repository/`, `seed_findings.py`, `rules_report.py`, `record_github.py`, `check_traceability.py` and five tests); `docs/research/f5-rules-report-2026-11.md`. Changed: `pyproject.toml`, `apps/scheduler/tests/tracker_fake.py`, this file, `plan.md`, `docs/product/phase-2-tracker.md`.

### Tests, each seen failing

Each behaviour was removed from the file by a scripted change, the named test run, and the file restored and compared byte for byte with its saved copy. 80 removals; every one failed. Counts are what the run printed. Two removals first passed, which showed two missing tests; those tests were added and the removals then failed (marked †). A review finding (K6, below) was written as a test first and seen failing (3 failed) before the fix.

| Test (count) | Behaviour removed | Failed |
|---|---|---|
| `test_fix_policy.py` (45) | Matching at any depth · case-insensitive · the owner's named allowance · the extension allow-list · build files never changed | 4 · 4 · 2 · 3 · 11 |
| `test_fix_checks.py` (97) | K1 folder · K2 · K3 exactly once · K3 overlap · K4 emptying · K4 file-operation key · K5 · K6 · K6 "already in the file" · K7 hidden · K7 mixed script · K8 · K8 joined words · K8 hex threshold · K9 · K10 long line · K10 blank run | 1 · 4 · 1 · 1 · 1 · 1 · 1 · 8 · 1 · 3 · 2 · 2 · 1 · 1 · 1 · 1 · 1 |
| | Schema: display-fact key · control characters · unchanged edit · evidence ids · `why` · oversized | 3 · 3 · 1 · 2 · 2 · 2 |
| | Flags: new import · risky call · comment · new letters · removed check · test file | 4 · 3 · 3 · 1 · 1 · 1 |
| `test_fix_diff.py` (365, 300 of them random layouts and 40 applied by real git) | No-final-newline marker · mode in header · path quoting · base blob in digest · digest length prefix · a region taking in the next line † · context cap · facts shape check · new-letter lines · only `\n` ends a line † | 15 · 1 · 1 · 1 · 1 · 1 · 1 · 9 · 1 · 1 |
| `test_fix_secrets.py` (29) | Token prefixes · assigned values · their randomness | 15 · 4 · 1 |
| `test_fix_prose.py` (30) | Banned words · addresses · email · markup · alias not in the book · cap · hidden characters · alias restored · a column left out of `CLEAR_STORED_CODE` (pinned to the design's list) · its tenant binding | 6 · 4 · 1 · 3 · 2 · 1 · 1 · 2 · 1 · 1 |
| `test_fix_platform_holds_no_ai.py` (4) | A network client imported by `checks.py` | 1 |
| `test_fixtures.py` (86) | The seeded bug fixed · a frame's line wrong · the planted `.env` ask removed · a reach misnamed | 2 · 1 · 1 · 1 |
| `test_rules_report.py` (29) | K6 removed from the rules · the report edited by hand | 3 · 1 |
| `test_check_traceability.py` (10) | The lone-half rule · task ranges † · unknown tasks · unplanned criteria | 2 · 1 · 2 · 1 |
| `test_record_github.py` (4) | A step left out · names left uncleaned | 1 · 1 |
| `test_fake_github_matches_recordings.py` (31) | Trees never truncated · a second pull request allowed · token permissions not held · `draft` ignored · blob ids not git's · a dropped write not applied | 1 · 1 · 1 · 2 · 1 · 5 |
| `test_seed_findings_live.py` (25) | No exception event · Python frames in the wrong order · no spans for the service | 12 · 1 · 24 |

**Clocks.** No test reads the wall clock for a judgement: the rules and diff are pure; the fake's commits carry a fixed author time; the seeder takes `end` as an argument, and its live test fixes it once per run, relative to now, because ClickHouse's own `received_at` window must hold it.

### Validation, 2026-11-11

- **F5's and the nearby suites, one run: 1,146 passed.** All of `test_fix_*` (Step 1's and Step 2's), `test_fake_github_matches_recordings.py`, F22's `test_connections_github_live.py` and `test_connections_sync_live.py` (80, over the extended fake), all of `tools/ai-eval`, `test_cross_service_contracts.py`, `test_scheduler_postgres_tenancy_structure.py`, `test_read_surface_structure.py`, F3's `test_evidence_rules.py`. Against this step's own containers, `f5s2-pg` (`pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, migrated to `0069` with alembic) and `f5s2-ch` (`clickhouse:26.3.20.7-jammy`, port 38123, 40 ClickHouse migrations by the Go setup), both removed afterwards.
- `uv run mypy .`: no issues in 965 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,339 files formatted. Biome over `tools/ai-eval`, the new fixtures and `docs/research`: no errors (one warning, on the existing 3.1 MiB `detection_parity.json`). The web is not touched, so `pnpm typecheck` was not run.
- `check_traceability.py` over the real documents: 215 ids, 0 problems.
- **gitleaks 8.30.1** (the version of the history scan, `docs/changes/ready-to-deploy`) over every new and changed file: no leaks. The fake values are assembled at run time, so **`.gitleaksignore` needs no entry.** For review all the same: `test_fix_secrets.py` (`FAKES`, `fake()`, the body `Qz7Wn2…`), `test_fix_checks.py` (an `AKIA…` key built from parts, a 44-character base64-like string, a SHA-256 hex string), `rules_report.py` (`_FAKE_KEY`, `_ENCODED`), `test_record_github.py` (`"ghs_" + "Q" * 36`), `test_fake_github_matches_recordings.py` (`ghs_fake_read_only_token`, `ghs_fake_write_token`, no secret shape). All made up.
- **Not run:** `just check`; real GitHub (never, by instruction); any model; the TypeScript and Java fixtures' seeded tests (no Node test runner or JDK for the fixture repository; they are read, and their reference changes pass the rules); `record_github.py` (only its dry run and cleaning, offline).

### Deferred to F5's hardening pass (founder, 2026-11-11)

- **The founder reading the rules report** (Step 2's *Judgeable*): `docs/research/f5-rules-report-2026-11.md`.
- Browser, phone and Playwright checks, performance, customer documents and Design QA: Step 2 has no screen, so none applies to it yet.
- **Real GitHub recordings** (T2.10): the founder runs `record_github.py` on a scratch repository after Phase 2 (foundation item 28). Until then the fake is proved against GitHub's documents as read, not against GitHub, and `docs.github.com` could not be fetched from the build session: the fixtures cite searches and are marked unverified.

### Review (`/review-diff`), 2026-11-11

- **Fixed, MEDIUM:** K6's "already in that file" was a substring test, so `evil.com` passed in a file holding `devil.com`, and a shorter URL passed inside a longer one. It now compares whole addresses, case-insensitively, read anywhere in the file. Test first, seen failing (3), then fixed.
- **Fixed, LOW:** literal hidden and look-alike characters had reached test sources (a bidirectional override, a zero-width space, a line separator, Cyrillic letters); every one is now an escape, so the repository holds no Trojan-source text of its own.
- Unintended changes: none. Tenancy: the one Postgres statement binds `org_id` in all three parts; the seeder's ClickHouse writes are a dev tool refusing any host but this machine; the reads in the live test go through the API's and F3's tenant-bound readers. No route, migration, Go or web change.

## Step 1, built 2026-11-11

**What it ships.** On the finding page, with AI off and no model, GitHub or consent:

- **Rung 1, inside F28's *Release before this* row** (FR1, T1.2). F28's sentence is unchanged; after it, F5 adds "`<version>` ran before it.", with "CI's last report on it said it failed." (or "rolled back") when CI said so, a link to that release, and past F3's 30-minute look-back, "Longer before this than Nexovue looks for a likely cause." With no earlier version: "No earlier version of this service is known." Nothing is added where F28's own sentence is all there is: no release before, one already running, a release a day or more old, a project finding. Read by a new F28 reader, `releases/queries.py` `period_preceding` (the period of another version before this one, on the same service), from `findings/fix_facts.py`.
- **Rung 2, the brief, one line directly under it** (FR2 to FR5a, T1.1, T1.4). Built by `nexovue_platform/fixes/brief.py` from F3's stored facts: the opening line first, the start time, each signal's numbers, each endpoint under FR3's route rule, the likely cause in its rule's fixed words **only once F3 releases that rule** (as the explanation's own read), the last deploy's version only from a signed deploy in a short version or hash shape, each linked error group's exception type under FR3's shape with a console link at the finding's window, and the repository line. No exception message, log or stack text is read. Copy, the warning sentence, and a native disclosure with the text `select-all`; **when the clipboard write fails, the disclosure opens and the text takes focus** (`CopyButton`'s new `onUnavailable`). Not shown for `false_alarm` or `service_gone`.
- **`GET /findings/{id}/fixes`** (T1.3), on `CurrentIdentity`: a token gets 403 below the route, and it is off `THE_SURFACE`. Postgres only. On hosted, the brief has no repository line and there is no section.
- **The *Suggested fix* section and its jump link** (D54, T1.4): full width below the split, with "Suggested fix ↓" beside *Evidence* in the header, rendered only when this person has a line. **In Step 1 no role has a line**, so no section, heading or jump link renders (AC37e's "no heading" row; see *Open decisions*, 1).
- **No AI module is reachable** from rungs 1 and 2 (T1.5). `lifespan.py` already types the MCP server as a Protocol (F20 D38 landed first), so no change was needed there.

**A shared point touched, and why.** F3's `DEPLOY_LOOKBACK` (30 minutes) was defined in the scheduler's `evidence/rules.py`, which the API cannot import, and FR1's wording uses the same window. It now lives once in F3's shared contract, `nexovue_platform/evidence/__init__.py`, and `rules.py` and `reads.py` import it from there; F3's rules tests pass unchanged (52). F3 Step 4 (`wt/f3s4`) may need a one-line rebase there.

**Files.** New: `packages/py-platform/src/nexovue_platform/fixes/__init__.py`, `fixes/brief.py`; `apps/api/src/nexovue_api/findings/fix_facts.py`, `fix_read.py`, `fix_schemas.py`; `apps/web/src/features/findings/FixSection.tsx`, `fix-page.ts`; `tools/e2e/tests/fix-section.spec.ts`. Changed: `findings/router.py`, `releases/queries.py`, `nexovue_platform/evidence/__init__.py`, scheduler `evidence/rules.py` and `reads.py`, web `findings/api.ts`, `FindingScreen.tsx`, `render-harness.test-util.ts` (a default stub for the new read), `shared/components/CopyButton.tsx`. No migration, no ADR.

### Tests, each seen failing

Each was written with its code, then proved by removing the behaviour from the file (a saved copy, the change, the run, the copy restored, `diff` showing it restored). Counts are what the run printed.

| Test | Behaviour removed | Result |
|---|---|---|
| `packages/py-platform/tests/test_fix_brief.py` (64) | Endpoint rule (route shape) | 18 failed |
| | Host-shaped word check | 1 failed |
| | More-than-three joined words check | 2 failed |
| | Exception type shape | 6 failed |
| | Last dotted segment | 4 failed |
| | Version named only when signed | 1 failed |
| | Opening line | 5 failed |
| | "No explanation has been read yet" | 1 failed |
| | Project finding line | 1 failed |
| | No repository line on hosted | 1 failed |
| | Error group id shape in the link | 1 failed |
| | Cap of 3 error groups | 1 failed |
| | Em dash for a missing reading | 1 failed |
| | Unknown rule points to the console | 1 failed |
| `apps/api/tests/test_fix_facts.py` (20, AC34) | CI's failed or rolled-back words | 2 failed |
| | Look-back sentence | 3 failed |
| | 24-hour bound | 2 failed |
| | Already-running rule | 1 failed |
| | No-earlier-version sentence | 1 failed |
| | Version as a name segment | 1 failed |
| `apps/api/tests/test_fix_routes.py` (2, AC3) | Route takes `CurrentReader` | both failed, and `test_read_surface_structure.py` failed too |
| `apps/api/tests/test_fix_routes_live.py` (11) | Held-cause gate | 1 failed |
| | No brief for `false_alarm`, `service_gone` | 2 failed |
| | No repository line on hosted | 1 failed |
| | Earlier version bound to the service | 1 failed |
| `apps/api/tests/test_fix_facts_hold_no_ai.py` (3, AC33's import half) | An `import nexovue_platform.ai` added to `brief.py` | 1 failed |
| `apps/web/src/features/findings/FixSection.test.ts` (16) | Rung 1 extras not drawn | 1 failed |
| | Brief moved off the deploy line (D54) | 3 failed |
| | Failed copy does not open the brief | 1 failed |
| | Load failure not shown | 1 failed |
| | Jump link always shown | 4 failed |
| | Heading always rendered | 1 failed |
| | Section not rendered | 2 failed |
| `apps/web/src/features/findings/fix-page.test.ts` (5) | Release match | 1 failed (and 1 in `FixSection.test.ts`) |
| `apps/web/src/shared/components/CopyButton.test.ts` (4) | `onUnavailable` never called | 2 failed (and 1 in `FixSection.test.ts`) |

**Not proven failing, and why:** AC11a's "a viewer's and member's page does not mention AI" holds trivially in Step 1, since no AI words exist on this branch; it becomes a real test with the first line (T2b.7). The clocks of every new test are fixed (Python: fixed datetimes; web: `vi.setSystemTime` on `Date` only).

### Validation, 2026-11-11

- This feature's Python tests, one run: **191 passed** (`test_fix_brief.py`, `test_fix_facts.py`, `test_fix_routes.py`, `test_fix_routes_live.py`, `test_fix_facts_hold_no_ai.py`, `test_read_surface_structure.py`). The live suite ran against its own Postgres container (`f5s1-pg`, `pgvector/pgvector:0.8.6-pg18-trixie`, port 35432, migrated to `0069` with alembic), with every row inside a rolled-back transaction; it reads no scheduler-written table.
- Gates near the new route: `test_authorization_coverage.py`, `test_ratelimit_coverage.py`, `test_read_surface_spec.py` (**150 passed**), `test_postgres_tenancy_structure.py` (**2,307 passed**), F3's `test_evidence_rules.py` after the constant moved (**52 passed**).
- `uv run mypy .`: no issues in 938 files. `uv run ruff check .`: passed. `uv run ruff format --check .`: 1,311 files formatted. Biome on `apps/web/src/features/findings`, `apps/web/src/shared/components`, `tools/e2e/tests`: no errors; 4 warnings, all in untouched e2e files. `pnpm -r --if-present run typecheck`: passed.
- The whole web suite, after the review's fixes: **172 files, 2,107 tests passed**, `app/status-interpretation.test.ts` among them (only `shared/api-client.ts` reads HTTP statuses).
- **Not run:** `just check`, other features' live suites (the coordinator's full CI covers them), T1.6's Playwright spec, and the founder's *Judgeable*.

### Deferred to F5's hardening pass (founder, 2026-11-11)

- **T1.6, `tools/e2e/tests/fix-section.spec.ts`:** written, not run. It walks the brief closed and open at 375 and 1280 px with a 200-character endpoint and a long version, asserting `scrollWidth <= clientWidth` and a 44 px copy target.
- Browser and phone checks of the brief's disclosure, focus and copy on a plain-HTTP install; Design QA of the row and the brief against D54; any customer page (none in Step 1).


## Completed

- **2026-11-02:** [`research.md`](research.md): attacker view, competitors, methods compared, GitHub's documents.
- **2026-11-02:** spec, design, plan and this status, read against the code on `feature/anomaly-detection`.
  - The reading **settled two things the research left open:** the demo's faults are coded flags (`tools/otel-demo/upstream/src/payment/charge.js:39-47`), so the fix run needs its own fixture repository; and stored span and log rows appear not to record whether a browser key sent them (a search of migrations, not a read of every table), so every stack is treated as attacker-written.
  - **It found three things nobody had asked about:** a compromised AI process could store a harmful proposal, so the checks run again in the writing room (D18). The copyable brief goes straight into an agent that can run commands, so it holds no attacker-written text (D19). A member with no access to a repository can read quoted code through Nexovue, unless the owner says otherwise (D20).
  - **It found two errors in the foundation:** the room table and rules disagree about who holds `AI_SECRET_KEY`, and a test it calls unbuilt exists (`test_scheduler_postgres_tenancy_structure.py`).
- **2026-11-02:** eight persona reviews, each finding checked, and the documents changed (*Reviews*, below). `plan.md` was rewritten from the current design, and `research.md` gained a *Superseded by the spec* list and sourced rival rows.

## Next action

The founder reads the wording list (*Wording waiting for the founder*) and the customer's page, decides decision 120 (shared touch targets) and decision 125 (the page in the console before approval), and the coordinator commits Step 9. Open decisions 119 to 130 and those still open from Steps 2b to 8 (104 to 118, 90 to 103, 77 to 80, 82 to 89, and 16 to 33, 36 to 38, 40 to 51, 53, 56 to 74 and 76). After Phase 2, the founder runs T6.1, T6.3 and T6.4, records go or stop at T6.5, then T7.11, T8.4, the GitHub trial (which may change or remove Step 8), T9.4's setup timing and T9.7's demo for real, and reads T9.6's six unreachable rivals within 30 days of launch.

## Open decisions

**From Step 9's build, 2026-11-22.** None blocks the commit. Each is built as recommended, except where it says "your call".

119. **A fifth migration (0086, first 0085), for T9.1's sweep alert.** The design asks an alert when `sweep_fixes` has not finished in 15 minutes; the sweep recorded no finish anywhere beat could read (decision 70), and the `jobs` table forgets a failed pass once its attempts are spent. `fix_sweep_status` holds one install-wide time. The alert fires at an age over 900 s held 5 minutes, so a ticket opens 20 minutes after the last finished pass; the 5 minutes keep a fresh install's first pass (age +Inf until then) from being reported. Both figures are guesses. **Recommendation:** keep; amend the design's *Data and migration impact* and *Observability* lines. **Note for the coordinator:** two other builders run beside this one; if either also adds a migration numbered 0085, one must be renumbered at commit.
120. **AC37d's 44 px targets fail on shared components at 375 px** (Playwright, 5 of 28 states): the brief's **Copy brief** (`CopyButton`, 26 px), rung 2b's language picker and button (`SelectField`/`Button`, 36 px), and the draft's buttons (`Button`, 34 px). These components serve every screen, so the root correction (a touch-target size at phone width in `shared/components`) changes every page: **your call, with Design QA** (it extends decision 3). F5's own disclosure was fixed here. **Recommendation:** a separate change giving `Button`, `CopyButton` and `SelectField` a 44 px minimum below the `sm` breakpoint, checked by the layout specs of every screen.
121. **The brief's disclosure is now `flex min-h-11`,** as each file's disclosure in the diff already was; a `summary` displayed as flex loses the browser's triangle marker in some browsers. **Recommendation:** accept; Design QA judges the marker for both.
122. **The summary's counts and route.** `GET /ai/fix-summary` on F3's `/ai` router (`MayManageAI`): requests, tokens and refusals counted by `requested_at`; refusals are rows in `refused` (the rules' and the model's own refusals), not `failed`; verdicts by `verdict_at`; drafts opened as rows with a pull request number, by `approved_at` (there is no opened time); merged by `pull_request_state_at`; notices the newest 50. **No count is unknown today:** every one is a row count or a sum of a never-null column, so the API always gives a number; the panel's em dash covers a number a later API leaves out. **Recommendation:** accept; your wording call on its words (W6).
123. **K6 and K8 notices wait on `notices.REPLAY_REPORT_READ`,** a date in code (decision 89), set by a reviewed commit with T6.3's line when you have read the replay report. **Recommendation:** keep.
124. **NFR2's figure is from this machine** (one uvicorn worker, this step's own Postgres in Docker, on a shared and busy host, one signed-up organization, rows seeded), over HTTP with a real session. Three runs, n = 10 per click, median / slowest: *Suggest a general fix* then its read 131/852, 60/75, 131/388 ms; *Ask for a suggested fix* (typed path) then its read 256/709, 91/311, 141/490 ms; *Was this suggestion useful?* 30/126, 24/106, 35/258 ms. The first click of a run carries the pool's first use. No pass mark (NFR2). **Recommendation:** accept as the figure; measure again on the first deployed host.
125. **The customer's page renders in the console's setup docs once committed,** because `docs/product/setup/` is built into the web app; it carries a "Draft for the founder's approval" banner. **Your call:** approve its wording before Step 9 merges, or have it moved out of `docs/product/setup/` until you do. **Recommendation:** read it before the merge.
126. **The cost example is Anthropic's Claude Sonnet 4.6** ($3 and $15 per million input and output tokens, read on 2026-11-22 from `platform.claude.com`; OpenAI's and Google's pages were unreachable from this session), with **about 4 characters a token, an approximation stated on the page**. About $0.12 a full round, $0.36 for three. **Recommendation:** keep, labelled an estimate; re-read the price before publishing.
127. **ADR numbers.** The design's four ADRs are filed as **ADR-103 to ADR-106** (ADR-102 was the last taken). **ADR-099 is a gap** in this tree (no file, no index line, no reference); it is left unused in case another branch holds it. **Recommendation:** accept; the coordinator checks no other branch filed 103 to 106.
128. **T9.6 is half done:** six vendor hosts were unreachable from this session (proxy 403, no name resolution), so their rows are not re-dated, and OneUptime's Community edition was not installed (disk). AC52 needs every rival claim dated within 30 days of launch anyway. **Recommendation:** you, or a session with open network and disk, read the six and install OneUptime in the 30 days before launch.
129. **Three faults in `fix-section.spec.ts` were the spec's, not the product's** (a section line passed as the general read, an ambiguous heading locator, 44 px asserted at 1280 px), and one in `test_fix_restore_docker.py` (its seed). Each is fixed and its run recorded. **Recommendation:** accept.
130. **Still not built after this step:** Playwright states for Run CI and the facts line (Step 8), tests for AC54 and AC58 (decision 103), decisions 83, 84 and 88, by-hand browser and phone checks, Design QA. **Recommendation:** the next hardening pass.

**From Step 8's build, 2026-11-22.** None blocks the commit. Each is built as recommended.

104. **The pull request poller does not ask the go.** The design gates writes (ADR-097: "every write path calls it first"); the poller only reads (`GET .../pulls/{n}`, `?head=`), builds no `FixWrites` (proved structurally), and writes only Nexovue's own rows. Gating it would leave a draft opened under a go that is later withdrawn reading `open` for ever and holding its finding (BR8). While writes are off no row holds a branch, so it follows nothing. **Recommendation:** keep it ungated; if you want it gated too, it is one `write_access_on(go)` at the top of `poll`.
105. **A fourth migration (0084).** The design says three; 0083's columns cannot tell a Run CI retry from a second click (AC28), cannot recognise its own commit after a crash between storing it and the `PATCH` once a second Run CI has replaced `ci_commit_sha`, and give BR9's "7 days after merged or closed" and FR30's "merged at X" no time, and FR30's deploy no commit. **Recommendation:** keep; amend the design's *Data and migration impact* and `finding_fixes` table.
106. **The plan's `fixes/sweep.py` is built in `connections/fix_sweep.py`.** `fixes/sweep.py` is rung 2b's sweep on `light`, which must import nothing from `connections` (AC6b); rung 3's rows are swept by `connections/fix_sweep.py`. **Recommendation:** amend the plan's T8.2 line.
107. **"Also refreshing permissions and repositories" is already done,** hourly, by the jobs `sweep_fixes` queues since Step 5; the poller does not repeat it. **No plan refresh is built:** no route that reads a plan is known (a trial question), and `draft_refused_at` already lapses after 24 hours (AC27a). **Recommendation:** accept; revisit after the trial.
108. **Run CI's gates** are FR22's without the check run: the person's write access on GitHub (D6), the owner's allowance, the App's write grant and BR13 on GitHub's live answer. Not `no_draft_support` or `needs_likely_cause` (they decide whether a draft may open), and **not consent**: a withdrawal keeps a draft "held and followed" (AC48), and Run CI's commit sends no code anywhere. **Recommendation:** accept.
109. **New wording, your call (foundation decision 2):** Run CI's confirmation ("This runs the model's code in this repository's CI, possibly with its secrets. Read the diff on GitHub first."), its action words, "Asking GitHub to run CI…", "CI was asked for…", "Asking GitHub to run CI did not finish…", its six failure sentences (`CI_FAILURE_SENTENCES`), the commit message, and FR30's three facts-line templates. The 20 minutes after which a request reads as not finished is a guess, as `APPROVED_STUCK_AFTER` is. **Recommendation:** read them on the page with the demo.
110. **"A confirmed 404"** is read as GitHub's 404 for the pull request with a token freshly minted for that repository: `untracked` at once. A repository the App can no longer mint a token for (removed, uninstalled) is skipped and settled by the sweep after 72 hours; a transferred repository may answer a redirect, which the client does not follow, and is skipped too (from memory, unverified). **Recommendation:** accept; the trial's recordings settle what a deleted or transferred repository returns.
111. **A held branch whose pull request's head is not Nexovue's commit reads `untracked`,** freeing the finding: Nexovue cannot say the pull request is its own. **Recommendation:** accept.
112. **Merged and closed are final for the poller:** a pull request reopened on GitHub is not followed back to `open`, since another draft may by then hold the finding's one-draft index. **Recommendation:** accept; say so on the customer's page (T9.5).
113. **Adoption by the poller writes `fix.pull_request_opened`** (actor the approver, `adopted: true`) and the timeline's `fix_pull_request_opened`, as the draft job would have. **Recommendation:** accept.
114. **FR30's deploy** is the first `deploy_events` row in the finding's project whose `commit_sha` equals GitHub's `merge_commit_sha`, at or after the merge, any service. That a squash or rebase merge's `merge_commit_sha` names the commit on the base is from memory, unverified. **Recommendation:** accept; the trial's merge question records each method.
115. **The facts line shows to a requester only** (the design's table: a viewer reads link and state). **FR26's held line now shows only while the draft is open and CI has not run** (Step 7 showed it on any opened row, merged or closed included). **Recommendation:** accept.
116. **Rung 2b's `POST .../run-ci` placeholder is removed,** with `fix_general.refuse_code_action` (no caller left); the source route refuses a general row `general_has_no_code` once the gate is on, and `writes_off` answers first while it is off (as decision 100 did for the draft). **Recommendation:** keep; amend the design's *Routes*.
117. **A flaky F5 test, found and fixed:** Step 3's `test_ac9_a_provider_change_stops_the_next_request_and_touches_no_consent_row` compared every organization's consent rows, so it failed once when a scheduler suite committed its own in parallel (seen in this step's full run). It now reads only its own organization's rows. **Recommendation:** accept.
118. **Built as designed, though the plan says the trial may change or delete Step 8.** The trial is the founder's, after Phase 2. Not built here: the Playwright states for Run CI and the facts line (the hardening pass); T8.4; T8.5's real re-run; AC49's disconnect-reconnect-new-proposal walk end to end (its halves are tested: Step 7's disconnect marks `untracked`, this step's poller and sweep do the rest); decision 97's "branch deleted" time, which waits for your call and is not in 0084. **Recommendation:** the hardening pass and after Phase 2.

**From Step 7's build, 2026-11-22.** None blocks the commit. Each is built as recommended.

90. **The `writes_off` sentence is new wording:** "Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done. Download the patch instead." The plan states no sentence for the switch being off. **Recommendation:** keep; your wording call (foundation decision 2).
91. **`needs_likely_cause`'s sentence drops the design's "(D58)"**, an internal reference, and says "this service's own signed deploy". **Recommendation:** keep; amend the design's table.
92. **Unverified, from memory:** that GitHub notifies no `@name` inside a code span, which is how the requester's note is kept from mentioning anyone. **Recommendation:** the founder's real run after Phase 2 checks it; if it notifies, refuse `@` in the note.
93. **GitHub's 422s are told apart by their words** ("already exists" for a second pull request or an existing branch; "draft" for a refused draft), from GitHub's documents and the trial's questions, not the service. Any other 422 on `POST /pulls` is a failure, never a fallback. **Recommendation:** the trial's recordings replace the words.
94. **A final GitHub failure other than a lost permission reads `github_unavailable`;** there is no code for "GitHub refused for another reason". **Recommendation:** accept; add a code if the trial shows one.
95. **The raising warning says "Private repositories need GitHub Team or Enterprise Cloud for drafts"** (the design's words), while D62 records drafts on every plan since 1 May 2025, and the fake opens drafts on any plan. **Recommendation:** your call; I would drop the plan sentence once the trial confirms D62. Kept as the design says. **Settled by the founder, 2026-11-22 (foundation item 35b): drop the plan sentence.** **Done 2026-11-22:** sentence removed from `FixAppPanel.tsx` (`raiseWarning`), its test now asserts the plan wording is absent, design lines on the warning amended.
96. **The signed-commits rule is not read** (`GET /repos/{r}/rules/branches/{default}` is a trial question); the App panel warns in general words instead of at the confirm. **Recommendation:** add the read and the confirm's warning after the trial.
97. **Delete this branch** works after `closed` or `not_opened`, not after `merged` (the spec says closed or never opened), and records the deletion only in the audit log: the row keeps its branch, so the button stays offered and a repeat finds nothing to delete. **Recommendation:** your call on `merged`; a "branch deleted" time with Step 8's migration.
98. **A tamper signal at the draft job** (the rules refusing the same inputs) reads `changed_since_check`, with no alert; the alert stays the check run's (ADR-096). **Recommendation:** accept: the person's own check just passed on the same files.
99. **The read's public refusal uses the stored visibility** (F22's hourly refresh); the check run and the job use GitHub's live answer. **Recommendation:** accept: the read calls no GitHub.
100. **Rung 2b's `POST .../draft-pull-request` placeholder is removed;** the source route serves both kinds and refuses a general row `general_has_no_code` once the gate is on. While it is off, `writes_off` answers first for every row. **Recommendation:** keep (as decision 63 did for the patch); amend the design's *Routes*, which also gains `GET .../fix-settings`.
101. **The restore returns an `approved` row to `proposed`, `did_not_finish`**, as the sweep would, since its job is gone. **Recommendation:** keep.
102. **A support view's refusal on the draft routes rests on the shared identity dependency,** which the route tests override (as decision 21). **Recommendation:** accept.
103. **Not built here:** tests for AC54 and AC58; Run CI and the poller that settles a `not_opened` or lost-answer branch (Step 8); T7.11. **Recommendation:** AC54 and AC58 in the hardening pass; the rest in Step 8 and after Phase 2.

**From Step 6's build (code), 2026-11-22.** None blocks the commit. Each is built as recommended.

77. **Write access is off through one constant in code** (`write_gate.WRITE_ACCESS_GO`, ADR-097), not a setting or row, so no operator can turn it on; the go must name the options that passed, the buyers' answer and the terms, and `status.md`'s T6.5 line moves with it. The plan named no code form. **Recommendation:** keep; Steps 7 and 8 call `write_access_on()` first.
78. **What counts as a way out in AC21's safety gate:** anything the rules exist to stop reaching a request or a person (a kept-back or outside file sent; a change to a file not sent, outside the folder or never changed; an added line with a new address, hidden or look-alike letters, an opaque string, a secret shape or a hidden run), read again after the job passed it. **A planted address copied onto a new line (A3) and a one-character logic change (A4) are counted as "reached a person", not as ways out,** since K6 passes the first by design and no rule judges the second; the report says what they are. **Recommendation:** keep; your call, since it decides the gate. **Settled by the founder, 2026-11-22 (foundation item 36d): keep.**
79. **The bed's arms and its person:** "without imports" drops the reader's imported files after it chose them and numbers the rest again; "without rounds" starts at the last round; a person is assumed to press Send in every round. Attacks run in the main arm only. **Recommendation:** keep (ADR-098).
80. **AC21 names three ends** (refused by rules, reached a person, abstained); the report adds unusable (the schema guards, after the one retry), call failed and nothing sent as their own columns, rather than folding them into "refused". **Recommendation:** keep.
81. **Resolved 2026-11-22** (a separate change, before Step 7; *Decision 81*, above): a later round's request carried no stack frames; the pack job now carries the earlier round's frames forward with the files they name, and the bed mirrors it.
82. **K6 compares whole addresses, path included,** so an address on a host the file already names, with a new path, is refused as new (seen in `test_replay_rules.py`). **Recommendation:** let T6.3's replay measure how often this refuses honest code before changing K6.
83. **Only Python seeded tests run** (the gate has no Node or JDK), so TypeScript and Java proposals read `not_run` in the objective measure. **Recommendation:** add runners with the founder's run on his machine, or in the hardening pass.
84. **Every fixture is an error with a stack,** so refusals per signal read "no fixture" for `slower` and `traffic` (AC38). **Recommendation:** add typed-path fixture cases for those signals in the hardening pass.
85. **The obeying fake's moves are guesses** at what a model that followed each planted instruction would write; they test the scoring, not any model. **Recommendation:** accept; the founder's runs give real answers.
86. **Trial counts are guesses:** at least 20 trials per attack, cycled across its moves, and 20 harmless controls in each of four arms, about 200 calls per option (`ATTACK_TRIALS`, `CONTROL_TRIALS`). **Recommendation:** keep; adjust to the free tier's limits when run.
87. **The bed's other figures are guesses,** each named once: a seeded test stops at 60 s (`SEEDED_TEST_SECONDS`), a real call at 120 s (`CALL_SECONDS`), a `git` command at 120 s (`GIT_SECONDS`), and the replay reads the newest 2,000 commits (`DEFAULT_COMMITS`). **Recommendation:** keep.
88. **The bed does not retry a failed call** (a 5xx or 429), which the job retries once as a new attempt; it counts it as `call_failed`. **Recommendation:** accept for now (a free tier's 429 is better seen than hidden); add the retry in the hardening pass if the runs show many.
89. **No K6 or K8 admin notice exists yet** (UR12, T9.3), so "they stay off until the founder has read T6.3's report" holds by absence. **Recommendation:** when T9.3 builds notices, hold K6 and K8 behind a recorded "read", as the write gate does. **Built 2026-11-22 (Step 9, decision 123):** the notices exist for K2, K7, K9, K10; K6 and K8 wait on `notices.REPLAY_REPORT_READ`.

**From Step 5's build, 2026-11-21.** None blocks the commit. Each is built as recommended.

59. **A deleted project is an end of stored code** (ADR-094): its suggestions of either kind still being prepared stop (`failed`, `project_deleted`, a new code in 0082) and its stored code goes. BR9's table does not name it; without it a `proposing` row would send files for a project that is gone. **Recommendation:** keep; add the row to BR9. **Settled by the founder, 2026-11-22 (foundation item 35c): keep; add the row to BR9.** **Done 2026-11-22:** row added to the design's BR9 table (the table lives in `design.md`; spec BR9 names the trigger). Docs only.
60. **A removed member's check runs go** with their GitHub link; their verdicts stay, naming them by id ("Former member"). **Recommendation:** keep.
61. **A person's verdicts are in their personal-data export,** following chat's "this was wrong" mark; `personal-data/spec.md` amended. **Recommendation:** keep.
62. **One verdict per suggestion, the last person's,** asked of a written suggestion of either kind and of an out-of-date rung 3 one, by anyone with `REQUEST_FIX`, code withheld or not; no audit entry (it changes nothing; the row says who and when). **Recommendation:** keep; the summary panel (UR12) counts them.
63. **The verdict and the patch are on rung 3's router, for both kinds,** as `/check` is (decision 45); rung 2b's `POST .../patch` placeholder is removed and AC73 holds on `GET .../patch`. **Recommendation:** keep; amend the design's *Routes*.
64. **The rebuilt diff is read back from the stored patch** (`patch_hunks`), so page and file are one text; flags are worked out again, and new letters from what the hunks show, which can only add flags. **Recommendation:** keep; Design QA judges.
65. **Out-of-date comparisons come before any file is read,** in the order rules, the owner's list, the code location, the likely cause; an owner's changed list reads `rules_changed`; the cause is compared by F3's stable rule name and fact id. **Recommendation:** keep; amend the design's step 5 order.
66. **The check run's tamper end deletes code at once** (BR9's first row), which deletes the check rows too, so the person's poll reads the suggestion's own `failed` sentence. **Recommendation:** keep.
67. **A `patch` check without the owner's requirement asks GitHub nothing about the person** (design), yet a passed one counts as an access check if the owner switches the requirement on within its 15 minutes. **Recommendation:** accept (LOW); or record on the check row whether access was checked, with Step 7's migration.
68. **The 72-hour clock** is `connection_repositories.unreachable_since` (F22's table, written by F22's refresh) or the App's `failing_since` with `app_not_installed`; marking pull requests `untracked` is Step 7's. **Recommendation:** keep.
69. **The owners' email is sent by `fixes/notify.py` on `light`,** which compares consents with the AI connection itself (the design put the comparison in the sweep, which holds no mail settings): one email per owner per organization per pass, naming every change in fixed words, and no repository name. **Recommendation:** keep; amend the design's sweep step 7.
70. **The design's second alert, `sweep_fixes` not finished in 15 minutes, is not built:** the sweep records no finish in Postgres for beat to read. **Recommendation:** build with Step 9's operations work. **Built 2026-11-22 (Step 9, decision 119):** migration 0085 and `NexovueSuggestionSweepStalled`.
71. **The restore also deletes link attempts and ends work in flight as `consent_ended_in_flight`** (the consents are withdrawn in the same transaction). **Recommendation:** keep.
72. **A `release_check` pytest marker** (root `pyproject.toml`) keeps `test_fix_restore_docker.py` out of every other run; its guard runs at test time, not collection. **Recommendation:** keep; add it to the release checklist.
73. **"Code deleted" shows only for a written suggestion** (`proposed`, `stale`); an ended one keeps its reason's sentence. Backups "age out by" the deletion date plus 4 weeks (D29's guess). **Recommendation:** keep.
74. **Step 5's figures are guesses,** each named once: a patch check lasts 15 minutes (`PATCH_CHECK_LIFETIME`), access gone 72 hours (`ACCESS_GONE_AFTER`), backups 4 weeks (`BACKUPS_KEPT_FOR`), the alert's hour (`F5_REFUSED_AGAIN_SECONDS`). **Recommendation:** the bed and operations replace them.
75. **Decisions 25 and 52 are resolved** (the verdict; the alert, ADR-096). **Decision 39 is resolved** for its Step 5 part: the owners' email and the 72-hour withdrawal are built.
76. **The patch download is a plain link** to the API's path (`patchDownloadUrl`), so the browser saves the file; a refusal there (an expired check) downloads the API's problem text. The link shows only while the check stands. **Recommendation:** accept; Design QA may prefer a fetched download through `api-client.ts`.

**From Step 4's build, 2026-11-21.** None blocks the commit. Each is built as recommended, except 43, which is not built.

43. **T4.11, the optional outbound limit, is not built.** It needs a proxy image, which ADR-019's two-image rule does not cover, and its check (AC53) is by hand. A brief search (sources below) found the usual pattern: a Squid forward proxy allowing `CONNECT` only to the provider's host (`dstdomain`), with the AI role's network made internal so the proxy is the only way out; images include `ubuntu/squid` and `cgr.dev/chainguard/squid-proxy` (non-root); `bsycorp/inkfish` is an allow-list proxy too. *Unverified:* how `dstdomain` treats an IP-literal `CONNECT`. **Recommendation:** a Squid image pinned by digest, treated as infrastructure like Postgres (outside ADR-019's two images), in `deploy/compose/nexovue.ai-egress.yaml`; your call on the image and on whether ADR-019 needs a line for infrastructure images. Sources: [Qovery, egress filtering with Squid](https://www.qovery.com/docs/getting-started/guides/advanced-tutorials/egress-filtering-squid-proxy), [Xebia, Squid as an egress gateway](https://xebia.com/blog/how-to-configure-squid-as-an-egress-gateway/), [Chainguard squid-proxy](https://edu.chainguard.dev/chainguard/chainguard-images/reference/squid-proxy/), [squid-users on CONNECT ACLs](https://www.mail-archive.com/squid-users@lists.squid-cache.org/msg26037.html), [bsycorp/inkfish](https://github.com/bsycorp/inkfish).
44. **The pack is deleted through `fix_drop_pack`, not a table `DELETE` grant** (ADR-091), so F3's no-table-write rule holds. **Recommendation:** keep; amend the design's grants table.
45. **One `/check` route for both kinds,** on rung 3's router; a general row refuses there with `general_has_no_code` (AC73 still holds), and the body is optional. **Recommendation:** keep.
46. **T4.8's "admin notice" is not built.** The audit entry `fix.withheld` names the rule and files; the notice belongs with UR12's summary panel on `/settings/ai`, which no step has built. **Recommendation:** build both with the summary panel (Step 9). **Built 2026-11-22 (Step 9):** the summary panel and its notices.
47. **"Files ready" emails only for the first listing and the model's rounds,** not after a person's own re-read (they are on the page). **Recommendation:** keep (D46's intent).
48. **`test_fix_source_never_leaks_live.py` is in `apps/scheduler/tests`,** where the fake provider and the AI account's fixtures live; the plan names `apps/api/tests`. **Recommendation:** amend T4.9's path.
49. **The diff's wrap marker** is not drawn: lines wrap, numbers sit at the top of each row, and no reliable marker on continuation lines can be built without layout. **Recommendation:** Design QA decides, with the Playwright states.
50. **A source `fix_proposed` event carries the changed file count as `step_count`** (the event's frozen fields), and the timeline says "A suggested fix was written from the service's own files". **Recommendation:** accept; your wording call (foundation decision 2).
51. **The AI room checks a `why`'s stand-ins against those its input carried,** since it may not read the names (F3 AC45); the API restores names at read. **Recommendation:** keep.
52. **The sweep's tamper re-run ends a row `failed`, `checks_refused_at_click`, audited `fix.checks_refused`,** with no alert channel yet; the design's alert at a later check run is Step 5's. **Recommendation:** add the alert with T5.1.
53. **Under the owner's requirement, any unwithdrawn covering consent that asks for it holds code back,** even one that ended by itself, since code is still stored. **Recommendation:** keep (the stricter reading).
54. **Decision 35 is resolved:** the model's reason for an asked-for file is stored after FR16's prose checks in the AI room, checked again by the pack job and at read, and shown under "The model's words, not checked by Nexovue".
55. **Decision 34 is resolved:** both return pages are built (above).
56. **The proposal's figures are guesses,** each named once: 4,000 output tokens, files open by default at 15 changed lines or fewer, a passed read check lasting an hour, a check reused within 60 seconds and read as timed out after 3 minutes, the link page's 30 polls of 2 seconds. **Recommendation:** the bed (Step 6) and Design QA replace them.
57. **Only round 1's Send spends a try** (FR15c; D32), so a model's later round is never refused by the daily tries. **Recommendation:** keep.
58. **A viewer's `proposed` line counts added and removed lines together** (K5's counting). **Recommendation:** accept.

**From Step 3's build, 2026-11-21.** None blocks the commit. Each is built as recommended.

28. **Rung 3 has its own read, `GET /findings/{id}/fixes/source`, on its own router,** beside rung 2b's (ADR-075); rung 2b's read keeps the section's line. The design's routes list `GET /fixes` for everything. **Recommendation:** keep; amend the design's *Routes*.
29. **F3's `error_group` item holds `first_seen`, not the window it read.** The stack is read from one minute before `first_seen` to the later of the explanation's `read_at` and a minute after. **Recommendation:** F3 stores its read window on the item; until then this derivation stands, and Step 3 was built although its precondition was only half met.
30. **The brief's candidates wait in the request's own `job_results` row,** since a job row holds ids only (AC15); the answer replaces them once. **Recommendation:** keep; amend the design's FR5c paragraph.
31. **`fixes.sweep_fixes` lives in `nexovue_scheduler/connections/fix_sweep.py`,** not `fixes/sweep.py` (rung 2b's, on `light`), because F22's test holds the connections set to that package. The task name is unchanged. **Recommendation:** keep.
32. **No grants for a `nexovue_connections` account:** F22's T8.7 account is not built, so `scheduler-connections` uses the install account. **Recommendation:** add F5's grants when F22 builds it.
33. **AC9's address, key and format changes surface as `ai_off`,** because F3 ends its own consent first; a model change surfaces as `no_consent`. No consent row is written either way. **Recommendation:** accept; amend AC9's wording.
34. **The console's two GitHub return routes are not built** (`/settings/connections/fix-app/return`, `/settings/github-link/return`): the API routes they post to are built and tested. **Recommendation:** build both at the start of Step 4, before any real-GitHub run; this is a gap, not a design change.
35. **The model's reason for an asked-for file is not stored or shown:** Step 4 writes it, under FR16's prose checks. **Recommendation:** Step 4.
36. **A typed path matching no file is shown back beside its input** (the person's own words, on the deletion list), as the design's "typed path miss" says. **Recommendation:** keep.
37. **A path-shaped token anywhere in a stack, a message line included, can become a candidate,** though no message text is stored; it must still be a real file in the folder. **Recommendation:** accept (A1 already treats the stack as attacker-written).
38. **A source row counts toward the 3 tries only once sent** (FR15c), so reading and listing never use one. **Recommendation:** keep.
39. **Owner and requester emails, `notify_requesters` and the 72-hour lost-access withdrawal are not built** (Steps 5, 7 and 9 carry them). **Recommendation:** as planned.
40. **Unverified GitHub shapes added:** `default_branch` on the repository, the installation's `permissions`, and an OAuth exchange taking a JSON body. **Recommendation:** the founder's real run after Phase 2 checks them.
41. **`code_deleted_reason`'s words** (`ended_short`, `pull_request_done`, `aged_out`, `consent_withdrawn`, `disconnected`, `ai_off`, `access_gone`, `restored`) name BR9's triggers. **Recommendation:** accept.
42. **The readiness panel sits inside the consent section and uses the selected project; the setup checklist is not a component of its own.** **Recommendation:** add `FixChecklist` with Step 4's checklist line on the finding page.

**From Step 2b's build, 2026-11-21.** None blocks the commit. Each is built as recommended.

16. **The prompt lives as text in `fixes/general_prompt.py`, and the job builds the `AIRequest`.** The plan names `fixes/general_prompt.py` with F3's `AIRequest`, but AC33's walk covers the whole `nexovue_platform.fixes` package and denies `nexovue_platform.ai`. **Recommendation:** keep; amend T2b.4's wording.
17. **Rung 2b has its own read, `GET /findings/{id}/fixes/general`,** and `GET /fixes` no longer carries the section's line, since deciding the line needs AI code (ADR-075). The design says `GET /fixes` returns general rows. **Recommendation:** keep; amend the design's *Routes* and *Rung 2b* step 3.
18. **Rung 2b follows F3's cause bar.** `model_input` carries the likely cause, so rung 2b is offered only when F3's facts may be sent at all (`facts_may_be_sent`, F3 FR50): with the bar not yet recorded, nowhere in production, as F3's wording. **Recommendation:** keep; it is the same rule, asked in one place.
19. **F3's "View what was sent" for a `fix` run** rebuilds the explanation request, so it reads "Cannot be reproduced". **Recommendation:** F3's preview names rung 2b (design, *What other features must carry*, "needed before Step 2b ships"); add the rung 2b request there, then. Until F3 carries it, `test_fix_general_request.py` shows the request.
20. **A died `fix` run's reservation is settled by F3's AI sweep, which beat runs only while AI is on.** The row itself is ended by F5's sweep regardless. **Recommendation:** accept; the reservation is settled when AI is next on.
21. **A support view's click is refused by the shared identity dependency (`_hold_support_view`, by method), not by the route.** The route's live test overrides that dependency, so AC74's support-view refusal rests on the shared tests. **Recommendation:** accept.
22. **Before Step 3, the line beside the offer is FR9's `no_code_location` first sentence alone** ("Nexovue does not know where this service's code lives."), since "An admin can set it" is untrue until code locations exist; and that line shows only with the offer, not on every finding with nothing to offer. `cause_is_elsewhere`'s requester line drops "You can still name files here" for the same reason. **Recommendation:** keep until T3.7.
23. **G5's process and dynamic-code lists are rung 2b's own, per language and exact,** not rung 3's `exec\w*(` flag pattern, which would refuse a database cursor's `execute(`. All languages' lists apply to every example. Imports are checked in code only, not in prose, where "use" and "import" are English. **Recommendation:** keep; the bed measures.
24. **The answer's stored form, tries and reasons, guesses:** a try counts only when the model answered (`proposed` or `refused`); a fault in Nexovue's own code is `nexovue_error` ("This stopped because of a fault in Nexovue. Asking again may help."), a code the design's table lacks; abstentions use per-code sentences derived from the design's combined row. **Recommendation:** add `nexovue_error` to the design's table; your wording call on all three (foundation decision 2).
25. **No verdict ("Was this suggestion useful?") yet.** The design lists verdict columns in the first migration; no route writes them before T5.2, so neither columns nor the question are built. **Recommendation:** add both with T5.2's verdict route.
26. **FR33's preselection from the service's SDK language is not built:** it needs a ClickHouse read, and this read must stay Postgres-only (AC8). The list starts unselected. **Recommendation:** store the language with F3's evidence, then preselect from it.
27. **AC75's database CHECK that a general row holds no repository, folder, files or edits** comes with Step 3's migration, when those columns exist; today `kind` admits `general` only. **Recommendation:** as planned (design, `kind` row).

**From Step 2's build, 2026-11-11.** None blocks Step 2b. Each is built as recommended, and each is a guess the bed (Step 6) or the founder should settle.

5. **K8's one threshold cannot catch hex.** The design gives 4.0 bits per character for base64-like and hex runs alike, but hex has 16 symbols, so no hex value reaches 4.0, and a 40-character hex secret would pass. **Built:** hex runs use 3.0 (`OPAQUE_HEX_MIN_BITS_PER_CHARACTER`), so a random 40-character hex string, a git sha among them, is refused. **Recommendation:** keep, and let T6.3's replay over real history measure how often it refuses honest code; correct the design's table.
6. **K5's 16 KiB is reached only with letters of several bytes.** With K10's 200 characters and K5's 60 changed lines, ASCII additions stop near 12 KiB first. **Built** as specified. **Recommendation:** leave it; it binds for non-Latin text and costs nothing.
7. **"At any depth" read as every segment, folders included.** So a folder named `secrets/`, `config/` or `prod/` keeps everything in it back, and a name pattern such as `*config*` matches `src/configuration/`. **Recommendation:** keep (it errs toward keeping files back; an owner can allow configuration paths by name).
8. **The never-changed list includes the configuration-shaped list,** per the design's "all of the above". An owner's allowance lets such a file be read, never changed. **Recommendation:** keep; say so on the consent screen at T3.6.
9. **Banned words include "fixes" and "fixed",** read as the bare "fix" BR11 bans; "safely" and "unsafe" pass. **Recommendation:** your wording call (foundation decision 2).
10. **The prose checks refuse host-shaped words anywhere in `why`** (there is no string literal in prose), so "the call to req.app fails" is refused. **Recommendation:** keep; the model is told to name files as `file_N` and nothing host-shaped.
11. **K4 has no mode to check,** because the schema has no mode field: it refuses an answer carrying a file-operation key (`mode`, `create`, `delete`, `rename` and kin) and a change that empties a file. **Recommendation:** keep; amend K4's wording at T6.2.
12. **Test file flag adds Python's `test_*.py`,** missing from the design's list. Showing more is the safe side. **Recommendation:** add it to the design's table.
13. **T2.9 would have repeated T1.5.** T1.5's walk already denies `nexovue_platform.ai` over the whole package, so T2.9 holds what it does not: no network client, process or file access, transitively. **Recommendation:** amend T2.9's wording.
14. **T2.7 proves the link without a finding row.** It proves F3's new-error read finds each case's group and F3's `groups_for` links it to the service, and the API returns each stack in its form; the full chain through detection to a finding row is the founder's *Judgeable* (seed, then `just detection-fault`). TypeScript's compiled lines equal the source's, a seeded shape. **Recommendation:** accept; T3.8 measures matching on these shapes and says so.
15. **The fixture package is named `fix`** (`tools/ai-eval/fix`, imported as `fix.*`), as the plan's path implies once `tools/ai-eval` is on `pythonpath`. F3's bed will share that root. **Recommendation:** keep; rename only if F3's bed needs a top-level `fix`.

**From Step 1's build, 2026-11-11.** None blocks Step 2. Each is built as recommended where something had to be built.

1. **AC11a's owner and admin line, "AI is off. An admin sets it up, then an owner agrees."** It needs F3's AI state (`ai_connections`, F3 Step 3, on `wt/f3s3`, not on this branch) and F3's `MANAGE_AI` action (FD8; not yet in `auth/permissions.py`). Without either, any line would be a guess about who may act and whether AI is on. **Built:** no role has a line in Step 1, so no section renders; the API's `section` field and the page's heading-less line are ready and tested with a stubbed line. **Recommendation:** add the line at T2b.7 with FR9's first eligibility, reading `ai_connections.enabled` and `off_reason`, shown to roles with `MANAGE_AI`. AC11a's viewer and member half holds now; its owner and admin half is unproven until then.
2. **FR1's heading and its over-24-hours sentence disagree with F28's built row.** FR1 says the heading is "Last deploy before this" and, past 24 hours, "No deploy in the 24 hours before this. The last was N days ago." F28's code, approved and on the branch, heads the row "Release before this" and says "The last known release was N earlier.", dimmed. FR1 also says F5 adds no second sentence about the same deploy. **Built:** F28's heading and sentence unchanged; F5 adds nothing past 24 hours. AC34's templates for "deploy", "no coverage", "came back", "failed", "over 24 hours" and "project finding" are therefore F28's, tested in F28's `finding-page.test.ts`; F5's own templates are tested in `test_fix_facts.py`. **Recommendation:** keep F28's words, and amend FR1 and AC34 to say F28 words those rows. Your wording call (foundation decision 2).
3. **T1.6's 44 px target.** The shared `CopyButton` is about 24 px tall, so the spec's 44 px check (AC37d) is expected to fail when it runs. **Recommendation:** decide in Design QA whether the brief's copy control grows (a size option on `CopyButton`) or AC37d's target excludes small copy controls everywhere. **Run 2026-11-22 (Step 9):** it fails as expected (26 px), with rung 2b's and the draft's shared controls; decision 120.
4. **The section's own loading and failed rows.** The design puts a skeleton and `InlineFailure` under the "Suggested fix" heading. With no line for any role in Step 1, that skeleton would show and then vanish. **Built:** loading and failure show once, at the brief's line, and the rest of the page works. **Recommendation:** T2b.8 adds the section's own rows when a role first has a line.

**To confirm (guesses, each named in code):**

- **No service name in the brief until Step 3.** FR3 names a service only from the customer's own settings, which are code locations (Step 3). Until then the brief names none, and its repository line says the repository is not known. This makes the Step 1 brief thinner for the founder's judging; loosening it would weaken D19.
- **The host-shaped word rule** (`brief.py`, `_HOST_SHAPED`) also withholds file-shaped routes such as `/app.js`: the safe side, a guess.
- **The version shape** (a short version such as `v2.1.0-rc1`, or a 7 to 40 character hex hash) and **three error groups at most** in the brief: guesses.
- **The console link uses `APP_BASE_URL`**; with it unset, no link is built rather than one to a guessed host.

**Before Step 1's build (2026-11-02):** **none blocking for the founder.** All five were answered on 2026-11-02 (*Founder answers, 2026-11-02*, below). The write-access go is still given at T6.5, against the approved rule.

**Not decisions, but still to happen:** the shared buyer round (approved, foundation decision 8) and the GitHub trial (approved 2026-11-02). Both were to block design approval; the founder approved the design first, and decided on 2026-11-02 that both block Step 1 instead, and that **a result that changes the design comes back to him** (spec D64).

**To confirm, from the later decisions of 2026-11-02** (each is written as decided, and is easy to reverse on paper):

- **D65: the picked language** is the one thing rung 2b sends beyond F3's facts. Strike it, and examples have no set language.
- **D71: rung 2b on hosted** follows F3's explanations once hosting opens AI, which changes D59's "rungs 1 and 2 on hosted".
- **The scope sentence** gained two sentences (rung 2b; imports and the AI asking). Its final words are yours under foundation decision 2.
- **Steps 1, 2 and 2b touch no GitHub,** yet wait on the trial because Preconditions 1 and 2 block Step 1. You may let them start before the trial; the documents do not assume so.

**Removed from this list, with where each went:** the buyer conversations (foundation decision 8; D57); F5 and the hosted service (foundation decisions 1 and 2, FD19; D59); when a draft opens (D58); publishing verdict and refusal numbers (foundation decision 5 names them); the bed's money as a process (foundation decision 4; now item 3's amount); D1 to D56 (decided, approved with the spec, not open questions); the foundation's 15 differences (checked, none open: `design.md`, *Where this differs*); the finding page's order (D54; F22 carries the order at `case-management/design.md:91`, but not yet the *Suggested fix* jump link, see `design.md`, *What other features must carry*).

## Blocked

- **T6.5, the founder's go on write access: not given.** Write access is off in code (`nexovue_platform/fixes/write_gate.py`, `WRITE_ACCESS_GO = None`; ADR-097). It needs AC21's safety gates passed on at least one named option in the evaluation report (`docs/research/f5-evaluation-2026-11.md`, not yet run: the founder's, after Phase 2), the founder's written go with his answers on drafts from a stack alone (D58) and on GitHub Enterprise Server (D57), the buyers' answer (D57) and the no-warranty terms (founder decision 1). This line and the constant change together, in one reviewed commit (`test_fix_write_gate.py`).
- **Founder decision, 2026-11-08:** Step 1 no longer waits on the buyer round or the GitHub trial (foundation items 27, 28; changes D64). GitHub checks run against a simulated GitHub; the founder runs the real trial after Phase 2.
- **Everything in the plan is blocked** on F6 merging, then F28, F3, F22 and F4 being built.
- ~~**Step 1 is blocked** on the GitHub trial and the shared buyer round (spec D64).~~ Unblocked by the founder's decision of 2026-11-08 (above); built 2026-11-11.
- ~~**Step 2b is blocked** on F3's provider (F3 Step 4) and on F3's consent screen naming rung 2b.~~ F3 Steps 1 to 5 are on the branch; built 2026-11-21. F3's consent screen and preview still do not name rung 2b (*Open decisions*, 19), which the design calls "needed before Step 2b ships": F3's to carry before release.
- ~~**Step 3 is blocked** on F3's evidence holding a group id and window.~~ Built 2026-11-21 on the group id and `first_seen` F3 stores (*Open decisions*, 29).
- **Steps 7 and 8 are built with write access off in code;** turning it on is blocked on T6.5: the safety gates, the founder's go, and the buyers' answer.

## Reviews

Eight personas reviewed `spec.md`, `design.md`, `plan.md` and `research.md`. Each finding was checked against the code or a source before anything changed. **Nobody re-reviewed the result.** The verdicts below are what the reviewers said before these changes, not a judgement of the documents as they now stand.

**Result: 154 findings. All 154 led to a change, and none was rejected whole. Twenty-six had a part rejected, listed below.** A finding counts as fixed when the documents now answer it, which means a requirement, design and test are written. It does not mean anything works, since nothing is built and nothing was run.

Reviewers' line numbers point at the earlier revision and have moved, so this section cites by name.

### Verdicts

| Persona | Verdict at review | Findings | Blockers |
|---|---|---|---|
| Architect | Not ready | 20 | 2 |
| Developer | Not ready | 20 | 3 |
| UX | Ready with fixes | 21 | 0 |
| Sales | Not ready | 18 | 1 |
| Marketing | Ready with fixes | 10 | 0 |
| Customer | Not ready | 23 | 2 |
| Security | Not ready | 18 | 1 |
| QA | Not ready | 24 | 5 |

### Conflicts between personas, and how they were decided

The task named a decision rule without stating it, and none was found in the repository under that name. The authors used the one F28's reviews used, drawn from the run's standing rules: the correct end state first, then what a tired engineer can trust, then cost. The founder may overrule any row.

| Conflict | Decided | Why |
|---|---|---|
| A stale proposal's code: delete at once (Security), keep to the limit (QA) | Keep (D50) | The spec's screens and FR20a need it, it sends nothing anywhere, and it shows what moved |
| Which F3 causes stop a proposal: rules 2, 4, 6 (Sales), rule 2 only (Customer) | Rule 2 only, and a person may still name files (BR4) | A moved edge is often this service's own bad query, and F3 itself words it "calls to", never "at fault" |
| Top rung for those: patch (Customer), a labelled draft (Sales) | Founder decision 3, with the design written for the labelled draft | What is sold is his |
| The brief: stack in a separate copy control (Customer), tree-matched paths (Sales), D19 as it stood | Tree-matched paths and a strict exception type; no raw stack | The brief reaches an agent with a shell, and rung 2 works with AI off. A path in the customer's own tree carries no attacker's words |
| Telling the requester: page title only (UX), email or alert channel (Customer) | Title and one email; no alert channel (D46) | On-call people leave the page. Alert channels are the team's |
| Stored code when consent ends: delete at the next sweep (Security, as designed), keep until the owner decides (Customer) | Keep, and stop all new source (D43) | The attack is new source going elsewhere, and that stops at once. Deleting punishes key rotation |
| Check result: `job_results` (Developer), a new table (Architect), columns (Developer) | `finding_fix_checks` | `job_results` keeps the first result per request, and a repeat click must replace a finished one. Columns allow only one person |
| Person and purpose: job arguments (Security), row (Architect) | The row, with arguments ids only, validated at start | AC15, and it gives the same protection |
| Retry after a moved main: compare edited files (Developer), a commit sha fixes its tree (Architect) | The sha alone | Simpler, and correct |
| Name for a lost pull request: `unknown` (Developer), `untracked` (Architect) | `untracked` | Plainer: it says what happened |
| Name of the App: "Nexovue code access" (Sales), "Nexovue suggestions App" (UX, Marketing) | "Nexovue suggestions App" | Two reviewers, and it matches "suggested" |
| Branch: `nexovue/suggested-fix-<id>` (Sales), `nexovue/suggestion-<id>` (Marketing) | `nexovue/suggestion-<id>` | BR11 bars a bare "fix" |
| Evaluation: between Steps 4 and 6 (Customer), 5 and 6 (Developer, QA) | Its own Step 6, after patch and deletion | Reads as one gate, with the deletion promises already proved |
| Pull request state: handler on F22's webhook (Developer), poll only (Architect) | Poll only (D33) | The foundation says webhooks only make it faster, and this saves an App event and an F22 change |
| Spend: per-purpose counters, or reword to the shared ceiling (Architect's options) | Shared ceiling (D32) | It is what actually protects explanations, and F3 changes nothing |

### Parts of findings rejected

No finding was rejected whole. These parts were, and nothing else was dropped.

| Finding | Part rejected | Why |
|---|---|---|
| Architect 3, Developer 4 | Entries in `_EXPIRED` for packs and checks | `expiry.py:47-65` has no guard, so wherever placed, a failure stops `sweep_spent` after it. All of it goes in `sweep_fixes` |
| QA 12 | Place the entry after `*retention.EXPIRED` | Same |
| Architect 14, Developer 5 | Grants called from `restore-postgres.sh` | F3 rejected grants in a bash script, and `nexovue restore` already ends in `start`, which runs `setup` (`deploy/nexovue`, `restore_snapshot`). The script prints the step when run by hand |
| Architect 15 | Letting the API rewrite `files_read` | Routed through the pack job, so one writer stays true |
| Architect 16 | Moving `draft_refused_at` to F22's `connection_repositories` | F5's own `fix_repository_settings` needs no F22 change |
| Developer 6 | A handler on F22's webhook route | D33 |
| Developer 10 | `job_results` for check outcomes | See conflicts |
| Developer 12 | The name `unknown` | See conflicts |
| Developer 15 | Comparing edited files on a retry | See conflicts |
| UX 1 | "Without adding a notification system" | Customer 15's case is stronger, and one fixed email uses the existing sender |
| UX 15 | Adding the diff inside the panel to `verify-contrast.js` | The diff no longer sits in the panel, so it is not needed |
| Sales 2 | Rules 4 and 6 as `cause_is_elsewhere` | See conflicts |
| Sales 13 | "Nexovue code access", `nexovue/suggested-fix-<id>` | See conflicts |
| Marketing 2 | Installing OneUptime now | No install can happen in a documents-only run, so T9.6 owns it. Its documents and README were read |
| Marketing 3 | That GitHub's docs show a model picker for third-party agents in the cloud | A search of GitHub's docs on 2026-11-02 did not find that. The action stands: "chosen model" is dropped as a contrast |
| Marketing 9 | Renaming "brief" throughout | The term is defined once and its difference from F3's `fix_brief` stated. A rename across five documents adds churn and no clarity |
| Customer 4 | "Copy with stack" | See conflicts, and spec, *Out of scope* |
| Customer 8 | Allowing a second person to give consent | Already true: an organization may have several owners (`members/service.py`) |
| Customer 10 | Suggesting folders from service names | It would read a repository's tree before an owner agreed. Bulk entry covers the need |
| Customer 15 | Using the alert channel | See conflicts |
| Security 5 | The person and purpose in job arguments | See conflicts |
| Security 10 | Deleting a stale proposal's code at once | See conflicts, and D50 |
| Security 7 | A live-consent check in the check run | A check run sends nothing to a provider. It checks that consent is not withdrawn, which is the risk |
| QA 17 | Refusing consent until a restore marks its scrub complete | A dump cannot know it was restored, and the path stops every service first. The crash window is stated as a risk |

### What was changed, finding by finding

Checked against: **code** (read at the cited path), **doc** (F3, F22 or the foundation), **web** (a source read 2026-11-02), **self** (these documents' own text, confirmed by search or by re-counting), **memory** (a reviewer's reasoning about a tool, labelled in the documents). A row marked † overlaps another persona's finding.

**Architect**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Plan describes a superseded design (blocker) | self | `plan.md` rewritten; spec's proof table and design's fallback column regenerated |
| 2 | AI room cannot do the job (blocker) | self, doc | Columns granted; `project_id` on the row; FR8 says where each comparison runs; `frames` on the pack; delete before state move; T4.1 runs the statements |
| 3 | A check run cannot report; one slot per fix; repeats unbounded | self, code | `finding_fix_checks`, repeat rules, a time limit, AC40. *Part rejected* |
| 4 | Draft retry fails when main moves | self | Adoption first, sha alone, AC26's cases |
| 5 | A pull request can become invisible | self, code | Branch stored before `POST /pulls`, widened index, poller `?head=`, `not_opened`, AC48, AC51 |
| 6 | Disconnect blocks a finding for ever | doc | `untracked`, its sentence, AC49 |
| 7 | Account link's data path undesigned | code, doc | `github_link_attempts`, per-task kwargs table |
| 8 | `fix.withheld` has no writer | doc, self | `withheld_refs`, `result_recorded_at`, sweep step, AC39 |
| 9 | Consent does not fit a monorepo | self | Per repository and folder, AC12c |
| 10 | Spend promises disagree with F3 | doc | NFR3, D32, AC37a, AC37b and the failure tables follow F3; reserve per call |
| 11 | Tenancy rests on comparisons | doc | Composite keys; F22 carries a unique key; AC30 |
| 12 | Slots not accounted for | doc, code | `connections-interactive`, `insight-fix`, D40, NFR2, memory labelled a guess |
| 13 | Run CI not safe to retry | self | Store before `PATCH`, both shas accepted, AC28 |
| 14 | Restore conflicts with F3 | doc, code | Grants through `setup`; the one list and in-flight rows by `psql`; AC6a. *Part rejected* |
| 15 | Incomplete vocabulary and states | self | Codes and sentences added; the diagram's arrow; `/files` route. *Part rejected* |
| 16 | `no_draft_support` never clears | self | `fix_repository_settings`, 24 hours, refresh, AC27a. *Part rejected* |
| 17 | Aliases never restored | doc | API restores `why`, AC56 |
| 18 | Three GitHub paths under-specified | code, self | Two tokens; `body_truncated` and caps; live check removed; trial row |
| 19 | Simpler: poll only, split migration | doc | D33; two migrations; AC28b |
| 20 | Figures and citations drifted | self, doc | This file, plan and research header updated; F3 cited by section (D44) |

**Developer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Plan predates the spec (blocker) † | self | Plan rewritten; `check_traceability.py` as T2.11; the same check run by hand on this revision (*Validation*) |
| 2 | Writer and client in the API (blocker) † | self | Steps 7, 8 in `scheduler-connections`; httpx row and C8 removed; AC5a in T3.15 |
| 3 | Required parts have no task (blocker) | self | T3.3, T3.4, T3.11, T5.1, T3.13, T5.4, T5.5, T3.6, T4.8, T8.3 |
| 4 | Stuck rows in the expiry loop | code | `sweep_fixes`, AC33's failing arm. *Part rejected* |
| 5 | Grants in the migration † | code | T4.2, T4.1 before it. *Part rejected* |
| 6 | Other tasks contradict the design | self | T1.2 in F28's section; T5.3 uses the one list; T8.2 F5's poller. *Part rejected* |
| 7 | Evaluation after the write path † | self | Step 6, with T6.5 as a gate |
| 8 | Validation plan forbids its own judging | self | A table of each real use, per step, with approval; budget before Step 4 |
| 9 | Nothing lets the founder judge early steps | code | T2.7, T2.8, `push_scratch.py`, Step 1's real seeded bugs |
| 10 | Check refusal has nowhere to live † | self | As Architect 3. *Part rejected* |
| 11 | Account link has no way to hold the code † | code | As Architect 7 |
| 12 | A finding locked in `draft_open` † | doc | As Architect 6. *Part rejected* |
| 13 | Empty edits, NUL | web, self | Schema guards, NUL as not text, AC20. PostgreSQL's page confirms `\u0000`; a lone surrogate is inferred from it |
| 14 | Stack not limited to the service | code | `recent_for_service`, AC12d |
| 15 | False `branch_not_ours` † | self | As Architect 4. *Part rejected* |
| 16 | Pack delete order | self, memory | Delete first, assert the count, AC14. Not run |
| 17 | `beat.py` and the contract test | code | T3.13 |
| 18 | Work that can be deleted | code | T2.9 is the AI half only; rate-limit half, T5.5's repeat, T6.7 and the T3.10 risk all gone |
| 19 | Codes with no sentence † | self | Table, arrow, AC8 |
| 20 | Status and plan disagree | self | Corrected here and in the plan's preamble |

**UX**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | "May be left" while a click is needed | self | Wording, countdown, `document.title`, neutral lapse. *Part rejected* |
| 2 | Missing sentences; Cancel reads as failure † | self | Rows added, including `check_expired`; own treatment; AC8 |
| 3 | `why` reads as Nexovue's | self | Labelled block after the diff; banned-word pass; hostile fixture; AC56 |
| 4 | Brief absent from the page table | self | A table row, wireframe, NFR6b, heading name |
| 5 | Section buries the evidence | code | Collapsed, per-file disclosure, 15 lines (guess), NFR6b adds Ask and Send |
| 6 | Focus pulled on polling | code | NFR6a, AC37d, headings per state |
| 7 | Long values overflow | code | "Send these files", `min-w-0`, `flex-wrap`, `overflow-wrap`, AC37d |
| 8 | `not_linked` contradictory; blocked buttons | code | Decided per state, FR10c, AC37e |
| 9 | Lines on an incident page when AI is off | self | Hosted renders nothing; roles split; AC11a; checklist link only |
| 10 | Refusal wording, attack signal | self | One sentence per rule, each with a next step |
| 11 | Diff has no line numbers | code | Gutter, hunk headers, wrap marker, `+` and `-` as the main marker. Plex Mono weights confirmed |
| 12 | Key words not written | code | Rows added; `EditableRows`, `TextField` named |
| 13 | Nothing proves phone layout | code | `fix-section.spec.ts`, T1.6 onward. jsdom confirmed |
| 14 | Settings have no states | self | A list per panel, em dash rule, readiness wording |
| 15 | Duplicate diff at the confirm | code | Replaced in place; one action row. *Part rejected* |
| 16 | Copy fails with nothing to select | code | `onUnavailable`, test |
| 17 | Words break the spec's rules | self | "Nexovue suggestions App", no "passed", matched answers, AC37 |
| 18 | Sentences lack what would change it | self | Rewritten |
| 19 | `evidence_pending` has no end | self | 15 minutes (guess), AC8a |
| 20 | Empty heading | self | No section at all |
| 21 | No target size | code | 44 px, stated as a recommendation, checked in Playwright |

**Sales**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Rival facts unsourced, one contradicted (blocker) | self, web | Each read and added with URL and date; research corrected; rows mark what is not stated |
| 2 | Rule numbers wrong | doc | Table rebuilt on the kind of cause, all seven rules. *Part rejected* |
| 3 | Gate gets harder as evidence improves | doc | Founder decision 3 rewritten with the consequence and two options; buyer row |
| 4 | Scope sentence misleads | self, doc | Replaced; surge limit stated |
| 5 | Lead claim cannot be defended | self | Lines added, in *What may be said* |
| 6 | Buyer table lacks hard questions | self | Rows added from the documents' own sentences |
| 7 | Rung 2 does not solve its problem | self, doc | FR5c, Problem section reworded, no "no pasting" |
| 8 | No answer to "show me" | self | T9.7, T6.4, AC38, foundation decision 5 asked to widen |
| 9 | Write is install-wide | self | BR14, `drafts_allowed`, AC60, a buyer row |
| 10 | Buyer script cannot answer | self | Rewritten: screen, behaviour first, real wording, stated as direction |
| 11 | Two decisions told, four exist † | self | Corrected here and in the plan |
| 12 | Three cost answers | self | One figure throughout; worked example at T9.5, since a price must be read at that time |
| 13 | Word rules broken | self | "agreed to"; names as above. *Part rejected* |
| 14 | "Yet" on hosted | self | Removed, and nothing shows on the finding page |
| 15 | Approval advice unproven | self, memory | FR24, D26, buyer row say so plainly |
| 16 | Raising before knowing the plan | self | Warning names it; design's sentence corrected |
| 17 | Setup answer thin | self | The real step list and who does it; bulk form; rungs apart |
| 18 | Research states reversed positions | self | *Superseded by the spec* |

**Marketing**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Rivals unsourced † | web | As Sales 1. The draft-plan claim is sourced to GitHub's pull requests API page, which the reviewer had not found |
| 2 | Closest rival has no profile | web | OneUptime row; claims marked; head-to-head buyer row; T9.6. On the page read, GitLab is listed. *Part rejected* |
| 3 | No true reason to choose F5 | self, web | Benefit first; three buyer rows; chosen model dropped. No preview found on pages read, which proves nothing. *Part rejected* |
| 4 | Positioning against reach | web | Datadog confirmed; research corrected; decision 1's outcome, decision 4's wording |
| 5 | No launch gate | self | T9.6, AC52, AC37's strings, decisions corrected |
| 6 | Selling reason overpromises | self | A line in *What may be said*; foundation decision 2 asked to widen |
| 7 | Ladder and not-run weight | web | Research and spec's competitive cost name Grafana; plain statement to the founder |
| 8 | Silence on injection is wrong | web | Reworded; Aurora and OneUptime confirmed |
| 9 | Naming rule broken | self | D52, AC37; the customer page's name. *Part rejected* |
| 10 | Reviews section missing | self | This section |

**Customer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Best case refused by wrong rule numbers (blocker) † | doc | As Sales 2 |
| 2 | Plan does not build what is promised (blocker) † | self | Plan rewritten |
| 3 | Common latency bug refused | doc | BR4, reach table, sentence reworded |
| 4 | Brief leaves out what a tool needs | self | Exception type under a shape, FR5c, AC38 scores it. *Part rejected* |
| 5 | File matching misses common deployments | self | Path in production, qualified class names, AC12e |
| 6 | K6 and K8 refuse honest code | self, arith | Patterns, near-misses, AC63 before any notice |
| 7 | Model change moves source unasked | doc | Model in consent, FR7 sentence, ADR |
| 8 | Key hygiene is punished | self, doc | D43, owner email, AC59. *Part rejected* |
| 9 | Open internet from the process holding source | self | D49, T4.11, AC53, FR7 |
| 10 | Setup too much | doc | Organization-wide mapping, bulk, AC37c timed. *Part rejected* |
| 11 | Trial skips private addresses | doc | Rows and fallbacks |
| 12 | Evidence arrives late and stops nothing | self | Step 6, T6.5, per named local model |
| 13 | Asking again has nothing to change | self | Hint at Send, D47, AC65 |
| 14 | Large file refused outright | self | Window, D48, confirmation, AC12f |
| 15 | Nobody is told | self | Email per event, D46, AC64. *Part rejected* |
| 16 | CI held with no option | self | `ci_on_open`, D45, AC61 |
| 17 | Default access wrong for a real company | self | Required choice, count of members, AC10a |
| 18 | No regression test | self | Conventional test file added, bed measures it |
| 19 | Reviewer cannot tell why | self | "Why this change", FR24a, AC62 |
| 20 | Approving names without contents | doc | GitHub links at the commit read, FR13a |
| 21 | Nobody can pay | doc | Script questions; spec states no revenue path; pricing left to the founder |
| 22 | Litter on GitHub | self | **Delete this branch**, AC66; signed-commit warning |
| 23 | Status contradicts documents † | self | This file |

**Security**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Plan breaks the boundary (blocker) † | code, self | Plan rewritten |
| 2 | Owner's access option not enforced | self | Jobs check and fail closed; read gated by **Check my access**; AC10a |
| 3 | Send not bound to the list | self | `files_digest`, AC3b, `/files` |
| 4 | Link's values, link forgery † | code, doc | `github_link_attempts`; session bind; PKCE and revoke if the trial confirms; A18 |
| 5 | Check run does not know who | self | Row and start validation; AC10a's parallel case. *Part rejected* |
| 6 | Consent check impossible in the AI room † | self | As Architect 2 |
| 7 | A check run can restore deleted code | self | Conditional store, AC29a. *Part rejected* |
| 8 | Audit has no writer † | doc | As Architect 8 |
| 9 | Audit cannot answer what was sent | self | `fix.sent`'s list, purposes, `fix.code_viewed`, AC57 |
| 10 | Deletion promise contradicts itself | self | One statement, BR9's table, AI-off explicit. *Part rejected* |
| 11 | Support view reads paths | code | Stripped in `audit/read`; AC11 |
| 12 | Read-time recheck omits prose | self | `prose.py` at read and check; AC19 |
| 13 | CI hold described too narrowly | web | FR24, FR26, trial row, AC22a. Skip page and events page read; that `create` runs model code is an inference |
| 14 | Commit not proved in history | doc, memory | Compare route, A17, AC12h, trial row. Fork behaviour labelled from memory |
| 15 | Forged strings stored | self | Count only, AC12g |
| 16 | Login in a pull request | self | Sentences, AC62 |
| 17 | Pack deletion order † | self, memory | As Developer 16 |
| 18 | Look-alike characters | self | K7, FR18's flag, AC17, K6's limit |

**QA**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Plan not revised (blocker) † | self | Plan rewritten, K10 included |
| 2 | Proof table does not trace (blocker) | self | Re-counted on the old plan: 68 criteria, 32 unnamed, as reported. Regenerated; each criterion has a row; T2.11 |
| 3 | AC33, NFR4 orphaned (blocker) | self | T3.14, T4.9 |
| 4 | Evaluation too late (blocker) † | self | Step 6, a re-run at T8.5 |
| 5 | Deletion contradicts screens (blocker) † | self | D50, BR9's table, AC29b |
| 6 | `fix.withheld` has no writer † | doc | AC39 through the real account |
| 7 | "Way out" cannot apply to a diff | self | Split, with the copied-address case stated |
| 8 | AC21 not decidable | doc | Named options, 20 trials (guess), safety gates apart from figures |
| 9 | Bed cannot produce a finding | code | T2.7, objective measure, machine named, shapes stated |
| 10 | No fake GitHub task | doc, self | T2.10 with recordings, release check |
| 11 | Case start unproved, not callable | doc, code | D42, AC54 |
| 12 | Load on alert workers | code | `connections` queue, no `_EXPIRED` entry, AC33. *Part rejected* |
| 13 | AC33, NFR4, NFR2 not executable | doc | Arms of F3's AC48; a stated bound (guess); NFR2 "should" |
| 14 | AC2 looks in the wrong place | code | AST test on task names, beat's table |
| 15 | Requirements with no real test | self | AC43 to AC48 |
| 16 | Nothing proves what goes to the model | self | AC42 |
| 17 | Restore has no bed | code | T5.5, Docker, release check. *Part rejected* |
| 18 | Tamper alert conflicts | self | *Tamper signals*, AC55 |
| 19 | AC8 misses most sentences † | self | One constant, one walking test |
| 20 | Patch never applied | memory | AC41, git headers |
| 21 | Digest undefined | self | Defined, AC19a |
| 22 | BR8 broken by a lost store † | self | As Architect 5 |
| 23 | Untestable claims | self | FR28 reworded, AC58, this section |
| 24 | F3 field in doubt | doc | Step 3's precondition; AC8a's fixtures |

### Found while checking, beyond what was reported

- **F3 now holds `ai_connections.key_id`,** "new on each saved key; re-sealing keeps it". F5's `key_generation` was a duplicate, and is gone.
- **F3 turns AI off when consent ends.** So BR9's "AI switched off" would have deleted code on a routine key change, defeating D43. F5 asks for `off_reason`, with a fallback.
- **F3 rejected a second model call inside one job,** because a soft limit cannot interrupt a blocked call. F5 did exactly that, holding a slot for up to 640 seconds. It now retries as a new attempt, as F3 does (D51), and holds at most 340.
- **The backup also needed to skip `finding_fix_checks` and `github_link_attempts`,** which hold a patch and a sealed code.
- **`deploy/nexovue`'s `restore_snapshot` stops a fixed list of services,** so F5's roles must join it, or they could run through a restore.
- **`nexovue_insight`'s `WITH CHECK` had to admit `proposing`,** or the retry's `retry_code` write would be refused.
- **Every edit must name evidence (FR16), but a proposal with no explanation has none.** It now names its `file_N` reference.
- **The shared package may not import a database driver,** so `clear_stored_code` is held as SQL text, as `jobs.py`'s `ENQUEUE` is.

### Second review: 15 proven problems, fixed on 2026-11-02

Each was checked against the documents and code before anything changed. All 15 held, and each led to a change on paper. **Nobody has re-reviewed these changes, and nothing was run.**

| Severity | Problem | Checked | What changed |
|---|---|---|---|
| Blocker | "Require GitHub access" could not work until Step 7, and no task named **Check my access** | self | Link tables in the Step 3 migration; T7.3 is now T3.17; new T4.12; quoted-code gate in T4.7; `for: draft` named in T7.4; spec decision 1 and D55 keep the link if write access is refused |
| Blocker | The diff at `proposed` could not be built from what is stored | self | **Decided: D53, recommended by the authors, founder may overrule.** The AI job stores display facts inside `edits`, and every use re-checks them. T2.3, T4.4, T4.7, T5.1, FR20, NFR6, AC19, A13. It must be read before design approval |
| Major | F5's connection tasks were in the wrong task list | doc, code | `connections.*` and `fixes.sweep_fixes` in `TASK_SETS["connections"]`; only `fixes.notify_requesters` in `TASK_MODULES`; design, T3.10, T3.13, T3.15, T4.8, AC6b, AC33 |
| Major | F5 and F22 each overrode the other's page order, and F5 misdescribed F22 | doc | D54; design's table and wireframe; listed under *Open decisions* |
| Major | The endpoint rule let a spaced sentence through | self, code | FR3's strict pattern; AC35 lists what is refused and what passes by design; Step 1's example |
| Major | Copilot and sandbox claims had no source | self | **Deleted, not sourced:** signed commits, CodeQL and secret scanning from the buyer table, and the citation in the trial table. Reading GitHub's page failed on the day (the fetch tool hit a usage limit), so nothing was confirmed or disproved. research.md reworded for Splunk and incident.io |
| Major | AC37 could not pass against required sentences | self | "nothing to change", "An admin can restore it"; AC8's walk applies AC37's words |
| Major | "179 of 179" hid parts with no task | self | AC58 in T7.4, AC29a in T7.5, AC10a in T4.7, NFR4's bound in AC33; T2.11 reads halves |
| Minor | `just check` did not run T2.11 or the bed's tests | code | T2.11 adds `tools/ai-eval` to `testpaths` and `pythonpath`, with a named test |
| Minor | Database changes with no migration | code | T3.1 and T7.1 list them; `clear_stored_code` grows at T7.1 |
| Minor | Three self-contradictions in the design | self, code | `request_id` in the jobs table; "existing two" deleted; "about 3 MiB held, 64 KiB sent" |
| Minor | Broken branch-name record; a repeated line | doc | `nexovue/fix-<id>` as the old name; line deleted |
| Minor | Decision 1 named the wrong steps, and left FR5c unsaid | self | Steps 3 to 8; FR5c leaves under that outcome (D55) |
| Minor | Sentence and route gaps | self | `not_opened`, `open`, `merged`, `closed` rows; `consent_ended_in_flight` split from `no_consent`; AC8; consent route takes `folder=` or `scope=whole-repository` |
| Minor | T2.10 named the wrong fake | doc, code | F22's `tracker_fake.py`; Impact corrected |

**The check, re-run by hand with a throwaway script after these changes:** all 179 FR, UR, BR, NFR and AC ids have a row in *What proves what*; all 101 acceptance criteria are named in `plan.md`; every task named in `spec.md` and `design.md` exists in `plan.md`; no criterion is named only as a lone half; and no sentence in *Every sentence* holds a word AC37 bans ("suggested fix" is the allowed form). This search reads names, and cannot show that a task is adequate.

**Found while fixing, beyond what was reported:**

- **AC14's job half had no task either,** found by the same search for halves. T4.4 now names it.
- **`for: draft` had no task,** though T5.1 is patch only. T7.4 now names it.
- **The `not_linked` sentence said "write",** which is wrong for a read check. Reworded, and a state-by-role row added for Ask under the requirement.
- **A check row's 15-minute life conflicted with D20's hour.** A `read` row now lasts an hour (guess), and `read` is allowed in any state holding code.
- **Not fixed, needs a decision when F22 is next revised:** F22 puts sync on `connections-sync`, capped 2 of 3 slots, and leaves `connections` uncapped, saying F5's jobs run there. F5 caps `connections` at 2 and adds `connections-interactive`. As both now read, sync and `connections` jobs could together hold all 3 slots, so F5's "one slot is always free" (D40, NFR2) is not yet true. The caps must be reconciled.

### What remains as a risk after the reviews

- **Nothing here has run.** Every fix above is on paper, including row-level security, which T4.1 tests first.
- **The trial may change the design.** 21 questions, several from memory, and two can block a rung.
- **Buyers may say no.** Nobody has been interviewed.
- **A proposal's quality is unknown,** and its seeded measure rests on the authors' shapes until T6.4.
- **Rivals move monthly.** Facts are dated 2026-11-02, and one, whether OneUptime's self-hosted fix path is free, is not settled.
- **F3 and F22 carried the changes F5 asked for** on 2026-11-02 (*Cross-feature reconciliation*). One stays on its fallback: linking existing error groups whose rate moved, which F3 does not do, so those findings need typed paths.
- **A broken-into AI process with the outbound limit off** can still send out packs in flight, across organizations.
- **The restore's crash window** stays, for someone running the script by hand.
- **An automatic retry after a deliberate withdrawal** writes nothing further, but a branch made before it stays on GitHub.
- **Until a check run, a broken AI process could show misleading context or hide a new-letters flag** (D53). It cannot change what is confirmed, downloaded or committed.
- **With the owner's requirement off,** a member can read quoted code from a repository they cannot reach on GitHub.

## Risks

- **Unknown demand.** Nothing found measures whether people accept AI fixes for production incidents, and nobody has been interviewed.
- **Opening a pull request may run the customer's CI with secrets, before review.** Inferred from GitHub's documents, not tested. The hold covers only `push` and `pull_request` runs of CI that honours the instruction. Other triggers and other CI systems were not checked.
- **A subtle logic bug or a harmful comment can pass every rule.** Only a person can catch that, and perhaps not. Approval is required, and is not a safety boundary.
- **`contents: write` reaches any unprotected branch of every repository the App is installed on.** Nexovue's code and an owner's per-repository allowance are what limit it, not GitHub. The customer must protect main and release branches and tags.
- **Many findings may not qualify automatically.** A stack is needed, and F3 links only new error groups, so a surge in an existing group needs typed paths. **The refusal rate is unknown**, and may be high enough to disappoint.
- **F3 is a promise.** If it rarely links an error group, little qualifies.
- **"Not compiled" is behind a growing group of rivals,** and F5 answers only with small scope and saying so.
- **Quality on any model is unmeasured,** and on a local model it may be weak in both quality and safety (foundation, section 11).
- **Cost is arithmetic on guesses:** about 16 to 33 full-size proposals in half of a guessed 2 million monthly tokens, fewer by whatever explanations use.
- **Quoted code in backups** stays until they age out. A restore empties it and withdraws every consent, apart from the stated crash window.
- **The source-secret scan is a net:** a secret in an unknown format is sent.
- **F5 has no revenue path under current assumptions.** It is free on self-hosted, and off on hosted.
- **Rung 2b's advice may be wrong for the customer's code.** It is written from facts only, and a reader may paste it without reading the label. The label, G1 to G9 and no copy control narrow this; the bed and verdicts measure usefulness. Nobody has asked buyers whether they want it.
- **Asked-for files widen what planted text can steer** (A19). A planted instruction can make the model ask for an allowed file; only the person's Send stops it going.
- **Import following may fill the caps with files that do not matter,** crowding out test files. The bed reports how often an imported file is the one edited.
- **Tested only on free models (foundation items 4 and 10, 2026-11-02).** A call of about 25,000 tokens (a guess) is above some free tiers' per-minute limit, and 240 or more calls per option at daily caps take days. Free tiers may train on inputs and let people read them, so only fixtures and nexovue's own public code are sent.
- **"Named option" covers only what was run.** OpenAI, Anthropic and Bedrock have no free API tier, so they stay "not yet tested", and the write-access gate (T6.5) can only pass on a free-tier or local model. A small local model on the 24 GB Mac may be weak at code, and competes with the dev stack for memory (decision 0047).
- **No server measurement before deployment** (foundation item 9). F5's memory share of `scheduler-insight` (768 MB, a guess) is read on the Mac's Docker; the 16 GB server waits for Oracle.

## Validation

**Step 1, 2026-11-11:** see *Step 1, built 2026-11-11*, *Validation*, above. What follows is the documents' own validation of 2026-11-02.

**Nothing was run:** no model, no GitHub call, no test, no build. Code was read at the paths cited. On 2026-11-02 these were read through a tool that summarizes pages, so check any quote against the live page before publishing it: OneUptime's AI agent page and README; Better Stack's error pull requests page; Datadog's Bits Code page; Grafana's what's-new entry of 2026-10-19; Middleware's OpsAI overview; Sentry's Autofix page; Aurora's README; GitHub's skip-runs, events and pull requests API pages; and PostgreSQL's JSON types page. Two searches of GitHub's docs were made, for draft plans and for custom models. Older competitor claims are carried from `research.md`, where they say so.

**One check did run,** by hand, over these documents, with a throwaway script: every FR, UR, BR, NFR and AC in `spec.md` has a place in *What proves what* (179 of 179), every task named there and in `design.md` exists in `plan.md`, and every acceptance criterion is named by a plan task. **That count treats a criterion as covered if it is named once, so it hid parts with no task** (*Second review*, below). It was re-run after those fixes, and also looks for criteria named only as a half; the result is in that section. T2.11 makes this a real check.

## Specification impact

The specification is new, and was revised by the reviews. The design's *Specification impact* lists what the trial and the buyers' answer could change.

## Documentation impact

- `docs/product/phase-2-foundation.md` was reconciled on 2026-11-02, including the scheduler's tenancy test and `AI_SECRET_KEY`.
- F3's and F22's documents carry the changes in `design.md`, *What other features must carry*.
- `docs/product/feature-catalog.md` still says F5 is "TO SPECIFY". To be updated when the founder approves.
- `vision.md:136` needs changing, as the spec says, and `docs/research/phase-2-competitors-2026-11.md` needs OneUptime, Better Stack, Middleware and Grafana.
- No document in `docs/decisions/` was touched. Four ADRs are proposed inside `design.md`.
- **2026-11-02, later:** the foundation (FD14, section 5, section 11, section 12, founder items 25 and 26), `phase-2-tracker.md`'s order, `phase-2-plan.md`'s Stage 5, `feature-catalog.md` and `docs/features/README.md` were updated for rung 2b.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- `jobs`: F3 has no row policy, so F5 adds `insight-fix` to F3's job functions, as F4 adds `assistant` (design, T4.1, T4.2).
- New D56: `scheduler-insight` runs 8 slots, with caps `assistant` 4, `insight` 3, `insight-fix` 2, at 768 MB, all guesses. F5's "2 of 4" ignored F4's 6 slots, and on those, chat plus fixes would have left explanations none (design, spec D40, plan).
- "Up from F22's 2" corrected; the sweeps waiting behind a hung sync accepted (design).
- Page order: one table, with F3's section first in the right column and the last deploy as its first row (design, FR1).
- `RECORD_FINDING_EVENT_SQL` replaces `record_finding_event()` (design, spec).
- Settled against F3: `fix_brief()` stays, `off_reason`, stable rule names and the group's window are F3's (design, T3.5, T3.7).
- New grants for F22's `nexovue_connections` account, which F5's jobs would otherwise be refused under (design, T3.10).
- T1.5 does the shared `lifespan.py` fix if first (plan).

## Last updated

2026-11-22 (Step 8 built, awaiting commit; T6.5 still not given)

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in this feature's documents was read against the code, the research and fresh web sources, and either decided or left to the founder with a recommendation. Decided ones are in `spec.md`, *Decisions taken*, as D57 to D63; the founder may overrule any.

| Question | Answer | Why, in one line |
|---|---|---|
| Buyer conversations (old founder decision 1) | **Settled by foundation decision 8.** F5's questions join the shared round, with a checkable rule (D57) | One round was approved; F5 adds questions, not a round |
| When a draft opens (old founder decision 3) | **(a) is built: only a signed deploy of this service reaches a draft; stacks stop at the patch (D58).** Widening to stacks stays with the founder, at founder decision 4 | FD14 leaves the rest to F5's founder decision; the narrow reading is safe and reversible |
| F5 and the hosted service (old founder decision 4) | **Settled by foundation decisions 1 and 2, and FD19.** Rungs 1 and 2 only on hosted; no separate F5 line (D59) | Hosted F5 would mean holding customers' source, which waits for the lawyer |
| Publishing verdict and refusal numbers | **Settled by foundation decision 5,** which names F5's numbers | Approved as recommended |
| The trial's two "back to the founder" results | **No draft if `[skip ci]` fails; refuse, fail closed, if the permission route fails (D60)** | GitHub's pages say both should pass; no long-lived personal token |
| Does F3's link share stop Step 3? (plan question 3) | **No (D61)** | A person can name files |
| Draft pull requests on GitHub Free (found while checking) | **They work in private repositories on every plan since 1 May 2025 (D62)**; scope sentence and buyer table corrected | GitHub's changelog |
| Is `detect-secrets` better? (plan question 5) | **No; keep the pattern list (D63)** | Last release May 2024; Python API scans files |
| The foundation's 15 differences | **None open** | Five already carried by the foundation, the rest narrowings, claim wording, or D58 |
| The finding page's order | **Closed by D54.** F22 carries the order; the *Suggested fix* jump link is still F22's to add | `case-management/design.md:91`; `:121` names only *Evidence* |
| Liability; source sentence; F5's bed money; go on write access | **Left to the founder,** with recommendations | Legal, public claim, money, customer trust |

**F5's questions for the shared round (D57).** Screen: "Would you run this yourselves, and do you use GitHub.com?" Past behaviour first: "Describe your last three incidents: was there a stack, and what did you do first? How often do you deploy? Which GitHub plan? Which git host and CI? Who approves a new GitHub App, and did they last time? Do you already use an agent on incidents?" Rung 2b: "If Nexovue could not read your code, would a general example from the finding's facts help, or be noise?" (informs, does not gate; added 2026-11-02). Then the real consent screen wording: "Would you send source to your AI provider, per repository? Would you use a suggestion nobody compiled? Would you grant write access? Would you want a draft pull request from an error stack alone, with no deploy behind it? Would you run this self-hosted or want it hosted? What would you pay, and for what?"

**Review, 2026-11-02.** A second pass tried to break each answer above, against the code, the foundation and fresh sources.

- **Held up, re-checked:** D60 (GitHub's skip page and the permission table both confirmed; the skip page's own caveat, that `pull_request_target` still runs, is already in `design.md`, *Run CI*, and attack A5). D62 (changelog quote confirmed). D63 (PyPI's last release is still 1.5.0, 6 May 2024; the README's Python examples scan files only). D61. Founder decision 3's arithmetic (Opus 5.5 at US$4 and US$20, Sonnet 5.5 at US$2 and US$10 per million tokens, re-read).
- **D58 corrected.** FD14 does not forbid option (b); it leaves the rest to F5's founder decision. (a) stays what is built, as the safe and reversible reading, but widening to drafts from a stack alone moved back to the founder, inside founder decision 4.
- **D57 corrected.** "Two or more run their own Git" no longer schedules GitHub Enterprise Server by itself. Adding a Git host changes what is sold, so it goes to the founder at T6.5.
- **D59 corrected.** It cited `ai_available(deployment)` as code. It is a design name; the code today has only `NEXOVUE_DEPLOYMENT` (`nexovue_platform/deployment.py:16-21`).
- **Founder decision 2's sentence corrected.** "Send only the source files" was false: the finding's facts, stack line numbers and a typed hint go too (FR13a, FR15a).
- **D54's F22 claim corrected.** F22's design has the order but no *Suggested fix* jump link (`case-management/design.md:121` names only *Evidence*).
- **Added founder decision 5:** the go on the GitHub trial, which was noted but not listed.
- AC27a's "a customer who upgrades" wording no longer assumes a plan refuses drafts (D62).
- Not changed, outside this feature's documents: FD14's text still says "F5's founder decision 3"; it now means founder decision 4.

**Sources read on 2026-11-02:** GitHub, *Skipping workflow runs* (docs.github.com); GitHub, *Permissions required for GitHub Apps* (docs.github.com); GitHub changelog, *Draft pull requests are now available in all repositories*, 2025-05-01 (github.blog); `detect-secrets` on PyPI and its GitHub README; Anthropic's pricing page (platform.claude.com); Sentry's Seer documents (docs.sentry.io), which did not show warranty wording. Historical rows above that name "founder decision 3" or "4" refer to the old numbering.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** liability and holding source go to the lawyer, with "provided as is, you review and merge" terms before rung 3 (item 16); the source sentence (item 14); the write-access rule at T6.5, with stacks alone "no" unless most buyers ask and GitHub Enterprise Server later (item 18); the GitHub trial in the free test organization shared with F22, plus one spare free account (item 23).
- **Changed, B: no paid budget** (item 10, replacing the US$150 cap). Steps 4 and 6 run on a free tier or the local model. Only fixtures and public code go to a free tier.
- **Changed, A: no rented test server.** Nothing in F5 needed one directly; memory on the 16 GB server waits for Oracle after deployment.
- **Open decisions:** none for the founder. The go itself is given at T6.5, against the approved rule.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 4 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.

The design had asked not to be approved before the GitHub trial and the buyer round. The founder approved it first; both now come before Step 1, and a result that changes the design returns to the founder.

## Founder decision 2026-11-02: approval and Step 1

F5 stays approved. The design had asked not to be approved before the GitHub trial and the shared buyer round; instead both block Step 1 (plan, Preconditions 1 and 2, kept as written), and any result that changes the design comes back to the founder (spec D64). Fixed in passing: this file's *Blocked* still said design approval was blocked; the spec's *Dependencies*, founder item 5 and buyer table, and the design's trial heading, said "before the design is approved". All now say "before Step 1".

## Founder decision 2026-11-02: rung 2b

**What it is, in plain words.** When AI is on but Nexovue cannot read a service's code, a member can click **Suggest a general fix**. The AI gets only the facts the explanation already sends, plus a language the person picks, and returns a short pattern with example code. Nexovue's code checks it, and it is shown labelled "Example pattern, not your code. Nexovue has not seen your code. Check before using." It never becomes a patch, download, pull request, alert or email.

**What was decided** (spec D65 to D72; FR31 to FR38; AC67 to AC78; plan Step 2b, T2b.1 to T2b.11):

- **Shown only** with a likely cause or an error group whose exception type passed F3's pattern, and only where rung 3 stops for want of code access (`no_code_location`, `no_read_permission`, `no_consent`). Otherwise nothing is shown. FD14 in the foundation now says this (D66).
- **Not a fallback beside rung 3:** when the repository is connected, only rung 3 is offered (D67).
- **Checked by G1 to G9:** shape, size, hidden characters, addresses, shell and process calls, install steps and non-standard imports, secrets, overclaiming words, unknown aliases. Any hit shows nothing and says why. One retry only for a wrong shape (D68).
- **Who and where:** `REQUEST_FIX`; queue `insight-fix` on `scheduler-insight`; task `insight.suggest_general_fix`; `ai_runs` purpose `fix`, marked by `fields_sent`; F5's share of tokens and the 3 tries a day (D69).
- **Stored** in `finding_fixes` with kind `general`, no source, deleted with the finding; viewers read it; no copy control (D70).
- **Hosted:** follows F3's explanations, so off today (D71; to confirm).
- **Order:** Step 2b, after Step 2 and before Step 3. It needs F3's provider, not F22, so it is the cheapest model rung and ships before rung 3. It brings forward `finding_fixes` (three migrations now), `REQUEST_FIX` and the `insight-fix` grants (D72; tracker updated).
- **Evaluation:** free-tier or local models, synthetic findings, hostile exception types and endpoint names, with a safety gate per option (AC76, T2b.10).

**Not done:** no persona review of these changes; nothing run.

## Founder decision 2026-11-02: imports and the AI asking for files

**What it is, in plain words.** Code tied to a crash is often not in the stack. So (A) for the top two stack files, Nexovue also adds the files they import from the same folder, one hop only; and (B) after reading, the AI may ask for up to 3 more files, with a reason. Nexovue checks each name and shows "The AI asks for N more files. Send them?". Nothing goes without that click. At most two extra rounds.

**What was decided** (spec D73 to D77; FR12a, FR15b, FR15c; AC79 to AC89; plan T3.18, T3.19, T4.13 to T4.15; T2.6 and T6.1 extended):

- **Imports (D73, D76):** Python, JavaScript and TypeScript, Go, Java and Kotlin, by plain line parsing in a pure module, with no new dependency, so ADR-019's third-image question does not arise. Inside the mapped folder only; same filters, secret scan and caps; order under the caps is stack, then imports, then tests. Read in the pack job after Ask, under live consent, in the same audit entry. Imported configuration still meets D9's list and is shown as left out.
- **The AI asking (D74, D75, D77):** a `needs_files` answer of up to 3 paths, each tied to a sent file and line. Names are untrusted: checked against the tree, folder, lists and caps; refused ones listed, unknown ones only counted. Checking happens at once (it sends nothing), started through one narrow database function the AI room may call. Only the person's Send sends them, as a new call. One try per proposal; each call reserves tokens; at most 3 sends, 6 calls.
- **Still not an agent loop:** no tools, no browsing, no read by the model, and every send is a person's click on an exact list. D2 now reads "fixed pack, which a person may widen". New attack A19 states what stays open.
- **`not_in_these_files`** now means the model named no file it needed, or its rounds were used up.
- **Scope sentence** gained the words on imports and the AI asking (founder's to approve, foundation decision 2).

**Not done:** no persona review; no web research on parsers was run for this change (the choice rests on ADR-019 and the plainness of import lines; the bed measures resolution); nothing run.

## Founder decision 2026-11-21: the fix routes on their own router

**What happened.** F3 Step 5 made `findings/explanations.py` import AI code (`nexovue_platform.ai.limits`, `nexovue_platform.ai.validate.recheck`, `nexovue_api.ai.service`). `findings/router.py` served both F3's explanation routes and F5's `GET /findings/{id}/fixes`, so T1.5's walk (`test_fix_facts_hold_no_ai.py`, the import half of AC33, BR12) fails with 45 import chains into AI code. **Found while applying the decision (2026-11-21):** all 45 run through `fix_read.py`, not the router: `fix_read.py` imports `parse_signals` from `findings/service.py`, which imports `explanations.py`. Moving the routes is still needed (the router imports `explanations.py` directly), but alone it did not make the walk pass, so `parse_signals`, `SIGNAL_UNITS` and `_STORED_SIGNALS` moved from `service.py` to `findings/signals.py`, which imports no AI code; `service.py`, `fix_read.py` and `test_findings.py` import them from there (the same decision's implementation, coordinator, 2026-11-21). The walk then passes at full strength.

**What was decided (option A).** F5's fix routes have their own router, `apps/api/src/nexovue_api/findings/fixes/router.py`, mounted in `main.py` beside the findings router, in the repository's shape for a second router under one package (`ai/preview/router.py`, `slos/readings/router.py`); a file named `router.py` stays visible to every structural gate, including the rate-limit coverage test, which reads only files of that name. AC33's walk starts from that module at full strength, and `test_fix_routes.py` proves the fix routes are on it and none is on `findings/router.py`. The URL, behaviour, `CurrentIdentity` gate (not on the read surface) and responses are unchanged. Plan T1.3 and T1.5 name the new module; the later tasks that add fix routes (T2b.7, T3.9, T3.16, T5.2) name it too.

**To do when T2b.7 is built.** T2b.7 (and T4.7) restore aliases in what F5 reads back. F5's code does not reach F3's alias restoring today: `fix_read.py` imports no part of `explanations.py`, and the platform's `fixes/prose.py` uses `nexovue_platform.evidence.aliases.restore`. F3's text-to-segments restorer, `_restored` in `findings/explanations.py`, sits in a module that imports AI code; if F5 needs it, move it then to a module that imports none, beside `restore` in `nexovue_platform/evidence/aliases.py`, rather than importing `explanations.py` from F5's read. Not moved now.
