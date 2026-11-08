# Status: Case management (F22)

## Lifecycle status

**In development.** The founder approved the specification, design and plan on 2026-11-02; F6 merged the same day (PR #24). Plan **Step 1 is on `feature/phase-2`** (commit 0094862). **Step 2 (2026-11-07) except T2.9, which waits on F3**: built on the worktree branch `wt/f22`, to be committed with T2.9 unticked (founder, 2026-11-07); T2.9 is built once F3 lands.


## Current task

Step 2 (2026-11-07) except T2.9, which waits on F3. On `feature/phase-2` (commit 631c027). Next: the
judgeable checks of Steps 1 and 2 on a dev stack, then Step 3, which needs the
founder's GitHub test organization first.


## Step 2 built, 2026-11-07

Plan tasks T2.1 to T2.8, T2.10 and T2.11, as written, with the notes below. T2.9
waits on F3 (founder, 2026-11-07): F3's `evidence/gather.py` is not on the
branch, and Step 2 is committed without it. **No migration:** every
column, table and index Step 2 reads was created by 0051. ClickHouse untouched.

- **T2.1, joining at birth.** `project_job._OPEN` sets `root_finding_id` from a
  subquery: the same scope and service, a case a person acted on within
  `CASE_JOIN_WINDOW` (7 days, now one constant in `nexovue_platform/cases.py`,
  bound from the job's own clock). Both callers pass it, and
  `events.finding_opened` writes `finding_joined_case` on the root unless the
  opening falls in a flapping spell (FR18). **The API half was already built in
  Step 1** (T1.5: detach on every resolve, `JOIN_OPEN_SQL` on start and reopen,
  with tests); nothing more was needed there.
- **T2.1, the barrier (AC101).** Two live tests drive the job's own statements
  (`_OPEN_FINDING`, `_CLOSE`, `_OPEN`) in one transaction against a person
  holding the root `FOR NO KEY UPDATE`: the job never waits, and the cycle
  (person waits on the joined finding while the job's insert needs the root)
  commits on both sides. **Run once with the person's lock as `FOR UPDATE`, as
  a negative control: the first test timed out and the second raised
  `DeadlockDetectedError`.** The job's real batch could not be used for the
  close-then-open, because a run judges at most 2 minutes and a new finding
  needs 5 minutes of sustain after the old one's 10 clear minutes.
- **T2.2.** The tenancy test reads the new statements in `detection/events.py`,
  `project_job.py` and `nexovue_platform/cases.py` with **no new exception**, and
  now asserts those modules (and `nexovue_scheduler.cases`) are walked.
  `cases_sweep`'s six first reads are named cross-tenant exceptions with their
  reasons, as the design says (*Keeping and ending*); T4.6 adds
  `sweep_connections`' and beat's.
- **T2.3, the feed.** One rank, `CASE_RANK` and `CASE_SEVERITY_RANK` in
  `findings/queries.py`, with `EXISTS` on an open joined finding; the console's
  `orderFindings` follows the same rule, and **one shared fixture,
  `features/findings/feed-order.fixture.json`, holds both** (AC27). It caught a
  real defect while it was written: the console promoted an acknowledged open
  finding with an active case to the first tier. The case filters are a separate
  statement bound to `org_id`, joining projects and users, roots only, muted
  included. A person's rows are a new `FindingFeedRow` naming the assignee, when
  they took it and who resolved it, read in the one statement (AC71); a token's
  rows stay plain `Finding`. `needsAttention` is deleted. F6's test "the feed
  never carries the member-only fields" was narrowed to AC71's rule, since a
  person's feed row now names the assignee. **F4's MCP `list_findings` tool
  passes `for_person=False`**, so its rows still name nobody.
- **T2.4, the screen.** *Show* filter (`SelectField`) in the address through
  `validateSearch` on the feed and finding routes (`feed-filter.ts`, parsed in
  one place); a case filter has its own query key with no project and waits on
  nothing else; no detection line, summary or muted link; "Across all your
  projects…"; zero results in each filter's words with **Show all**; "Showing the
  newest 200."; the case row (full sentence, case severity in words, project, no
  sparkline, no "Acked", the idle warning, Resolve on *Active cases* and
  *Assigned to me*, one tap with the signal closed and a confirmation while it
  is open); on *All*, a closed finding with an active case keeps the case row,
  an open one states the case under its title, a joined one links "Part of a
  case"; the new subtitle; "Back to Assigned to me" on the finding page.
- **T2.5.** `_PAST_DERIVED_RETENTION` gains the two clauses, the first clause
  unchanged so `ix_findings_closed_at` still serves it, no clause on a root.
  AC30's test passed before the change: it pins behaviour the new clauses must
  keep.
- **T2.6, `cases_sweep`** (`nexovue_scheduler/cases.py`, every 5 minutes on
  `light`, registered in `TASK_MODULES`): clears a removed or demoted assignee,
  heals a missed join and a finding left on a resolved root, ends idle cases
  (`no_activity`) and untouched ones (`recovered_untouched`) through one ending
  that mirrors the API's (resolve, detach with events, event, audit naming
  nobody), and starts the idle warning with its `idle_warning` row in
  `finding_emails_owed` (new shared `RECORD_EMAIL_OWED_SQL`). **Every lock is
  `SKIP LOCKED`, so the sweep never waits and cannot be half of a deadlock**; a
  test holds a row and shows the sweep finishing at once and clearing it on the
  next pass. The durations moved to `nexovue_platform/cases.py`
  (`CASE_IDLE_LIMIT`, `CASE_IDLE_WARNING_AHEAD`, `CASE_UNTOUCHED_AFTER`), so the
  page's "ends on" and the sweep read one definition.
- **T2.7.** `FindingForMember` gains `earlier_case`, `case_activity_at`,
  `case_ends_on`, `case_ends_by`, `deletes_on` and `joined_kept_count`;
  `finding-page.ts` gains `caseEndingSentence`, `earlierCaseLine`,
  `keptSentence` and `reopenEarlier`; `CaseSection.tsx` shows them, with
  **Reopen that case** beside Take this when the earlier case ended without a
  person's decision. **A person's take that joins a quiet case now restarts that
  case's idle clock**, as the design's *Joining* says; Step 1's
  `_join_existing_case` did not (test seen failing first).
- **T2.8.** The export gains `notes_written`, newest first, with its own
  `notes_truncated` flag, read one past the bound so the flag means a note was
  left out.
- **T2.10.** FR53's line on the finding page's linked alerts and the incident
  page; BR16's sentence on account deletion and on the members screen's own
  **Leave** confirmation (`shared/case-words.ts` holds both sentences, as two
  features show each); the personal-data spec says so, and lists notes in the
  export. **An admin's Remove of someone else** says "Notes {name} wrote stay, in
  their own words, as your organization's record." (`notesStayFor` in the same
  file; founder's option B, *Founder answers, 2026-11-07*). AC121 and AC122 are
  satisfied.
- **T2.11.** FR47's line, `Part of a case started <UTC>. Open it: <link>`, is
  composed from the row as the message is claimed (`outbox._TAKEN` reads the
  root) into `AlertMessage.case_line`, kept out of `details`; `dispatch.send`
  adds it for people-read channels only, so PagerDuty and webhook bodies are
  byte-equal with and without a case, and a finding detached before sending
  carries none (live test).

### Where the code needed more than the plan named

- **T2.11 touches `alerts/outbox.py` and `alerts/dispatch.py`** besides
  `messages.py`: "decided from the row when composed" needs the claim's read,
  and "people-read channels only" needs the place that knows the channel. A
  message batching several notices (a digest) carries no case line; each of its
  lines is one notice's summary.
- **T2.7's lines are drawn in `CaseSection.tsx`**, not listed in the plan.
- **Fields beyond the design's list:** `case_ends_by` and `case_activity_at`
  (FR26 and FR49 need different sentences, and "No activity for 76 days" needs
  the last act), and `case_resolved_by_name` on a feed row ("Asha resolved the
  case at 16:10" in *Resolved cases*).
- **`render-harness.test-util.ts`** now records each call's query string and
  takes a starting address, for the filter tests.

### Validation run (read, not inferred)

Against this worktree's own Postgres (port 25432) and ClickHouse (port 28123),
migrated to 0051 and to ClickHouse's 37 migrations, torn down afterwards.

- **Seen failing first, then passing:** the job's join tests (5 failed:
  `CASE_JOIN_WINDOW` missing, `root_finding_id` None); the barrier's negative
  control (above); AC29's retention test (kept rows deleted); the sweep suite
  (ImportError); the beat entry (ImportError); the platform's owed-email
  statement (AttributeError); the feed tests (6 failed); the T2.7 page tests (8
  failed with `_keeping` blanked); the clock restart (assert on
  `case_activity_at`); the export's notes (KeyError, AttributeError); FR47's
  tests (ImportError, then assertions); `ordering.test.ts` (3 failed, then the
  fixture's real defect); `finding-page.test.ts` T2.7 (10 failed);
  `FindingsScreen.test.ts` (2 failed: the summary's 503 hid the list);
  `FindingsView.test.ts` (13 failed); the back link and earlier case on the page
  (3 failed against the committed `FindingScreen.tsx`/`CaseSection.tsx`); the
  four T2.10 lines (4 failed); the admin-removal sentence (1 failed: the
  confirmation had no notes line for Priya).
- `apps/scheduler/tests`: **4,881 passed, 3 failed** — the 3 are
  `test_alert_telemetry_live.py`, which needs the seeded dev project
  (`just seed`); four suites needing Kafka, SeaweedFS or DNS were left out
  (`test_connect_guard`, `test_detection_failure_live`,
  `test_detection_settle_live`, `test_erasure_live`). None is touched here.
- `apps/api/tests`, every offline suite: **3,872 passed, 1 skipped**. Live:
  `test_findings_api_live` (31), `test_findings_cases_live` (32),
  `test_findings_actions_live`, `test_personal_data_live` (10),
  `test_members_live`, `test_deletion_live`, `test_mcp_live`, `test_admin_live`,
  `test_alert_defaults_live`, `test_audit_read_live`,
  `test_detection_status_api_live`, `test_db_session_commit_order`: **229
  passed** together.
- `packages/py-platform/tests`: **135 passed**.
- Web: **1,587 vitest tests passed** in 138 files; `tsc -b` clean; Biome clean on
  every touched file (one warning remains in untouched
  `connection-health.ts`). After the admin-removal sentence (2026-11-07):
  the whole web suite **1,588 passed** in 138 files,
  `pnpm -r --if-present run typecheck` clean, Biome clean on the three touched
  files.
- `uv run mypy .`: no issues in 741 files; `ruff check` and `ruff format` clean.
- **Not run:** `just check` (the coordinator runs it); the judgeable checks of
  Steps 1 and 2 on a full dev stack, including T1.0's live half;
  `test_anomaly_detection_migration_live.py`; the four suites above.

### Implementation notes and residual risks

- **The untouched end ignores links for now:** `finding_links` arrives at T4.2,
  and T4.3 proves a link stops the untouched end.
- **The idle warning owes its email; nothing sends it until T4.6,** and the
  synced issue's comment request also lands then.
- **"Warned in this spell" is `case_warned_at < case_activity_at`**, so the
  sweep warns once per idle spell whether or not every act clears the marker.
  `take` and acknowledging set `case_activity_at` without clearing
  `case_warned_at`; with this condition that changes nothing.
- **The feed's joined count and "ends on" are read per row set,** two small
  statements beside the feed's one; the case filters read no ClickHouse at all.
  Not measured.
- `spell_state`'s one-hour edge (Step 1) is unchanged.


## Step 1 built, 2026-11-02

Plan tasks T1.0 to T1.12, exactly as written. What exists now:

- **Migration 0051** (`a_finding_grows_into_a_case`): the case columns, checks and
  partial unique indexes on `findings` (one active case per service and per
  project), composite tenant keys (`UNIQUE (org_id, id)` on `users` and
  `findings`, `UNIQUE (org_id, project_id, id)` on `findings`), and
  `finding_events`, `finding_notes`, `finding_emails_owed`. Concurrent builds on
  the tables with rows; roll-forward only (notes are people's words). The
  composite-key risk against `test_deletion_live.py`'s catalog query did not
  bite: the suite passes unchanged (25 passed).
- **`nexovue_platform`**: `detection.py` gains `CASE_STATES`, `CASE_RESOLUTIONS`
  (with `recovered_untouched`) and `FINDING_EVENT_KINDS` (with `explained`,
  frozen by 0051 and held by `test_alert_vocabulary.py`); new `cases.py` with
  `RECORD_FINDING_EVENT_SQL` + typed `finding_event_parameters()` (no free-form
  detail, AC24), `RESOLVE_CASE_SQL`, `REOPEN_CASE_SQL`, `DETACH_OPEN_JOINED_SQL`,
  `JOIN_OPEN_SQL`, `SET_WANTED_STATE_SQL`, `RECORD_CASE_AUDIT_SQL`; new
  `connections.py` holding `strict_name()` early, because FR5's email needs the
  strict form now (T4.1 grows the module and owns its gate suite).
- **Actions**: `OPERATE_CASES` (working roles) and `DELETE_ANY_CASE_NOTE`
  (owner, admin), through `permissions.py`, `policy.py`, the web `identity.ts`,
  and the team spec's FR1 table.
- **API**: new `findings/cases.py` (take, assign with FR4's expectations,
  resolve with detach, reopen with rejoin, notes, `clear_assignee`), locks
  `FOR NO KEY UPDATE` (D78), every operation committing before its view
  (AC102); `PATCH /findings/{id}/case` (returns `email_outcome`),
  `GET /findings/{id}/timeline` (persons all, tokens kinds and times, support
  view hides note bodies), `POST`/`DELETE` notes (the delete on
  `CurrentIdentity`, in `EXEMPT` with its reason); `acknowledge`,
  `mark_false_alarm` and `accept_new_normal` call into `cases.py`;
  `to_finding`/`_member_view` carry the case fields; `Reader.is_real_person`.
  The assignment email (`Message.CASE_ASSIGNED`): strict name, severity, link,
  nothing quoted, 5-minute limit read from `assigned` events under the root's
  lock (D91).
- **Members**: removal, account deletion and a move to viewer clear every
  active assignment with a system `unassigned` event, `SKIP LOCKED` (AC10's
  route half; the sweep half is T2.6's).
- **Scheduler**: new `detection/events.py` — the job's timeline events written
  beside the notify calls, with a read-only `spell_state()` so a flapping spell
  (muted included) is one `flapping_started` row and F6's `flapping` column is
  untouched (AC23); wired into `project_job` at the open, close, escalate, join
  and adoption sites.
- **Console**: the work-first finding page (D86) — `CaseSection`, `CaseLine`,
  `Timeline`, `NoteBox`, `ReassignPanel`; the two labelled groups *The case*
  and *The signal*; the Evidence jump link; the page-level status region with
  own-act quieting; 30-second visible-page refresh; Copy as text; every
  sentence from the spec's tables in `finding-page.ts`/`finding-words.ts`.
  **Acknowledge is gone from the console (D47): Take this is the one button**,
  and the API route stays.
- **Tooling**: `just detection-fault --service <name> [--clear]`
  (`tools/detection-seed/fault.py`) writes abnormal or normal rollup minutes so
  the real project job opens and closes a finding; refuses non-localhost; no
  direct `findings` insert.
- **ADR-024 filed** (*A case is a finding's columns, with one active case per
  service*), with the README index. The other two proposed ADRs (reconcile +
  doorbell; `scheduler-connections`) stay in `design.md` until Step 3 starts,
  since nothing built in Steps 1–2 rests on them and D97 numbers them when
  written — the coordinator may overrule and file them now.

### Validation run (read, not inferred)

- `packages/py-platform/tests/test_finding_events.py` + `test_case_transitions.py`: **51 passed** (seen failing first: ImportError before the module existed).
- `apps/api/tests/test_findings_cases_live.py` (new, Postgres live, ClickHouse replaced by a commit-witness stub that fails any read before the case transaction commits — AC102): **31 passed**, after three real defects the suite caught first (an autoflush outside the savepoint, the assignment email counting its own event against the 5-minute limit, an ISO cursor dying in a query string).
- `apps/scheduler/tests/test_detection_events_live.py` (new): **5 passed** (first run failed on an open-findings unique clash, fixed in the test's own writes).
- `tools/detection-seed/test_fault.py` (new): **4 passed**.
- Existing suites rerun after the change: `test_deletion_live.py` 25, `test_findings_actions_live.py` + `test_findings_api_live.py` + `test_members_live.py` + `test_personal_data_live.py` + `test_db_session_commit_order.py` + `test_audit.py` **95 passed** (two grown: the token field list, the member-only fields), `test_detection_project_job_live.py` + scheduler tenancy structure **49 passed**, offline structure suites (`test_authorization_coverage`, `test_read_surface_structure`, `test_findings`, `test_api_conventions`, `test_postgres_tenancy_structure`) **1,917 passed**, `test_alert_vocabulary` + `test_migration_rules` + `test_setup_schema` + `test_erasure_inventory` green.
- Web: **1,541 vitest tests passed** across the app (136 in `features/findings`, including the new state-sentence table and the AC18/AC104/AC106/AC132 screen tests); `tsc -b` and Biome clean on the app.
- `uv run mypy .`: **no issues in 737 files**; `ruff check` clean.
- Migration 0051 applied forward against live Postgres (including a re-run after a deliberate partial state); ClickHouse untouched by design.
- **Not run:** `just check` (the coordinator runs it); T1.0's live run (the real
  job opening, closing and reopening a finding over `detection-fault`) — it
  needs the full dev stack judging, which this shared 16 GB machine was not to
  carry; it is the judgeable check's first step. `just e2e`/chaos: not relevant
  to this step.

### Implementation notes and residual risks

- **`Reader.is_real_person` is built directly** (`identity is not None and
  impersonation is None`) because F3's `Reader.is_person` is not on the branch
  yet; when F3 lands it becomes `is_person and impersonation is None`, as the
  design words it. Semantics are identical today.
- **`events.spell_state` counts openings over the plain hour window** (it does
  not subtract at a previous spell's end as the notify guard does), so two
  distinct spells inside one hour would write one `flapping_started`, not two.
  One event per spell (D35) still holds; the edge is noted for T2.x review.
- **Deadlock prevention is still reasoning** until T2.1's barrier test runs, as
  the plan says.
- The ClickHouse-read-under-lock proof covers the case routes; the three
  existing actions commit before their views too, exercised by the same stub.
- `findings/queries.person_name` (singular) was superseded by the bulk
  `person_names` and deleted with this change, per the working rules.


## Completed

- [`research.md`](research.md): users, attacker paths, competitors, methods, limits. Rival facts corrected from each vendor's page on 2026-11-02.
- [`spec.md`](spec.md): approved by the founder 2026-11-02. **139 acceptance criteria, 54 functional requirements, 98 decisions taken; every founder decision answered on 2026-11-02** (item 2 by the foundation).
- [`design.md`](design.md): revision 2, regenerated from the spec, approved by the founder 2026-11-02. Three proposed ADRs, written inside it.
- [`plan.md`](plan.md): revision 2, regenerated, approved by the founder 2026-11-02. Eight steps, plus T0.1 and T0.2 first. Every acceptance criterion has exactly one owning task, placed at or after every task its table says it depends on. That table is written by hand, so the check is only as complete as the table.
- Read for these: the findings service, router, queries and schemas; `Finding`'s model and migration; the project job's writes; `notify`; retention and expiry; the job queue, registry, worker and preload; the guarded client; the sender; webhook signatures; rate tiers; sealing and keys; Compose, Caddy, monitoring rules and the install script; member removal and the export; the finding and feed screens; F3's, F4's and F5's designs where they touch F22.
- Checked again on 2026-11-02 from GitHub's own pages: the manifest flow, the setup URL's warning, the issue list's parameters, and the App permissions. Jira's webhook page too. **Atlassian's reference pages for transitions, status categories and search would not load.**

## Next action

Step 2 is committed (631c027) with T2.9 unticked; T2.9 is built when F3's evidence job lands. Step 3 waits on the founder's free GitHub test organization (foundation decision 23).
Then the judgeable checks of Steps 1 and 2 on a dev stack
(`just detection-seed`, `just detection-fault --service checkout`, take, note,
`--clear`, the fault again, *Active cases*, an untouched case's end line),
recorded here. Step 3 needs the founder's GitHub test organization (D93), asked
for at the time. T2.9 is built once F3's `evidence/gather.py` exists. Step 7
(Jira) still waits on Atlassian's written answer; T0.1's question should be sent
if it has not been. The two tracker ADRs are filed when Step 3 starts.


## Open decisions

None for the founder. Items 1 and 3 of the founder decisions were approved on
2026-11-02, as recommended; item 2 is foundation item 6 (*Founder answers,
2026-11-02*, below). Step 7 waits on Atlassian's written answer, not on the
founder.

## Founder answers, 2026-11-07

**1. BR16's sentence when an admin removes someone else (T2.10, AC122, FR54).
Founder answer, 2026-11-07: option B.** Built that day: an admin's **Remove**
says "Notes {name} wrote stay, in their own words, as your organization's
record.", with the removed person's name as plain text; the leaver's own
**Leave** and account deletion keep the first-person sentence. `spec.md` BR16
and FR54 and the personal-data spec carry it; AC122 is satisfied.

The question as it was put:

The spec gives one sentence for "the member removal and account deletion
screens": "Notes you wrote stay, in your own words, as your organization's
record. Delete any you want gone before you leave." It is addressed to the
person leaving. On the members screen the same confirmation serves an admin
removing a colleague (`features/members/MembersScreen.tsx:228`, `{isSelf ?
"Leave" : "Remove"}`, one `RemoveConfirmation`), and the person removed never
sees it. Built: the sentence on account deletion and on one's own **Leave**.
Not built: anything on an admin's **Remove** of someone else.

- *Option A:* the literal sentence there too. It tells the admin to delete
  notes "before you leave", which is wrong.
- *Option B (recommended):* a third-person fact for the admin: "Notes {name}
  wrote stay, in their own words, as your organization's record." It keeps
  BR16's meaning and states nothing false; it is a new line, so it needs the
  founder's word.
- *Option C:* nothing on an admin's removal; read FR54 as the leaver's screens.

**2. Committing Step 2 without T2.9. Founder answer, 2026-11-07:** Step 2 is
committed now with T2.9 unticked, waiting on F3, because F3's
`evidence/gather.py` is not on the branch. T2.9 is finished when F3 lands.

## Blocked

- **Founder decision, 2026-11-08:** Steps 3 to 6 are built against a simulated GitHub; T3.0's manifest-flow trial and T5.2's real webhook are run by the founder after Phase 2 (foundation item 28). Step 7 (Jira) still waits on Atlassian's written answer.
Nothing is blocked now. Implementation waits on approval of the specification, design and plan, and on F6 merging. T0.1 is approved (2026-11-02) and can be sent now. Step 7 waits for Atlassian's written answer, 4 weeks at most. T5.2 (a real webhook delivery, AC130) waits for the Oracle deployment, since no test server is rented.

## Risks

- **Nobody has asked a user.** Every need here is inferred. Whether status-only sync is enough is a guess. It should be in the founder's five to eight conversations.
- **F22 is a reason to stay, not to arrive,** by the research's own reading. Steps 1 and 2 are the value. A tracker is what makes it stick.
- **Jira's API details are second-hand** until T7.0.
- **The manifest flow from a private address is untried.** T3.0 starts Step 3 by trying it.
- **Numbers are guesses:** 90 idle days, 72 untouched hours, a 7-day join window, 5-minute polling, a 5-minute sweep, 30-second refresh, 1 MiB body cap, 384 MB ceiling, note limits. Each says so where it appears.
- **Status-only sync may read as thin** beside rivals who sync comments. Stated in the spec and research.
- **A public repository reference can be closed by an outsider.** It changes nothing unless promoted, and promotion warns.
- **Deleted notes stay in backups for a while,** how long was not traced. Issues stay in the customer's tracker after deletion. Both go in customer documents.
- **No reachable server before deployment** (foundation item 9). A real GitHub webhook delivery (T5.2, AC130) is not proved until Oracle; until then sync is proved by polling and recorded fixtures. Memory (T4.9) is read per container on the Mac; the whole 16 GB server, with about 1.5 GB left by arithmetic, is not measured before then.

More risks, from the reviews, are under *Reviews*, *What remains as a risk*.

## Validation

**Nothing was run: no test, no query, no call to GitHub or Jira.** These documents were checked by reading code and pages only. Lock behaviour comes from reading the code and Postgres's rules.

One thing was checked by script, on the documents: every AC in `spec.md` (AC1 to AC139, no gaps) has exactly one `Owns` in `plan.md`, no owner sits before a task *Criteria that depend on other tasks* names for it, and every task named in `spec.md` and `design.md` exists in `plan.md`. T0.2 makes that check permanent while F22 is being built. **What this does not prove:** an earlier version of this check passed while seven owners could not sign off when they ran. It sees only dependencies someone wrote in the table.

## Specification impact

New specification. Asks to amend `feature-catalog.md:85,337,339` and `vision.md:133,140,244` when approved. Differences from `phase-2-foundation.md` are listed in `design.md`.

**Most disagreements with other documents were closed on 2026-11-02** (*Cross-feature reconciliation*, below). Still open: the personal-data wording, and the competitors research's word "gap".

## Documentation impact

Nothing yet disagrees with the code, as nothing is built. On approval: the catalog and vision lines above. On build: `sizing.md` (its total is also out by 0.75 GB today), `key-rotation.md`, and a new `docs/product/setup/trackers/`.

## Reviews

Eight persona reviews read `spec.md`, `design.md` and `plan.md` on 2026-11-02. They reported **145 findings**. Each was checked against the code or the vendor's page before anything changed. **144 are fixed, some with a part rejected. 1 is rejected whole.**

### Verdicts

| Persona | Verdict | Findings |
|---|---|---|
| Architect | not_ready | 17 |
| Developer | not_ready | 22 |
| UX | not_ready | 21 |
| Sales | ready_with_fixes | 16 |
| Marketing | ready_with_fixes | 13 |
| Customer | not_ready | 19 |
| Security | not_ready | 21 |
| QA | not_ready | 16 |

These verdicts are on the documents as they were. Nobody has given a verdict on the revision.

These counts and verdicts are for the eight persona reviews. The *Second review* section below is separate.

### What was verified by reading

The main claims held, read from these places:

- **The documents had drifted:** a script confirmed 35 ACs had no task. `spec.md` cited T2.9 and T8.6, which did not exist.
- **Locks:** `queries.py:108` takes `FOR UPDATE`. `project_job.py:262-268,1100-1145` closes and opens in one transaction, in batches at `:616`. `service.py:178-189` reads ClickHouse under the lock. `deps.py:63-73` commits after the route. Deadlock itself is reasoning, and nothing was run.
- **Muted findings:** `notify.py:213-215` returns before the guard.
- **Edge:** `Caddyfile:83` has `form-action 'self'`.
- **Settings:** `settings.py:172-178`; `__main__.py:59,74` shows `Settings` never sees the queues.
- **Demotion:** `members/service.py:147-167` does not call `scrub`.
- **Support view:** `findings/router.py:90` is true during one.
- **Seed:** `seed.py:1-9` writes history only.
- **Alert rule:** `nexovue.yml:16-22` pages on `absent`.
- **F5:** `ai-solution/design.md:276,463-466,688`. Corrected by the second review: this line first gave numbers that did not hold those claims.
- **Rival facts:** SigNoz, incident.io, Elastic, Datadog and Sentry's GitHub and states pages were read on 2026-11-02, through a summarizing tool.

### Where a reviewer was partly wrong, or the claim needed narrowing

| Finding | The claim | What the source says |
|---|---|---|
| Security 1 | A task must "build" the scheduler's `org_id` test | It already exists. The foundation is out of date. The plan extends it (Architect 17 is right) |
| Developer 19 | JWT belongs in `py-platform` | F5's design has moved its work to the scheduler. Rejected |
| Architect 2 | A person's act blocks a detection batch | True for a foreign key check, and fixed. When the root is the service's open finding, the job's own `FOR UPDATE` still waits on a person, as F6 designed. Committing before the ClickHouse read shortens that wait |
| Architect 9 | Reading the spell needs a count "on detection's hot path" | It runs only when a finding opens, which is rare, not each minute judged |
| Marketing 5 | incident.io "can resolve an incident from a ticket" | Its page says so for Linear only. The research now says that |
| Sales 13, Marketing 6 | OpenObserve Enterprise may cover a small team | The 50 GB a day is sourced. That it covers a team this size is a guess, and is labelled one |

### Conflicts between personas, and how each was decided

No written decision rule was found for F22, so this order was applied and recorded as D89: founder decisions stand; then AI safety and security; then what a customer is told being true; then behaviour the spec requires; then what other features' documents already rely on; then simplicity.

| Conflict | Decided | Why |
|---|---|---|
| **Developer 19 against Architect 7:** JWT in `py-platform`, or in the scheduler | Scheduler | F5 already relies on it, and nothing in the API signs |
| **Sales 10 against Marketing 3:** keep "incident tracking without the incident process" with a qualifier, or withdraw it | Withdraw (D58) | What a customer is told must be true. The foundation reserves the word, and a qualifier does not survive being quoted |
| **Customer 6 against Sales 5 and Security 10:** hold a tracker's close while the signal is open, or let it win and tell someone | Hold, **and** email on every close (D73) | Closing a case while the problem is measured is the worse failure. The email answers the other two, whichever path applies |
| **Customer 7 against the spec's "one comment at the end":** when the idle comment goes | At the start of the warning, one only (D75) | A developer can still act, and the words are then true |
| **Architect 12 against Security 5:** `POLICY_OF` or `EXEMPT` for the note delete route | `EXEMPT` | `POLICY_OF` names an action, and this route has none |
| **UX 20:** `SelectField` or shorter tab labels | `SelectField` (D85) | FR22 names the labels, and the console's convention fits |
| **UX 7 against F5's table:** Case left and first, or right and sixth | Left and first (D86) | NFR8 and FR46 require it, and F5's sections do not exist yet. Handed over |
| **QA 11 against Architect 6:** a browser job or a hand list | Hand list, and a real Caddy image for the header (D84) | A browser job is its own piece of work. Named as step L |
| **Customer 1 against BR3 and the foundation:** an untouched case ending at 72 hours | Adopted, with BR3 narrowed (D71) | BR3 says recovery does not end the work. Silence does here, and the page warns. Since decided as D94: kept, and tested in the founder's one round of conversations |

### What was changed

**`spec.md`.** New FR48 to FR54, AC90 to AC133, D70 to D89, and a third founder decision. Nothing renumbered. The largest behavioural changes:

- A note or link no longer acknowledges (D70).
- An untouched case ends 72 hours after recovery, and a row can Resolve a case with no synced link (D71, FR52).
- Resolving with a synced link always confirms, and can leave the issue open (D72).
- A tracker's close holds while the signal is open (D73).
- Entering `investigating` resets the idle clock (D74).
- The idle comment goes at the warning's start (D75).
- "Asha is on it" became a fact with its time (D76).
- PagerDuty's limits show on the page (FR48).
- The story, Goal, May-say and Do-not-say lists were rewritten, with honest limits and a buyer's questions table.
- BR9, BR14 and BR15 corrected, and the F5 exception removed (D53).

**`design.md`.** Regenerated. Removed: ETags, the webhook's job, `Nobody is on this`, `Start a case`, the joined page without actions, the retention clause on a root, joining as activity, the unwindowed join, the `url` column, the typed Jira webhook secret, and notes readable in a support view. Added: *Locks*; shared transitions; the second queue; the third role value; the read-only spell check; the case feed statement and rank; five indexes; composite tenant keys; setup columns; the start route and setup tables; the full state table; the case row; note box, Reassign and refresh rules; `strict_name()`; frozen facts; the comment and notice mechanisms; the audit table; the test harness; *Testing limits*; *What other features must change*.

**`plan.md`.** Regenerated. Added T0.1, T0.2, T1.0, T1.4, T1.11 and T1.12's split, T2.9 to T2.11, T3.9, T3.11 to T3.13, T4.9, T7.6, T8.5 to T8.9, and *Not in this plan*. T2.2's old content moved to T1.10 so events exist before the judgement. Atlassian's question moved to T0.1. AC37's running-job half moved to T4.5. Two judgement steps now name real commands.

**`research.md`.** SigNoz and incident.io rows corrected. Rows added for Elastic Cases, Datadog Work Management and Sentry self-hosted. A GitHub table added, marked incomplete. "The usual two-way model is narrow" and "the real gap" replaced. Lessons added on recovery, regression and the word "case".

### Every finding, and what became of it

"Fixed" means changed in the documents. Nothing is built. "Part rejected" is explained in the next section.

**Architect**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | Design and plan describe a different system from the spec | Fixed: both regenerated; AC check; T0.2 |
| 2 | Blocker | Lock-order claim false; ClickHouse read under the lock | Fixed: *Locks*, D78, AC101, AC102 |
| 3 | Major | Ending a case in the wrong service | Fixed: `nexovue_platform/cases.py`, D77, AC103 |
| 4 | Major | Tracker reopen undone within the hour | Fixed: D74, AC96 |
| 5 | Major | Table definitions cannot meet tenancy and uniqueness | Fixed: *Data and migration impact*, AC72 |
| 6 | Major | GitHub connect fails at the first step | Fixed: start route, setup columns, T3.0, T3.9 |
| 7 | Major | F22 and F5 disagree on GitHub, connection and mapping | Fixed in F22 (D41, D53, BR15, AC32); F5 already agrees on most; page order handed over |
| 8 | Major | F3's settings shape does not fit a third role | Fixed: D80, T3.2; F3 told through the handover table |
| 9 | Major | Events in `notify` do not work | Fixed: `spell_state()`, T1.10, AC23 |
| 10 | Major | Organization-wide feed not designed | Fixed: separate statement, rank, indexes, T2.3 |
| 11 | Major | Three side effects have no mechanism | Fixed: `comment_wanted`, `tell_pending`, split markers |
| 12 | Major | Viewer deleting a note contradicts itself | Fixed: AC9 excepts it; `EXEMPT`; difference table |
| 13 | Major | Demotion does not clear assignments | Fixed: T1.8, AC10 |
| 14 | Major | Optional role treated as always running | Fixed: profile, `connections_active`, AC118, AC123 |
| 15 | Major | `explained` ownership contradictory | Fixed: T2.9, frozen list; F3 told |
| 16 | Minor | Capacity stale and measured last | Fixed: T4.9, T8.4, 13.75 GB |
| 17 | Minor | Scheduler tenant test already exists | Fixed: *Security*, T2.2, T4.6, AC129 |

**Developer**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | Plan older than spec | Fixed: regenerated; Atlassian moved; decision 2 in Questions |
| 2 | Blocker | Manifest form refused by the browser | Fixed: T3.9, AC73 |
| 3 | Blocker | Deadlock; D42's reasoning wrong | Fixed: D78; D42 corrected; AC101 |
| 4 | Major | Judgeable checks cannot run | Fixed: T1.0, T1.10 moved; commands named |
| 5 | Major | T2.2 cannot meet AC23 | Fixed: T1.10; risk row deleted |
| 6 | Major | Join and activity rules are the rejected ones | Fixed: *Joining*, activity rule, AC68, AC31 |
| 7 | Major | Retention contradicts D43 | Fixed: clause and index removed |
| 8 | Major | Unique indexes block required behaviour | Fixed: three indexes, T4.2 |
| 9 | Major | Hosted entry and unconditional alert | Fixed: no entry, tested; AC81, AC118 |
| 10 | Major | No-AI proof passes while everything loads | Fixed: T3.7 walks the set, checks `sys.modules` |
| 11 | Major | Settings check cannot depend on queues | Fixed: `SCHEDULER_ROLE`, T3.2 |
| 12 | Major | Three outbound actions have no process | Fixed: as Architect 11; `_VERIFIED_ADMINS` |
| 13 | Major | Permission tasks incomplete; note gate wrong | Fixed: T3.5; route gate |
| 14 | Major | Required behaviour with no task | Fixed: T1.8, T2.3, T2.11, T3.10, T3.11, T8.5 |
| 15 | Major | Sync machinery the spec rejected | Fixed: all removed; two queues |
| 16 | Major | AC72 not met by plain keys | Fixed: composite keys; T1.2, T4.2, T6.2 |
| 17 | Major | AC37 assigned before the job exists | Fixed: T4.5; step 5 of *The job* |
| 18 | Minor | Dots allowed in service names | Fixed: `strict_name()`; T4.1 |
| 19 | Minor | JWT in the wrong package | **Rejected** |
| 20 | Minor | Differences table incomplete | Fixed: four rows added |
| 21 | Minor | Moved issue always ends `gone` | Fixed: 301 as a signal; T4.8 copies a real response |
| 22 | Minor | Two tasks name no real test | Fixed: T3.3's shell test; T3.7's allowlist |

**UX**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | Joined page has no case actions | Fixed: state table |
| 2 | Blocker | Core wording contradicts the spec | Fixed: wording, four filter values, zero results |
| 3 | Blocker | Setup screens do not exist | Fixed: setup tables; AC124 |
| 4 | Major | State table omits common states | Fixed: full table; AC133 |
| 5 | Major | Nothing says PagerDuty is not told | Fixed: FR48, AC98 |
| 6 | Major | Resolve closes an issue with one tap | Fixed: FR6, FR46, AC92, AC66 |
| 7 | Major | Layout cannot meet tab order | Fixed: D86; container query |
| 8 | Major | Behaviours with no task | Fixed: T1.12, T2.10, T2.11, T3.10, T4.7 |
| 9 | Major | A note removes urgency | Fixed: D70, AC99 |
| 10 | Major | API fields cannot produce the sentences | Fixed: fields; `email_outcome` |
| 11 | Major | Success lines state unknown things | Fixed: *Words after an action* |
| 12 | Major | Cross-organization filters on a one-project screen | Fixed: *The feed*; AC109 |
| 13 | Major | Case row not designed; no phone rule for feed | Fixed: case row; FR46; AC108 |
| 14 | Major | Phone order loses signal actions | Fixed: AC66; jump link |
| 15 | Major | Enter sends a note | Fixed: FR14, AC104 |
| 16 | Major | Live refresh undefined | Fixed: FR45, AC105 |
| 17 | Major | Empty timeline has no words | Fixed: FR18, AC106 |
| 18 | Major | Jira webhook secret typed | Fixed: show-once state |
| 19 | Minor | Times have no date | Fixed: FR50, AC107 |
| 20 | Minor | Filter links do not round-trip | Fixed: T2.4, AC128, D85 |
| 21 | Minor | Long lists disagree; Reassign unspecified | Fixed: FR17, AC132 |

**Sales**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | One-line story false | Fixed: story reworded; limits row; warning recipients (D88) |
| 2 | Blocker | Design contradicts the safety claim | Fixed: as Developer 6 |
| 3 | Major | T8.6 does not exist; real evidence thin | Fixed: T8.6, T4.8, AC130, AC131 |
| 4 | Major | Lead claim holds only after a click | Fixed: May-say, buyer table, T8.2 on `vision.md:244` |
| 5 | Major | `Fixes #412` breaks "if it comes back" | Fixed: limits row, FR13 button, emails, T4.8; see also D73 |
| 6 | Major | Goal overclaims | Fixed: Goal; Do-not-say; `strict_name()` |
| 7 | Major | Packaging gap undecided | Fixed: founder decision 3; step H; Do-not-say |
| 8 | Major | History short, no export or answer | Fixed: limits table, Do-not-say, buyer table. **Part rejected:** export |
| 9 | Major | No way to demo or trial | Fixed: stated limit; T8.1 demo script. **Part rejected:** practice finding |
| 10 | Major | Public line uses the wrong word | Fixed by withdrawing it (Marketing 3) |
| 11 | Major | Limit of one case per service unstated | Fixed: limits row; FR12 names the signal; conversations |
| 12 | Major | PagerDuty line hides the cost | Fixed: May-say, UR1, buyer table, FR48 |
| 13 | Major | Competitive argument overstated | Fixed: research; difference row; foundation handed over |
| 14 | Major | Rival facts contradict the research | Fixed: page read and row corrected |
| 15 | Minor | Literal statements inaccurate | Fixed: *What leaves*; D75; AC63 words |
| 16 | Minor | Atlassian timing disagrees | Fixed: T0.1; Questions row 4 |

**Marketing**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Major | Story does not set nexovue apart | Fixed: row reworded; combination row, unchecked until T8.6 |
| 2 | Major | Story leaves out its conditions | Fixed: "What that does not cover" |
| 3 | Major | Public line promises what is refused | Fixed: D58 |
| 4 | Major | T8.6 missing; no launch copy | Fixed: T8.6 outputs a launch note |
| 5 | Major | Rival facts stale, all favouring nexovue | Fixed: research rows and lesson |
| 6 | Major | "Real gap" unproven | Fixed: research item 1; new rows, tiers marked unconfirmed |
| 7 | Major | No GitHub comparison | Fixed: research table, marked incomplete; T8.6 finishes it |
| 8 | Major | Naming ignores the market | Fixed: D58; research rows |
| 9 | Major | Jira gate inconsistent | Fixed: T0.1; T8.1 conditional; AC131 |
| 10 | Major | "Included" hides the retention limit | Fixed: May-say, Do-not-say, status |
| 11 | Minor | Banned seat-price line unrecorded | Fixed: difference row; research; foundation handed over |
| 12 | Minor | Return story narrower than worded | Fixed: UR5, D8, May-say |
| 13 | Minor | D4's SigNoz wording | Fixed: D4, spec row; T8.6 |

**Customer**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | Normal on-call act makes a 90-day case | Fixed: D71, FR49, FR52, AC90, AC91. **Part rejected:** multi-select |
| 2 | Blocker | Design builds a different join and idle rule | Fixed: as Developer 6 |
| 3 | Blocker | Plan not updated | Fixed: as Developer 1 |
| 4 | Major | A case can trap unrelated outages | Fixed: D72, AC92. **Part rejected:** "Not part of this case" |
| 5 | Major | Most first-week pages cannot become cases | Fixed: step I, Do-not-say, T8.1. **Part rejected:** building it now |
| 6 | Major | Closing the issue ends the case while measured | Fixed: D73, AC93, AC94. The reference-from-Create point is answered by the hold, so not added |
| 7 | Major | One-line story untrue for a GitHub developer | Fixed: story, D75, D88, AC97 |
| 8 | Major | "Asha is on it" | Fixed: D76, AC100 |
| 9 | Major | Handover reaches only email | Fixed: strict name in the email. **Part rejected:** channel line |
| 10 | Major | Issue too thin | Fixed: FR51, the sentence, Open to assign. **Part rejected:** endpoint option; link to the comment box |
| 11 | Major | Issue-text rules weaker than the spec | Fixed: `strict_name()` |
| 12 | Major | Support staff can read notes | Fixed: design matches D62; `is_real_person` |
| 13 | Major | Joined page removes the promise | Fixed: as UX 1 |
| 14 | Major | Hosted gets no trackers, decided by authors | Fixed: founder decision 3 |
| 15 | Major | Jira contradicts D66 and D57 | Fixed: confirmed transitions; T7.2, T7.5; AC125 |
| 16 | Minor | Cost rests on ETags | Fixed: removed; every read charged |
| 17 | Minor | Jira exposure warned once | Fixed: preview line, AC126; T7.0 |
| 18 | Minor | Egress proxy unaddressed | Fixed: T3.0; stated unsupported, D83 |
| 19 | Minor | No way to connect Linear | Fixed in part: webhook named as first candidate. **Part rejected:** clickable host list |

**Security**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | Plan has no task for most security criteria | Fixed: regenerated; T3.9, T8.5; test extended, not built |
| 2 | Blocker | Outbound rule weaker than D39 | Fixed: `strict_name()`, AC41, AC82 |
| 3 | Major | Telemetry pins names past retention | Fixed: as Developer 6 and 7 |
| 4 | Major | Support staff can read notes | Fixed: as Customer 12 |
| 5 | Major | Author made a viewer cannot delete | Fixed: as Architect 12 |
| 6 | Major | Schema lacks tenant refusal | Fixed: composite keys, including actor, author and project-scoped root |
| 7 | Major | GitHub secrets have no home | Fixed: `sealed_manifest_code`, hashed state; client secret discarded; AC35 |
| 8 | Major | Audit misses acts; no route | Fixed: audit table; credential route; AC77 reads the router |
| 9 | Major | Public check only on the first try | Fixed: every try; AC51 |
| 10 | Major | A non-member starts the deletion clock | Fixed: AC94. **Part rejected:** the case on the banner |
| 11 | Minor | Disconnect does not stop a running job | Fixed: check before each call; window stated; revoke tried |
| 12 | Minor | Webhook secret and flow disagree | Fixed: generated; no job; excludes old links; header from `kind` |
| 13 | Minor | Free text stored | Fixed: project key only; live names; `last_failure_field`; BR9 |
| 14 | Minor | `url` check cannot work | Fixed: D81 |
| 15 | Minor | Jira email has no home | Fixed: sealed with the token |
| 16 | Minor | Leaving wording has no task | Fixed: T2.10, AC122; the export decision recorded in `design.md` |
| 17 | Minor | Role's reach understated | Fixed: wording; T8.7, if F3's pattern exists |
| 18 | Minor | Note sentence false when F4 is on | Fixed in F22's words; F4 handed over |
| 19 | Minor | Token and connection boundaries untestable | Fixed: BR14, AC79, AC110 |
| 20 | Minor | Preview not guaranteed | Fixed: D82, AC83 |
| 21 | Minor | Autolink shapes missing | Fixed: AC41, T4.8; rule if any link |

**QA**

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Blocker | 35 ACs in no task | Fixed: all owned; AC4, AC64, AC65 sit in Steps 1 and 2 |
| 2 | Blocker | Detection-facing design contradicts ACs | Fixed: as Developer 6 and 7; detach; AC17 |
| 3 | Blocker | Connection setup untestable | Fixed: T3.9, by hand in a real browser |
| 4 | Blocker | Sync design describes dropped behaviour | Fixed: comparison rewritten; all eleven items |
| 5 | Major | Person-facing text contradicts ACs | Fixed: as UX 1 and 2; rank shown |
| 6 | Major | Real-service checks no task performs | Fixed: T4.8, T7.6, AC130; fixtures |
| 7 | Major | Deadlock; no criterion | Fixed: AC101, T2.1. The transaction's span is confirmed by reading `:616` |
| 8 | Major | AC23 and the flap guard | Fixed: as Architect 9 |
| 9 | Major | FR47 has no criterion | Fixed: AC120, T2.11 |
| 10 | Major | Requirements with no criterion | Fixed: AC104, AC105, AC111 to AC119, AC125 |
| 11 | Major | Screen criteria unprovable in jsdom | Fixed: D84; hand list; *Testing limits* |
| 12 | Major | AC44 has no harness | Fixed: *Test harness*; fault points in AC44 and AC87 |
| 13 | Major | Whole-system criteria on the wrong tasks | Fixed: T4.5, T8.3, T8.4, T8.5, `ci.yml` |
| 14 | Major | AC75 and AC77 rest on missing things | Fixed: route; audit table |
| 15 | Minor | Criteria leave out values | Fixed: 6 digits, `@` rule, exact outputs, 60 s, 10 lines; re-render rule |
| 16 | Minor | Founder's check cannot be done | Fixed: T1.0 |

### Rejected, and why

**Rejected whole (1):**

- **Developer 19, JWT in `nexovue_platform`.** Its reason was that F5 signs in the API. F5's design now says the API calls no GitHub host (`ai-solution/design.md:276`) and relies on the scheduler. A JWT dependency in a package the API imports would add a signing path nothing uses. D41 was changed the other way.

**Parts rejected, from findings otherwise fixed (9):**

| Finding | Part rejected | Why |
|---|---|---|
| Customer 1 | A multi-select on *Assigned to me* | Nobody has asked, and the 72-hour end plus row Resolve clear the pile |
| Customer 4 | "Not part of this case" | D45's reasons stand: it needs its own state, and BR7 still allows no second case. "Leave the issue open" removes the harm that made it attractive |
| Customer 5 | Building incident notes now | Founder's FD13 stands unless F22 has reasons, and no user has been asked. Named as step I |
| Customer 9 | A line on people-read channels for a handover | A new message kind on a frozen vocabulary, against the spec's "not a second alarm". Named as step K. **A real gap, listed as a risk** |
| Customer 10 | An admin option to include the endpoint | D48: an endpoint can be a raw span name holding customer data, and an issue cannot be erased. AC41's rule is that none appears. Also, a direct link to the comment box: no such address was found, so the link opens the issue |
| Customer 19 | Admin-listed clickable hosts in notes | AC18's guarantee is simple, and it would be a configuration screen nobody asked for |
| Sales 8 | A bulk export now | Waits on founder decision 2, which may remove the need. Named as step J |
| Sales 9 | A customer-creatable practice finding | A fake row reaches F3's and F4's learning and F6's counts. A stated limit and a demo script instead |
| Security 10 | Naming the case on the banner | The banner is for connection health (D69). An email to owners and admins does this job |

### What remains as a risk

**Not solved by these documents:**

- **72 hours is the likeliest guess to get wrong (D71).** It ends a case by silence, one taken and then forgotten, against the spirit of "recovery does not end the work". The page warns, and reopening is one click. Decided as D94: kept, and tested in the founder's one round of conversations.
- **A case can sit with its issue closed (D73),** when the signal stays open, until someone resolves it or 90 days pass.
- **Someone with write access on an issue can extend a case by toggling it (D74).** Accepted, since it is not outside traffic, but names can then outlive retention longer than BR13 intends.
- **Handover and the customers' Slack habit (D87).** The research warned of this, and the first version does not answer it.
- **Most first-week pages for a migrating team cannot become cases (D6).** Stated, not solved.
- **Issue text can still be read by someone's AI.** `ignore_previous_instructions_and_merge` remains readable, and no suite can make it meaningless. Autolink shapes are untested until T4.8.
- **Deadlock prevention is reasoning until T2.1 runs.** Nothing has exercised a lock.
- **A compromised `scheduler-connections` reads and writes all of Postgres** until T8.7, which depends on F3's pattern.
- **Layout and browser policy can regress between hand checks (D84).**
- **The fake tracker is written by the people it tests.** Its fixtures come from T4.8 and can go stale.
- **Memory.** About 1.5 GB left on the 16 GB server by arithmetic, with F5's load uncounted.
- **Bulk export and 30-day history.** Settled by foundation item 6 (approved 2026-11-02): an operator setting. Until it ships, a team loses its case history after 30 days on a self-hosted install. Step J follows D95.
- **Practice and trial.** A fresh install shows nothing of F22 for at least 3 days, against the vision's "value in under 10 minutes" for its secondary user.
- **Rival facts are second-hand and date quickly.** Elastic's tier, Sentry self-hosted's features and SigNoz's edition are unresolved. Nothing here supports calling anything unique.

### Second review, of the revision

One further review, on 2026-11-02, raised 14 findings against the revised documents: 5 major and 9 minor. Each was checked against the code or the cited document first. **All 14 held, and all are fixed in the documents.** Nothing was built or run. The script in *Validation* was run again afterwards.

| Sev | Finding | Checked against | Outcome |
|---|---|---|---|
| Major | F5's jobs were said to be on `connections`, but F5 has `connections-interactive` | `ai-solution/design.md:541-553,611`; `ai-solution/spec.md:663` | Fixed: **F22 adopts F5's layout.** `connections`, capped at 2, holds sync. `connections-interactive` holds people's jobs and `sweep_connections`. D51, D79, NFR1, AC34, AC50, T3.1, T3.2, the queue table and the handover. "Nothing to change" is gone: F5's "up from F22's 2" and its sweeps' wait are handed over |
| Major | Owners could not sign off when they ran | The seven named, and the plan read again for others | Fixed: owners moved, a dependency table added, T0.2 extended. **The reading found 16 more of the same kind:** AC4, AC35, AC41, AC45, AC59, AC66, AC67, AC68, AC75, AC90, AC93, AC99, AC105, AC108, AC114 and AC133, plus AC89 and AC124, which each had a Jira half and were split |
| Major | A reopen during a hold matched no useful row, and the confirmation was false on a closed issue | *The comparison*; FR6; FR16; AC93 | Fixed: row 4b, `CLEAR_HOLD_SQL`, D92, FR38's split row, FR6's words, AC92, AC93, AC94 narrowed |
| Major | The job's first read of `connections` could not bind `org_id`, and the webhook lookup had no exemption | `test_scheduler_postgres_tenancy_structure.py:12-14`; `test_postgres_tenancy_structure.py:32-41` | Fixed: `org_id` in kwargs, AC129, new AC139, T4.5, T5.1 |
| Major | AC48 could not be met beside D50 | D50; *The job* | Fixed: AC48 rewritten to the lease. D50 stands |
| Minor | T4.8 left out the webhook delivery, and a Step 4 task owned Jira's half | T4.8; T5.1; T7.6 | Fixed: new T5.2 owns AC130, new AC135 goes to T7.6 |
| Minor | Nothing stopped the role during a restore | `deploy/nexovue:601` | Fixed: T3.12, with the setting on and off. What `compose stop` does with an inactive profile was not checked, so the design does not rely on it |
| Minor | BR5, BR10, BR11 and D2 contradicted later decisions | The spec's own rows | Fixed: reworded, with the comment limit per link and per act |
| Minor | One `tell_pending` column could lose an email | *The record*; AC94 | Fixed: table `finding_emails_owed` (D90) |
| Minor | The 5-minute email limit had no mechanism | FR5; AC11; T1.9 | Fixed: a query over `assigned` events under the root's lock (D91) |
| Minor | The design did not declare F5's second App | `phase-2-foundation.md:8,26,343` | Fixed: a row in the differences table |
| Minor | Citations into F5 and F3 pointed at the wrong lines | Each line read | Fixed. **The reading found three more:** F3's account pattern (`:647`), F5's memory line (`:749`) and F4's provider line (`:591`), and F5's registry task is T3.10, not T3.8 |
| Minor | Plan names broke its own conventions | `AppShell.tsx` has no test; `docs/product/setup/` has no `trackers/`; `test_cross_service_contracts.py:265` | Fixed: both marked new. `record_finding_event()` is now named as `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, in `cases.py`, and handed over |
| Minor | AC81 pointed at a sentence that was not there, and Python tests could not show a screen | Spec; T3.5 | Fixed: the sentence quoted, the screen at T3.10, the route as new AC136 |

**Decisions taken here, recommended by the authors, founder may overrule:** the queue layout (D79), the owed-email table (D90), the email limit's store (D91), and that a reopen during a hold does not reset the idle clock (D92). No new founder decision arose.

**New acceptance criteria, all from splits or the tenant fix:** AC134 to AC139. Numbers were not reused. Criteria that say "if Step 7 goes ahead" do not block calling the GitHub half done, which remains founder decision 1.

**New risk:** F5's two sweeps can wait up to 270 seconds behind a hung sync. It is a guess that this does no harm, and F5 must accept it or move them.

### Still disagreeing

**Other features' documents still disagree,** because this run did not edit them: F5's "up from F22's 2" (`ai-solution/design.md:553`), its sweeps sharing the capped queue, and `record_finding_event()` in F5, F3 and the foundation; F5's page order (`ai-solution/design.md:56-67`), and its `jobs/registry.py` task; F3 on the third role value and T2.9; F4 on the note sentence; the personal-data wording; the foundation at `:495,597`; and the competitors research at `:133,344`. Until each is changed, a reader of those documents gets the old answer. `design.md`, *What other features must change*, is the list to work from.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- `is_real_person` is built from F3's `Reader.is_person`, and F4's T7.2 adds the ticket's row to AC69 (design, spec).
- The wireframe carries F5's D54: F3's section leads the right column, its first row is the last deploy, with a jump link to the fix, and services precede linked alerts as the code has them (design).
- Notes can reach the AI provider once chat is on: FR14's sentence and a row in *What leaves this server* (spec), as F4 asked.
- `nexovue_connections` gains F5's tables once F5 lands (design).
- The savepoint cites F22's own statement, since F28 rewrites the block it copied (design).
- Founder item 2's recommendation now matches F19's item 2, and foundation item 6 holds it (spec).
- Foundation section 12's seat comparison and section 6's webhook, App and token rows were changed to F22's.


## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in this feature's documents was read again. Those not truly the founder's were decided and recorded in `spec.md`, *Decisions taken*. All stay Proposed, and the founder may overrule any of them.

- **Founder item 2, self-hosted retention:** settled by foundation item 6, removed from the open lists with a pointer. The spec's wording now says "30 days by default" and points to the operator setting.
- **D93, the test GitHub account for Step 3** (was plan question 5): a separate free test organization with one private repository, not `nexovue-tech`. Creating it still needs the founder's approval at Step 3.
- **D94, 72 hours for an untouched case** (was "the founder should look"): kept as a default, tested in the foundation's one round of conversations. It is reversible and touches no money, law or promise.
- **D95, bulk export (step J):** waits. The approved retention setting is the real fix; step J is built only if the default stays below 395 days after the test bed measures disk use.
- **D96, F22's questions for the founder's one round of conversations:** six questions in one list, each tied to the step or default it changes.
- **D97, ADR numbers** (was plan question 6): take the next free number at merge. ADR-024 is already named by another feature.
- **Plan question 4** (GitHub-only if Atlassian says no) is folded into founder item 1.
- **Design's "the founder should have these corrected" rows** (foundation section 8 and 10 lines, competitors research `:133,344`) are editorial, not founder decisions: corrected in the next cross-feature pass.
- **Founder item 1, Jira:** still the founder's. Fresh research changed the recommendation: Atlassian's staff now say the token rule covers service accounts and private apps, so Step 7 is built only on a written yes (`research.md`, *Founder decisions needed*).
- **Founder item 3, hosted trackers:** still the founder's, unchanged.

### Review, 2026-11-02

A skeptical review of the answers above, checked against the code and the sources. What changed:

- **Founder item 1, Jira: a better route was missing.** Atlassian lets an org admin give a service account an OAuth 2.0 credential (client id and secret, client credentials, hour-long tokens; [Atlassian's page](https://support.atlassian.com/user-management/docs/create-oauth-2-0-credential-for-service-accounts/)). It is not an API token, the thing Atlassian's rule names. Added as option E in `research.md`, as D98 (build on it if allowed), in FR29, in `design.md`'s Jira section, and in T0.1, T7.0 and T7.1. T0.1's question now asks about it first. Still the founder's: whether to build Jira without a clear yes is vendor and legal exposure, and the fallback changes the *Done when*.
- **Wrong line cited.** The *Done when* naming Jira is `phase-2-plan.md:68`, not `:62`. Fixed in `spec.md` and `plan.md`.
- **D93:** GitHub's registration page names no plan limit (100 Apps per account), so "not checked" became a cited fact; T3.0 still confirms it.
- **D97:** `ai-solution/design.md:931` does not name ADR-024 for itself; it says the next free number is ADR-024 and numbers are given on acceptance. The reason now says that. The decision stands.
- **Held up:** item 2 (retention matches `0005_plans_table.py:34-37` and foundation item 6), item 3 (foundation section 10 says tracker connections are absent on hosted until it is answered), D94, D95, D96, and the editorial rows. Plan question 4 stays folded into item 1.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** Jira (item 17): send T0.1 now, build Step 7 only on a written yes, and with a no or 4 weeks of silence call F22 done with GitHub only and amend `phase-2-plan.md`'s *Done when*. Tracker sync on hosted (item 19): a launch condition of hosted paid plans, step H. The free GitHub test organization, plus one spare free personal account, shared with F5 (item 23, D93).
- **Changed, A: no rented test server.** T5.2's real webhook delivery and AC130 wait for the Oracle deployment. The 16 GB memory figure stays arithmetic until then; T4.9 reads one container's memory on the Mac.
- **Changed, B: no paid model calls.** F22 calls no model, so nothing changes here.
- **Open decisions:** none for the founder. Step 7 waits on Atlassian.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 3 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.
