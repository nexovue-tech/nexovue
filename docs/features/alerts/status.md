# Status: Alerts & notification connectors (F21)

## Lifecycle status

**Released to `main`.** All six steps built and proved; `/review-diff` and `/readiness-check` done
2026-09-19, committed after the founder read the diff. Not deployed — nexovue has no deployment
target (`docs/operations/production-readiness.md`).

*This said "uncommitted" until 2026-09-21, long after the work was in history.* A status that
outlives its own state is worse than no status: it is the document a later session believes.

## Current task

None. Waiting on the founder to read the diff before the commit.

## Completed

- Research; [`spec.md`](spec.md) approved 2026-09-18, amended 2026-09-19.
- [`design.md`](design.md) approved 2026-09-19, with ADR-012, ADR-013 and the ADR-010 amendment.
- [`plan.md`](plan.md) approved 2026-09-19; every task ticked, each deviation stated in the task itself.
- Step 1 — a check goes down and its owners are emailed (the email port moved to `nexovue_platform.email`).
- Step 2 — every channel kind (owners, email, Slack, Teams, Discord, PagerDuty, webhook) with send test, sealed
  secrets, the private-address guard, channel health.
- Step 3 — the console: Alerts, the rule editor, channels, the alerts strip on a check's and a service's page.
- Step 4 — noise controls: flap guard, grouping, burst cap, quiet hours, repeats, mutes.
- Step 5 — service conditions: error rate and p95 thresholds, silence, new error groups.
- Step 6 — end-to-end suite, restart proof, sweep timings, architecture and user docs.
- `/review-diff` (two independent read-only reviews, every finding verified by reading the code before acting),
  then the fixes below, each with a test observed failing first unless stated.

## Review — found and fixed, 2026-09-19

| Sev. | What was wrong | Fixed by | Test |
|---|---|---|---|
| HIGH | Ten channels claimed at once and sent one by one: with slow receivers the last leases lapsed mid-run, another worker sent them too, and the first worker's late *retry* put delivered rows back to be sent again | One channel per claim; `record` updates only rows still carrying the claim's own lease | `test_a_worker_whose_lease_ended_cannot_undo_the_delivery_that_replaced_it` |
| MEDIUM | A *resolved* could overtake its *opened* when the opening was waiting on a retry — PagerDuty drops the resolve and keeps the late trigger open for ever; a person reads "recovered" then "down" | Claims go in decision order, and a row waits while an earlier one of its incident on its channel waits on a retry | `test_a_resolve_waits_for_its_opening_s_retry_and_goes_after_it` |
| MEDIUM | The hour of retries counted from the decision, so a warning held overnight by quiet hours failed on its first 503 without one retry | Counted from the batch's first attempt | `test_a_notification_held_for_hours_still_gets_its_hour_of_retries` |
| MEDIUM | Email to several addresses: one refusal retried the whole batch, re-mailing everyone already reached (about nine copies in the hour); the *channel failing* report did the same every 15 s | A Resend `Idempotency-Key` per batch and address (per failing spell and owner for the report) | `test_a_retry_carries_the_same_key_per_address…`, `test_an_idempotency_key_reaches_the_provider_only_when_given`, `test_a_report_retried_carries_the_same_key_per_owner` |
| MEDIUM | A new-error read stops at 50,000 groups but the watermark moved past the rest — each later announced as *new* | Groups read oldest-first-received; a full read moves the watermark only to its last group; the baseline counts as taken only when a read comes back whole (`baselined_at` empty until then — 0029 changed in place, pre-deployment) | `test_a_history_larger_than_one_read_is_baselined_in_parts_and_never_announced` |
| MEDIUM | Disabling or narrowing a rule, or deleting a check or a project, left its open incidents open for ever — nothing evaluates them again; a deleted project's rules stayed named on channels | Closed as a deleted rule's are — people not told, PagerDuty and webhooks sent *closed* (decision 1) — threshold positions forgotten; a deleted project's rules deleted with it | four tests in `test_alerts_live.py`, one in `test_alert_defaults_live.py` |
| MEDIUM | One value Postgres refuses (a NUL in a customer's service name — confirmed) aborted a whole sweep, for every tenant after it, every minute | Each subject judged in its own savepoint (`incidents.judged_alone`) | `test_a_subject_the_database_refuses_does_not_stop_the_others` |
| MEDIUM | A rule naming a since-deleted check could not be saved at all, and the console could not show the check to untick; `check_ids: []` was storable | Checks the rule already named and that are gone are dropped on save; an empty choice is refused | `test_a_rule_naming_a_deleted_check_can_still_be_edited_and_forgets_it` |
| LOW | A delivery run ended when the one channel it claimed was muted or fully held, leaving other channels' criticals to the next sweep | A run ends only when a claim finds no channel | `test_a_muted_channel_claimed_first_does_not_end_the_run_for_the_others` |
| LOW | Threshold waits compared second-precise times, so a 5-minute wait could fire at 6 | Positions kept on the minute | `test_a_wait_counts_in_whole_minutes_whatever_second_the_sweep_runs` |
| LOW | Quiet hours ending at a time the clock skips (DST) held warnings to one instant but let new ones through at another | Compared as instants | `test_an_end_the_clock_skips_is_one_answer_for_held_and_new_warnings` |
| LOW | A digest of services going silent read "N checks are failing" | Checks only | `test_services_going_silent_together_are_not_called_checks` |
| LOW | A failing channel with no mail provider logged an error every 15 s for ever | Given up once; the console still shows it | `test_a_report_with_nowhere_to_go_is_given_up_once_not_every_sweep` |
| LOW | Email channel settings stayed *unsaved* after a save (the server lowercases and trims) | The form compares against what the server stored | `ChannelsScreen.test.ts` |
| LOW | Deleting, disabling or narrowing a rule left its closed incident on the Alerts screen for up to 30 s | Rule changes refresh incidents; the test harness now uses the app's stale time so this class shows | `RuleEditor.test.ts` |
| LOW | Creating a channel from the rule editor overwrote anything typed while the request ran | The draft updates against its current state | none — the race cannot be staged in the harness |
| LOW | The rule editor offered a kind change the API always refuses | A saved rule states its kind | `RuleEditor.test.ts` |
| LOW | A muted rule was listed as watching on a check's page | Marked *(muted)* | `CheckDetailScreen.test.ts` |
| LOW | Send test's *pending* message said to check the delivery log, which holds notifications, not tests | Copy | — |
| — | The API's send-test route had no test of its own | Added: dispatched only for the caller's channel; outcome unreadable by another organization (observed failing with the ownership check removed) | `test_a_send_test_is_dispatched_only_for_the_callers_own_channel` |

Rejected after verification: *open incidents listed twice* — the design's *Recent incidents* is "the last 30 days,
resolved and open", and a test asserts it; *send test does not refresh the delivery log* — a test writes no
delivery-log row (FR20 is notifications).

## Decisions — answered by the founder, 2026-09-19

1. **Machines are told when an incident closes without recovering — yes.** Built: spec D8 and BR5 amended.
   Deleting, disabling or narrowing a rule, or deleting its check or project, writes a `closed` notification to
   every PagerDuty and webhook channel that was sent the opening (a *resolve* to PagerDuty, `incident.closed` to
   a webhook); a recovery during a mute still reaches them as *resolved*. People are not told. Proved by three
   tests in `test_alerts_live.py`, `test_a_recovery_during_a_mute_still_closes_what_a_machine_was_sent`, and the
   message and payload tests. Migration 0028 changed in place to allow the new kind (pre-deployment).
2. **Owners with an unverified address get no alerts — kept.** The verify-email banner says so.
3. **FR11 — the sentence corrected** to the rule as built: nine messages, not three.
4. **PagerDuty and webhooks are not grouped — kept.**
5. **A read-only ClickHouse user — deferred to hosting**, with the other hosting blockers.

The QA organization's test check (`f21-qa`, *Orders API*) was deleted through the API.

## Found outside F21 — not fixed, for their own change

Seen during the final gates, 2026-09-19. None is caused by F21. Taken up in
[`docs/changes/gate-found-defects/`](../../changes/gate-found-defects/bug.md): all three fixed there.

1. **The worker stays up after a fatal pipeline error.** A ClickHouse insert refused for memory
   (`MEMORY_LIMIT_EXCEEDED`) ended every pipeline — "the worker cannot continue" — but the process did not exit,
   so the container sat *unhealthy* and Docker never restarted it; ingest stopped until a manual restart. A worker
   that cannot continue should exit and be restarted.
2. **Development ClickHouse hits its 4 GiB guard with 0.6 GiB in use.** jemalloc kept ~4 GiB of freed memory
   resident; the server's limit counts resident memory, so ordinary test queries were refused (63 failures in one
   gate). A restart cleared it; it returned under test load within ten minutes. `memory.xml` needs a look
   (allocator purge or the limit's basis).
3. **`test_synthetics_live.py` picks a failing check.** It chooses the check with the most successful runs in two
   days and then assumes every run of it is healthy. A check left behind by an earlier synthetics E2E run
   (`end-to-end-8ddbde8e`, 2026-09-17), failing about half its runs, overtook acme's — two tests fail on data, not
   code. The suite should pick a check whose runs all succeeded, and the E2E should not leave checks running.

## Blocked

Nothing.

## Risks

- Scheduler SQL has no structural tenancy test (Q3, accepted for F21); every per-tenant statement was read by name
  in review and carries its row's `org_id`.
- A NUL in a service name or exception type is now contained to its own judgment, but is still dropped with an
  error in the log. Whether ingest should strip NUL is not checked.
- No automated process-kill test for AC19 (see T6.2).
- The sweeps run every tenant's rules in one task each minute: 636 ms at 100 threshold rules locally. Past a few
  thousand rules they need partitioning (design, *Observability*).

## Validation

Final gate after the founder's decisions, 2026-09-19: Python **3,408 passed**; the 16 failures and 47 errors
were all in eight ClickHouse-backed suites refused by ClickHouse's memory guard (see *Found outside F21*). Those
eight re-run after restarting ClickHouse and the worker: all pass except two `test_synthetics_live.py` tests
failing on a leftover check (item 3); web **1,214**; browser SDK 120; Go 32
packages `ok`; `buf` lint and breaking clean; `alerts.spec.ts` passed against the restarted stack.

Earlier gate, after the review fixes: `just check` exit 0 — Python **3,454 passed**, 3 skipped; web
**1,214**; browser SDK 120; Go every package `ok`; ruff, mypy, biome, `tsc` clean. The stack restarted on the final
code and `alerts.spec.ts` passed (3/3 with its setup); its incident's owners notification was claimed after the
grouping wait and recorded, with the expected reason for an unverified test account.

Live, in the running stack (compose dev, OpenTelemetry Demo), 2026-09-19:

- **Step 1** — a fresh signup's API check answering 404 was confirmed down; the owner's email was rendered by
  `scheduler-deliveries` 35 s after the check was created; fixing it produced *resolved*, "Lasted: 1 min.".
- **AC19** — `scheduler-deliveries` stopped between decision and send; after restart the notification was
  delivered exactly once.
- **Send test** — full round trip through the console to a receiver and back.
- **p95 rule** — fired on the demo's `checkout`.
- **Silence** — the demo's `quote` stopped for 5 min: *went silent* opened; resolved within a minute of restart.
- **New error** — baseline of 33 groups announced nothing; one novel exception was announced once, as an event.
- **Threshold sweep timing** — 22 ms at 1 rule, 90 ms at 10, 636 ms at 100 (one read per project and window).
- **Visual QA** — every alert screen at desktop, dark mode and phone width.
- **E2E** — `tools/e2e/tests/alerts.spec.ts`, 2 tests.

## Acceptance criteria — where each is proved

| AC | Proved by |
|---|---|
| AC1 | `test_alert_senders_live.py` (each kind against the recording site), `alerts.spec.ts` |
| AC2 | `test_channels_live.py` (every response and audit row searched), `test_alert_senders_live.py` |
| AC3 | `test_alert_channels.py` (published Standard Webhooks vector), `test_alert_senders_live.py` |
| AC4 | `test_alert_senders_live.py` |
| AC5 | `test_alert_check_state_live.py`, `test_alert_delivery_live.py`, live run |
| AC6 | By composition: `test_a_run_inside_a_maintenance_window_is_marked_and_changes_no_state` (no state change, so no decision) and the check-state suite (the first recorded change after the window opens one incident) |
| AC7 | `test_alert_check_state.py`, `test_alert_check_state_live.py` |
| AC8 | `test_alert_service_conditions_live.py`, live run |
| AC9 | `test_alert_thresholds.py`, `test_alert_service_conditions_live.py` |
| AC10 | `test_alert_service_conditions_live.py`, live run |
| AC11, AC12, AC14, AC15 | `test_alert_noise_live.py` |
| AC13 | `test_alert_messages.py`, `test_alert_outbox_live.py` |
| AC16 | `test_alert_check_state_live.py` |
| AC17 | `test_alert_outbox_live.py` |
| AC18 | `test_alert_send_test_and_health_live.py` |
| AC19 | live run, and the lease tests in `test_alert_outbox_live.py` |
| AC20 | `test_alert_payloads.py` |
| AC21 | `test_alert_defaults_live.py`, `test_alert_delivery_live.py`, `alerts.spec.ts` |
| AC22 | `test_alert_defaults_live.py`, `test_alerts_live.py` |
| AC23 | `test_alert_messages.py` (links), `alerts.spec.ts` |
| AC24 | `test_alert_messages.py`, `test_alert_reasons.py` |
| AC25 | `AlertsScreen.test.ts`, `alerts.spec.ts` |
| AC26 | `test_alerts_live.py`, `test_channels_live.py` |
| AC27 | `test_alert_check_state_live.py` |

## Production readiness

Assessed as one change, 2026-09-19 — recorded in
`docs/operations/production-readiness.md`. **F21 adds no blocker of
its own; the platform remains NOT READY.** F21 depends on three existing blockers: email delivery (the default
rule emails owners), the check secret key (channel secrets are sealed with it), and migrations as a deploy step
(0028 and 0029 before the scheduler starts). Recommended: a read-only ClickHouse user for the scheduler;
healthchecks for the scheduler services and a watch on the oldest `pending` notification.

## Specification impact

FR1, FR2 and FR10 amended and AC27 added, 2026-09-19, with the design's approval. After review, by the founder's decisions: BR5 and FR11 amended, D8 added.

## Follow-up — the open-incident badge (2026-09-19)

Founder's request, in place of web push and an in-app notification centre (neither reaches someone
who is not looking at the console; see the spec's *Out of scope*, "Mobile push"). The sidebar shows
how many incidents are open beside **Alerts**, tinted critical when any is, and nothing when none is.

- `GET /alerts/incidents/open-count` — a count by severity, not the list, since every screen asks
  every 15 seconds. Migration 0031 adds `ix_incidents_project_id_open` (partial, `resolved_at IS NULL`).
- Validated: `test_alerts_live.py` (the count is this project's open incidents only; another
  organization's project is refused), `alert-words.test.ts` (badge wording), and
  `tools/e2e/tests/alerts.spec.ts` (the badge appears when a real check opens an incident and goes
  when it closes); `just check` green.
- Not done: the phone drawer is closed by default, so on a phone the badge is seen only once the
  menu is opened.

## Documentation impact

- `docs/decisions/` — ADR-012 (with *As built*), ADR-013 (one channel per claim, decision order, email
  idempotency — from review), ADR-010 amended, `README.md`.
- `docs/architecture/{system,database,deployment}.md` — alerts, the tables, the three scheduler processes.
- `docs/product/setup/alerts/{channels,webhooks}.md` — user documentation, webhook schema v1.
- `docs/product/feature-catalog.md`, `docs/features/synthetic-checks/design.md`.
- `docs/operations/production-readiness.md` — the F21 assessment.

## Next action

Founder: read the diff; then the commit (Raj's identity only).

## Last updated

2026-09-19
