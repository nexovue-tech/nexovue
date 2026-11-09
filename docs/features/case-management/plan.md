# Plan: Case management (F22)

> **Status:** Revision 2, approved by the founder, 2026-11-02. Regenerated from the revised spec after the persona reviews (`status.md`, *Reviews*). Ready to implement once F6 merges.
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under each step name the file that changes and the test that proves it, read from the code. File names marked *new* do not exist yet. **Every acceptance criterion has exactly one owning task, marked `Owns`.** A criterion may be exercised elsewhere, but its owner is where it is signed off, so **the owner is the last task the criterion depends on.** *Criteria that depend on other tasks* lists those dependencies, and T0.2 checks them.

## Preconditions

- **`spec.md` and `design.md` approved by the founder.** Both were, on 2026-11-02.
- **F6 merged to `main`.** Findings, the outbox's finding subject and migrations 0041 to 0050 exist only on this unmerged branch. `main` is protected, so F22 is its own branch and pull request.
- **F28 and F3 come first by the plan's order, and F22 needs neither.** If F3 has merged, T3.2 adds a third value to its `SCHEDULER_ROLE` and `TASK_SETS`. If it has not, T3.2 builds them, and F3 takes them over. T2.9 happens only once F3's `evidence/gather.py` exists.
- **Step 7 alone waits on Atlassian's written answer** (the founder's Jira decision, approved 2026-11-02: `spec.md`, *Founder decisions needed*, 1).

### Before Step 1

- [ ] **T0.1** · no repository file · **approved by the founder 2026-11-02 (foundation item 17): send Atlassian's developer support the one written question now:** may open-source software that a customer runs on their own server store and use (a) an OAuth 2.0 credential of that customer's own service account, or (b) that service account's API token, for their own Jira site? (D98 prefers (a).) Wait at most 4 weeks (a guess). Since Atlassian's staff said on 2025-11-21 that the token rule covers service accounts and private apps (spec, founder decision 1), Step 7 is not built unless the answer is a written yes. Nothing public mentions Jira until it is answered · the reply, or its absence, goes in `status.md`
- [ ] **T0.2** · `apps/api/tests/test_case_plan_coverage.py` *new* · reads `spec.md` and `plan.md`, and fails if any `AC<n>` in the spec has no `Owns` here, or more than one, or if any task the spec cites does not exist, or if an owner sits before a task named for it in *Criteria that depend on other tasks*, by the order of tasks in this file. **Its limit:** it checks only the dependencies written in that table, and cannot find one nobody wrote down · itself. It is deleted when F22 reaches `Implemented`, since the plan is then history

## Current system

Read from the code on 2026-11-02, with `file:line` for that working tree. Where intent and code disagree, `design.md` has the two tables: *Where this differs from the foundation* and *Where this differs from the research*.

| Area | What exists |
|---|---|
| Record | `Finding` at `apps/api/src/nexovue_api/data/postgres/models.py`, with checks and two open indexes. Migration `0041_anomaly_detection.py:256-292` |
| Person's acts | `findings/service.py:168-291`, gated at `findings/router.py:95-135`, with `_lock` at `:294`. `queries.lock_finding` takes `FOR UPDATE` (`queries.py:108`). `_member_view` reads ClickHouse under it. The session commits after the route returns (`deps.py:63-73`) |
| Feed | `findings/queries.py:39,46-50,55-74`, bound to one project. `router.py:41-54` requires `project_id`. `features/findings/ordering.ts`. `useFindings` sends `state: "all"`. `findingsRoute` declares no search (`router.tsx:249-253`) |
| Job's writes | `_OPEN`, `_UPDATE`, `_CLOSE` at `detection/project_job.py:278-303`. `_OPEN` called at `:910,1430`. Notify calls at `:925,945,1116,1139,1372`. One transaction per 25 services (`:163,616`). `_OPEN_FINDING ... FOR UPDATE` at `:262-268` |
| Notify | `detection/notify.py:200-276`. `finding_opened` returns on muted before `_flap_guard` (`:213-215`), which writes `flapping` (`:402-420`) |
| Shared SQL pattern | `RESOLVE_FINDING_SQL` in `nexovue_platform/detection.py:241`, imported by `findings/queries.py:15-20` |
| Retention | `detection/retention.py:30-37`, run by `jobs/expiry.py` |
| Queue | `jobs/queue.py`, `jobs/registry.py:19-30`, `QUEUE_CAPS` at `jobs/worker.py:72`, preload at `jobs/preload.py`, default queues in `__main__.py` `worker_queues`, enqueue contract `nexovue_platform/jobs.py` |
| Outbound | `probes/http_check.py` `perform`, `probes/addresses.py`, `alerts/senders/http.py:31-53` |
| Click and wait | `channels/send_test.py`, `checks/run_now.py` `add_job`, `collect_result` |
| Webhook | `deploys/signatures.py:56-93`, `deploys/router.py:38,127-140`, `ratelimit/policy.py:125-127,277` |
| Secrets | `nexovue_platform/checks/sealing.py:93-137`, `nexovue_api/keys.py:58-93` |
| Install | `deploy/compose/nexovue.yaml:84-115,380-404`, `deploy/nexovue:105-143,230-231`, `roles.py:19-30`. Settings check at `scheduler/settings.py:172-178` |
| Edge | `deploy/caddy/Caddyfile:83` has `form-action 'self'`. `apps/api/tests/test_caddyfile_headers.py` reads it |
| Monitoring | `deploy/monitoring/rules/nexovue.yml:16-22`, `prometheus.yml:45` |
| Email | `alerts/senders/email.py:21` selects owners only. Mail settings are on `scheduler-light` |
| Alert words | `alerts/messages.py:63-70,276,394-395`, `alerts/payloads.py` |
| Screen | `features/findings/FindingScreen.tsx`, `finding-page.ts:85-101`, `finding-words.ts`, `FindingsScreen.tsx:62-64,338-352,381-395`, `shared/settings/settings-nav.ts`, `app/router.tsx:448-513`, `app/layout/AppShell.tsx`, `features/alerts/IncidentScreen.tsx`, `features/auth/account-sections.tsx` |
| Member removal | `members/service.py:147-167` `change_role`, `:188-222` `scrub`, which keeps the row. `profile/personal_data.py:48-115` is the export |
| Seeding | `tools/detection-seed/seed.py` writes history only, and cannot open a finding |
| Tenant test | `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` exists |
| Web test bed | vitest and jsdom (`apps/web/package.json:12,40,45`). No browser tests |

**Work already done, and so struck:** none. No lifecycle, connection or tracker code exists.

**A small finding:** `needsAttention` in `ordering.ts:53` is exported, and only its tests use it. T2.3 removes it rather than teaching it about cases.

## Change required

[`spec.md`](spec.md) and [`design.md`](design.md), once approved. This section restates neither.

## Impact

| Area | What changes |
|---|---|
| Files | About 35 new, about 70 changed, guesses. Listed below |
| Services | API; `scheduler-light` gains a task every 5 minutes; beat gains two entries and a metric; new optional role `scheduler-connections`; edge gains one route's header |
| Schema / migrations | Four Postgres migrations. No ClickHouse change |
| UI | Finding page, feed, new `/settings/connections`, the console banner, the incident page, the member removal and account deletion screens |
| Alert messages | One fixed line on people-read channels |
| Tests | About 20 new files. Existing inventories extended |

## Reuse

Must not rebuild: `signature_matches`; `_bounded_body`'s pattern; `SealedBox`; `SEALED_COLUMNS`; `perform`; `add_job` and `collect_result`; `Tier` and `_OWN_TIERS`; `Button`, `ConfirmPanel`, `InlineFailure`, `SelectField`, `SkeletonRows`, `CopyButton`, `Permitted`; `formatZonedTime`, `formatRelativeTime`; `useCan`, `isConflict`, `liveOptions`; `person_name`; `FORMER_MEMBER_NAME`; `RESOLVE_FINDING_SQL`'s shape; `apps/scheduler/tests/recording_site.py`, as the base of the new fake; `tools/detection-seed`; the structure of `test_detection_has_no_ai.py`; F3's `SCHEDULER_ROLE` and `TASK_SETS`, if merged.

## Gaps

Genuinely new: case columns, `finding_events`, `finding_notes`, `finding_links`, `connections`, `connection_repositories`, `service_repositories`; `nexovue_platform/cases.py`, with `RECORD_FINDING_EVENT_SQL` and `finding_event_parameters()`; `spell_state()`; `issue_text()`; `Tracker` and its two classes; the App manifest flow and its edge route; `scheduler-connections`; `cases_sweep`; the settings screen; a stateful tracker fake; a fault command for development.

## Steps and tasks

Each step ends with `just check`-level tests of what it touched. Per the founder's way of working, the whole-change run is once at the end of a step, not after every edit.

### Criteria that depend on other tasks

The task that first builds a behaviour exercises it. The owner below signs it off, once every part it names exists. T0.2 reads this table. Each row names the last task the criterion needs.

| Criterion | Needs | Why |
|---|---|---|
| AC4 | T4.5 | "Every link unchanged, no tracker call" means nothing until a link and the job exist |
| AC10 | T2.6 | A locked case is cleared by the next sweep |
| AC28 | T2.4 | The filter lives in the address, through `validateSearch` |
| AC31 | T4.6 | The email and comment. `finding_links` arrives at T4.2 |
| AC35 | T3.4 | The sealed columns exist from T3.4, and T3.3's reseal test grows there |
| AC41 | T4.8 | GitHub's own rendering |
| AC45 | T4.5 | "Running the job twice", and each link status's moves. T4.4 proves only the pure table |
| AC46 | T5.1 | The webhook half |
| AC57 | T4.6 | `no_activity` comes from T2.6, and `tracker_closed` from T4.5 |
| AC58 | T4.7 | A synced link (T4.2, T4.3) and the confirmation on the screen |
| AC59 | T2.4 | The feed row and the page, beyond the server's rank |
| AC66, AC67 | T8.9 | Layout and visual order are checked by hand at Design QA, and the page's links section arrives at T4.7 |
| AC68 | T2.7 | Its page names the quiet case |
| AC75 | T4.5 | Links resume, which needs links and the job |
| AC81 | T3.10 | The screen |
| AC90, AC99 | T4.3 | Each names a link, which arrives then |
| AC93 | T4.7 | The job (T4.5), the table (T4.4) and the page saying so |
| AC105 | T4.7 | It announces link status |
| AC108 | T8.9 | Checked by hand, and `/settings/connections` is not finished until T6.3 |
| AC114 | T4.7 | "Real counts" of links, from T4.2 |
| AC130 | T5.2 | A real webhook delivery, after T5.1 |
| AC133 | T4.7 | The state table has joined, held, linked and ending rows |
| AC134, AC135 | T7.6 | Real Jira |
| AC137 | T7.5 | The Jira screens |
| AC138 | T7.2 | `JiraTracker` |

### Step 1: A person can work a case on a finding

Nothing external. This is the part the founder can judge fastest, and it answers "is a case on the finding page right?" before any tracker work.

*Judgeable:* run `just detection-seed`, then `just detection-fault --service checkout`, and wait for the real project job to open a finding. On its page, click **Take this**, write a note, reassign a colleague, resolve, reopen. Read the timeline, which shows the opening. Then `just detection-fault --service checkout --clear`, and see "Recovered" arrive on the timeline. Sign in as a viewer and see no action.

- [x] **T1.0** · `tools/detection-seed/fault.py` *new*, `justfile` · a development command that writes abnormal or normal per-minute rows for a named service, so the real project job opens and closes a finding. Refuses any host but localhost, as `seed.py` does. No direct row inserts into `findings` · `tools/detection-seed/test_fault.py` *new*, and a live run that it opens, closes and reopens a finding
- [x] **T1.1** · `packages/py-platform/src/nexovue_platform/detection.py`, `nexovue_platform/cases.py` *new* · in `detection.py`, `CASE_STATES`, `CASE_RESOLUTIONS` with `recovered_untouched`, and `FINDING_EVENT_KINDS` with `explained`. In `cases.py`, `RECORD_FINDING_EVENT_SQL` and typed `finding_event_parameters()` with no free-form detail. Each caller executes it · new `packages/py-platform/tests/test_finding_events.py`, `apps/scheduler/tests/test_detection_lifecycle.py` vocabulary, `test_cross_service_contracts.py` holding the no-driver rule. **Owns AC24**
- [x] **T1.2** · `apps/api/migrations/versions/*new*_a_finding_grows_into_a_case.py`, `data/postgres/models.py`, `organizations/tables.py` · columns, checks, two unique indexes, the organization-wide partial indexes, `UNIQUE (org_id, id)` on `users` and `findings`, `UNIQUE (org_id, project_id, id)` on `findings`, `finding_events`, `finding_notes`, `finding_emails_owed`, composite keys for assignee, resolver, actor, author and root, and inventory entries, in one task (F6's C20). Check `users`' change against the deletion order at `organizations/tables.py:32-52` · `apps/api/tests/test_deletion_live.py`, `test_setup_schema.py`, `apps/scheduler/tests/test_erasure_inventory.py`; live direct-insert refusals for an assignee, actor, author and cross-project root, which grow in T4.2 and T6.2
- [x] **T1.3** · `auth/permissions.py`, `auth/policy.py`, `apps/web/src/shared/identity.ts`, `identity.test-util.ts`, `docs/features/team-and-api-access/spec.md` FR1 · `OPERATE_CASES`, `DELETE_ANY_CASE_NOTE` · `test_policy.py:22-37`, `test_auth_routes.py:278`, `MembersScreen.test.ts:67-87`
- [x] **T1.4** · `packages/py-platform/src/nexovue_platform/cases.py` · `RESOLVE_CASE_SQL`, `REOPEN_CASE_SQL`, `DETACH_OPEN_JOINED_SQL`, `JOIN_OPEN_SQL`, `SET_WANTED_STATE_SQL`, `RECORD_CASE_AUDIT_SQL`, each with its parameter function, including the rule that entering `investigating` sets `case_activity_at` · `packages/py-platform/tests/test_case_transitions.py` *new*
- [x] **T1.5** · `findings/cases.py` *new*, `findings/queries.py`, `audit/service.py` · `_root_of`, start, take, assign, resolve with the leave-open choice, reopen, notes, with events and audit. Locks are `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`), and each commits before building its response · `apps/api/tests/test_findings_cases_live.py` *new*. **Owns AC2, AC3, AC6, AC7, AC14, AC16, AC19, AC20, AC61, AC62, AC102**
- [x] **T1.6** · `findings/service.py`, `findings/schemas.py` · `acknowledge`, `mark_false_alarm`, `accept_new_normal` call into `cases.py` and commit before their view; `to_finding` and `_member_view` carry the new fields; `reader.is_real_person` · `test_findings_actions_live.py`, `test_findings.py`. **Owns AC1, AC8, AC60, AC65**
- [x] **T1.7** · `findings/router.py`, `findings/schemas.py`, `test_authorization_coverage.py` · `PATCH /case`, `GET /timeline`, `POST` and `DELETE /notes`. The note delete route takes `CurrentIdentity` and goes in `EXEMPT`, with its reason · `POLICY_OF`, `test_read_surface_structure.py:55`, `test_findings_api_live.py`. **Owns AC9, AC21, AC22, AC69**
- [x] **T1.8** · `members/service.py` `scrub` and `change_role`, `findings/cases.py` `clear_assignee` · clear assignments with an event, for removal, account deletion and a move to viewer, with `SKIP LOCKED`. `assign` reads the assignee `FOR SHARE` · `test_personal_data_live.py`, a live test per route in `test_findings_cases_live.py`. AC10's sweep half and its sign-off are T2.6's
- [x] **T1.9** · `packages/py-platform/src/nexovue_platform/email/templates.py`, `findings/router.py` · assignment email after commit, as `members/router.py` sends, with the strict name, and `email_outcome`. **The 5-minute limit needs no new column:** a query over `assigned` events on the root by recipient, under the root's lock, excluding self-assignment (D91). `finding_event_parameters()` carries the recipient as `assignee_id` · `test_findings_cases_live.py`, including two assignments inside 5 minutes and after them. **Owns AC11, AC117**
- [x] **T1.10** · `detection/events.py` *new*, `detection/project_job.py` at the notify call sites · events for opened, severity raised, joined, closed and how, adopted, written beside the notify calls and not inside them. `spell_state()` is read-only, on `_OPENINGS_SINCE` and `_LAST_FINDING`, never `_MARK_FLAPPING` · `test_detection_events_live.py` *new*: muted, snoozed and normal findings each give the right events, with row counts. **Owns AC23**
- [x] **T1.11** · `features/findings/api.ts`, `finding-page.ts`, `finding-words.ts` · every sentence in the spec's tables, available actions, FR48's lines, zoned times with a date and zone · `finding-page.test.ts`: a table over every combination. **Owns AC5, AC63, AC98, AC100, AC107**
- [x] **T1.12** · `features/findings/FindingScreen.tsx`, new `CaseSection.tsx`, `CaseLine.tsx`, `Timeline.tsx`, `NoteBox.tsx`, `ReassignPanel.tsx` · the work-first layout, every row of `design.md`'s state table, the two labelled action groups, the jump link, the page-level status region, 30-second refresh, Copy as text · `FindingScreen.test.ts`, `render-harness.test-util.ts`. Layout goes on the Design QA list. It builds the tests AC66, AC67, AC105 and AC133 grow from. **Owns AC18, AC104, AC106, AC132**

### Step 2: A case survives recovery, and a returning problem lands on it

*Judgeable:* run `just detection-fault --service checkout`, take the finding, then `--clear`. The feed still shows it near the top, with its sentence. Run the fault again: the real job opens a new finding that joins the case. Choose *Active cases* in the filter. Then take another finding, clear it, do nothing else, and see the page say when it ends by itself.

- [x] **T2.1** · `detection/project_job.py` `_OPEN`, `findings/cases.py` · subquery for `root_finding_id` with the 7-day window; detach on every resolve; `JOIN_OPEN_SQL` on start and reopen · `test_detection_project_job_live.py`, with a barrier test for the lock order: F6's open indexes and notifications as before. **Owns AC12, AC13, AC64, AC101, AC111**
- [x] **T2.2** · `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` · the new statements in `detection/events.py`, `project_job.py` and `nexovue_platform/cases.py` pass with no new exception · itself
- [x] **T2.3** · `findings/queries.py`, `findings/service.py`, `findings/router.py`; `features/findings/ordering.ts`, removing the unused `needsAttention` · a separate statement for the case filters, bound to `org_id`, joining projects and users; `project_id` not used for a person, required for a token; the shared `EXISTS`-based rank; `case_severity` · `test_findings_api_live.py`, `ordering.test.ts`, and a shared fixture. **Owns AC26, AC27, AC71, AC110**
- [x] **T2.4** · `features/findings/FindingsScreen.tsx`, `features/findings/api.ts`, `app/router.tsx` (`validateSearch` for `case`), `FindingScreen.tsx` back link · `SelectField` filter, own query key, hidden summary and headline, the case row, row Resolve, zero results, subtitle, comment · `FindingsView.test.ts`, `FindingScreen.test.ts`. **Owns AC28, AC59, AC91, AC109, AC128**
- [x] **T2.5** · `detection/retention.py` · new clauses, docstring, with no clause on a joined finding's root · `test_detection_prune_live.py:101` and new cases. **Owns AC29, AC30**
- [x] **T2.6** · `apps/scheduler/src/nexovue_scheduler/cases.py` *new*, `jobs/registry.py`, `beat.py` · `cases_sweep`, every 5 minutes: the idle warning, which adds its `finding_emails_owed` row, idle resolve, untouched resolve, healing joins, leaving a resolved root, clearing a removed or demoted assignee. It writes `audit_log` with no person · `test_beat_schedule.py`, `apps/scheduler/tests/test_cases_sweep_live.py` *new*, including a stream of joined findings that must not extend a case. **Owns AC10, AC17**
- [x] **T2.7** · `findings/queries.py`, `findings/service.py`, `finding-page.ts` · earlier case, Reopen that case, ends-on values, deletes-on, joined count kept · `test_findings_api_live.py`, `finding-page.test.ts`. **Owns AC15, AC68, AC95, AC112**
- [x] **T2.8** · `profile/personal_data.py`, `profile/queries.py`, `profile/schemas.py` · `notes_written`, with a truncation flag · `test_personal_data_live.py`. **Owns AC25**
- [ ] **T2.9** · *Waits on F3 (founder, 2026-11-07): Step 2 is committed without it, and it is built once F3 lands. F3's `evidence/gather.py` is not on the branch; F3 creates it in its Step 1 (T1.6) and adds the `explained` notification this writes beside in its Step 2 (T2.7).* · `apps/scheduler/src/nexovue_scheduler/evidence/gather.py` (F3's) · writes `explained` to `finding_events` beside F3's notification, in its transaction, on `scheduler-light`. **Only once F3's code exists.** If F3 is not present, nothing breaks and this waits · `test_evidence_gather_live.py`
- [x] **T2.10** · *Done 2026-11-07. The admin-removal wording is the founder's option B (`status.md`, *Founder answers, 2026-11-07*); AC121 and AC122 are satisfied.* · `features/alerts/IncidentScreen.tsx`, `features/findings/FindingScreen.tsx`, `features/auth/account-sections.tsx`, the members screen, `docs/features/personal-data/spec.md:100-101` · the case-versus-alert line, and BR16's sentence · `IncidentScreen.test.ts`, and a test on each leaving screen. **Owns AC121, AC122**
- [x] **T2.11** · `apps/scheduler/src/nexovue_scheduler/alerts/messages.py` · FR47's line, on people-read channels only, decided from the row when composed · `test_alert_messages.py`, comparing rendered output as well as rows. **Owns AC120**

### Step 3: The connection, and a working GitHub App

*Judgeable:* on `/settings/connections`, click through creating and installing a private App on a test repository, through the real console behind Caddy. See it listed, tested, and failing plainly when uninstalled. Abandon a setup and start again. **This needs a separate free test organization with one private repository, owned by the founder's account (D93). Creating it, and each settings change on GitHub, needs his approval at the time.**

*Built 2026-11-08 against a simulated GitHub, by the founder's decision of that day (foundation item 28): `apps/scheduler/tests/tracker_fake.py`, shaped from GitHub's documentation and unverified against GitHub. The judgeable check above, and T3.0, are the founder's after Phase 2. `status.md`, *Step 3 built*, lists what stays unverified until then.*

- [ ] **T3.0** · *Not yet run: the founder runs it after Phase 2 (foundation item 28, 2026-11-08). It cannot be run from the build session, and its result is not faked. Step 3 was built without it; what stays unverified until it runs is in `status.md`, Step 3 built, Unverified until the founder's real-GitHub run.* · no repository file · **a trial first:** run the manifest flow by hand from a private address, with `active: false`, through a throwaway console route behind the real Caddy, including the redirect back. Record what GitHub answers, whether `installation` events need asking for, who must act in GitHub, and whether the flow and later calls work through a proxy · result goes in `design.md` and `status.md`. If it fails, the *unreachable install* path changes before T3.5 is built
- [x] **T3.1** · *Done 2026-11-08; also `LINK_STATUSES`, `CONNECTION_FAILURES`, `FAILURES_BEFORE_FAILING`, the sealing purposes and job arguments, `is_github_account`, `is_manifest_code`.* · `packages/py-platform/src/nexovue_platform/connections.py` *new* · queue names `connections` and `connections-interactive`, the cap of 2 on `connections`, task names, purposes, status enums, `kind` with `github_fix`, `link_url()` · `test_cross_service_contracts.py`, `apps/scheduler/tests/test_connections_vocabulary.py` *new*
- [x] **T3.2** · *Done 2026-11-08. F3 had not merged, so this built `SCHEDULER_ROLE` (`full`, `connections`), `--tasks`, `TASK_SETS`, `PRELOAD_OF` and `worker.use_task_set`, for F3 to take over.* · `roles.py`, `__main__.py` (`--tasks`), `settings.py` (`SCHEDULER_ROLE`), `jobs/registry.py` (`TASK_SETS`), `jobs/preload_connections.py` *new*, `jobs/worker.py` (`QUEUE_CAPS` gains `connections: 2`; preload chosen before the first `Slot`), `deploy/compose/nexovue.yaml`, `nexovue.quickstart.yaml`, `nexovue.build.yaml`, `nexovue.kafka3.yaml`, `docker-compose.yaml` · the role, on `--queues connections,connections-interactive --concurrency 3`, as F5's D40 requires, and its narrow settings, keys on the API and this role only. `worker_queues`' default stays without either queue. **No `nexovue.hosted.yaml` entry** · `test_roles.py`, `test_scheduler_settings.py` (Settings starts with exactly the allowed list and `ENV=production`), `test_compose_contract.py` (asserts the hosted file has none; the new key in `SETTINGS`), `test_compose_dependencies.py:155`. **Owns AC33**
- [x] **T3.3** · *Done 2026-11-08: `KEYS_ADDED_LATER` in `deploy/nexovue`.* · `deploy/nexovue` `write_secrets`, `add_missing_secrets`, `keys.py`, `docs/operations/key-rotation.md` · new key, and upgrade adds it · `test_keys_live.py`; `apps/api/tests/test_install_secrets.py` *new*, running those shell functions on a temporary settings file: an upgrade adds the key and leaves existing values alone. The reseal half of `test_keys_live.py` lands in T3.4, with the columns
- [x] **T3.4** · *Done 2026-11-08: migration 0055, which also lets `job_results` hold `connection_test`.* · `migrations/versions/*new*_connections.py`, `models.py`, `organizations/tables.py` · `connections` with setup columns, `connection_repositories`, `UNIQUE (org_id, id)` on each, checks and inventory, `SEALED_COLUMNS` · `test_deletion_live.py`, `test_setup_schema.py`, `test_keys_live.py`, carrying each sealed column to a new key. **Owns AC35**
- [x] **T3.5** · *Done 2026-11-08. The job is added on the request's own transaction (`connections/jobs.py`), so the sealed code and its conversion commit together.* · `auth/permissions.py`, `auth/policy.py`, `shared/identity.ts`, `identity.test-util.ts`, `identity.test.ts`, `test_policy.py`, `test_auth_routes.py`; `apps/api/src/nexovue_api/connections/` *new*: `router.py`, `service.py`, `queries.py`, `schemas.py`; `main.py` · `MANAGE_CONNECTIONS`, manifest and complete routes, state check, list with fields by reader, disconnect and its hook, hosted refusal, marking an expired setup `disconnected` · `apps/api/tests/test_connections_live.py` *new*, `test_authorization_coverage.py`. **Owns AC76, AC136**
- [x] **T3.6** · *Done 2026-11-08, against the simulated GitHub only. `tracker_harness.py` (forked child, fault points) moves to T4.5, whose kill tests are its only user; Step 3's tests drive the jobs' own functions with an injected endpoint.* · `apps/scheduler/src/nexovue_scheduler/connections/` *new*: `tasks.py`, `github.py`, `trackers.py`; `TASK_SETS`; `apps/scheduler/pyproject.toml` (PyJWT, with its crypto extra if RS256 needs it); `apps/scheduler/tests/tracker_fake.py` *new*, `tracker_harness.py` *new* · conversion, nulling the code and discarding `client_id` and `client_secret`, installation listing, narrowed tokens, repository refresh by connection, `check` · `apps/scheduler/tests/test_connections_github_live.py` *new*. **Owns AC38, AC74**
- [x] **T3.7** · *Done 2026-11-08. AC32's list holds the install's object store too: open decision 1 in `status.md`.* · `apps/scheduler/tests/test_connections_have_no_ai.py` *new*, starting its walk from `TASK_SETS["connections"]`, with a check of a running child's `sys.modules`; `apps/api/tests/test_no_tracker_calls_in_api.py` *new*, an allowlist of HTTP clients holding `billing/razorpay.py` alone. **Owns AC32, AC34**
- [x] **T3.8** · *Done 2026-11-08: tier `connection-tests`, 10 a minute.* · `ratelimit/policy.py`, `connections/router.py`, `connections/service.py` · `connection-tests` tier and routes, on `send_test.py`'s shape · `test_ratelimit_coverage.py`, `test_connections_live.py`, a test per connection state and move. **Owns AC79**
- [x] **T3.9** · *Built 2026-11-08; the hand check in a real browser and the run against the real Caddy image are not run (no Docker here, no GitHub): the founder's, with T3.0. AC73 stays open until then.* · `deploy/caddy/Caddyfile`, `apps/api/tests/test_caddyfile_headers.py`, `features/settings/GitHubStartScreen.tsx` *new*, `app/router.tsx` · the full-page start route, and its own `Content-Security-Policy`. The test proves only that route widens `form-action`. A run against the real Caddy image proves the header reaches the browser. **By hand, in a real browser, recorded in `status.md`:** the form posts, and the redirect back passes. **Owns AC73**
- [x] **T3.10** · *Done 2026-11-08. The banner's tests are in `ConnectionsScreen.test.ts`, mounting `ConnectionsBanner`, not a new `AppShell.test.ts`.* · `features/settings/ConnectionsScreen.tsx` *new*, `features/settings/api.ts`, `shared/settings/settings-nav.ts`, `app/router.tsx`, `app/layout/AppShell.tsx` (banner) · every state and setup row in `design.md`, *What leaves this server* on the screen, stacked tables at phone width, the banner · `ConnectionsScreen.test.ts` *new*, `route-manifest.test.ts`, `viewer.test.ts`, `app/layout/AppShell.test.ts` *new*. **Owns AC81, AC113, AC124**
- [x] **T3.11** · *Done 2026-11-08.* · `connections/router.py`, `connections/service.py`, `connections/tasks.py`, `ConnectionsScreen.tsx` · `POST /connections/{id}/credential`, re-sealing in place · `test_connections_live.py`, `test_connections_github_live.py`. AC75's sign-off is T4.5's, once links exist
- [x] **T3.12** · *Built 2026-11-08 except its beat clause: `sweep_connections` does not exist until T4.5, so "no sweep job with none live" has nothing to hold yet. Founder, 2026-11-09: the beat clause and the hourly repository refresh move to T4.5. AC118's other clauses are met.* · `deploy/nexovue` (`write_choices`, the profile at `:230-231`, and `restore_snapshot`'s stop list at `:601`), `deploy/compose/nexovue.yaml` (profile), `api` settings, `beat.py` (no sweep job with none live) · `NEXOVUE_CONNECTIONS`. Restore stops `scheduler-connections` when the setting is `yes`, and names no such service when it is `no` · `test_compose_contract.py`, including that the stop list holds every role writing to Postgres; a shell test of `restore_snapshot`'s list with the setting on and off; `test_connections_live.py`, `test_beat_schedule.py`. **Owns AC118**
- [x] **T3.13** · *Done 2026-11-08, except the oldest-due-link alert, which needs links (T4.2). The promtool tests were written and not run: no Docker or promtool here.* · `deploy/monitoring/prometheus.yml`, `rules/nexovue.yml`, `tests/nexovue_test.yml`, `scheduler/metrics.py`, `beat.py` (`connections_active`), `docs/self-hosting/sizing.md` (correcting the total to 13.75 GB, and the role's guess) · the alert, metrics, memory · monitoring tests, `test_scheduler_metrics_live.py`. **Owns AC123**

### Step 4: Create a GitHub issue, and stay in step by polling

*Built 2026-11-09 against a simulated GitHub (foundation item 28); T4.8 and T4.9 are not run. `status.md`, *Step 4 built*, has what stays unverified.*

*Judgeable:* on a case, click **Create an issue**, read the preview, confirm. Close it on GitHub, and within 5 minutes the case reads resolved. Reopen it there; the case follows. With a signal still open, close it: the case stays, and says so. Resolve in nexovue; the issue closes, with one comment. Resolve another and leave its issue open.

- [x] **T4.1** · *Done 2026-11-09. The comment words beyond FR26's are new: open decision 4 in `status.md`.* · `nexovue_platform/connections.py` · `strict_name()`, `issue_text()`, fixed comments, with GitHub and Jira forms · `packages/py-platform/tests/test_issue_text.py` *new*, each hostile input with its exact output. The suite named in the spec's gate. **Owns AC42, AC82**
- [x] **T4.2** · *Done 2026-11-09: migration 0061 (down_revision 0055 until the coordinator re-chains). Beyond the design's columns: `idle_warning_at` (BR10's one idle comment per link) and `gone_at` (row 9's week of daily reads). The index tests are `test_finding_links_schema_live.py`.* · `migrations/versions/*new*_finding_links.py`, `models.py`, `organizations/tables.py` · `finding_links`, its three unique indexes and strict pattern checks, with no `url` · `test_deletion_live.py`, `test_setup_schema.py`; a live test for each index: a reference on two cases, the slot freed by `stopped`, a cross-organization insert
- [x] **T4.3** · *Done 2026-11-09. The link routes live in `findings/links.py`, what an act asks of the issue in `findings/link_sync.py`; tests in `test_findings_links_live.py`. Promote and a pasted reference stay Step 6's. Open decisions 1 and 2.* · `findings/cases.py`, `findings/router.py`, `findings/schemas.py` · preview, create with frozen facts, retry, remove, `wanted_state` and `comment_wanted` on every act that moves a case, leave-open · `test_findings_cases_live.py`, `test_cases_sweep_live.py`: a note and a link each stop the untouched end, and neither acknowledges. **Owns AC90, AC99**
- [x] **T4.4** · *Done 2026-11-09.* · `connections/compare.py` *new* · the pure comparison table, including the hold and row 4b, a reopen during it · `apps/scheduler/tests/test_connections_compare.py` *new*, one test per row
- [x] **T4.5** · *Done 2026-11-09, against the simulated GitHub only (foundation item 28). The job is `connections/sync.py`, registered in `tasks.py`; loading moved to `connections/store.py`; the sync suite is `test_connections_sync_live.py`, the seam `test_connections_tracker_seam.py`. Beat's clause and the hourly refresh are built (founder, 2026-11-09). Open decisions 5 and 6.* · `connections/tasks.py`, `connections/github.py`, `beat.py`, `tests/tracker_fake.py`, `tests/tracker_harness.py` · `sweep_connections`, `sync_connection` taking `org_id` in its kwargs and binding it on every read, the lease, pacing, backoff, `retry-after`, end of run at first failure, public check before every create, find-existing, reading connection state before each call, token revocation, comment order. **Moved here from T3.12 (founder, 2026-11-09):** AC118's beat clause (no sweep job with none live) and each connection's hourly repository refresh, scheduled by `sweep_connections`. The harness: a forked child killed with `SIGKILL` at each named fault point, commit-then-drop replies, an injected clock · `test_connections_github_live.py`, including a recovery with a live link, every comparison row through the real job, a job whose `org_id` does not match its connection, and a hold from start to both ends; `test_beat_schedule.py`. **Owns AC4, AC37, AC44, AC45, AC49, AC51, AC52, AC55, AC75, AC83, AC86, AC87, AC89, AC96**
- [x] **T4.6** · *Done 2026-11-09. Sending is `nexovue_scheduler/case_emails.py`, called by `cases_sweep`; the scheduler's shared ending is `case_endings.py`. The parity test covers resolving by all three; reopening by person and by sync shares `REOPEN_CASE_SQL` but is not compared row by row.* · `cases.py`, `alerts/senders/email.py` (`_VERIFIED_ADMINS`), `packages/py-platform/.../email/templates.py` · the emails owed through `finding_emails_owed`, failure and expiry markers, the idle comment request · `test_cases_sweep_live.py`, including two emails owed before one sweep, the idle warning with a tracker close, and a stream of joined findings against AC31; `test_case_transition_parity_live.py` *new*, person, sync and sweep, covering the detach of AC57 on every ending; `test_scheduler_postgres_tenancy_structure.py`, naming `sweep_connections`, `cases_sweep`'s first reads and beat's read as exceptions. **Owns AC31, AC57, AC78, AC94, AC97, AC103, AC129**
- [x] **T4.7** · *Done 2026-11-09. The rules live in `features/findings/link-words.ts`; page tests in `FindingLinks.test.ts`. AC58's and AC105's page wiring is tested through the pure rules, not the DOM. Open decision 3.* · `features/findings/LinksList.tsx`, `CreateIssuePanel.tsx` *new*, `api.ts`, `finding-page.ts`, `FindingScreen.tsx` · panel, link states with words by reader and by age, resolve's confirmation and leave-open, the already-closed confirmation and line on a hold, the page's hold line, the false alarm's confirmation, the status region's link changes, every remaining row of the state table, Open to assign, Copy my notes, Disconnect's real counts · `FindingScreen.test.ts`, `finding-page.test.ts`, `ConnectionsScreen.test.ts`. **Owns AC58, AC92, AC93, AC105, AC114, AC116, AC119, AC127, AC133**
- [ ] **T4.8** · *Not yet run: the founder runs it after Phase 2 (foundation item 28). `fixtures/github/issue.json` and `issue_comment.json` are shaped from documentation and marked unverified; this run replaces them.* · no repository file; `apps/scheduler/tests/fixtures/github/` *new* · **the written script, against real GitHub:** create then immediately list; close with each reason; a `Fixes #n` merge; reopen; the label's result; a transfer and its 301; delete; remove a repository; a kill-simulated retry; hostile names rendered in title and body, including `GH-12` and a hexadecimal value; `PATCH` under `issues: write`; revoking a token. Save sanitized responses as fixtures the fake replays. Record date and account in `status.md`. A real webhook delivery is T5.2's, since the route does not exist yet. **Owns AC41, AC84**
- [ ] **T4.9** · *Not run in the Step 4 build: a measurement of the built `scheduler-connections` container, which this session did not build; a number from a bare process here would not be the container's. `sizing.md` keeps its 384 MB guess, marked so.* · `docs/self-hosting/sizing.md` · **measure** idle and worst-sync memory, with the narrow task set, and state it beside the guess · a measured run. It is early because later decisions rest on it

### Step 5: Webhooks make it faster

*Judgeable:* with a reachable server, closing the issue reflects within a minute. Disabling the webhook slows it, and changes nothing else.

*Built 2026-11-09 against the simulated GitHub (foundation item 28); no migration. T5.2 is not yet run: it needs a reachable server, and the founder runs it after Phase 2. `status.md`, *Step 5 built*, has what stays unverified, and open decisions 8 to 11.*

- [x] **T5.1** · *Done 2026-11-09. Each kind's signature header and issue id live in `connections/webhooks.py` (new; GitHub only, Jira's with Step 7). The handler seam for F5 is not built: F5's D33 removed its use (open decision 10). The webhook's own tests are `test_connections_webhook_live.py`; AC46 through the real job and AC48's lease with two jobs started together are in `apps/scheduler/tests/test_connections_sync_live.py`, where the job and the simulated GitHub are.* · `connections/router.py`, `connections/service.py`, `ratelimit/policy.py`, `test_authorization_coverage.py` `EXEMPT` · `POST /connections/webhook/{id}`, 1 MiB cap, tier, a handler seam for F5, no job added; `connections/queries.py` `live_webhook_connection` · `test_connections_webhook_live.py` *new*, including AC48's lease test and AC46 by polling and by webhook; `test_ratelimit_coverage.py`, `test_ratelimit_callers.py`; `apps/api/tests/test_postgres_tenancy_structure.py`, with that lookup in `EXEMPT` and its reason. **Owns AC46, AC47, AC48, AC85, AC139**
- [ ] **T5.2** · *Not yet run: the founder runs it after Phase 2 (foundation item 28), once a reachable server exists; its result is not faked. `test_connections_webhook_live.py` builds GitHub's `issues` delivery from documentation, and whether it carries `issue.id` is unverified until then.* · no repository file; `apps/scheduler/tests/fixtures/github/` · **finishes T4.8's script against real GitHub:** a real webhook delivery reaching a real server, signature checked. Save the sanitized delivery as a fixture. Record date and account in `status.md`. No test server is rented (foundation item 9, 2026-11-02), so this waits for the Oracle deployment after Phase 2 is built, and AC130 stays open until then. **Owns AC130**

### Step 6: Reference links, mapping and public repositories

*Judgeable:* paste an issue address; it attaches. Closing it does nothing to the case. Promote it, with the warning. Map a service, and creation preselects. Try a public repository: refused; allow it as admin: warned; withdraw it.

- [ ] **T6.1** · `findings/cases.py`, `connections/tasks.py` · parse a pasted address, attach, promote as a job, cap 20 per case · `test_findings_cases_live.py`, `test_connections_github_live.py`. **Owns AC53, AC54, AC88**
- [ ] **T6.2** · `connections/router.py`, `migrations/versions/*new*_service_repositories.py`, `models.py`, `organizations/tables.py` · mapping, public allow and withdraw, with audit, and telling admins once · `test_connections_live.py`, `test_deletion_live.py`. **Owns AC40, AC72, AC115**
- [ ] **T6.3** · `features/settings/ConnectionsScreen.tsx`, `features/findings/CreateIssuePanel.tsx`, `LinksList.tsx` · mapping table, allow and withdraw, preselect, promote · `ConnectionsScreen.test.ts`, `FindingScreen.test.ts`

### Step 7: Jira (waits on the founder's decision, and on a written yes from Atlassian)

*Judgeable:* connect a test Jira Cloud site. Confirm the statuses. Create an issue. Move it to Done; the case resolves. Resolve in nexovue; the issue moves. Remove the transition; the case still resolves, and the page says what to do. **A test Jira site is needed, on a free plan if one will do. This was not checked.**

- [ ] **T7.0** · no repository file · **confirm from Atlassian's own reference:** how a client-credentials token reads `cloudId` and which scopes it needs (D98); status category keys; transition shape; `search/jql` and whether it lags; `cloudId`; scopes; how to tell a service account from a person; whether a project's access can be read. Update `design.md`
- [ ] **T7.1** · `connections/router.py`, `connections/service.py` · form, strict site pattern, sealing the service account's client id and secret (D98; email and token only on the fallback), a generated webhook secret shown once · `test_connections_live.py`; the guard half in `test_connect_guard.py`. **Owns AC39, AC80**
- [ ] **T7.2** · `connections/jira.py` *new* · `JiraTracker`: ADF, confirmed transitions, create metadata, `RateLimit-Reason`, the injectable retry wait · `apps/scheduler/tests/test_connections_jira_live.py` *new*, repeating the fault points and comparison rows, with a fake offering "Declined" first, and AC89's test extended to `JiraTracker`. **Owns AC43, AC125, AC138**
- [ ] **T7.3** · `connections/service.py` · header chosen from `kind` · `test_connections_webhook_live.py`
- [ ] **T7.4** · `cases.py` · 14-day expiry warning · `test_cases_sweep_live.py`
- [ ] **T7.5** · `features/settings/ConnectionsScreen.tsx`, `CreateIssuePanel.tsx` · form, exposure warning, expiry, confirming a project, show-once, and the fixed preview line · `ConnectionsScreen.test.ts`, `FindingScreen.test.ts`, a test per row of the Jira setup table. **Owns AC126, AC137**
- [ ] **T7.6** · no repository file; `apps/scheduler/tests/fixtures/jira/` *new* · **the same written script against real Jira, including a real webhook delivery and the label,** saved as fixtures · recorded in `status.md`. **Owns AC134, AC135**

### Step 8: Customer documents, and the whole gate

*Judgeable:* follow the setup page as a stranger would. Run all acceptance criteria with AI off. Read the claims table.

- [ ] **T8.1** · `docs/product/setup/trackers/github.md` *new*, `docs/product/setup/index.md`, migration and setup pages; `docs/product/setup/trackers/jira.md` *new*, **only if Step 7 went ahead** · setup, what crosses, what stays in the tracker after deletion, that a note lives in backups a while, an issue notifies nobody until assigned, the proxy limit, and that cases cover anomaly findings only. A demo script on the seeded development stack, covering take, note, recover, return, create issue, close issue · review; `repo-audit` link check
- [ ] **T8.2** · `docs/product/feature-catalog.md:85,337,339`, `docs/product/vision.md:133,140,244`, `apps/web/src/features/findings/FindingsScreen.tsx` comment if any remains · the amended wording, and `vision.md:244` recorded as superseded · review
- [ ] **T8.3** · `apps/api/tests/test_ai_off_cases_live.py` *new*, running the scheduler's real workers; `.github/workflows/ci.yml` · a step that fails if an AI setting or `scheduler-insight` is present · itself. **Owns AC56**
- [ ] **T8.4** · `docs/self-hosting/sizing.md`, `docs/features/case-management/status.md` · **measure the timeline read** at D65's worst shape, seeded by a named script in `tools/detection-seed/`, against the 200 ms guess. Final memory against T4.9 · a measured run
- [ ] **T8.5** · `apps/api/tests/test_connections_end_to_end_live.py` *new* · real workers with a defined log capture: a full connect, create and disconnect, searched for credentials; a hanging tracker with an alert delivery and a detection job alongside · itself. **Owns AC36, AC50**
- [ ] **T8.6** · `docs/features/case-management/status.md` · **the claims check.** Within 7 days before any public mention, re-read each rival's own page (including SigNoz's edition, Elastic's tier and Sentry self-hosted), and check every May-say and Do-not-say line against the built product and T3.0, T4.8 and T7.6's results. Run it on a private address with `active: false`. Output: a claims table in `status.md`, and a short launch note and changelog entry using only passed lines, stating self-hosted, GitHub and status-only limits. **Owns AC131**
- [ ] **T8.7** · `apps/api/migrations/versions/*new*`, `deploy/compose/nexovue.yaml`, `deploy/nexovue` · give the role its own Postgres account, `nexovue_connections`, by F3's pattern, with grants limited to its tables and the findings case columns. **Only if F3's pattern is merged;** otherwise it stays a named risk · `test_compose_contract.py`, a live privilege test
- [ ] **T8.8** · `apps/api/tests/test_finding_event_kinds_live.py` *new*, `apps/api/tests/test_case_audit_coverage.py` *new* · one test per kind in FR16; the audit test that reads the router and fails on a route with no entry. **Owns AC70, AC77**
- [ ] **T8.9** · `docs/features/case-management/status.md`, this plan's *Outcome* · record what was built, and the Design QA list, run by hand · review. **Owns AC66, AC67, AC108**

## Not in this plan

Named so that they have a home, and scheduled by nobody yet.

| Step | What | Waits on |
|---|---|---|
| **H** | Tracker connections on hosted: one shared GitHub App with GitHub's user-token check, and one Atlassian OAuth app | Founder decision 3 |
| **I** | On an incident page, one click writes a note and a tracker issue linked to the incident, with no case columns (D6) | The founder's one round of conversations (foundation item 8, approved), question 4 of D96 |
| **J** | A bulk export of resolved cases, with notes | Built only if the self-hosted default stays below 395 days after the test bed measures disk use (D95; foundation item 6, approved) |
| **K** | One fixed line on people-read channels for a handover (D87) | The founder's one round of conversations, question 5 of D96 |
| **L** | A browser test job for layout and policy (D84) | A decision to take on that work |

## New risks

Only what this analysis found.

| Risk | Why | What is done about it |
|---|---|---|
| The manifest flow behaves differently from a private address | Not tried | T3.0, before building on it |
| A GitHub App's permission for `PATCH` issue differs from the page's plain reading | "Additional permissions" mark. A fake cannot answer this | T4.8 against the real thing |
| The lock change does not stop every deadlock | Read from code and Postgres's rules; nothing run | T2.1's barrier test, which must pass before Step 2 merges |
| A composite foreign key confuses `test_deletion_live.py`'s catalog query | Read from its shape, not run | T1.2 runs it |
| F3 and F22 both change the settings check and `add_missing_secrets` | F3 goes first, and its documents are being written at the same time | T3.2 and T3.3 read what F3 did |
| Other features' documents could drift from F22 again on page order, role values and the event writer's name | They were reconciled on 2026-11-02, and each may change before approval | `status.md`, *Cross-feature reconciliation* |
| Step 3 needs real GitHub, and the founder's approval for each settings change | Personal rule | A separate free test organization (D93), asked for at Step 3, not earlier |
| Memory on the 16 GB server | About 1.5 GB left by arithmetic, with both new roles | T4.9 measures early; F5's load is separate |
| Layout and browser policy regress unnoticed | jsdom proves neither | The Design QA list; step L |
| 72 hours for an untouched case is wrong | A guess, and it ends cases by silence | The page warns, reopen is one click, and the founder's one round of conversations tests it (D94, D96) |
| Nobody uses status-only sync | No real user has been asked | Steps 1 and 2 are valuable without any tracker. Question 1 of D96, in the founder's one round of conversations |

## Questions

None open. Founder items 1 and 3 were approved on 2026-11-02 as recommended (foundation items 17 and 19). Own questions were answered on 2026-11-02 (`spec.md`, D93 to D97; `status.md`, *Own questions answered*).

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Jira's route, given Atlassian's rules on tokens. **Approved 2026-11-02:** send T0.1; build only on a written yes; no or silence for 4 weeks means GitHub only, amending `phase-2-plan.md:68`'s *Done when* | **Step 7 waits for Atlassian's written answer**, as does any public mention of Jira | `spec.md`, *Founder decisions needed*, item 1 |
| 3 | Whether tracker sync must be there when hosting opens. **Approved 2026-11-02:** yes, a launch condition of hosted paid plans | **Blocks no step.** Step H is required before hosted paid plans open | `spec.md`, *Founder decisions needed*, item 3 |

Settled or answered, kept so old references resolve: **2**, self-hosted retention, is foundation item 6, approved 2026-11-02 (step J is now D95). **4** is folded into 1. **5**, the test GitHub account, is D93. **6**, ADR numbers, is D97.

## Gates

- [x] Specification approval: 2026-11-02
- [x] Design approval: 2026-11-02
- [x] **Plan approval:** 2026-11-02
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA: the built screen against the approved design, using the hand list below
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- **Per step:** the tests each task names, then `just check`, once at the end of the step. Read the counts; an exit code is not evidence.
- **Live suites** need Docker, as they do today.
- **The gate suite for outbound text:** T4.1, T7.2, and T4.8 against real GitHub. Any change to `issue_text()` repeats T4.8's render check.
- **Coverage of criteria:** T0.2, within its stated limit.
- **Real tracker runs:** T3.0, T4.8, T7.6, and T8.6. **CI proves the state machine against a fake,** whose fixtures come from those runs and can go stale.
- **Crash tests:** T4.5's harness kills a forked worker at each named fault point. T7.2 repeats it for Jira.
- **Design QA hand list,** recorded in `status.md`, since jsdom has no layout: at 375, 768, 1280 and 1920 px, on the finding page in each state, the feed in each filter, and settings in each state: no sideways scroll, no truncated case sentence, tab order matches what is seen, action targets, stacked tables, and the form post in a real browser.
- **Not done by this plan:** a load test. Poll and write figures stay arithmetic until a real install shows them.

## Rollout and rollback

Nexovue is pre-deployment, so there is no live data to protect. Migrations add columns and tables, and a rollback before release is simply not merging.

**The real risk is Step 1 shipping and nobody judging it.** Steps 1 and 2 can merge and be useful with no tracker at all, and should be judged before Step 3 begins. The second real risk is that the Jira question lingers, which is why T0.1 comes first. GitHub need not wait for it.

**Once customers exist:** tracker sync can be stopped per connection by disconnecting, and for an install by `NEXOVUE_CONNECTIONS=no`, leaving cases fully working (AC55).

## Outcome

Filled in when the work completes.
