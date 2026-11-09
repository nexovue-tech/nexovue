# Status: slo-tracking (F19)

## Lifecycle status

**In development. Step 5 is on `feature/phase-2` (2026-11-09).** Steps 1 to 4 are on `feature/phase-2` (2026-11-08), except T2.7, the founder's judgement; Step 4's decisions were answered 2026-11-09, each as recommended. The founder approved the specification, design and plan on 2026-11-02. Steps 4 and 5 were built under foundation item 27 (2026-11-08): the demand gate is a customer-validation milestone read afterwards, and its result is still to be read.

## Current task

Plan Step 5, *What a burn may withhold*: on `feature/phase-2` (2026-11-09; migrations 0063 and 0064, ADR-038). T5.1 to T5.9 ticked, with three open decisions below. Steps 1 to 4 are on `feature/phase-2`; T2.7's list awaits the founder's judgement.

## Completed

- **Research** ([`research.md`](research.md)), 2026-11-02: code, competitors, personas, methods. Rival rows corrected after review.
- **Specification, design and plan**, 2026-11-02, written against foundation and the code on `feature/anomaly-detection`.
- **Three things research left untraced are now traced by reading:**
  - A self-hosted organization is on Free's row, so it keeps 7 days raw and 30 days derived. This changed the history design from an hourly table to daily counts in Postgres (spec D2).
  - Check readings use derived retention.
  - Browser-door spans do reach the rollups. Nothing filters them.
- **Eight persona reviews,** then a full rewrite of the design and plan against the spec, and changes to the spec and research. See *Reviews*.

## Next action

The coordinator commits Step 5 after `just check`, restoring 0063's `down_revision` to the parallel 0061 (or the next free number) and renumbering 0063, 0064 and ADR-038 if taken. The founder answers Step 5's three open decisions (*Open decisions*, below), judges T2.7's list, and checks one inbox message and one PagerDuty alert against the running demo after Phase 2 (Step 5's *Judgeable*, not walked here). Step 6 next.

## Step 5, built 2026-11-09

**What was built, by task.**

| Task | Done | Where |
|---|---|---|
| T5.1 | `notifications.withheld_why` (`same_news` or `service_snoozed`, checked `NOT VALID`), `withheld_by_incident_id`, `withheld_by_finding_id`, `released_at`, no foreign keys (Alembic **0063**); the partial index `ix_notifications_withheld_unreleased` built `CONCURRENTLY`, and the check validated (**0064**). Both reversible: nothing exists only in these columns. `WITHHELD_REASONS` and `NEWS_KINDS` in the platform model, the frozen copy held by `test_alert_vocabulary.py`. **ADR-038** filed | `0063_…`, `0064_…`, `models.py`, `alerts/model.py`, `docs/decisions/` |
| T5.2 | `incidents.Subject` carries `about_key`, `about_signal`, `about_endpoint`, `about_check_id`; `_INSERT_INCIDENT` writes them; `_open` links a burn to an open finding on the service it is about; `_LINK_INCIDENTS` links a finding opening later to an open burn by `about_key`; the job composes a burn's subject (`alerting.burn_subject`); a deleted check finds its burns by `about_check_id` (founder decision 6) | `alerts/incidents.py`, `detection/project_job.py`, `slos/alerting.py`, `slos/burns.py`, `apps/api/…/alerts/queries.py` |
| T5.3 | `_same_news` (FR21's table, both ways), `_open_and_judged` (Y open; a burn as Y judged within 15 minutes), `_reached`; `_WITHHOLD_SECOND` drops a due news row not in a batch whose same news reached the channel within 60 minutes at the same or higher severity, or is due beside it and wins the tie (finding or threshold over a burn, older burn over newer); `_WITHHOLD_UNTOLD_ENDING` drops an ending whose subject has a withheld row and no news that reached the channel (FR24). People's channels only, after `_shape` | `alerts/outbox.py` |
| T5.4 | `release`: per channel, each subject with unreleased withheld rows that nothing holds any more gets one `opened` at its current severity, unless news of it has reached the channel since; a snoozed ending goes to a channel its news reached; all its rows are marked released. Called at the start of a claim of a people's or PagerDuty channel, so it goes with Y's ending in one message, and from `sweep_deliveries` across channels (`release_everywhere`, `_CHANNELS_HOLDING`, named in the tenancy structure test) | `alerts/outbox.py`, `alerts/tasks.py` |
| T5.5 | `_DROP_SNOOZED`: a burn's due row on a people's channel, or its news on PagerDuty, while its service's `snoozed_until` is ahead, after the channel's mute and before `_shape` and `_PIN_KEY`; `incidents` gains no `silenced` keyword, `incidents.muted` stays false. All three steps sit behind `_BURN_INVOLVED`, one guarded lookup | `alerts/outbox.py` |
| T5.6 | `_PIN_KEY`: the linked branch takes a host only at the same or higher severity when a burn is either side; a new branch shares a burn's news with an open burn about the same thing, an error-rate or p95 incident on its service, or its check's `check_state` alert, and a threshold incident's with a burn on its service, at the same or higher severity. `service_silent` and `check_state` rows never join a burn's | `alerts/outbox.py` |
| T5.7 | `LinkedIncident.burn` (target id, nexovue's label, kind, rate, hours left, allowed, left) from the incident's detail; the finding page's line "Also using allowed failures too fast: …. 14 times faster than allowed. What is left lasts about 2 days.", per kind, and "Open the target"; the service page's "2 targets. 1 running out.", linked to `/slos`; the snooze says "Also holds back reliability target messages.", the mute "Stops findings only. Reliability target alerts continue." MCP labels the label untrusted; the kind is on the closed allow-list | `findings/schemas.py`, `findings/service.py`, `assistant/tools.py`, `features/findings/`, `features/servicedetail/slo-line.ts` (new), `ServiceDetailScreen.tsx`, `shared/detection/quiet.ts` |
| T5.8 | `test_slo_regression_live.py` (new): AC95 (a), (b), (c) at both burn severities, and (e): the outbox and notify suites collected a second time, unedited, against a tenant holding an unrelated open burn | `apps/scheduler/tests/` |
| T5.9 | What a burn's webhook events say, and that a webhook gets everything; what a people's channel withholds, releases, and what a snooze and a mute do to targets; PagerDuty's sharing with targets | `docs/product/setup/alerts/webhooks.md`, `channels.md` |

**Files the plan's tasks do not name, and why.** `apps/api/…/assistant/tools.py` and `test_mcp_injection.py` (the new finding field must be labelled or allow-listed); `test_scheduler_postgres_tenancy_structure.py` (the sweep's cross-tenant channel statement, named); `shared/detection/quiet.ts` and its new test (the controls' words live there, shared by the finding and service pages); `apps/api/tests/test_slos_live.py` (the linked burn's API half); `slos/burns.py` (it builds the job's `Minute`).

**Decisions taken while building, within the design.** Each is the founder's to overrule.

- **`released_at` means "no longer held"**, a release written or none owed: a subject whose news reached the channel since (an escalation went through), or that ended unheard, gets no message, and its rows are marked so they are not read again.
- **A release looks at any Y, not only the one that withheld it**, as the design's "nothing withholds it any more" says; `withheld_by_*` stays as the delivery log's record.
- **The sweep releases on at most 100 channels per run**, locking each as a claim does, so a sweep and a claim never release one subject twice.
- **The tie in one claim only sees Y's row if it is pending and unattempted**; a Y itself withheld in that claim leaves X withheld until the next claim or sweep releases it, seconds later.

**Not built or not proved in Step 5, said plainly.**

- **AC58**: F3 writes no `explained` notification on the branch, so there is nothing to hold (*Open decisions*, 2).
- **A burn noted on a lower-severity PagerDuty alert that escalates** stays a note (*Open decisions*, 3).
- **Step 5's *Judgeable*** (one email, PagerDuty showing one alert) needs a real inbox and PagerDuty: **not yet run, founder checks after Phase 2.**
- **The finding and service lines in a browser at 375px**: not checked.
- **AC50's claim time with and without the rule**: T6.2.
- **AC53 claims at real past minutes**, because the job reads real ClickHouse minutes; against the dev stack its deliveries worker could claim those rows first. On this worktree's own stores no worker runs.

**Self-review (`/review-diff`), 2026-11-09.** Found and fixed: a snoozed ending was released at `max(severity)` of its held rows, which compares words ("warning" above "critical"), now the incident's severity; the burn guard ran twice per claim, now once; `_CHANNELS_HOLDING` read every tenant's channels unnamed (the tenancy structure test failed; named as a sweep). Tenancy: every new outbox statement binds `org_id` from the claimed channel, and every join inside it matches `org_id`; the one sweep statement is named. Authorization: no new route; `GET /findings/{id}` gains a field read under its existing gate. Migrations: nullable columns, the check `NOT VALID` then validated, the index `CONCURRENTLY` alone, both reversible. No credential, and no target name, description or reason in a notification or the finding's burn (the label is nexovue's). Unintended changes: none; every file is named above. One shell slip: `git checkout --` was run on `DetectionStrip.tsx` to discard this step's own first edit of it, which moved to `ServiceDetailScreen.tsx`; nothing else was in that file's diff.

## Step 4, built 2026-11-08

**Built under foundation item 27** (2026-11-08): the demand gate is a customer-validation milestone, read afterwards, and its result is still to be read. **P2 is on the branch with AC37's end-to-end test in its record** (`docs/changes/browser-door/plan.md`, T1.3, `apps/api/tests/test_browser_door_live.py`).

**What was built, by task.**

| Task | Done | Where |
|---|---|---|
| T4.1 | `slo_burn` and `SloBurnCondition` (`slo_ids`, `minimum_severity`, `covers_target`), parsed by kind; Alembic **0058** widens both condition CHECKs `NOT VALID`, adds `incidents.about_key`, `about_signal` (checked `NOT VALID`), `about_endpoint`, `about_check_id`, creates `slo_exclusions`, and backfills the default rule; **0059** validates the three checks; both join `ROLL_FORWARD_ONLY` where they drop records (0058). `coverage_of_service` skips the kind, `_covers_subject` reads `slo:<id>`, and every other `parse_condition` consumer was read: each skips a kind it does not judge. **ADR-034** filed | `alerts/model.py`, `0058_slo_burn_alerts.py`, `0059_…`, `models.py`, `tables.py`, `alerts/service.py`, `alerts/queries.py`, `docs/decisions/` |
| T4.2 | *Reliability targets* to the owners for a new project (`provision_project`) and an existing one (0058's `BACKFILL_SLO_RULE_SQL`, idempotent) | `alerts/defaults.py`, 0058, `docs/product/setup/alerts/channels.md` |
| T4.3 | The tiers, limits capped at 90% (FR15), the floor from counts so far or 2 (AC103), the half-failing skip (FR14), FR16's minimums and a check's short window of two intervals, severity, how long what is left lasts, and windows summed from minutes with exclusions out and lost minutes against coverage | `slos/burn.py` (new) |
| T4.4 | The six windows summed from each source's per-minute read of the last 24 hours, with no `SLO_BURN_*_SQL`; kept as built by the founder, 2026-11-09 (decision 4), and the plan's wording amended | `slos/burn.py`, `slos/store.py` |
| T4.5 | Each target's burn each minute: `slo_status` keeps the tiers, when the burn began, the last held minute and since when no tier could judge for coverage; each covering rule's position by `step()`, F19's own save keeping `evaluated_at` on a minute no tier judges (`-infinity` when never judged), `fire` each breached minute, `refresh_detail` before any `clear`, and `close_unjudged` after a measured silent day (FR17); the API reads Running out from status (`burn_from_status`) | `slos/burns.py`, `slos/alerting.py` (new), `slos/job.py`, `alerts/incidents.py`, `nexovue_platform/slo.py`, `slos/service.py` |
| T4.6 | *Nothing left*: one warning to the union of unmuted covering rules' channels, claimed once by `exhausted_armed`, re-armed above 5%, not within 7 days, not while Building history | `slos/notices.py` (new), `incidents.fire_notice` |
| T4.7 | `GET`, `POST /slos/{id}/exclusions`, `DELETE …/{exclusion_id}`, FR29's limits, `slo.excluded` and `slo.exclusion_removed` with ids only; the counts leave excluded minutes out, and the next counts run recounts the days a new or removed exclusion touches, final or not, back to the recount limit (`changed_why` `exclusion` or `exclusion_removed`); a removal past the limit is stored as not recounted, and each month it touches says "removed, figure not recounted" (AC69's last clause, 2026-11-09); the notice, to the union of covering rules' channels muted or not, or the owners with no rule; an overlapping exclusion's id and times on the open burn's detail; the reason scrubbed and exported (T3.5's deferral) and a removed one lapsing at 400 days | `slos/exclusions.py` (new), `router.py`, `schemas.py`, `queries.py`, `audit/service.py`, `slos/personal.py`, `profile/`, `slos/job.py`, `slos/counts.py`, `slos/notices.py`, `slos/retention.py`, `docs/product/setup/api.md` |
| T4.8 | `notices.report_blind` in `sweep_deliveries`: one critical incident per project with no rule and a fixed name, opened by `ON CONFLICT DO NOTHING` on **0060**'s `uq_incidents_blind_open` (built `CONCURRENTLY`, limited to `slo-project:` keys), sent to the union of unmuted burn rules' channels or the owners, resolved with one `resolved` row per channel told | `slos/notices.py`, `alerts/tasks.py`, `0060_slo_blind_notice_key.py`, `models.py` |
| T4.9 | `_said`'s `slo_burn` case by kind; *Nothing left*, exclusion and blind notices; "No longer judged" for a burn closed for a silent source | `alerts/slo_words.py` (new), `alerts/messages.py` |
| T4.10 | A rule narrowed, a target disabled, deleted or re-pointed, and a deleted check each close the open burn (machines told) and delete every rule's position | `alerts/service.py`, `alerts/queries.py`, `slos/service.py` |
| T4.11 | `slo_ids` checked against the project's live targets at write; the rule editor's kind, target choice and minimum, and its words. A burn's incident carries `zone` and `burn` (its joined findings, its service's last deploy before it began, whether the project has recorded any deploy, whether its target is deleted); the page shows its current figures and "As of", "With finding", "Last deploy: 02:30, 11 minutes before this started", "No deploy recorded" or "Deploys are not connected", links to the service or the check, the service's errors from the burn's start (the window pinned to it), and the target or "This target has since been deleted. What it recorded is above."; every time in the project's zone with its date when not today; no `max-w-3xl`. MCP labels the finding titles untrusted and allow-lists the zone | `alerts/schemas.py`, `alerts/service.py`, `alerts/queries.py`, `assistant/tools.py`, `features/alerts/` |
| T4.12 | `told` on every target (rules, channels, mute, snooze), *Who is told* on the target page in FR25a's three cases and on the form (the rules covering every target, or "No alerts will be sent"), the list's *No alerts* and *Muted* chips; channels ticked on the form write this target's own rule in the same save, `Reliability target rule <first 8 of its id>` (D57), and a second, `…, criticals`, minimum critical, with PagerDuty pre-ticked; `GET /slo-readings/{id}/burn`, the tier `slo_status` names by the minute over the last day from the same counts the job judges, exclusions left out as it left them, a gap where a window held no event, and the chart with "On pace (1)" and a description of the current and peak rate | `slos/service.py`, `slos/schemas.py`, `slos/queries.py`, `slos/readings/` (router, service, queries, schemas), `nexovue_platform/slo.py` (`without_left_out`, now shared with the scheduler's counts, and `rolling_rates`), `features/slos/` (new `SloBurnChart.tsx`), `docs/product/setup/api.md` |
| T4.13 | The payload suite, in the gate | `apps/api/tests/test_slo_ai_paths.py` (new) |

**Files the plan's tasks do not name, and why.** `slos/burns.py` and `slos/alerting.py` keep `job.py` (over 1,000 lines already) to the hand-out and counting; `alerts/slo_words.py` keeps a burn's words testable alone and scanned by AC13; `slos/exclusions.py` the same for the routes. `test_projects.py`, `test_deletion_live.py` and `test_channels_live.py` count default rules, so a third changed their numbers. `test_slo_words.py` treats a placeholder inside a string (`${x}`, Python's `{x}`) as code, so the scan can read Python.

**CI fix, 2026-11-09: a fourth suite counted default rules.** `test_apikeys_live.py::test_an_organization_cannot_revoke_another_organizations_key` lists a new tenant's whole audit, and still expected two `alert_rule.created`; CI (run 42650457940) found the third, from *Reliability targets*. The test was stale, not the code: FR25 makes this rule "like the other default rules", `provision_project` records each default rule in the creating person's name inside the creating transaction (`alerts/defaults.py`), and `test_projects.py` already asserts three such entries. A default rule is a rule the person's act of creating a project made, so it is audited as theirs, as F21's and F6's are. The test now expects three.

**CI fix 2026-11-09: the job suite counted the delivery sweep's *not being judged* notice.** CI (run 42666090002) failed `test_slo_job_live.py::test_ac60_an_exclusion_overlapping_an_open_burn_shows_on_its_incident` at `[burn_incident] = [...]`: two non-event `slo_burn` incidents. The second was not a second burn. It was the stack's delivery sweep (`scheduler-deliveries`, every 15 s) opening the project's `slo-project:<id>:blind` notice, *Reliability targets not being judged* (FR13b). That is right: the test judges minutes two hours old, so the target's judged minute is past `BLIND_AFTER`. CI's own log shows these mails. The test was wrong, not the code. Two judges cannot open two burns for one target: `judge_slos` holds the project's session lock and skips a minute that is not newer, `_POSITION` locks the position `FOR UPDATE`, and `incidents.fire` inserts `ON CONFLICT (rule_id, subject_key) WHERE resolved_at IS NULL DO NOTHING` against 0028's partial unique index. So `_incidents`, `_notifications` and T5.2's read now keep only incidents about a target (`subject_key LIKE 'slo:%'`), which is everything `judge_slos` writes. The blind notice is `test_slo_blind_live.py`'s subject. *Seen failing, then passing, under CI's conditions:* with `notices.report_blind` running every 50 ms beside the dev stack (scratch script), the unchanged suite failed this test 5 of 6 runs and then 4 of 6 runs, each time with `ValueError: too many values to unpack`; the extra row was `slo-project:<id>:blind`. With the change, the four tests that read these helpers (this one, AC60's two notice tests, T5.2) passed 12 of 12 runs under the same loop. *Still exposed, not seen failing:* the stack's own `judge_slos` (`scheduler-light`) judges each live project at the real settled minute. If it lands between a test's two judgings, the test's later, older minute is skipped. Nothing here fences that. A fix would judge the suite at minutes newer than the stack's (`test_slo_regression_live.py` uses `future()`), which is a wider rewrite of this suite.

**Found while fixing, not fixed here, for the founder: deleting an organization stalls cross-tenant work for a moment.** The stack's logs show three sweeps hitting a foreign-key error on an organization a test had just deleted: beat inserting into `jobs` (`fk_jobs_org_id_organizations`), the delivery sweep inserting into `notification_attempts`, and `report_blind` inserting into `incidents`. The beat one is a real product defect. Beat keeps its schedule in memory and re-reads `checks` every `beat_refresh_seconds` (30 s). `_enqueue_due` adds every due entry in one transaction (`queue.enqueue_all`). So a deleted organization's check that is still in memory makes the whole insert fail, and **every tenant's due checks and every sweep stay unqueued until the next refresh, up to 30 s,** retried and failing each tick ("beat could not add due jobs; they stay due"). The two sweeps fail one run (15 s) for every tenant. Production deletes organizations (erasure), so this can happen there. The fix is one change across all three: each entry or tenant gets its own savepoint, and a row the database refuses because its organization is gone is dropped, not retried. It needs its own change record and regression tests, and it is not why either CI test failed.

**Decisions taken while building, within the design.** Each is the founder's to overrule.

- **Running out needs no rule** (FR10): `burning_since` is set from the first held minute and cleared 10 minutes after the first minute no tier holds, as `step()` resolves, or after a measured silent day; a minute no tier can judge changes neither.
- **The blind notice's channels** are the unmuted burn rules' union, falling back to the owners when that is empty; a muted rule means a person said they will look.
- **The exclusion notice** is sent by the next judging, within about a minute, not by the API, because `fire_notice` is the scheduler's; one made while judging was stopped is told when it returns (up to a day later).
- **"No longer judged"** is a `closed` row to every channel told, people included, with `closed_why` in the detail; a person's closing still tells machines only.
- **"Deploys are not connected"** means the project has recorded no deploy at all. Every project is created with a deploy webhook secret, so holding one says nothing.
- **The form's channels** write up to two rules: warnings and criticals, and criticals only. Routing is `operate_alerts`, which every role that may set a target holds.
- **A target re-pointed** is any change to what is counted (service, endpoint, check, threshold, left-out endpoints), the same set that recounts.

**Not built or not proved in Step 4, said plainly.**

- **The "With finding" line** shows a burn's joined findings, but nothing joins a burn to a finding until Step 5 (T5.2), so it shows none yet.
- **AC108's "same start time":** the target page and the incident page show the same clock in the project's zone, but word the zone differently ("IST" from `slo-words.timeIn`, "Kolkata time" from the shared `formatZonedTime`). LOW; one formatter for both is T6.7's design QA.
- **The chart in a browser:** the web test mocks `TrendChart`, which draws on a canvas jsdom lacks; what the page hands it is tested, not the drawing.
- **AC96 with an AI switch:** no feature has built one; held by determinism and the no-AI walk. **F4's `get_incident` and F3's evidence** do not exist on the branch, so AC72 covers MCP only; each must add this incident when it lands.
- **AC19's quiet hours and AC92 end to end** are the outbox's existing behaviour and the pure tiers; not run through a delivery.
- **The Exclusions tab** on the target page is not built; exclusions are read and written through the API only.
- **Step 4's *Judgeable*** was not walked on the dev stack with the demo.
- **The messages for a burn closed by a person** (machines only) use the generic "nothing watches it any more" wording, which names a rule or check.

**Second self-review (`/review-diff`), 2026-11-08, after T4.11 and T4.12.** Fixed: two incident fields reached MCP unlabelled (the joined findings' titles, now untrusted; the zone, allow-listed as a zone checked at write); the delivered-at time ignored the project's zone; the form hid the owners channel, which a project whose default rule was deleted needs. Tenancy: the burn route loads its target by `org_id` and checks the caller's project; its four ClickHouse statements bind tenant and project (`test_tenant_chokepoint_structure.py`); the burn page's queries bind `org_id`. The form's channels are checked against the organization beside their field. Not fixed, recorded above: the zone's two wordings (LOW).

**Self-review (`/review-diff`), 2026-11-08.** Findings fixed: the first save of a position on a minute no tier could judge failed on `evaluated_at NOT NULL` inside its savepoint, so a burn on a stopped service never closed (AC20, AC94 seen failing, fixed with `-infinity`); `test_slo_ai_paths.py` left committed tenants whose never-judged targets led another suite's hand-out (AC65 failed in the joint run; the suite now deletes them); two unused columns read into the job's targets were removed. Not fixed, recorded: a burn rule naming only deleted targets is not marked "covers nothing" on the rules list (LOW). Tenancy: every new statement binds `org_id` but the two named sweeps (`_BLIND_PROJECTS`, `_OPEN_BLIND`), which `test_scheduler_postgres_tenancy_structure.py` holds; an exclusion id is loaded with its target's id (AC33). Authorization: both new write routes take `MayConfigureSlos`, held by `test_authorization_coverage.py`. Migrations: the condition and about checks `NOT VALID` then `VALIDATE`, the new index `CONCURRENTLY` alone, 0058 roll forward only. No credential, and no target name, description or reason in an incident, a notification or an audit detail (AC72, AC88). Unintended changes: none; every file is named above.

## Step 3, built 2026-11-08

**What was built, by task.**

| Task | Done | Where |
|---|---|---|
| T3.1 | Alembic **0053**, `slo_deleted_months`: one row per purged target and local month, keyed `(org_id, slo_id, month)`, cascading from its project; the downgrade refuses and the revision joins `ROLL_FORWARD_ONLY`; the inventory line | `0053_slo_deleted_months.py`, `models.py` (`SloDeletedMonth`), `organizations/tables.py`, `test_migration_rules.py` |
| T3.2 | A count never writes over a final day, nor over a day a newer definition counted. A history read under a newer definition rewrites every day back to the recount limit (`slo.recount_limit`: the later of what the source holds and 29 days), final or not, and a final day whose figures moved gets `changed_at` and `changed_why = 'definition'`; past the limit a day is only first read, so an existing one keeps its old definition (AC45) and a new one reads loss not known (AC69). `FINAL_AFTER_DAYS` and `LOSS_RECORDS_DAYS` moved to the platform; `longest_quiet` too, so the API's months cross midnight by the same function. The sharp-fall line is worked out where months are read (below) | `slos/job.py`, `slos/counts.py`, `nexovue_platform/slo.py` |
| T3.3 | `GET /slos/{id}/months` (up to 13, newest first), `GET /slos/months?project_id=&month=` (*Last month*: N, X, Y of M, and how many met have time not counted; no blended figure), `GET /slos/months.csv?project_id=&months=&slo_id=` (complete months, `spreadsheet_safe` on the label), `GET /slos/{id}/history` (who, to a person only). A month is the platform's one function over stored days (`slo.month_figures`): result, coverage, quiet and longest stretch, lost and excluded time, a check's missed and maintenance runs, days held, may still change, changed after it ended, an earlier definition's last day, loss known, sharp falls (FR27c), and each traffic finding or silence incident on the service (FR27a). A target deleted after a month ended counts in it; purged ones from `slo_deleted_months`. Each kind's fixed label is `slo.report_label`, used by the CSV and sent to the copy action (D42). Four routes join the read surface and `docs/product/setup/api.md` | `slos/months.py` (new), `router.py`, `schemas.py`, `queries.py`, `service.py`, `test_read_surface_structure.py` |
| T3.4 | The expiry sweep purges a target deleted over 30 days ago: each month that ended before its deletion goes to `slo_deleted_months` (once, whatever reruns), then the target goes and its status and days cascade, one transaction per target; its changes stay. Daily figures, changes and purged months lapse at 400 days. `_DUE_FOR_PURGE` is named in `CROSS_TENANT_STATEMENTS` | `slos/retention.py` (new), `jobs/expiry.py`, `test_scheduler_postgres_tenancy_structure.py` |
| T3.5 | `members.scrub` replaces the person's address and full name, as whole words ignoring case, inside every target's name and description (live or deleted) and at any depth in every change's `before` and `after`, across the organization, whoever wrote them, within each column's length. The export gains the target changes the person made, bounded with a flag as notes are | `slos/personal.py` (new), `members/service.py`, `profile/personal_data.py`, `profile/schemas.py` |
| T3.6 | The target page gains tabs *By month* (each month's sentence, what it leaves out with links to the finding or incident, a copy action, the raw counts beneath, a CSV link, "different periods", who a figure is for), *History* and *Definition* (the only place "error budget" is said, and how far back figures reach). `/slos` gains *Now* and *Last month*: the headline, why no single figure, one project, who a figure is for, a row per target with a copy action, deleted rows read "Deleted on 3 Nov.", and a CSV link. `test_slo_words.py` names the new files and allows "error budget" only in the Definition tab | `features/slos/` (new: `SloMonthsTab.tsx`, `SloHistoryTab.tsx`, `SloDefinitionTab.tsx`, `LastMonthView.tsx`, `month-words.ts`; changed: `SloScreen.tsx`, `SlosScreen.tsx`, `api.ts`), `test_slo_words.py` |

**Files the plan's tasks do not name, and why.**

- **`slos/service.py`, a restore.** Step 1's restore raised `definition_version` to queue a recount. With T3.2's recount by version, that would mark every day past the recount limit as "counted under the earlier definition" although nothing changed (AC45 false), and rewrite final days. A restore now leaves the version alone and marks its history unread (`history_read_version` below it), so the read fills the gap and leaves counted days as they were. Test seen failing first.
- **`nexovue_platform/slo.py`**: months, the recount limit, `RESTORE_FOR` and the deleted-check label moved there, because the scheduler's purge and the API's months must agree; `DayCounts.of_row` reads a stored day for both.
- **`docs/product/setup/api.md`**: the read surface's customer list, which `test_read_surface_structure.py` ties to the routes.

**Decisions taken while building, within the design.** Each is the founder's to overrule.

- **13 months include the month under way**, so the oldest is twelve months back; daily rows lapse at 400 days, which holds them. The CSV carries complete months only.
- **A sharp fall (FR27c)** compares a day's requests with the median of the same weekday in up to four earlier weeks held, as the suggestion reads four weeks; today and the first event's day are partial and never compared. Request kinds only.
- **Time not counted** (*Last month*'s "N of those met have time not counted") is lost minutes, excluded minutes, or an outage line. Quiet time is not: a nightly lull is normal.
- **An outage line** gives the outage's whole length, not the part inside the month. Check targets get none, since they count runs, even where one names a service (D19).
- **The copy and the CSV also carry "For your own team and board, not for customer promises."** (founder decision 2, foundation item 16) beside FR27b's fixed label.
- **The copied sentence always states quiet and excluded time**, "No minute without requests." and "Nothing left out for planned work." when there was none, because FR27b says both always carry them.
- **History** is read for a live or deleted target; once purged, its changes stay 400 days but no route reads them (the target row that proves its project is gone). Kept so by the founder, 2026-11-09 (decision 3).

**Not built or not proved in Step 3, said plainly.**

- **AC69's last clause** ("removed, figure not recounted") and "Changed on 3 Nov by an exclusion": exclusions do not exist until T4.7. The words are in place; nothing writes them yet. *2026-11-09: both are written from Step 4; AC69's last clause moved to T4.7 (founder decision 1).*
- **FR27's form line** on how far back each kind reaches at creation: T3.6 names no form change. The *Definition* tab says it; the create form does not yet.
- **AC81 at 375px and by keyboard** for the new tabs and view: `Tabs` is the shared accessible pattern and `DataTable` scrolls by design, but nothing was checked in a browser.
- **Step 3's *Judgeable*** was not walked on the dev stack with the demo.
- **A purged month** keeps its figures, not its outage links or sharp-fall lines (FR27a: "while those records last").
- **A removed person's name inside a target's name** could, in principle, make two live targets' names equal and fail the removal on the unique index; not handled, not seen. Residual risk.

**Self-review (`/review-diff`), 2026-11-08.** One finding, fixed with a test seen failing first: a check target that names a service (D19) would have shown the service's outages on its months, though it counts runs. Also tidied: a duplicated row-to-day conversion became `DayCounts.of_row`, and the month result is the platform's enum end to end (mypy caught the mismatch). Unintended changes: none; every file is named above. Tenancy: every new API statement binds `org_id`; every new scheduler statement binds it or is named (`_DUE_FOR_PURGE`); the month routes load the row and check the project, as `/slos/{id}` does (AC33). No new write route.

## Step 2, built 2026-11-08

**What was built, by task.**

| Task | Done | Where |
|---|---|---|
| T2.1 | Latency from buckets: `at_or_under`, the boundary holding a share, and a refusal outside the boundaries (AC101: above about 50.5 s is refused, not snapped); `SLO_SERVICE_LATENCY_SQL` reads `sumForEachMerge(latency_buckets)` per minute, and a latency target's failures are its slow requests | `nexovue_platform/slo.py`, `slos/store.py`, `slos/counts.py`; `apps/api/tests/test_slo_system.py` (new, not run) |
| T2.2 | `SLO_ENDPOINT_MINUTES_SQL` reads count columns only, binding service, inbound kinds and names; an endpoint target reads its own endpoint; left-out endpoints are subtracted minute by minute only where the service has a row, never below zero | `slos/store.py`, `slos/counts.py`, `slos/job.py` |
| T2.3 | `_COUNTED`, `UNMEASURED_OUTCOMES`, the status metric, the check id, the run attributes and the collapse moved to `nexovue_platform/readings.py` (C3); `synthetics/queries.py` binds them as module constants, and the synthetics tests pass unchanged. `SLO_CHECK_RUNS_SQL` counts confirmed runs, counted runs, up runs and maintenance runs per minute; a check day stores expected, missed and maintenance runs; a check's coverage is runs measured over runs expected outside maintenance; a check is read at its interval's cadence. `slo_rows.py` writes exact `metrics_gauge` readings (founder, 2026-11-08) | `readings.py`, `synthetics/queries.py`, `slos/store.py`, `slos/counts.py`, `slos/job.py`, `slo.py`, `apps/scheduler/tests/slo_rows.py` |
| T2.4 | `GET /slo-readings/suggestion` on `CurrentIdentity` and the telemetry tier; FR7 and FR8 in the platform; the service's endpoints with health checks marked; what a check's interval expects | `slos/readings/` (new: router, service, queries, schemas), `main.py`, `ratelimit/policy.py` |
| T2.5 | The form: what to watch, kind, generated name, suggestion and arithmetic with its loading, failed and retry states, boundary preview from the platform's boundaries, definitions by kind, refusals beside fields, sampling question with a link for *Not sure*, pre-ticked health checks, the stops-completely line with prefilled links, a check's interval line and FR16's line | `features/slos/SloForm.tsx`, `SloFormParts.tsx` (new), `api.ts`, `slo-words.ts` |
| T2.6 | Endpoint rows that add up to each service's, with a `GET /health` probe every busy minute, and check readings: confirmed up and down, overruled, a weekly maintenance hour, `prober_error` | `tools/detection-seed/seed.py` |
| T2.7 | Printed below. **Awaits the founder's judgement** | `status.md` |

**Files the plan's tasks do not name, and why.** Step 2's tasks name no API file for creating the new kinds, yet the step is "all four kinds" and its tests (AC3's 487.2 ms "stored and shown", AC4 and AC80 in `test_slos_live.py`) need the routes to take them; Step 1's code said "Step 2 widens this". So `slos/schemas.py`, `slos/service.py` and `slos/queries.py` take each kind's own fields, refuse another kind's beside the field, and check a check against the project. `slos/job.py` counts each kind, because the counts it writes are what T2.2 and T2.3 prove. No migration: 0052 already holds every column.

**Decisions taken while building, within the design.** Each is the founder's to overrule at T2.7.

- **FR7's latency suggestion** is the lowest boundary that held 95% of requests *in every week held*, so the slowest week decides it, as the worst week does for availability, and the target it proposes is 95%. The spec does not say whether "in those same windows" means each week or all four together.
- **Too few to suggest (FR8)** means the suggested objective would allow fewer than 10 failures (FR10's thin limit) over the four weeks' events.
- **A check's suggestion** applies FR7 to its counted runs by week, beside what its interval allows; the design named only services and endpoints, and T2.4 asks for check suggestions.
- **A missed run** is any expected run that was neither counted nor in maintenance, so a `prober_error` or `misconfigured` run lowers coverage rather than counting as a failure. Runs in a maintenance window leave coverage's denominator, as excluded time does (BR6).
- **Expected runs** use the check's interval and locations as they are now, from each location it names, or every location the deployment has. A disabled check expects nothing. Changing a check's interval mid-window misreads the days before the change until they are recounted.
- **Health-check names** are a guess: a whole segment `health`, `healthz`, `healthcheck`, `ready`, `readyz`, `readiness`, `live`, `livez`, `liveness` or `ping`.
- **The form asks for a suggestion when the source field is left,** not on each keystroke, so typing a name costs one ClickHouse read.
- **The stops-completely line** shows when no enabled `service_silent` rule covers the service. Nothing links a check to a service, so a check cannot hide it. The rule link prefills the service, not the silence kind, because the rule editor takes no kind.
- **The suggestion for a service availability target counts left-out endpoints in,** because it reads the service's own counts.

**T2.7: what the suggestion says for each demo service.** The running demo was not up, so this is four weeks of `tools/detection-seed` history (seed 6, the 15 demo services, one check every minute), written to this worktree's own ClickHouse and read by the API's own suggestion code. Synthetic history: it shows the arithmetic works on real rows, not whether it suits real traffic.

| Service | Availability (weeks, newest first) | Latency |
|---|---|---|
| accounting | 99.5% (99.81, 99.84, 99.79, 99.82) | 95% at or under 123.2 ms |
| ad | 99.5% (99.82, 99.82, 99.78, 99.82) | 95% at or under 11.1 ms |
| cart | 99% (99.30, 99.30, 99.29, 99.31) | 95% at or under 687.1 ms |
| checkout | 99.5% (99.77, 99.74, 99.75, 99.76) | 95% at or under 73.5 ms |
| currency | 99.5% (99.84, 99.82, 99.84, 99.83) | 95% at or under 52.1 ms |
| email | 99.9% (99.93, 99.93, 99.93, 99.94) | 95% at or under 26.2 ms |
| fraud-detection | 99.5% (99.71, 99.70, 99.70, 99.68) | 95% at or under 103.7 ms |
| frontend | 99.9% (99.92, 99.93, 99.93, 99.91) | 95% at or under 410.3 ms |
| frontend-proxy | 99.5% (99.67, 99.68, 99.65, 99.65) | 95% at or under 290.9 ms |
| image-provider | 99.9% (99.93, 99.92, 99.92, 99.92) | 95% at or under 206.3 ms |
| payment | 99.5% (99.79, 99.76, 99.77, 99.78) | 95% at or under 123.2 ms |
| product-catalog | 99.9% (99.94, 99.95, 99.95, 99.93) | 95% at or under 244.9 ms |
| quote | 99.5% (99.55, 99.64, 99.63, 99.62) | 95% at or under 22.1 ms |
| recommendation | 99.5% (99.79, 99.80, 99.80, 99.79) | 95% at or under 244.9 ms |
| shipping | 99.5% (99.88, 99.86, 99.87, 99.88) | 95% at or under 9.3 ms |
| a check, every minute | 99.5% (99.75, 99.65, 99.70, 99.67); 40,320 runs expected over 28 days | |

Every service's `GET /health` was pre-ticked. On one day, `checkout` had 48,728 requests of which 2,871 were probes; the check had 1,440 confirmed runs, 1,437 up.

**Not built or not proved in Step 2, said plainly.**

- **AC3's live half** (`test_slo_system.py`, spans through the real receiver and worker) is written and fails loudly without the whole stack; only Postgres and ClickHouse ran here. Not run.
- **AC81 for the form** (375px, keyboard): the form stacks by CSS, errors are tied to fields and a summary, the source fields are a fieldset; nothing was checked in a browser.
- **Step 2's *Judgeable*** on the dev stack with the demo was not walked.
- **AC80's form half and AC79** are proved in the form's tests; a viewer never reaches the form, so "a viewer reads who can add one" is the target page's, which shows no such line yet.

**Self-review (`/review-diff`), 2026-11-08.** Two findings, fixed: a PATCH re-asked `service_last_seen` about an unchanged service (Step 1 asked only on a change), and the shared select for the list and a target bound `org_id` only in its callers (`test_postgres_tenancy_structure.py` caught it). Not fixed, recorded above: expected runs read the check's current interval, and the service suggestion counts left-out endpoints in. Unintended changes: none; every file is named above.

## Step 1, built 2026-11-07

**What was built, by task.**

| Task | Done | Where |
|---|---|---|
| T1.1 | Kinds, limits, whole-day window, FR12's allowance, FR10's state and qualifiers and FR10a's order as pure functions, the composed label and definition key, the latency boundaries moved from `detection/method.py`, queue and task names, and the history job request | `nexovue_platform/slo.py`; `method.py`, `baselines.py`, `detection/store.py` and four test helpers import the boundaries from it |
| T1.2 | Alembic **0052**: `slo_targets`, `slo_status`, `slo_daily_counts`, `slo_target_changes`; downgrade refuses; inventory lines | `0052_slo_targets.py`, `models.py`, `organizations/tables.py`, `test_migration_rules.py` |
| T1.3 | `_query` takes `settings`; `SLO_SERVICE_DAY_SQL` under lowered bounds; **`sumForEachMergeIf` works on ClickHouse 26.3.20.7** (one statement split 64 buckets by window), so the fallback is not needed | `alerts/telemetry.py`, `slos/store.py` |
| T1.4 | A day's row, quiet and lost minutes, leading, trailing and longest stretches, a stretch across midnight; `holds.lost_minutes` and D37's header | `slos/counts.py`, `detection/holds.py` |
| T1.5 | The three queues and caps on `scheduler-light`; **the floor, built here** (`uncapped_floor`, `--uncapped-floor 4`, capped lanes taking turns), for F3 to rebase onto; `judge_slos`, `count_slos`, `read_slo_history`, `queue_jobs` with its three cross-tenant statements named; the coordinator's guarded call | `jobs/worker.py`, `__main__.py`, `roles.py`, `docker-compose.yaml`, `nexovue.yaml` (comment), `jobs/registry.py`, `slos/job.py`, `detection/coordinator.py` |
| T1.6 | `CONFIGURE_SLOS` (owner, admin, member) in all five copies, plus `test_auth_routes.py`, which pins a member's list too | as the plan names |
| T1.7 | `/slos` list, read, create, change, soft delete, restore; history rows and `slo.*` audit entries with ids only; token responses with no person; service checked against the project at write | `nexovue_api/slos/`, `main.py`, `audit/service.py`, `test_read_surface_structure.py`, `test_authorization_coverage.py`, `docs/product/setup/api.md` |
| T1.8 | `/slos`, `/slos/new`, `/slos/$sloId`, `/slos/$sloId/edit`; linked rows, tones from the severity tokens, headline, stale banner, deleted line with Restore, disable, ConfirmPanel with FR31's wording, 60-second refresh, staleness worked out in the browser, times in the project's zone; AC13's scan | `apps/web/src/features/slos/`, `router.tsx`, `route-manifest.ts`, `console-routes.json`, `apps/api/tests/test_slo_words.py` |
| T1.9 | Recorded below (*Decisions*) and in the spec's out-of-scope row | `spec.md` |
| T1.10 | Three roots walking subfolders; a network-client rule with its control; `lifespan.py` types the MCP server as a Protocol; **the walker moved to `nexovue_platform/import_walk.py`** (F19 landed first) | `test_detection_has_no_ai.py`, `lifespan.py`, `import_walk.py` |
| T1.11 | `slo_rows.py` writes exact `service_stats_1m` and `endpoint_stats_1m` rows with day and night traffic | `apps/scheduler/tests/slo_rows.py` |

**ADRs filed:** ADR-027 (daily counts in Postgres) and ADR-028 (SLO judging on `scheduler-light` under the floor). The design's other two proposed ADRs (*an incident can say what it is about*; *a burn does not repeat news*) belong to Steps 4 and 5 and are left for them. F3's own proposed ADR on the floor is F3's to file.

**Decisions taken while building, within the design.**

- **T1.9:** the worker sees each span's `tracestate` and already parses `ot=rv:`; an OpenTelemetry consistent-probability sampler also writes `ot=th:`, read by nothing. Detection would cover one kind of upstream sampler only, so FR5's question stays the decision. *The `th` behaviour is from memory of OpenTelemetry's sampling specification and was not re-read on the web here: unverified.*
- **A target never worked out** reads **Counting** for its first 15 minutes, then **Not up to date** (`Facts.expected_since`, the target's creation). Without it every new target read Not up to date at once.
- **The last event of a silence longer than a day** is read from the newest stored day with events, only when that day was counted after it ended, so its trailing quiet stretch runs to its end.
- **Counting before any sweep has settled a minute** counts to the current minute.
- **`queue_jobs` orders by `min(coalesce(evaluated_minute, '-infinity'))`**, so a project with one never-judged target leads, where the design's `min(...) NULLS FIRST` would let its other targets' age hide it.
- **The critical badge uses the severity badge fill** (`critical-subtle`, `critical-border`, `critical-text`); the tokens reserve `critical-solid` for dots and accent bars.
- **Step 1 never shows the "No alerts will be sent" qualifier:** no rule kind for burns exists until Step 4, so the state function takes `alerting=None` until then.

**Not built or not proved in Step 1, said plainly.**

- **AC81** (375px, keyboard, live region): the layout stacks by CSS and the live region is in place, but nothing was checked in a browser. Not proved.
- **AC64's "burn judged every minute with 20 history jobs queued" and AC65 at 2 times overload, as timings:** held by the floor test (separate capped queues, turns) and by the longest-waiting order, not by a timed run.
- **AC97 (F4's ticket refused):** no F4 ticket kind exists on the branch, so there is nothing to refuse yet; F4's Step 2 must add `/slos` to its refusals.
- **A token on a write** is refused by `CurrentIdentity`, held for every write by `test_authorization_coverage.py`; the in-process live harness has no session store, so `test_slos_live.py` does not call one.
- **AC35 in `test_tenant_isolation_live.py`:** that suite needs the whole running stack and has no per-route list; AC35 for `/slos` is proved in `test_slos_live.py` (another organization gets 404 on read, change, delete and list). Not run, not changed.
- **AC73's range:** the spec says day minutes total 1,410 to 1,500; a spring-forward day in New York is 1,380, which the test holds. The spec's range is narrower than the zones it covers.
- **Step 1's *Judgeable* on the dev stack** was not walked: no full stack was started on this shared machine.

**Self-review (`/review-diff`), 2026-11-07.** One MEDIUM finding, fixed: a target's service was not checked against the project at write (design, *Tenancy path*); now refused beside its field, test seen failing first. Unintended changes: none; every changed file is named above or is a test the plan names.

## Open decisions

Three from Step 5, 2026-11-09. Step 4's seven were answered 2026-11-09 (below).

1. **The finding page's burn figures come from the incident, not `/slos/{id}`.** The design's *Elsewhere* says the label comes with `LinkedIncident` "and the figures from `/slos/{id}` in the browser". Built: the figures come with `LinkedIncident.burn`, from the incident's detail, which the job rewrites every minute while the burn is open (`refresh_detail`), the same figures the incident page shows. **Options:** (a) keep: one request, no second feature's API inside the findings feature, and identical figures to the incident page; (b) fetch `/slos/{id}` in the browser, as written. **Recommendation: (a)**, and amend the design's line.
2. **AC58 has nothing to test yet.** "An `explained` row must go only to a channel a news message of that finding reached." No `explained` notification kind exists (`NOTIFICATION_KINDS`); F3 writes it, and F3 has not built it. **Recommendation:** when F3 adds it, it joins `_WITHHOLD_UNTOLD_ENDING`'s kinds (one line in `outbox.py`), and AC58's test goes into F3's step, named in F3's plan now.
3. **A burn noted on a lower-severity PagerDuty alert, which then escalates, stays a note.** FR24a shares an alert "as long as that alert is at the same or a higher severity", and a burn's opening is checked so (AC27). But a subject keeps the key its first row took (`_PIN_KEY`'s first branch), so its later critical escalation goes as a note on the warning alert and does not page. Fixing it means a subject on two alerts, and its ending sent to both, which changes the one-key-per-subject model F6 tested. **Options:** (a) on a burn's escalation above its host's severity, take its own key and page, and send its ending to every alert it is on; (b) leave it, documented. **Recommendation: (a)**, as a small task in Step 6 with its own test, because an unpaged critical is the failure PagerDuty exists to prevent.

**Answered 2026-11-09, kept for the record:**

1. **Founder answer, 2026-11-09: option (a).** **AC69's exclusion clause belongs to T4.7.** The plan mapped AC69 wholly to T3.2, but its last clause ("removing an exclusion reaching past it must show 'removed, figure not recounted'") needs `slo_exclusions`, which T4.1 creates. **Options:** (a) move that clause to T4.7, which builds exclusions and their recount; (b) build the exclusions table in Step 3. **Recommendation: (a).** T3.2 proves AC69's other two clauses (`test_slo_job_live.py::test_ac7_…`). **Built 2026-11-09:** each month an exclusion touched, removed once its days were past the recount limit, carries it (`removed_not_recounted` on `GET /slos/{id}/months` and the *Last month* rows, ids and times only), and the month says "The exclusion from 3 Oct (2 h) was removed, figure not recounted: its days too old to count again still leave it out." on screen and in the copied text. Proved by `test_slos_live.py::test_ac69_…` and `SloScreen.test.ts` (AC69), each seen failing first.
2. **Founder answer, 2026-11-09: option (a).** **A check's month states maintenance as runs, not hours.** FR27's example says "Runs in maintenance windows are not counted (6 h this month)". The API stores maintenance *runs* per day (`slo_daily_counts.maintenance_runs`), and turning runs into hours needs the check's locations, which for a check naming none is the deployment's probe list, known to the scheduler (`settings.probe_locations`) and not the API. **Options:** (a) keep "(72 runs this month)"; (b) store maintenance minutes per day in a later migration and say hours. **Recommendation: (a)** until a buyer asks; it is exact where hours would be derived.
3. **Founder answer, 2026-11-09: confirmed as built.** **`slo_deleted_months` holds more than the design listed, and a purged target's history has no route.** The design names `org_id, project_id, slo_id, month, label, figure, objective, result, deleted_at` (`design.md`, *Data*). FR27b says a copied month and the CSV always carry what it covers, quiet and excluded time and the sampling answer, so 0053 also keeps counts, coverage, quiet, longest quiet, lost and excluded minutes, a check's runs, days held, loss known, kind and sampling answer (`0053_slo_deleted_months.py`). Separately, UR9 keeps a purged target's changes 400 days, but `GET /slos/{id}/history` needs the target row to prove its project (`slos/months.py`, `history`). **Recommendation:** confirm the columns as built; leave the purged history unreadable until a screen for purged targets is asked for, since the audit log keeps the fact.
4. **Founder answer, 2026-11-09: option (a).** **T4.4's six-window statements were not written.** The plan asked for `SLO_BURN_*_SQL`, six windows in one statement per source. The judging already reads each source's last 24 hours minute by minute, one statement per source (`slos/store.py`; design, *What each job does*: "at most one statement per source"), and the burn needs those minutes anyway, to leave out a target's excluded minutes (which the design says a shared statement cannot) and to count nexovue's lost minutes against coverage. **Options:** (a) keep the windows summed from the minutes (`slos/burn.py`); (b) add the windowed statements as a second read. **Recommendation: (a)**: one read, and exclusions and coverage exact. T6.2 measures it either way. **Done 2026-11-09:** T4.4 ticked, its wording in `plan.md` amended and dated.
5. **Founder answer, 2026-11-09: accepted.** **`slo_exclusions.counted_at` is a column the design did not list.** "Queues a recount of the days touched" needed a queue: it is null after a creation or removal, the counts hand-out picks the project up, and the run marks it counted. **Recommendation:** confirm. **Done 2026-11-09:** added to `design.md`, *Data*, dated.
6. **Founder answer, 2026-11-09: as recommended.** **A deleted check's burns are found by `detail.check_id`,** because `incidents.about_check_id` is written only from T5.2. **Recommendation:** switch to `about_check_id` in T5.2. **Done 2026-11-09:** added to T5.2 in `plan.md`.
7. **Founder answer, 2026-11-09: option (a).** **The blind notice and muted rules.** FR13b says the union of the burn rules' channels, or the owners; built as the unmuted rules' union, else the owners. **Options:** (a) as built; (b) include muted rules, as the exclusion notice does. **Recommendation: (a)**, because a muted rule means a person said they will look; but (b) is the safer side if "nexovue went blind" should override a mute.

**Answered 2026-11-08, kept for the record:**

0. **AC73 wording corrected, founder 2026-11-08.** The spec said a local day holds 1,410 to 1,500 minutes; a New York spring-forward day holds 1,380, which Step 1's test already held. Read from the tz database (2026e, every zone, every day of 2026): 1,380 and 1,500 on one-hour changes, 1,410 and 1,470 at Australia/Lord_Howe (30 minutes), 1,320 and 1,560 at Antarctica/Troll (two hours). The AC now states those bounds, 1,320 to 1,560. No behaviour changed.


1. **Founder answer, 2026-11-08: option (b).** **T1.11's `metrics_gauge` rows.** The plan's T1.11 asks the helper to write exact `metrics_gauge` rows too, proved by AC2 through `test_slo_store_live.py`. In Step 1 no reader of check readings exists (that is T2.3, which moves `_COUNTED` from `apps/api/.../synthetics/queries.py:37-60`), and a reading is several JSON attributes (`synthetics/queries.py:37-48`: outcome, final, maintenance, run id, attempt) whose exact shape only that reader proves. **Options:** (a) write the helper now, proved only by counting its own rows back; (b) move this part of T1.11 into T2.3, beside its reader. **Recommendation: (b)**, so the helper is proved by the code that reads it. The service and endpoint parts are built.

The founder's own decisions from 2026-11-02 stay answered (*Founder answers*, below).

## Blocked

- **Founder decision, 2026-11-08:** Step 4 onward no longer waits on the demand gate (foundation item 27); it is built in full. **Step 4 was built under item 27 on 2026-11-08, with the gate's result still to be read.**
- Nothing blocks Step 3's commit; its three open decisions were answered on 2026-11-09.
- Nothing blocked Steps 2 and 3. Step 1 is on `feature/phase-2` (2026-11-08); T2.3 now also writes the helper's `metrics_gauge` rows.
- **Plan Step 4 onward** waits on the demand gate, and on precondition P2: a separate change keeping browser-door spans out of the rollups. **F19's release waits on P2 too.** It has no record yet. Foundation item 7 sets it before Stage 1 reaches a customer, and F28 owns it (item 22, 2026-11-02).
  **2026-11-07:** P2 is `docs/changes/browser-door/`, built (all four steps, with the AC37 live test through the real
  receiver and worker) and on `feature/phase-2` (2026-11-07).

## Risks

- **P2 affects what is already on this branch.** By reading the code, anyone holding a site key can forge server spans under any service name, and they count in the rollups F6 judges. It was not run. This should be seen before F6's release is decided.
- **F28's design still says "2 of 8" for `scheduler-light`** (`deployment-release-tracking/design.md:356`). F3's floor keeps probes and alert sweeps 4 of 8 (`ai-insights/design.md:271,829`), and F3 already asks F28 to correct this (`:785`). Nothing for the founder to decide. Since D66, F19's queues share those other 4 too.
- **F20's document disagrees with F19 until its author takes the hand-off.** F20 stores the SLO by name, and F19 now does too. F20 also wants the panel's burn rate from ClickHouse, and F19 recommends Postgres (design, *API*; `dashboards/design.md:436,446-447,970`). Both sides split the budget and burn functions the same way. F20's document was left alone.
- **SLO work shares `scheduler-light` (D66).** It waits behind detection, evidence and release reads, so burns can be judged a minute or two late under load, and a crash of that container stops probes and SLO judging together. Probes and alert sweeps never wait behind SLO work.
- **The rule on what a burn may withhold changes a path F6 has tested closely.** It is gated on a burn being involved, existing tests must pass unedited, and they run again beside an open burn.
- **All costs are unmeasured.** Reads, jobs, slots, `scheduler-light`'s 768M with SLO work added, and Postgres writes are arithmetic and guesses until T6.2. The named endpoint read is the largest figure.
- **Every guess in the spec is in one register:** *Cannot be proved before a customer*.
- **The suggestion has never been tried on real traffic.**
- **`sumForEachMergeIf` is untested.** T1.3 is the first check.
- **Demand.** Nothing shows SLOs win a purchase. Coroot and OpenObserve give this away on self-hosted, so it is a gap-filler against SigNoz, as of 2026-11-02, and that gap may close. The gate comes before the alert work.
- **Competitor facts** were read through a summarizing tool on 2026-11-02, which got one Datadog table wrong in a way only the page's HTML showed. Check before quoting in public (T6.8).
- **No test server, by the founder's choice** (foundation item 9, 2026-11-02). AC49's weeks-long run waits for Oracle after deployment and reads "not run" until then, so F19 launches unproved on faults. AC50 runs on the Mac's Docker; the 16 GB server is confirmed only on Oracle, whose free allowance (2 Arm CPUs, 12 GB) is below that size.
- **13 months on Free gives away what a rival charges for** (Grafana Cloud keeps 13 months on its paid plan). Approved knowingly on 2026-11-02.
- **AC38's outside tester** comes from the conversation round (spec D64). If nobody there agrees to a later screen test, it reads "not proved".

## Validation

**Step 5, 2026-11-09,** on worktree `wt/f19s5`, against its own stores (`f19s5-pg`, Postgres 18 with pgvector, port 35432; `f19s5-ch`, ClickHouse 26.3.20.7, ports 38123 and 39000), migrated to Alembic **0064** and ClickHouse 000039 (no new ClickHouse migration), with 0063 chained to 0060 for the run only; it now names 0061 again, for the coordinator to re-chain. 0063 and 0064 were run forward, back to 0060 and forward again. Removed afterwards. `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_alert_vocabulary.py` (no migration froze `_WITHHELD_REASONS`); `test_migration_rules.py` (0063 drafted with the index built plainly: `create_index on notifications`); `test_detection_links_live.py` (3: `Subject` took no `about_key`); `test_slo_closing_live.py`'s deleted check (found by `detail.check_id` with only `about_check_id` set); `test_slos_live.py`'s AC85 (`KeyError: 'burn'`); `FindingScreen.test.ts` (2), `slo-line.test.ts` (no module), `quiet.test.ts` (2); `test_scheduler_postgres_tenancy_structure.py` (`_CHANNELS_HOLDING` unnamed, a real finding) |
| Written beside their code, then seen failing by removing what each guards (`mutate.py` in scratch: one change, the suite run, the file restored) | `test_slo_job_live.py`'s T5.2 test (the job's subject without `about_*`). `test_slo_one_message_live.py` with the withholding calls removed from `claim`: 24 of 38 failed; the other 14 hold that news must still go, each failed on its own guard: the severity test (AC25, 2), the hour (AC54, 2), the project in both the statement and the guard (AC56), different endpoints (AC55), the finding's signal (AC55, 2), the batch in both the statement and the guard (AC57), the check pair one way only (AC30), `service_silent` as threshold news (2), the tie's winner (AC24, 2), a release written though news had since reached the channel (AC52), freshness and D56's save (AC53, each). Snooze and PagerDuty: `_DROP_SNOOZED` off (4 of 6 AC59), PagerDuty's endings dropped too (2), the snoozed ending never released (1), the new `_PIN_KEY` branch off (4), the linked branch's severity test off (AC27's critical page), `service_silent` admitted as a guest (1). `test_slo_regression_live.py`: withholding off ((c), 2), the type ignored ((b)), `about_key` ignored ((a)). `test_slo_words.py`: "burning" planted in `slo-line.ts`, "budget" in `finding-words.ts`, each failed |
| F19 suites, final | `test_slo_one_message_live.py` 55, `test_slo_regression_live.py` 41 (23 outbox and 13 notify tests rerun beside an open burn, unedited, and 5 of its own), `test_detection_links_live.py` 7, `test_slo_job_live.py` 37, `test_slo_blind_live.py` 6, `test_slo_closing_live.py` 6, `test_slo_words.py` 37; web `features/findings`, `servicedetail`, `shared/detection` 233 |
| Unedited (AC29) | `test_alert_outbox_live.py` and `test_detection_notify_live.py`: 36 passed before the change and after, and again inside `test_slo_regression_live.py` |
| Wider | Scheduler live suites above with `test_alert_noise_live.py`, `test_alert_delivery_live.py`, `test_detection_coordinator.py` (AC95(d)) and both tenancy structure tests: 2,379 passed. API: the F19, alerts, deletion, MCP, findings-cases, personal-data, read-surface and chokepoint suites, 586 passed and 2 failed: `test_alerts_live.py`'s two AC85 deploy tests insert a `deploy_events` row without `delivery_id`, which F28's 0057 made required; untouched here and failing without this change, for whoever owns Step 4's commit. Offline, API and scheduler (every `test_*.py` not `_live`, less `test_slo_system.py` and `test_connect_guard.py`): 9,781 passed, 1 skipped, 1 failed, `test_setup_schema.py`, because 0063 now names the parallel 0061, absent here (the coordinator's re-chain). Web: `features/findings`, `servicedetail`, `slos`, `alerts`, `app` and `shared/detection`, 420 passed. Whole-repo `mypy .` clean (871 files), `ruff check .` and `ruff format --check .` clean, Biome clean on the touched web folders, `pnpm -r --if-present run typecheck` clean, `verify-tokens.js` clean. No Go changed |
| Not run | `just check`; the whole-stack suites; a browser at 375px; Step 5's *Judgeable* with a real inbox and PagerDuty: **not yet run, founder checks after Phase 2** |

**Step 4's AC69 clause, 2026-11-09,** on worktree `wt/f19s4`, against its own stores (`f19s4-pg`, port 35432; `f19s4-ch`, port 38123), migrated to Alembic **0060** and ClickHouse 000039, removed afterwards. `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_slos_live.py::test_ac69_a_month_an_exclusion_removed_past_the_recount_limit_touched_says_so` (`KeyError: 'removed_not_recounted'`); `SloScreen.test.ts`'s "says an exclusion removed too late to recount was removed, figure not recounted (AC69)" (the month held no such line) |
| Suites, final | `test_slos_live.py`, `test_slo_words.py`, `test_slo_ai_paths.py`, `test_slo_model.py`, `test_slo_readings_live.py`, `test_slo_closing_live.py` and the three tenancy and read-surface structure suites: 2,230 passed. Web `features/slos` 51 passed. `mypy .` clean (808 files), `ruff check .` and `ruff format --check .` clean, Biome clean on `features/slos`, `pnpm -r --if-present run typecheck` clean, `verify-tokens.js` clean |
| Self-review (`/review-diff`) | Tenancy: the one new statement (`queries.removed_not_recounted`) binds `org_id`, and reads only targets already loaded for the caller's project. No reason leaves the API on a month (asserted). Unintended changes: none. **LOW, not fixed:** an exclusion of up to 7 days crossing a month boundary, whose later month's days were inside the recount limit, puts the line on both months; the line says only the days too old to count again still leave it out, which stays true |

**Step 4, 2026-11-08,** on worktree `wt/f19s4`, against its own stores (`f19s4-pg`, Postgres 18 with pgvector, port 35432; `f19s4-ch`, ClickHouse 26.3.20.7, ports 38123 and 39000), migrated to Alembic **0060** and ClickHouse 000039 (no new ClickHouse migration). `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_alert_vocabulary.py` (3: `_CONDITION_KINDS`, `_ABOUT_SIGNALS`, the backfilled rule) and `test_migration_rules.py` (0058); `test_alert_defaults_live.py` (5); `test_alerts_live.py` and `test_channels_live.py` (the coverage route raising on the new kind, `'SloBurnCondition' object has no attribute 'covers'`; the defaults' lists); `test_slo_burn.py` (no module); `test_slo_job_live.py` AC20 and AC94 (a real defect, above); `test_alert_messages.py` (7); `test_slo_words.py` (the scan reading Python placeholders); `test_slo_blind_live.py` (4 of 6 with `report_blind` stubbed); `test_slo_closing_live.py` (5 of 6 with the closing stubbed; the narrowed rule with `_covers_subject`'s new branch removed); the exclusion job tests (4 of 5 with exclusions removed from counting, burn and notices); `test_personal_data_live.py` (2); `test_slo_prune_live.py` (removed exclusions); `test_slo_model.py` (`burn_from_status`); `test_slos_live.py`'s AC51 (with its fixture fixed); `test_deletion_live.py` and `test_projects.py` (default-rule counts) |
| Written after their code, then seen failing by removing what each guards (a copy of the file saved first and restored from it, then seen passing) | Exclusion routes: `_refuse` removed (5 of 7 failed; the 9- and 501-character pair is the schema's, and failed with its length bounds removed), the creation's audit entry removed, a token's names shown, `removal_not_recounted` forced false, the exclusion's target filter removed: each failed. Who is told: `alerting` forced true; AC51: the status's burn dropped. The blind message: its branch disabled. `test_slo_ai_paths.py`: an exclusion's reason planted in the notice's detail, `rule_name` dropped from `UNTRUSTED_FIELDS`, an `nexovue_api.slos` import planted in `assistant/tools.py`, and a random value in a burn's detail: each failed its test. Web: the rule's sentence, the targets problem, the chosen `slo_ids`, the incident page's burn facts, the burn sentence, its next step, a latency target's noun, the no-alerts and snoozed told lines, the chip and the *Muted* chip: each removed or forced, each test failed (11 of 11) |
| T4.11 and T4.12's rest, written failing first | `test_alerts_live.py`'s three burn-page tests (no `zone`, no `burn`), `test_slos_live.py`'s two form-rule tests, `test_slo_readings_live.py` (3, no `burn` service), `IncidentScreen.test.ts`'s three (finding, deploy, zone, width, deleted target, a check burn's link), `SloScreen.test.ts`'s two chart tests and `SloForm.test.ts`'s channels test. Also seen failing by mutation: the form's "No alerts will be sent" (its line removed), the chart's equality (`rolling_rates` a minute too long: 1 of 3 failed) and its tier (forced to slow: 2 of 3 failed). `test_mcp_injection.py` failed on the two new unlabelled fields until labelled |
| F19 suites, final | `test_slo_burn.py` 29, `test_slo_job_live.py` 36, `test_slo_blind_live.py` 6, `test_slo_prune_live.py` 4, `test_slos_live.py` 47, `test_slo_closing_live.py` 6, `test_slo_ai_paths.py` 3, `test_slo_readings_live.py` 3, `test_slo_model.py` 104, `test_slo_words.py` 30, `test_alert_messages.py` 44; web `features/slos` 50, `features/alerts` 67 |
| Wider | Final run, after T4.11 and T4.12: API and scheduler offline suites (every `test_*.py` not `_live`, less `test_slo_system.py` and `test_connect_guard.py`) 9,025 passed, 1 skipped; the touched live and structure suites together (alerts, channels, defaults, deletion, personal data, members, projects, MCP, the read surface, rate-limit and tenancy structure tests, every F19 suite, the scheduler's jobs, notify and outbox suites) 2,927 passed. Earlier, `test_checks_live.py`'s object-store test errored without SeaweedFS, which this worktree does not start; it touches nothing here. Web: `features/alerts`, `features/slos` and `app`, 140 passed. Whole-repo `mypy .` clean (808 files), `ruff check .` and `ruff format --check .` clean; Biome clean on `features/alerts` and `features/slos`; `pnpm -r --if-present run typecheck` clean; `verify-tokens.js` clean. No Go changed |
| Not run | `just check`; the whole-stack suites (`test_slo_system.py`, `test_tenant_isolation_live.py`, `test_browser_door_live.py`); a browser at 375px; Step 4's *Judgeable* |

**Step 3, 2026-11-08,** on the worktree, against its own stores (`f19-pg`, Postgres 18 with pgvector, port 25432; `f19-ch`, ClickHouse 26.3.20.7, port 28123), migrated to Alembic **0053** and ClickHouse 000039 (no new ClickHouse migration). `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_migration_rules.py` (0053 not found); `test_deletion_live.py` (2: the new table unclassified); `test_slo_model.py` (21: no months, recount limit, labels, `longest_quiet`, frozen copies); `test_slo_job_live.py` (4: a final day kept under a newer definition, the version not moved on an unchanged recount, an older count over a newer day, AC64 with a final day); `test_slos_live.py` (9: no month routes, 422 on `/slos/months`, 404 on `/months` and `/history`, a restore bumping the version); `test_read_surface_structure.py` (four routes undocumented); `test_scheduler_postgres_tenancy_structure.py` (`_DUE_FOR_PURGE` unnamed); `test_slo_prune_live.py` (3: nothing purged or lapsed); `test_personal_data_live.py` (2: name unscrubbed, no `slo_target_changes` in the export); `test_slo_words.py` (5: the named files missing); `SloScreen.test.ts` (8) and `SlosScreen.test.ts` (2) against Step 2's screens; and, at review, the check-target outage test |
| F19 suites, final | `test_slo_model.py` 103, `test_slos_live.py` 33, `test_slo_words.py` 27, `test_slo_job_live.py` 20, `test_slo_prune_live.py` 3, `test_slo_counts.py` 15, `test_slo_store_live.py` 12, `test_personal_data_live.py` 12; web `features/slos` 42 |
| Wider | API offline suite (every `apps/api/tests` file not `_live`, less `test_slo_system.py`) 4,140 passed, 1 skipped. Scheduler offline suite 4,694 passed (`test_connect_guard.py` left out: public DNS). Live, 203 passed: the F19 suites above with `test_detection_prune_live.py`, `test_jobs_live.py`, `test_detection_store_live.py`, `test_detection_has_no_ai.py`, `test_slo_suggestion.py`, `test_deletion_live.py`, `test_members_live.py`, `test_findings_cases_live.py`. Web: 141 files, 1,630 tests. Whole-repo `mypy .` clean (777 files); `ruff check .` clean; `ruff format --check .` clean but for `docs/research/f20-board-mapping-2026-11.md`, from F20's commit 1c95fae, untouched here; `pnpm -r --if-present run typecheck` clean; Biome clean on the changed web files; `verify-tokens.js` clean. No Go changed |
| Not run | `just check`; the whole-stack suites (`test_tenant_isolation_live.py`, `test_slo_system.py`); a browser at 375px |

**Step 2, 2026-11-08,** on the worktree, against its own stores (`f19-pg`, Postgres 18 with pgvector, port 25432; `f19-ch`, ClickHouse 26.3.20.7, ports 28123 and 29000), migrated to Alembic 0052 and ClickHouse 000039 (no new migration). The machine restarted at 06:46 UTC; the stores were started again after Docker came back. `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_slo_model.py` (21: no creatable kinds, refusal, `at_or_under`, health names, run coverage, interval allowance); `test_slo_counts.py` (no `check_day_row`); `test_slo_suggestion.py` (no `slos.readings`, then the tier test); `test_ratelimit_coverage.py` (`/slo-readings` on the default tier); `SloForm.test.ts` (12 of 13 against Step 1's form); `test_seed.py` (no `HEALTH_ENDPOINT`); `test_slos_live.py`'s 8 new tests against Step 1's API (`kind` refused); `test_postgres_tenancy_structure.py` (a shared select without `org_id`, found by the offline run and fixed); `test_tenant_chokepoint_structure.py` (interpolating imported names in `synthetics/queries.py`, fixed by binding module constants) |
| Written after their code | `test_slo_store_live.py`'s 8 new tests (the offline counts tests above had failed first); they import code Step 1 lacks |
| F19 suites, final | `test_slo_model.py` 82, `test_slo_suggestion.py` 15, `test_slo_counts.py` 15, `test_slo_store_live.py` 12, `test_slo_job_live.py` 16, `test_slos_live.py` 23, `test_seed.py` 18 (with `test_fault.py`); web `features/slos` 32 |
| Wider | Scheduler offline suite 4,694 passed (`test_connect_guard.py` left out: it needs public DNS). API offline suite 4,064 passed, 1 skipped. Live: `test_jobs_live.py`, `test_detection_store_live.py`, `test_detection_has_no_ai.py` 31; `test_deletion_live.py` with the synthetics, structure and policy suites 429. Web: 141 files, 1,620 tests. Whole-repo `mypy .`, `ruff check .`, `ruff format --check .`, `pnpm -r --if-present run typecheck`, Biome on `features/slos`, and `verify-tokens.js`: clean |
| Live seed | `seed.py --tenant-id` wrote 576,281 service rows, 1,491,266 endpoint rows and 40,694 check readings; read back exactly; T2.7's list read them through the API's code |

**Step 1, 2026-11-07,** on the worktree, against its own stores (`f19-pg`, Postgres 18 with pgvector, port 25432; `f19-ch`, ClickHouse 26.3.20.7, port 28123), migrated to Alembic 0052 and ClickHouse 000038. `just check` was not run, by instruction.

| What | Result |
|---|---|
| Seen failing first | `test_policy.py` (no `CONFIGURE_SLOS`); `test_slo_model.py` (no module, then two clock-change days measured by wall clock, fixed by `day_bounds` in UTC); `test_deletion_live.py` (`test_the_database_holds_no_table_this_deletion_has_not_classified`, without the inventory lines); `test_slo_store_live.py` (3 of 4, `_query` took no `settings`); `test_slo_counts.py` (no module); the floor test and AC68 (7 failed); AC95(d) (the exception reached the sweep with the guard removed); `test_scheduler_postgres_tenancy_structure.py` (four unnamed statements); `test_slo_job_live.py` (AC8 at 16 minutes and 25 hours read Building history and Source gone: two real defects, fixed above); `test_authorization_coverage.py` and `test_read_surface_structure.py` (routes unplaced); the no-AI walk (`mcp.server <- nexovue_api.lifespan <- nexovue_api.auth.dependencies <- nexovue_api.slos.router`), and again with an `mcp` import planted in `nexovue_api/slos/queries.py`; `route-manifest.test.ts` (2 failed); `test_slo_words.py` (with "Uptime budget" planted); the service check at write |
| Written with their code, failing first only on real defects | `test_slos_live.py` (new targets read Not up to date; fixed), the web screen tests (the zone read "GMT+5:30" not "IST", and the clock test; both fixed) |
| F19 suites, final | `test_slo_model.py` 61, `test_slo_counts.py` 8, `test_slo_words.py` 15, `test_slo_store_live.py` 4, `test_slo_job_live.py` 16, `test_slos_live.py` 15, `test_detection_has_no_ai.py` 6; web `features/slos` 19 |
| Wider | Scheduler offline suite 4,687 passed (`test_connect_guard.py` left out: it needs public DNS). API suites touched 2,243 passed. Live: `test_jobs_live.py`, `test_detection_store_live.py`, `test_deletion_live.py` (25) passed. `test_alert_telemetry_live.py` fails 3 of 4 with and without this change: it needs the seeded `acme/production` project. Web: 140 files, 1,607 tests passed; `tsc` clean; Biome clean but for one existing warning in `infrastructure/databases/connection-health.ts`. Ruff and mypy clean on every changed Python file |

**Before Step 1:** almost nothing was run. No test, query or measurement of the product. One read-only run of the no-AI test's own import walk, from a scratch script, gave the evidence for D55. Documents only. Code was read, on a branch with uncommitted work, so line numbers will drift. The plan requires reading them again at start. A script checked that every FR and AC in the spec has a plan task and test, and that every T-number and D-number cited exists. That is a check of the documents, not of the design.

## Specification impact

New specification. The design's *Specification impact* names what would change in F6's, F20's and F21's documents, the catalog and foundation, on approval. None of those were edited.

## Documentation impact

The catalog's F19 entry and the features index now read approved, not built (2026-11-02). Foundation's F19 row, its section 12 line on exact counts, and research R2 differ from the spec, and the spec says so at the top. D66 is in the foundation (section 1's memory line and the names table) and in F3's design, *The floor (A56)*.

## Reviews

Eight personas reviewed the three documents and research on 2026-11-02. **Their verdicts were on the documents as they then stood, and they have not reviewed this revision.** Each finding was checked against the code or source, then changed in the documents.

### Verdicts

| Persona | Verdict | The one thing that decided it |
|---|---|---|
| Architect | Not ready | The design and plan predated the spec, and withholding, snooze and escalation could not work as designed |
| Developer | Not ready | A plan followed tomorrow would build the earlier spec |
| UX | Not ready | The design's screens described a different product, and the top state could state something false |
| Sales | Not ready | The buyer's monthly answer had no design or task, and the positioning line was false as written |
| Marketing | Not ready | The controls on claims existed only as references, and rival facts were thin |
| Customer | Not ready | When nexovue goes blind, nobody was told |
| Security | Not ready | Free text was designed into a row AI reads |
| QA | Not ready | 44 of 89 acceptance criteria were named by no task |

### How conflicts were decided

The task names a "decision rule". No document defines one by that name, so the standing rules of this run were used, in this order: **AI safety and what the code does; then founder decisions already recorded; then the correct end state over a compatible one, since nexovue is pre-deployment; then the smallest thing that meets the spec's stated goal.**

| Conflict | Decided | Why |
|---|---|---|
| Architect and developer (snooze stops burns, including mute) against customer (mute must not) | Snooze holds burns, mute does not (D18) | A mute lasts up to 30 days, and D18's reasoning covered only the 4-hour snooze |
| Architect (rotate or oldest first) against developer (rotate) | Oldest first | Self-correcting, and one statement |
| Architect (`scheduler-slo`) against developer and customer (settle slots and memory on `scheduler-light`) | `scheduler-slo` (D39) | 12 children at the limit cost the whole ceiling anyway |
| Architect and QA (read the target's name by id when composing a message), security (browser only), UX (a composed label) | Composed label in messages; name in the console only (D43) | Least free text, no join to F19 |
| UX (trailing 1-hour chart) against its own AC (last point equals the sentence) | The chart plots the sentence's window | A 1-hour line cannot equal a 6-hour rate |
| Sales (share with a label) against marketing (say not for customers) | Screen says marketing's wording; a seller answers sales' | Both true, and founder decision 2 can overrule |
| UX and QA (Not up to date outranks a burn) against the spec's AC51 and D27 | Not up to date and Disabled come first (D27 changed) | A present-tense sentence on stale data is the worst failure in principle 2 |
| UX (keep "Too fast" with extra words) against marketing (rename) | Running out (D41) | It names the harm on every kind |
| Sales (gate before Step 3, moved months up) against D38 (Steps 1 and 2 shown) | Months first, then the gate (D44) | D38 is a recommendation by the authors, not the founder's decision, and the month is the buying reason |
| Architect (close a stopped burn at 7 days, machines only) against QA (state a limit) and customer (people told) | 24 hours, people told (D40) | No person chose it, and FR24 owes an ending |

### What changed

- **`design.md`: rewritten** against the spec. Screens, tones, a new `scheduler-slo` role and three queues, which projects get a job, longest-waiting first, locks and guards, `fire` each breached minute and `refresh_detail`, the floor's exception, closing a stopped burn, typed `about_*` columns, withheld and release columns with `_RELEASE`, `_DROP_SNOOZED`, `_PIN_KEY`'s branch, notices once per target, the blind notice, a composed label, an *AI* section, full data model, migration rules, sizing all three sources, reads with lowered limits, CSV safety, the scrub, D37's limits.
- **`plan.md`: rewritten.** One migration per step; a real *Demand gate*; months before alerts; audit and no-AI tests in Step 1; T1.9 and T6.8 to T6.12; real demos; tools named; and tables mapping every FR and AC to a task and test.
- **`spec.md`:** positioning and claims; an objections table; must-not additions; FR10's order, next steps and sentences; FR10a order; FR13's times; FR13b; FR14, FR16 and FR17; the section's new name; FR22, FR23; FR25a, FR26; FR27, FR27b, new FR27c; FR29, FR31, FR33, FR34; NFR2, new NFR7; one register of guesses; error table; AC changes; AC91 to AC117; D27 and others changed, and D39 to D54 new; founder decision 3's wording. AC61 is recorded as retired.
- **`research.md`:** a note that the spec overrides it; Datadog, Honeycomb, Grafana, SigNoz, OpenObserve, Coroot and New Relic rows corrected and sourced; Better Stack and Elastic added; suggestions, grouping, Coroot head to head, and the monitor alternative added; "exact, true today" and "one problem, one message" corrected.
- **Facts checked by reading:** `step()`, `fire`, `clear`, `_PIN_KEY`, `claim`, `_RETRY_LATER`, `_covers_subject`, `coverage_of_service`, `active_projects`, `fair_order`, `roles.py`, `nexovue.yaml`, `alertmanager.yml`, `_query`, `DataTable`, `IncidentScreen`, `csv_cells.py`, `_without_person`, `seed.py`, `scale.py`, the migration rule test, the rollup tables, and each rival page cited. **Nothing was run.**

### Every finding

**Fixed** means verified and changed. **Part** means verified, changed, with a part rejected, listed below. Ids are by persona and order.

| Persona | Fixed | Part |
|---|---|---|
| Architect (18) | A-1, A-3, A-4, A-6, A-7, A-9, A-10, A-13, A-14, A-15, A-17, A-18 | A-2, A-5, A-8, A-11, A-12, A-16 |
| Developer (20) | DV-1 to DV-6, DV-8 to DV-10, DV-12 to DV-20 | DV-7, DV-11 |
| UX (16) | UX-1 to UX-5, UX-7, UX-8, UX-9, UX-11 to UX-16 | UX-6, UX-10 |
| Sales (17) | SA-1 to SA-11, SA-13, SA-15, SA-16 | SA-12, SA-14, SA-17 |
| Marketing (18) | MK-1 to MK-3, MK-6, MK-8 to MK-18 | MK-4, MK-5, MK-7 |
| Customer (15) | CU-1, CU-3 to CU-11, CU-13, CU-14 | CU-2, CU-12, CU-15 |
| Security (13) | SE-2 to SE-13 | SE-1 |
| QA (17) | QA-1, QA-2, QA-4 to QA-17 | QA-3 |

**Where each finding went:**

| Id | Finding | Where it was dealt with |
|---|---|---|
| A-1, DV-1, MK-2, CU-1, QA-1 | Design and plan older than spec | Both rewritten; plan's tables |
| A-2, DV-11, SA-10, CU-6, SE-1, QA-3, UX-2 | Free text in incidents; no replacement label | Design *Messages*, *AI*; FR33, AC72, AC105 |
| A-3, DV-2, SA-6, SE-9, QA-2 | Withholding rule | Design *What a burn may withhold*; project bound on both sides |
| A-4 | `about_key` too thin; PagerDuty | `about_*` columns; `_PIN_KEY` branch; a severity test |
| A-5, DV-3 | Snooze permanent | `_DROP_SNOOZED`, release; no `silenced` |
| A-6, DV-4 | Escalation and current figures | `fire` each minute; `refresh_detail`; AC19 |
| A-7, DV-5, CU-10 | Slots and memory | `scheduler-slo`, NFR2, AC50, AC68, `sizing.md` |
| A-8, DV-7 | Overload skips the same projects | Oldest first; counts by age; AC65 |
| A-9, DV-6 | Which projects get a job | New statement, AC8 |
| A-10 | Ordering, repeat, history | Lock, guards, `history_read_version`, AC64, AC66 |
| A-11, DV-9, SE-5 | Data model | Tables, partial unique index, routes, restore, purge |
| A-12, DV-17 | Cost; read limits | *Performance*; `_query`'s `settings` |
| A-13 | Existing condition readers | T4.1, AC70 |
| A-14, DV-14 | Migration rules | One per step, `NOT VALID`, `CONCURRENTLY`, roll forward only |
| A-15, DV-16 | Notices per rule; 7-day rule | `fire_notice`, re-arm, AC22, AC60 |
| A-16, QA-6 | A burn with no end | FR17, `close_unjudged`, AC94 |
| A-17 | F20 contract | `/budget` Postgres, `/burn`, hand-off in *Risks* |
| A-18, SE-8, SE-11 | P2's check half; D37 | P2 item 4; `holds.py` task; AC33; plan Q3. **P2's owner stays open, with a recommendation:** naming a person is the founder's |
| DV-8, QA-9 | Tools | T1.11, T2.6, T6.2, real demos |
| DV-10 | Closing paths | T4.10 |
| DV-12, SE-4 | Audit too late | T1.7; `slo.restored` |
| DV-13 | Structure tests | Plan's *Impact*, T1.2, T1.5, T1.7 |
| DV-15 | More contradictions | Design rewrite; T4.3, T5.6 |
| DV-18, QA-10 | End-to-end proofs | `test_slo_system.py`; P2's record; AC37, AC87 |
| DV-19 | Deletable work | No `state.py`; constants moved |
| DV-20, SA-4, MK-1 | Status and plan out of step with spec | This section; three decisions; T1.9, T6.8 to T6.10; *Demand gate* |
| UX-1 | Screens out of date | Design *UI structure* |
| UX-3, QA-11 | State false on stale data | FR10, D27, AC51, AC63 |
| UX-4 | Critical and warning alike | Tone table; order; AC39 |
| UX-5, CU-14 | 3 a.m. path | Messages; AC85; next steps; finding page figures |
| UX-6 | Stale incident page | `refresh_detail`, AC104 |
| UX-7 | Phone layout | Linked rows; AC81 |
| UX-8 | Missing surfaces | Design; routes; T3.6, T4.7 |
| UX-9 | Create flow | Design *User experience*, *Form states*, AC106 |
| UX-10 | Chart | Design; AC109 |
| UX-11 | No refresh | NFR7, AC107 |
| UX-12 | Quiet night | FR10, FR10a, AC98, AC110 |
| UX-13 | Times | FR13, AC108 |
| UX-14, MK-13, QA-15 | Words | AC13, D16, FR10, AC111 |
| UX-15 | One click | FR25a, AC79; qualifier order |
| UX-16 | Capped incident page; kind's name | Design *Elsewhere*, T4.11 |
| SA-1 | Last month | FR27b, design, T3.3, T3.6 |
| SA-2, MK-3 | Positioning | Spec's line, claims, must-not |
| SA-3 | Gate | *Demand gate*; D38, D44 |
| SA-5 | Sibling documents | Design goal, security table, research note |
| SA-7, MK-14 | Labels and customer use | FR27, FR27b, D42, founder decision 2 |
| SA-8 | "5 of 7" | FR27b, FR31, D46, AC112, an objection |
| SA-9, CU-3 | Floor at night | FR14, FR16, D28, AC92, an objection |
| SA-11 | Objections | Spec's table |
| SA-12 | 13 months | Claims, must-not, D53 |
| SA-13 | Customer asks | Objection; gate's branch |
| SA-14, MK-18 | Demo, trial, launch | T6.12; T6.3 matched to AC49 |
| SA-15 | Two answers; D1 | FR27, AC117, D1 |
| SA-16 | Whole company | D47, Out of scope |
| SA-17 | Owners woken | FR25a, docs (T6.6) |
| MK-4, MK-5, MK-6, MK-7, MK-8, MK-9, MK-10, MK-11, MK-15, MK-16 | Rival facts | `research.md`, spec's claims and ahead list, design's alternatives row |
| MK-12 | "Too fast" | D41 |
| MK-17 | Foundation's exact counts | Spec's differences table; design's *Specification impact* |
| CU-2 | Blind | FR13b, design, AC91, D45 |
| CU-4 | Weekend gaps | Design *Burn*, FR16, AC93 |
| CU-5 | Mute | FR26, D18, AC59 |
| CU-7 | Sampling | FR5, D48, T1.9, AC114 |
| CU-8 | Latency on a monolith | FR5, Out of scope, T6.2 |
| CU-9 | Partial outage | FR5, FR27c, error table, AC113 |
| CU-11 | On-call not paged | FR25a, AC38 |
| CU-12 | Exclusions | FR29 (cap), D52 |
| CU-13 | Thin and missed | FR10's order, AC111 |
| CU-15 | As code | Out of scope, D54, gate's question, ahead list |
| SE-2 | F4 | FR33, AC97, design |
| SE-3 | Proofs without tasks | Plan's table; T4.13 fails its step |
| SE-6 | Notices | `fire_notice`; owners; AC60 |
| SE-7 | Scrub | Design inventory; AC88 |
| SE-10 | CSV | FR27b, AC115 |
| SE-12 | Suggestion gate; project tokens | `CurrentIdentity`; AC33 |
| SE-13 | No-AI test | T1.10, three roots |
| QA-4 | Requirements unsupported | Design rewrite |
| QA-5 | Regression gate | AC95, T5.8 |
| QA-7 | AC49 | AC49, T6.3 |
| QA-8 | No test server | Design; plan's *Validation plan* |
| QA-12 | Behaviours with no AC | AC98 to AC103 |
| QA-13 | AI off | AC96, AC72 |
| QA-14 | AC38 | Moved to T6.11; "not proved"; AC86 labelled |
| QA-16 | Delivery cost | AC50; labels on unproved statements |
| QA-17 | Register | Spec's one register, T6.10 |

### Rejected

**No finding was rejected whole: each named a real problem.** These parts were rejected, with the reason:

| Id | Part rejected | Why |
|---|---|---|
| A-2, DV-11, QA-3 | Fetching or reading the target's name for message text | A composed label does the job with no join to F19, and a name may hold a customer's name (D43) |
| A-5 | A service's mute also stops burns | Customer's point wins (D18). Also, "people's channels only": a page wakes a person too (D50) |
| A-8, DV-7 | Rotating the order by minute | The oldest-first option in A-8 is simpler and corrects itself |
| A-11 | A `quiet_since` column; one running longest value | `last_event_minute` says it; three per-day values let a stretch cross midnight from Postgres alone |
| A-12 | `span_name NOT IN`, with no subtraction | By arithmetic, about 10 times the named read, because it reads every endpoint (D51). Its negative-minute worry is met by a guard |
| A-16 | 7 days, and machines only | 24 hours is the longest window, and FR24 owes people an ending (D40) |
| UX-6 | The incident page reading `/budget` | `refresh_detail` makes `detail` current, with one source and no second call. Its AC stands (AC104) |
| UX-10 | Always the 1-hour rate on the chart | It could not meet its own AC when a slower tier holds. The chart plots the sentence's window |
| CU-2 | Warning severity | It would wait out quiet hours while no burn can page (D45) |
| CU-12 | One exclusion across many targets | Keeps it small; it complicates the notice. Its cap point was fixed (D52). **Real pain, kept as a risk** |
| CU-15 | A copy action | Keeps it small; a conversation asks first (D54). **Kept as a risk** |
| SA-12 | Backfilling from `endpoint_stats_1m` | A second source that may not add up, since only service rows are capped (D53) |
| SA-14 | A demo dataset in a prospect's install | False figures in a real install, against BR10's spirit. The script uses the dev stack |
| SA-17 | A message to owners on the first target | One more message with nothing to act on. Form and docs say the default |
| MK-4 | Stating Coroot's presets as 99% and 500 ms | The page gives those as an example, not as the presets. It does not say every service gets them |
| MK-5 | "30 days" for Grafana; "since 2024" | The page read says "recent", and the older claim was not found |
| MK-7 | A documented Honeycomb budget reset | The page read describes none. Rest confirmed |
| SE-1 | A new `slo_label` | `LinkedIncident` gets the composed label, and the browser reads the rest from `/slos`. Rest done |

**Reviewers were right where a tool was wrong.** MK-6's Datadog mapping differed from what the summarizing tool returned, twice. The page's HTML matched the reviewer.

### Second review: 11 problems, all verified and fixed

Each problem below was checked against the code or the sibling document before changing anything, on 2026-11-02. **None was rejected.** Nothing was run but the walk named in *Validation*, and these documents have not been reviewed again since.

| Severity | Problem, as verified | Fix | Decision |
|---|---|---|---|
| Major | The no-AI test could not pass in Step 1: `auth/dependencies.py:51` imports `lifespan`, and `lifespan.py:21` imports `mcp.server`. The walk reproduced this from `findings.router` and `alerts.router`, and found nothing once stopped at `lifespan` | T1.10, FR33, AC47, design *AI*: one named stop for the API root, with controls | D55 |
| Major | A burn on a stopped service never froze, because `_SAVE_POSITION` writes `evaluated_at` on `breached=None` (`thresholds.py:55-63,234-244`) | F19's own save; judged-recently defined; AC53 runs the real job. **Also found:** the freeze takes up to 2 h 15 m, because the slow tier can judge for 2 hours after the last event. Stated in the design | D56 |
| Major | "No free text in an incident" was false: `rule_name` is copied into every incident, and it reaches AI | Rule restated; hostile rule name in AC72 and T4.13; fixed name for FR25a's rule. **Also found:** the notices' and Goal 5's wording said the same, and were corrected | D57 |
| Major | The blind notice could not stay single or resolve, with a null `rule_id` | Its own path and index; out of the closed-as-event statement; AC91. **Also found:** an unrestricted null index would make deleting rules fail, so it is limited to `slo-project:` keys | D58 |
| Major | The ClickHouse routes could not join `TELEMETRY_PATHS` route by route | A second router at `/slo-readings`, in T2.4 and T4.12, with T1.7 no longer touching the list. All of `/slos` on the tier was rejected because of AC40. **The 60-second refetch needs no costing against that tier now** | D59 |
| Major | The founder's `scheduler-light` claim was false: F3's floor keeps probes 4 of 8, and the cited line was a table column | Status, design, NFR2, D39 and the ADR corrected; dropped from the founder's list; `scheduler-slo` stands because capped queues would share 4 slots | D39 changed |
| Major | F19 and F20 disagreed on the panel, and F19's citations were wrong | Settled by name; split named; citations 436, 446-447, 970. F19 recommends the panel's rate from Postgres, which F20's author must take up | D60 |
| Major | AC109 could not be tested; AC50, AC91 and AC65 had no bar | AC109 is a live test at `evaluated_minute`, with a tier rule for On track and a change of tier; AC50 has three counts (a guess); AC91 is 16 minutes; AC65 is 5 minutes (a guess) | D61 |
| Minor | AC13's test had no task, and banned required words | T1.8 creates it and fails the step, later tasks extend it; whole-word match; "exactly" allow-listed | None |
| Minor | `_DROP_SNOOZED` was placed where PagerDuty could not reach it | *Where* gives its place, outside the people's branch; PagerDuty line reworded; T5.5. `_RELEASE` moved with it, since a snoozed news row must be released there too | None |
| Minor | Citations into F3 and F4 pointed at the wrong lines | Updated to F3's 269, 271, 294 and 829, and F4's 131, 136, 405. Line numbers there will drift too | None |

### What remains as a risk

- **The 16 and 5 minutes, and 200 open burns, are guesses or arithmetic** until T6.2 and T6.4.
- **The walk's stop at `lifespan` means whatever `lifespan.py` alone imports is not walked** from the API root. The assistant is denied by name wherever else it is reached, and review must still watch that file.
- **These reviewers have not seen this revision.** The design is new text, and new text has new errors. A second review of the design and plan is worth its cost, above all on `_RELEASE`.
- **`_RELEASE` is a new writer in delivery,** with one statement called from two places. Only AC52, AC57 and AC59 would catch a mistake, and none has run.
- **The endpoint read's skip is a guess** about how the sort key behaves on 8,192-row granules. Up to about 65 million rows a minute across the install, by arithmetic. It may force the fallback.
- **`scheduler-slo`'s 384M, and one more container on a 16 GB server,** are unmeasured. *(Superseded by D66: no new container; `scheduler-light`'s 768M with SLO work is what is unmeasured.)*
- **The 24-hour close** could close a burn on a service with a long regular gap, and page again on its return. It is in the register.
- **Withholding a burn for as long as its finding stays open** (D49) means a burn held back at 02:30 may be told only when the finding ends. Severity rises and repeats still go.
- **A short partial outage still reads well.** FR27c catches only a day-long fall. It is stated on the form, and in the docs, and it remains the most embarrassing figure this feature can show.
- **Latency on a monolith mixes health checks and every route.** The form says so, and T6.2 costs the alternative.
- **Someone can still tidy a bad month,** visibly.
- **Rival facts age.** Grafana's self-managed status, Elastic's tiers, rivals' alert grouping, and the SigNoz community pack remain unread.
- **P2's owner is F28** (foundation item 22, approved 2026-11-02). The gate's bar is now spec D62.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- D55 changed: `lifespan.py` drops its `mcp` import, by a small Protocol, rather than a named exception that F20's D38 would have undone. Whichever of F5, F19 or F20 lands first does it (spec FR33, AC47, D55; design; T1.10).
- `slo_budget` settled with F20: by name, from Postgres, no scan. F20 changed; the hand-off is closed (design).
- P2 is one record with F28's and F3's needs (plan, foundation section 9), and `scheduler-slo` and the F19 tables are now in the foundation.
- Founder item 2's recommendation now matches F22's item 2, and foundation item 6 holds it (spec).

## Last updated

2026-11-09

## Founder decision, 2026-11-02: SLO work runs on `scheduler-light` (D66)

The founder decided that F19 adds no role or container. **D66 supersedes D39** (`scheduler-slo`).

- **Where:** queues `slo` (cap 2), `slo_counts` (1) and `slo_history` (1) on `scheduler-light`, under F3's floor (A56). All capped queues together hold at most 4 of its 8 slots; probes and alert sweeps keep 4.
- **Trade-off:** capped queues now ask for 10 seats against 4. SLO work waits behind detection, evidence and release reads, and is judged late, not never (AC65, **Not up to date** on screen). Probes and alert sweeps never wait behind SLO work. A crash of the container stops both.
- **Memory:** `scheduler-light` keeps 768M, a guess, since its slot count does not change. AC50 measures it on the deployed server. Phase 2's extra memory drops by 384 MB (foundation section 1).
- **Changed:** spec (NFR2, AC50, AC64, AC68, AC91, D39, new D66, the register, the differences table); design (*Where it runs*, *Slots*, *Memory*, the fallback, alternatives, accepted costs, the ADR); plan (C5, services, files, P3, T1.5, T6.4, risks, rollback); foundation; F3's design; the tracker.
- **Depends on F3's floor** (plan P3).

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with D66 above. **The design's four proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in research, spec, design, plan and status was checked against the code and, where it turned on outside facts, the web. Results:

| Question | Outcome | Record |
|---|---|---|
| Hosted Free keeping 13 months of monthly figures (plan Q1) | **Founder's**, rewritten in plain English. Plans differ by retention (foundation section 10), so it is packaging | Spec founder decision 1 |
| Self-hosted retention (plan Q2, was spec founder item 2) | **Settled by foundation item 6.** Removed from F19's list | Spec, *Settled by the foundation* |
| P2's owner and timing (plan Q3) | **Timing settled by foundation item 7:** before Stage 1 reaches a customer. Naming the owner is the founder's under that item, not re-asked here | Plan Q3, design *Forged spans* |
| Can a real browser span be `SERVER`? (plan Q4) | **Decided: no.** nexovue's SDK sends only an `INTERNAL` page view (`packages/browser-sdk/src/rum.ts:68-71`); OpenTelemetry's fetch spans are `CLIENT` and document-load's `INTERNAL` (source read 2026-11-02). P2 filters by door | Spec D63 |
| Who a figure may be quoted to (plan Q5) | **Founder's**, legal exposure. Now numbered 2 | Spec founder decision 2 |
| The demand gate's bar (plan Q6) | **Decided:** at least 2 people tell a real, recent, named request for a reliability number that a monitor's uptime figure did not answer. Opinions do not count (*The Mom Test*). What to do if it fails stays the founder's at the time | Spec D62, plan *Demand gate* |
| AC38's outside tester, "nobody is named" | **Decided:** ask for a later 15-minute screen test at the end of each conversation in the round; the first yes is the tester. No person needs naming | Spec D64, plan T6.11 |
| Endpoint latency buckets, "T6.2 reports a figure so the founder can decide" | **Decided:** out of this version. T6.2's figure is recorded, and adding the kind later is a normal feature-work request | Spec D65, out of scope table |
| Test server money (design *Capacity*, plan *Validation*) | **Settled by foundation item 4.** The server's price is a foundation question | Design, plan |
| Customer conversations ("still open from foundation") | **Settled by foundation item 8:** one round before F28's Step 1b. F19's questions and D64's ask go into it | Spec, plan *Demand gate* |

**Review, 2026-11-02.** A skeptical panel re-checked each answer above against the code, the foundation and the web.

- **Free plan retention stays the founder's,** and the question now says what 13 months goes against: foundation section 10 assumes Free's 30 days limit SLO windows, F6's readings expire at derived retention (`detection/store.py:296-300`), and Grafana Cloud keeps 14 days on Free and 13 months only on paid Pro. It also says what 30 days would cost: no whole calendar month on Free, and the prune rule must spare self-hosted, which sits on Free's row.
- **D63 holds.** `rum.ts:68-71` sets no kind and `otlp/spans.ts:38` adds 1, and document-load's source passes no kind (re-read 2026-11-02).
- **D64 fixed.** Everyone in the round sees the month mock, so as written no one could be AC38's tester. Now the mock shows a month figure only, never the state names, and AC38 allows it.
- **D38 and the plan's *What is shown* fixed.** The one round comes before Step 1, so Steps 1 to 3 cannot be shown in it.
- **Held unchanged:** founder decision 2 (legal), D62, D65, and the foundation pointers for items 4, 6, 7 and 8. P2 still has no owner, which blocks Step 4. Naming one is the founder's under foundation item 7.

Also read for the gate: [SigNoz issue 658](https://github.com/SigNoz/signoz/issues/658) is still open on 2026-11-02; its last activity is an outside offer of a design proposal on 2026-10-23. Not changed: plan T6.8, T6.10 and T6.12 name the founder as a signer or as naming an owner once a customer exists. Those are approval steps at the time, not open questions.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** 13 months of monthly figures on every plan, Free included (item 20); figures for internal and board use only, labelled so, with customer quoting added to the lawyer's reading (item 16). F28 owns P2, the browser-door change (item 22).
- **Changed, A: no rented test server.** AC49 waits for Oracle after deployment and reads "not run" until then. AC50 (T6.2) runs on the Mac's Docker; its figures are not published as server figures before Oracle.
- **Changed, B: no paid model calls.** F19 calls no model, so nothing changes here.
- **Open decisions:** none for the founder.
