# Status: Case management (F22)

## Lifecycle status

**In development.** The founder approved the specification, design and plan on 2026-11-02; F6 merged the same day (PR #24). Plan **Steps 1 and 2 are on `feature/phase-2`** (Step 2 without T2.9, which waits on F3). **Step 3 (2026-11-08)** is on `feature/phase-2`, built against a simulated GitHub (foundation item 28), with T3.0 not yet run; its four open decisions were answered by the founder on 2026-11-09. **Step 4 is on `feature/phase-2` (2026-11-09)**, against the simulated GitHub, without T4.8 (real GitHub, the founder's after Phase 2) and T4.9 (memory, not measured). **Step 5 is on `feature/phase-2` (2026-11-09):** the webhook doorbell (T5.1), against the simulated GitHub; T5.2 (a real delivery) is the founder's after Phase 2. **Step 6 is on `feature/phase-2` (2026-11-11):** reference links, promotion, the service mapping and public repositories (T6.1 to T6.3), against the simulated GitHub; migrations 0068 and 0069; no new ADR. **Step 8 built 2026-11-22, awaiting commit** (T8.1 to T8.9, and T2.9, which T8.8 found unwritten; migration 0085, the tracker role's own Postgres account; the Design QA list is written and **not run**, it is the founder's). Step 7 (Jira) is out of Phase 2 (foundation item 32).


## Current task

**Step 8 built 2026-11-22, awaiting commit** (T8.1 to T8.9, with T2.9). Customer
documents, the AI-off case test with a real worker and its CI job, the timeline measured at
D65's worst shape, the connections end-to-end test with real workers and a credential search,
the claims check dated 2026-11-22, and the tracker role's own Postgres account
`nexovue_connections` (migration 0085). Five open decisions (20 to 24), none blocking the
commit. One test for each timeline event kind and the audit coverage test (T8.8); the first
found that `explained` was never written, and T2.9 now writes it. The Design QA hand list for
AC66, AC67 and AC108 is written below and **has not been run: it is the founder's**. Every
real-GitHub check stays the founder's after Phase 2 (foundation item 28); Jira is out of Phase 2
(foundation item 32), so no `jira.md` was written.


## Step 8 built, 2026-11-22

Plan tasks T8.1 to T8.7, each with its note in `plan.md`. Built and tested against the
simulated GitHub only. Worktree `wt/f22s8` on `feature/phase-2` at 4ad5d47; own stores
(`f22s8-postgres`, `f22s8-clickhouse`), removed at the end.

### What exists now

- **T8.1.** `docs/product/setup/trackers/github.md`: what a connection does and does not,
  what leaves the server (with the sample issue), that an issue notifies nobody until someone is
  assigned, that cases cover anomaly findings only (with D58's one search line), the proxy limit,
  who must act in GitHub, the operator's note on `nexovue_connections`, disconnecting, what stays
  in GitHub after a case or organization is deleted, that a deleted note lives in backups for a
  while, and that it is unchecked against real GitHub. The console's docs gain a **Trackers**
  group (`features/docs/pages.ts`). `index.md` links it. The demo script is
  `docs/features/case-management/demo.md` (take, note, recover, return with the real detection
  job; create and close an issue needs real GitHub, the founder's). **Not walked through by
  hand.** No `jira.md`.
- **T8.2.** `feature-catalog.md` (row, F22 section, glossary) and `vision.md` (table row, the
  chain) amended to ADR-022's reading; `vision.md`'s "Our value = auto-creating the case" is
  recorded as superseded. `FindingsScreen.tsx` holds no stale wording, so it is unchanged.
- **T8.3 (AC56).** `apps/api/tests/test_ai_off_cases_live.py`: through the routes, a member takes
  a case, writes a note, the signal recovers and the case stays, it is handed over, resolved and
  reopened, and a **real `scheduler-light` worker process** then ends it after 90 idle days as
  `no_activity`. It asserts no AI key in the API's settings or the worker's environment, **no
  session signed in as `nexovue_insight`** while it runs (what a running `scheduler-insight`
  would be), no job on an AI queue, and no `ai_connections` or `ai_runs` row. CI gains a
  separate **`ai-off`** job: Postgres alone, `setup` with no AI password (so no AI account),
  a step refusing any of nexovue's AI variables, a `scheduler-insight` container or an existing
  AI account, then the suite, selected with `-m ai_off`. The `ai_off` marker keeps it out of
  every other run: `check`'s stack runs `scheduler-insight`, so the suite failed there (seen in
  the first full gate, 2026-11-22). `insight-fence` is untouched. **The job has not run
  on GitHub**; its steps were rehearsed here on a throwaway Postgres (setup made neither account;
  the guard found nothing; the suite passed). Making it a required check is a GitHub settings
  change, the founder's.
- **T8.4.** `tools/detection-seed/timeline_seed.py seed|measure|remove` (D65's worst shape:
  540 joined findings over 90 days, 500 notes of 4,000 incompressible characters, 2,670 case
  events beside 150,000 others). Measured below.
- **T8.5 (AC36, AC50).** `apps/scheduler/tests/test_connections_end_to_end_live.py`, with
  `e2e_worker.py` and `e2e_github_preload.py`. Two **real worker processes**, started as their
  containers start them: `scheduler-connections` signed in as `nexovue_connections` (its
  children reach the simulated GitHub through a forkserver preload that changes only GitHub's
  address; no production setting moves it), and a `full` worker on `deliveries,detection`.
  AC36: setup, GitHub's return with a code, the conversion job, installation, refresh, the
  test, a person's **Create an issue** synced by the worker, and a disconnect; then every
  worker log line (at `debug`), every API log line, every job row seen while they ran, the
  results, timeline events, audit rows and the connections route's answers are searched for
  the App key, webhook and client secrets, manifest code, every installation token and App
  sign-in sent to GitHub, the connection key, the account's password and the sealed columns
  (as text, base64 and hex). AC50: GitHub stops answering (token mints outlast the client's
  10 s), the connections worker holds those jobs, and an alert delivery (console mail) and a
  detection judge job on the other worker each finish inside their soft limits while it still
  hangs. **Placed in `apps/scheduler/tests/`**, not the plan's `apps/api/tests/`, because the
  simulated GitHub and the tenant fixtures live there; the API half calls the routes' service
  functions in-process, not HTTP.
- **T8.6.** The claims table below.
- **T8.7.** The tracker role's own Postgres account, by F3's pattern:
  - **Migration 0085** (down_revision 0084; reversible: drops what it adds): seven
    `SECURITY DEFINER` functions with `search_path` fixed — claim, retry and finish limited to
    `connections` and `connections-interactive`; an enqueue (`ENQUEUE_UNLESS_WAITING`) that
    **raises** for any other queue; the connection test's result store with its kind written
    in; and F5's two `brief_paths` statements. `EXECUTE` taken from `PUBLIC`. Two permissive
    row-security policies admit the account to `finding_fixes` and its packs (the AI account's
    `PUBLIC` policies would otherwise have hidden every non-`proposing` row from it).
  - **`nexovue_api/connections_account.py`**: the grants, column lists everywhere a table is
    shared, `DELETE` only on F5's three short-lived tables; connection limit 20, timeouts 10 s
    and 30 s (guesses, the AI account's). **`nexovue_api/role_accounts.py`** holds the
    machinery both accounts share, moved out of `insight_account.py` unchanged in behaviour
    (F3's suites pass; two of its test references now name the shared module).
  - **Code:** `jobs/queue.py`'s `CONNECTIONS_STATEMENTS` for the role, `CONNECTIONS_ENQUEUE`
    for the two sweeps, `results.store_connection_test`, and `source_pack.py`'s brief statements
    through the functions.
  - **Setup and deployment:** `setup` (and `nexovue setup grants`) provisions it after the AI
    account; Compose gives `setup` `POSTGRES_CONNECTIONS_PASSWORD` (`:-`) and
    `scheduler-connections` a DSN as `nexovue_connections` (`:?`, so a missing password is
    refused by name); `deploy/nexovue` generates the password at install and adds a missing one
    at upgrade, like the AI account's; the dev stack and `just migrate-pg` use
    `nexovue_connections_dev`. No secret in the repository.
  - **How the grants are proved least and enough:** `test_connections_grants_live.py` reads the
    catalog back against a hand-written list (every table and column, `PUBLIC` included), and
    proves no right on `jobs`, `job_results`, alerts, check secrets, notes or sign-ins, no
    `LOCK` on the queue or the case tables, no advisory lock or temporary table, a plain login
    with its limit and timeouts, and the policies' exact text. **Enough:** every connections
    suite now runs its jobs as the account (`connections_engine`): the sync (61), GitHub (19)
    and F5's eleven connections suites (152) pass, and the end-to-end test runs the real worker
    as it. `test_connections_account_live.py` holds the functions to the shared statements and
    proves they never reach another queue.
  - **Found after merge (2026-11-22):** F5 Step 9's `fix_sweep_status` (migration 0086), which
    the sweep writes on this role, had no grant, and its suite ran the write as the install
    account, so every pass was refused its record. The account now holds
    `SELECT (sweep, finished_at)`, `INSERT (sweep, finished_at)`, `UPDATE (finished_at)` there,
    and the suite runs as it (`docs/changes/fix-sweep-records-as-its-account/`).

- **T8.8 (AC70, AC77), and T2.9 found by it.**
  - **`apps/api/tests/test_finding_event_kinds_live.py`:** 27 tests, one per kind in FR16, each
    named `test_<kind>_…`; a first test holds the list against the frozen vocabulary (FR16's 27
    plus F5's two, `fix_proposed` and `fix_pull_request_opened`, whose tests F5's suites hold). Each makes the event happen by the code that
    writes it, then shows the event, its actor and its fields. A person's acts go through the
    API's routes and are read back from `GET /findings/{id}/timeline` as a member sees it
    (kind, `actor_kind`, `actor_id`, `actor_name`, `detail`, a note's words). The job's five
    kinds go through `detection/events.py`. The tracker's six (`issue_created`, `issue_closed`,
    `issue_reopened`, `issue_gone`, `issue_moved`, `tracker_write_failed`) go through the real
    `sync_connection` with a scripted tracker where GitHub would be. `explained` goes through
    F3's real `gather`. The first half runs in one rolled-back transaction, the second commits
    and removes its organization. **`new_normal` is the one kind not driven through its route:**
    the route needs ClickHouse readings (`test_findings_actions_live.py` runs it), so the test
    reads that the route calls the shared operation and runs the operation.
  - **`apps/api/tests/test_case_audit_coverage.py`:** reads `findings/router.py`,
    `connections/router.py` and `connections/test_router.py`, finds every mutating handler, and
    holds each to `AUDITED` (the entries its work must reach, found through the functions it
    calls inside `findings/` and `connections/`) or `UNAUDITED` (with `design.md`'s reason, and
    the test fails if such a route starts writing an F22 entry). A route in neither table fails,
    as does a table entry for a route that is gone, and a check that the scan finds the same
    routes the application mounts. Two more rules, for the API's and the scheduler's writers
    alike: every function that resolves, reopens or starts a case (`_start_case`,
    `_resolve_root`, `reopen`, `end_case`, the sync's `_reopen`) writes its audit row, and
    `audit/service.py` never commits or begins a transaction, so an entry joins its act.
    **What it cannot tell:** that an entry is the right one, or written beside the act it
    records; the live suites read the rows back for that. The scan reads names, so two functions
    with one name are both followed, which can only over-report what a route reaches.
  - **T2.9, the gap.** `explained` was in the frozen vocabulary and nothing wrote it: T2.9 had
    waited on F3 (founder, 2026-11-07) and was not revisited when F3's `gather.py` landed.
    `evidence/explained.write` now takes the version the follow-up rests on and writes one
    `explained` event (system, `explanation_version`) when it wrote any follow-up row, in that
    transaction; both `gather` call sites pass it, so the settled read of an unchanged version
    still writes its one event. No migration (the kind and its field are frozen already), no
    new grant (`scheduler-light` writes `finding_events` as the install's account). Open
    decision 23.
  - **Two lines of `design.md`'s *Audit* table were wrong** and are corrected: a note on a
    finding with no case starts one, and is audited as `case.started`; `connection.place_changed`
    is not defined, since the route changes only the public allowance (open decision 24).
- **T8.9.** This section, the plan's *Outcome*, and the Design QA hand list below. **The list
  has not been run.** AC66, AC67 and AC108 are not claimed: their DOM-order and focus halves
  are in the suite (`FindingScreen.test.ts`, `ConnectionsScreen.test.ts`), their layout halves
  are the founder's.

### The claims check (T8.6), read 2026-11-22

Rivals' pages were read through this session's allowed hosts. **Unreachable, so not re-read:**
`www.elastic.co`, `incident.io`, `docs.rootly.com`, `support.pagerduty.com` (the proxy refused
the connection; nothing returned) and `www.observeinc.com` (redirects to `www.snowflake.com`,
refused). `docs.datadoghq.com` answered, redirecting Case Management to
`/incident_response/work_management/`, but its text needs scripts and could not be read.

| Rival page (read today) | What it says now |
|---|---|
| SigNoz, Jira channel | Tagged "SigNoz Cloud" and "Self-Host". "An alert firing creates a Jira issue." "Send resolved alerts: on by default … transitions the issue to a 'done' status". Reopen window default 3d. Self-hosted Jira supported from SigNoz v0.150.0 |
| SigNoz, pricing | Community edition's alert channels listed as "Slack, Pagerduty, OpsGenie & webhooks", MS Teams separately; **no Jira row, so the edition is still not confirmed** |
| Sentry, issue states | "You can manually mark an issue as Resolved", or by a commit or release; "Regressed: A resolved issue that's come up again". No resolving when errors stop |
| Sentry, Jira | Manual issue management on Team, Business, Enterprise; automatic on Business and Enterprise; issue sync of comments, assignees and status both ways on Team and up. Self-hosted Sentry: installation URL "resolvable on the internet". Pricing: Team $26/mo, Business $80/mo |
| OpenObserve, incidents | "available in OpenObserve Enterprise and Cloud editions"; Open, Acknowledged, Resolved, reopen; auto-resolve after 3 days by default; no mention of Jira or GitHub |
| Grafana IRM, Jira | Creates a Jira issue on declare and moves its status as the incident changes; Jira Cloud and classic API tokens only, "doesn't support self-managed Jira" |
| New Relic, notification integrations | Jira Cloud: "automatically create, update, and close Jira issues", two-way sync with an admin API key; no on-premises Jira |
| Dynatrace, Jira connector | A workflow action (page dated 13 Feb 2025); not read further |
| Coroot, incidents | An incidents list, ongoing and resolved; no tracker named |
| Honeycomb triggers, HyperDX, ClickStack | No case or tracker feature found on the pages read |

| Line (spec, *May say* / *Do not say*) | Built product | Rivals | Result |
|---|---|---|---|
| "A finding becomes someone's job with one click and no form." | Take is `PATCH /case` with no field required (`test_findings_cases_live.py`, `test_ai_off_cases_live.py`) | — | **Passed** |
| "Once someone has taken a finding, recovery does not end the work, and the page says so." | `test_ai_off_cases_live.py` (closed signal, investigating case); the page sentence's tests | Ordinary elsewhere (Sentry by hand; OpenObserve by hand or after 3 days) — not a differentiator | **Passed**, not to lead with |
| The GitHub line (one issue by a click with a preview; closing ends the case once recovered; status only; public refused) | Built and tested against the simulated GitHub only | Sentry creates and syncs more, both ways, Team and up | **Not passed:** T3.0 and T4.8 against real GitHub are the founder's after Phase 2 |
| "Case tracking is included … no per-person charge … keep PagerDuty for paging", with the 30-day retention limit beside it | No plan gate on any case route; retention default as stated | PagerDuty's page unreachable; not needed for the line | **Passed** for self-hosted, with the retention sentence |
| "Track the work on a detected problem …" and the search line | On the setup page verbatim | — | **Passed** |
| "A problem that returns while a case is active joins it. After a resolve, the new finding names the earlier case." | Step 2's join and earlier-case tests | Sentry regresses the same issue; SigNoz reopens the same Jira issue within 3d | **Passed** |
| The combination row (own server, statistical detection, one click, evidence, no per-person charge, AI off) | Built | Elastic Cases unreadable today; SigNoz's edition unconfirmed | **Not passed:** do not call it unique |
| Do not say "two-way sync" without status only; "your data never leaves your server"; "Jira support"; hosted sync; "works from behind a firewall"; "cases for every alert"; "incident tracking"; "keeps your incident history"; "the only" | The setup page says status only, lists what leaves, names GitHub only and self-hosted only, says a proxy is unsupported, cases cover anomaly findings only, and states deletion | — | **Held** in everything Step 8 wrote. "Works from behind a firewall" stays unsaid until T3.0 |

**Launch note, from passed lines only (draft, not published):** "Cases: a finding becomes
someone's job with one click and no form. Recovery does not end the work, and the page says
so. A problem that returns while a case is active joins it; after a resolve, the new finding
names the earlier case. Case tracking is included, with no per-person charge from nexovue; on
a self-hosted install a resolved case and its notes are deleted 30 days later by default, until
the operator raises retention. Self-hosted. GitHub issue sync is not in this note: it has not
yet run against real GitHub." **Changelog entry:** "Added: cases on findings — take, note, hand
over, resolve, reopen, a timeline, and the case feed. Self-hosted. GitHub issue sync ships once
its first real-GitHub run passes."

### Measured (T8.4), indicative only

Shared 4-CPU development VM under other builders' load (load average 9 to 14), Postgres 18 in
a container; one reading set, not a benchmark.

- **Timeline, first page (100 rows, notes joined):** medians 19.8, 23.1, 37.3 ms over three runs
  of 50 reads; 95th percentiles 44.6, 53.3, **264.9** ms; maximum 496 ms. Server-side
  `EXPLAIN ANALYZE`: 12 to 19 ms execution.
- **Timeline, earliest page:** medians 4.7, 5.1, 6.2 ms; 95th percentiles 6.4 to 12.5 ms.
- **Against D65's 200 ms guess:** the median is well under; one run's tail passed it, on a
  loaded machine, not attributed further. **The plan is a parallel sequential scan of all of
  `finding_events`** (the `OR … IN` filter does not use the `(org_id, finding_id, at)` index at
  this size), so the read grows with every organization's events. Open decision 20.
- **Memory (against T4.9's guess, 384 MB):** the real connections worker, its forkserver and
  three children after one job each, about 250 MB proportional (PSS) and 99/15/77/89/93/93 MB
  resident, read from `/proc` on this VM, not the container. T4.9 on the real server stays
  owed.

### Tests seen failing first (how)

- Grants: `test_connections_grants_live.py` before migration 0085 (provisioning refused: the
  functions did not exist); after, F5's upsert refused `EXCLUDED.bytes` and
  `EXCLUDED.github_login` without `SELECT` on them, which added those two columns.
- Queue parity: `test_connections_account_live.py` before the scheduler code (12 failed, no
  `CONNECTIONS_STATEMENTS`).
- The sync suite's sweep tests as the account with the old code: "permission denied for table
  jobs".
- Compose and installer: 16 failed before the wiring.
- Setup: `test_setup_grants_prepares_both_role_accounts` failed before `setup` called it.
- Docs: `pages.test.ts` failed before the Trackers group and page.
- Timeline seed: its test failed to import before the script.
- End-to-end: with the old scheduler code the worker could not claim; and a deliberate leak
  (the webhook secret added to a log line, reverted) made the search fail with "the webhook
  secret in worker logs".
- AI-off: a session held open as `nexovue_insight` made it fail (`ai_sessions: 1`).
- **Event kinds (T8.8).** `explained`: the test failed before the fix with "the follow-up was
  owed and no explained event was written" (the first run: `ValueError: not enough values to
  unpack`), the one real gap. **Every kind, one at a time:** a scratch edit of
  `RECORD_FINDING_EVENT_SQL` in `nexovue_platform/cases.py` (the one writer all of them use)
  made it insert nothing for the kind named by an environment variable `MUTE_KIND` and still
  return an id, so the callers ran on; each of the 27 kinds' own test then failed on "no event of this
  kind" (all 27 were run, one at a time; the log is in the scratchpad, not the repository).
  Restored byte for byte (sha256 compared) before any suite ran. One half-way run of the same
  edit, which returned no id, made the API's kinds fail by 500 instead; that is why the edit
  was changed, and it is not counted.
- **Audit coverage (T8.8).** Four scratch edits, each restored byte for byte (sha256 compared):
  (1) the `link.removed` audit call deleted from `links.remove`: `an_audited_route_reaches_each_entry_it_must_write[remove_issue]`
  failed; (2) a new `POST` route added to `connections/router.py` with no entry:
  `every_write_route_is_audited_or_says_why_not` failed; (3) `_audit(... CASE_REOPENED ...)`
  deleted from `cases.reopen`: `[change_case]` and the function-level rule failed; (4) the
  `record_audit` call deleted from the scheduler's `case_endings.end_case`: the function-level
  rule failed.

### Validation run for T8.8 and T8.9

`uv run --no-sync mypy .`: no issues in 1,332 files. `ruff check .` and `ruff format --check .`:
clean (1,757 files). The combined run of F22's API and scheduler suites (the two new files,
authorization coverage, audit, findings, cases, links, AI-off, connections, grants, migration
rules, tenancy structure, the transition parity, F5's audit suite, the explained, gather,
sweep, detection-events, sync and account suites, and the platform event test), against
Postgres and ClickHouse of its own: **1,015 passed**, 5 SQLAlchemy warnings. Not run: `just check`, a browser, any real-GitHub step.

### Not built in Step 8

**No F22 Playwright spec exists**, so none was run; the browser job is *Not in this plan*,
step L. Design QA is by hand, and is the founder's (below).

### Validation run (read, not inferred)

On the worktree's own stores (Postgres 18 with pgvector, ClickHouse 26.3; migrated to 0085),
2026-11-22, counts read from the output:

- **API side**, 32 files (F22's API suites, the grants and AI-account suites, setup, Compose
  and installer contracts, both tenancy structure tests, authorization coverage, the tenant
  chokepoint and read-surface structure tests, migration rules, the AI-off suite, AI settings):
  **3,869 passed**; `test_setup_live.py` **not run** (it needs the S3 store, which was not
  started on a nearly full shared disk).
- **Scheduler side**, 25 files (F22's sync, GitHub, cases sweep and detection-link suites, the
  new account and end-to-end suites, F5's connections suites, the queue and F3/F4/F5 account
  suites): **408 passed**.
- **Offline:** platform case, issue-text and address tests, the detection-seed tools, the
  seam, room, role and settings tests, the fake's recordings: **348 passed**. Web:
  `features/docs` and the setup-docs plugin, **11 passed** (Vitest).
- `uv run mypy .` (fresh cache): **no issues in 1,330 files**. `ruff check .` and
  `ruff format --check .`: clean. Biome on the two touched web files: clean.
  `pnpm -r --if-present run typecheck`: done for all three packages. Shellcheck on
  `deploy/nexovue`: only the existing SC1091 note.
- **Not run:** `just check` (the coordinator's); `test_setup_live.py`; the `ai-off` CI job on
  GitHub; every real-GitHub check; a browser.

### Residual risks

- A broken-into `scheduler-connections` still reads and changes every organization's
  connections, links, case columns and F5 suggestions, and reads explanation facts including
  `aliases`; it no longer reaches the alert queue, secrets, telemetry or sign-ins (ADR-030).
- The role's timeouts are defaults a session can change, and `NOTIFY` needs no privilege, as
  for the AI account (F3's open decision 7).
- A managed Postgres without `CREATEROLE` leaves tracker sync off; setup says so.
- The `ai-off` CI job and the end-to-end test's worker launcher have not run on GitHub's
  runners.


## Design QA hand list (T8.9): the founder's, and not run

**Written 2026-11-22. Nothing below has been run, and no result is recorded.** It is the
hand list `design.md` (D84) and the plan's *Validation plan* promise, because jsdom has no
layout: the suite proves DOM order and focus targets, and cannot prove that a line wraps, that
a page scrolls sideways or that a button is tall enough to press. It owns **AC66, AC67 and
AC108**; they stay unproved until someone fills in the boxes. Tick a box only after looking at
the real screen, and write what was seen beside any that fails.

**Set-up.** The seeded development stack (`just dev`) and `docs/features/case-management/demo.md`,
which makes a case, a note, a recovery and a return; a real browser with its responsive mode
(or a phone for 375). Widths: **375, 768, 1280 and 1920 px** (the page's two-column break is
measured on the content area, about 1,024 px, after the 13rem sidebar, so 1280 is two columns
and 768 is one). Sign in as an owner, a member and a viewer. Make the long text yourself: a
note holding one 500-character line with no spaces, and a note of 15 short lines.

**How to read "no sideways scroll".** The page itself must not scroll sideways: press the
arrow keys and drag on a phone, and in devtools check that the document is no wider than the
window. A table, the diff or a code block may scroll inside its own box, and nothing else may.

### 1. The finding page (AC66, AC67, FR46, NFR7, NFR8)

Open one finding in each state from `design.md`'s state table, at each of the four widths. The
states that change the layout:

- [ ] No case (open); no case (closed); case, nobody assigned; case, mine; case, someone
      else's; joined finding; closed joined finding whose root is resolved; resolved; resolved
      with the signal still open
- [ ] An earlier case on the service (resolved by a person; ended by itself, with **Reopen that
      case**); a quiet case exists (FR9)
- [ ] Issue closed with the signal open (a hold); about to end (idle); about to end (untouched)
- [ ] A synced link, a failing link, a reference link; no connection as an admin and as a member
- [ ] A viewer, and a support view (note bodies read "Hidden during support view")
- [ ] A finding with an empty timeline in a flapping spell, and a case with a flapping joiner
- [ ] Each panel open: reassign, create an issue, attach an issue, resolve with a synced link
      (confirmation), delete a note (confirmation), resolve in the feed

For each, at each width:

- [ ] **No sideways scroll** (AC66).
- [ ] **Order at 375 px**, top to bottom: the sentence; the *Evidence* jump link under it;
      *The case* actions; *The signal* actions (behind a labelled disclosure only if they need
      more than one row); one "What moved" line per signal with its current and normal values;
      the note box; links; the newest **5** timeline rows with **Show earlier**; the full
      evidence (AC66).
- [ ] **Order at 1280 px:** work on the left (case actions, signal actions, links, note box,
      timeline), evidence on the right (*What lines up with this* first, with the last deploy
      as its first row); *Suggested fix* full width underneath. At 768 px it stacks in the same
      order as at 375 (`design.md`, *One order, everywhere*).
- [ ] **Each case action is at least 44 px tall and on its own row** at 375 px, and **Resolve
      is never next to Reassign** (FR46). Measure one with the browser's inspector.
- [ ] **The case sentence is whole**: not cut with an ellipsis, and wrapping instead (AC108's
      page half).
- [ ] **A 500-character line in a note wraps** inside the page, and a **note over 10 lines
      collapses** with a way to open it (AC66).
- [ ] The note box's label and its sentence ("Visible to everyone in your organization. Do not
      paste secrets.") are visible and not covered; typing in it on a phone does not make the
      page zoom or jump (the design's guess about iOS, untested).
- [ ] The timeline shows **5 rows at 375 px and 20 at 1280 px**, a day heading when the date
      changes, and **Show earlier** adds rows and puts focus on the first new one.
- [ ] A state is always said in words, never by colour alone (NFR8).

**Tab order matches what is seen (AC67)**, at each width, in at least: no case, mine, resolved,
and with a panel open:

- [ ] Press Tab from the top and walk the whole page. The focus ring moves down the screen in
      the order the eye reads, with no jump to something off-screen, hidden or in the other
      column. At 1280 px it finishes the left column before the right; at 768 and 375 it is
      one column.
- [ ] The jump links (*Evidence*, *Fix*) land where they say, and the focus ring follows.
- [ ] **Focus after an act** lands on the updated case line (the page's own one-line statement)
      after **Take this**, **Resolve**, **Reopen** and **closing a panel**; after **deleting a
      note**, on the next timeline row, or on the note box when none is left. Do each with the
      keyboard, not the mouse, and look where the ring is.

### 2. The feed (AC108, FR21 to FR23)

At each width, in each filter (**All**, **Active cases**, **Assigned to me**, **Resolved
cases**), with rows in each tier, with the empty state of each filter, and with more than 200
cases if the seed allows ("Showing the newest 200"):

- [ ] **No sideways scroll.** The *Show* filter fits at 375 px and is usable by touch.
- [ ] **No case sentence is truncated**: the full sentence, not one quiet line cut short; the
      title is whole; the case severity is in words.
- [ ] In the case filters: the **project name** on each row, no sparkline, no "Acked", "Across
      all your projects" shown, the detection headline hidden; on *All*, a closed finding with
      an active case keeps its row in its own tier, and a joined finding says "Part of a case",
      linked.
- [ ] **Resolve** on a row with no synced link (*Active cases*, *Assigned to me*) is at least 44
      px tall at 375 px, and a row with a synced link has none.
- [ ] The warning in the last 14 days before an idle end is whole and not truncated.
- [ ] Tab order down the rows matches what is seen, and the filter keeps to the address (reload
      the page; the filter holds).

### 3. `/settings/connections` (AC108)

At each width, in each state the page can show (use the dev stack's switches and the simulated
GitHub where a state needs a connection; mark any state you could not reach as *not seen*, not
passed): hosted; connections off; empty; working; failing; needs attention (and the console-wide
banner on any other page); email off; test running; repositories (a public one with **Allow**
and the warning, an allowed one with **Withdraw**); the service-to-repository mapping, empty and
with rows; replace credential; disconnect (with its real counts); each GitHub setup screen
(starting, abandoned, link not valid, link expired, App made but not installed, no repository
chosen, first test failed, returning later).

- [ ] **No sideways scroll** (AC108).
- [ ] **Tables stack** into rows at 375 px (repositories, mapping), and each row keeps every
      fact it has at 1280 px.
- [ ] **No sentence is truncated**, including the "What leaves this server" block at the top
      and every failure sentence.
- [ ] Buttons (**Test**, **Replace credential**, **Disconnect**, **Allow**, **Withdraw**,
      **Continue to GitHub**) are at least 44 px tall at 375 px and not beside a destructive one.
- [ ] Tab order matches what is seen, and a destructive confirmation takes focus and returns it.
- [ ] The settings rail lists **Connections** for an owner and an admin, and not for a member or
      a viewer.

### Not on this list

The form post to github.com in a real browser (the page's `form-action` header) and every other
real-GitHub step are **T3.0**, **T4.8** and **T5.2**, the founder's after Phase 2 (foundation
item 28). This list is the layout, the order and the focus of screens only.

### Result

| Section | 375 px | 768 px | 1280 px | 1920 px | Run by | Date |
|---|---|---|---|---|---|---|
| 1. Finding page | not run | not run | not run | not run | | |
| 2. Feed | not run | not run | not run | not run | | |
| 3. `/settings/connections` | not run | not run | not run | not run | | |

The plan's gate *Design QA* stays unticked until this table is filled in and every box above is
ticked or has its failure written down and decided.


## Step 6 built, 2026-11-11

Plan tasks T6.1 to T6.3, each with its note in `plan.md`. **Built against the simulated
GitHub only** (foundation item 28): the step's judgeable check against real GitHub is
**not yet run; the founder tests it after Phase 2**, with T3.0, T4.8 and T5.2.
**Kind-agnostic (foundation item 29):** the parser returns a kind, and the attach finds the
place through the connection of that kind; the job's promotion, reference reads and public
check go through `Tracker` (`read`, `is_public`) and name no tracker; the issue's id comes
back on `IssueSeen.issue_id`, which Jira's adapter fills the same way. Jira's address forms
join `parse_issue_address` with Step 7.

What exists now:

- **Pasting an address** (`POST /findings/{id}/links` with `{url}`): parsed by
  `nexovue_platform.connections.parse_issue_address` (GitHub's issue page, with any
  trailing slash, query or fragment, or the key `owner/name#n`; a pull request, another
  host, a port, a user part, `.` or `..` as a name are refused), then found among the live
  connection's reachable places ignoring case, and stored under the place's own spelling.
  Nothing is fetched (AC54). Outside a connected repository: 409; not an address: 422. The
  same issue once per case (409); any number of cases (AC53); at most 20 references a case.
  It starts a case with nobody assigned, like any link (D70), counts as an act beyond
  taking (FR49), and on a resolved case leaves it resolved, as a note does. Audited
  `link.added`; timeline `link_added`; a sync job is added and the workers woken.
- **The job reads a reference hourly** (`REFERENCE_POLL`, FR41) whatever the case's state,
  until 30 days after the case resolved (`not_followed`). It records the status and never
  changes the case (the comparison's reference row, AC53). Its first read stores the
  tracker's own id for the issue (`external_id`), which a pasted address does not carry.
- **Promoting** (`POST /findings/{id}/links/{link_id}/promote`, `MayOperateCases`): only a
  reference already read (so its id is known), on a case that is not resolved and has no
  live synced link, whose connection works and whose repository is reachable and private
  or allowed; else 409 with the reason. The link becomes `synced`, `promoting`; one
  case per issue is the database's index (`uq_finding_links_synced_once`), so promoting an
  issue synced elsewhere is a 409. Audited `link.promoted`; timeline `link_promoted`. The
  job (`sync._promote`) runs the public check, reads the issue and ends `in_sync`, adopting
  the status it read, or `refused` (`public_repository`), or rows 8 to 11 for a gone,
  missing or moved issue. A failure that may pass leaves it `promoting`, with backoff.
- **AC88's "only the click route writes `creating`"** is `test_link_status_writers.py`, a
  scan of every ORM keyword, attribute and SQL text in the API, the scheduler and the
  shared package that sets `sync_status` to `creating`.
- **Public repositories** (`PATCH /connections/{id}/places/{place_id}`,
  `MayManageConnections`, `connections/places.py`): allow or withdraw, each audited
  (`connection.public_repository_allowed`, `…_withdrawn`, naming the repository); asking
  for what already holds writes nothing. Only a repository public now can be allowed
  (open decision 15). The refresh (`tasks._UPSERT_PLACE`) clears `public_told_at` when a
  repository turns public, sets it when one is first listed public (nothing turned), and
  clears the allowance when one turns private. The cases sweep (`case_emails.py`) sends
  `CONNECTION_REPOSITORY_PUBLIC` to verified owners and admins once per such repository,
  marking it told before the send. A link shows `place_public`, and the page says
  "acme/api is public: anyone can read this issue." (FR42).
- **The mapping** (`GET`, `PUT`, `DELETE /service-repositories`; reading for any person,
  none for a support view; writing `MayManageConnections`), audited
  `service_repository.changed`. Migration 0069's table, keyed `(org_id, project_id,
  service_name)`, refers to `projects` through 0068's `(org_id, id)` index and to the
  place, both composite, so a cross-organization insert is refused by the database (AC72).
  It goes with its project, or its place (`organizations/tables.py`).
- **The console:** `/settings/connections` lists each public repository as refused until
  allowed, with **Allow** behind the warning naming what becomes public, and **Withdraw**;
  and the mapping, with a form (project, service, repository) and **Remove**. The finding
  page has **Attach an issue** (`AttachIssuePanel.tsx`), a reference's own status in
  words ("Closed in GitHub, read 20 min. ago. It does not change this case"), **Sync with
  this case** behind the warning, the public line, and the create panel starting on the
  service's mapped repository.

### Unverified until the founder's real-GitHub run (foundation item 28)

- That `GET /repos/{r}/issues/{n}` carries `id`, which a reference's first read stores,
  is from GitHub's documentation as Step 4 read it; the fake answers so.
- The issue page's address form (`/<owner>/<name>/issues/<n>`) and GitHub's names being
  case-insensitive are general knowledge, not read from GitHub's pages in this session.
- That a repository turning public shows as `private: false` in the installation's
  repository list on the next refresh is the fake's behaviour, shaped from documentation.

### Where the code needed more than the plan named

- **Migration 0068**, a concurrent unique index on `projects (org_id, id)`: without it a
  mapping's project could not be keyed with its organization, and AC72's "a direct insert
  of a mapping across organizations must be refused by the database" would not hold.
- **New files:** `connections/places.py` (allowance and mapping, beside `service.py`),
  `findings/AttachIssuePanel.tsx`, `test_link_status_writers.py`,
  `test_pasted_issue_address.py`.
- **`IssueSeen.issue_id`**, so a pasted reference learns its tracker id.
- **A new email message**, `CONNECTION_REPOSITORY_PUBLIC`, for "admins must be told once".
- **One timeline sentence changed:** a refusal for a public repository now reads "nexovue
  did not write to the issue: the repository is public", true of a refused creation and a
  refused promotion, which share one event.
- **The plan's file names:** T6.1's work is in `findings/links.py` and `sync.py`, not
  `findings/cases.py` and `tasks.py`; tests sit where Step 4's link and sync tests are.

### Validation run (read, not inferred)

Against this worktree's own Postgres (`f22s6-pg`, port 45432, Postgres 18,
`pgvector/pgvector:0.8.6-pg18-trixie`, migrated to 0069) and ClickHouse container
(`f22s6-ch`, port 48123, not migrated: Step 6 touches no ClickHouse), torn down afterwards.

- **Seen failing first, then passing** (each by putting the committed file back from a
  saved copy, running, and restoring):
  - `test_pasted_issue_address.py`, 30: a collection error on the committed
    `connections.py` (no parser); 30 passed. A control putting back the old `.`/`..`
    lookahead failed the two dot-name rows.
  - `test_findings_links_live.py`, 19 new tests: on the committed `findings/links.py`,
    `router.py` and `schemas.py`, 19 failed and the 25 earlier passed; restored, 44 passed.
  - `test_connections_live.py`, 6 new tests: a collection error on the committed
    connections code (no `places` module). Controls on the built code: the private-refusal
    and idempotence lines removed failed 2; `fk_service_repositories_project` dropped in
    the database failed the cross-organization test; restored, 43 passed.
  - `test_link_status_writers.py`, 2: its first run failed on its own wrong source root
    (the scan saw nothing); fixed, with a guard that each root exists. A control planting
    a SQL writer in `sync.py` and `promote` writing `creating` failed, naming both;
    restored, 2 passed. It holds an invariant the committed code already kept.
  - `test_connections_sync_live.py`, 6 new tests: on the committed `sync.py`,
    `trackers.py` and `github.py`, 5 failed; the sixth (a reference past 30 days is not
    followed) passed, because that rule already held for every link, and stays as a guard.
  - `test_connections_github_live.py` and `test_cases_sweep_live.py`, one new test each
    (a repository turning public): both failed on the committed `tasks.py` and
    `case_emails.py`; restored, both passed.
  - `test_deletion_live.py`: with `service_repositories` left out of
    `organizations/tables.py`, 2 failed (an unclassified table; a tenant table not erased).
  - Web: `FindingLinks.test.ts` (5 new), `link-words.test.ts` (19 new rows, 1 changed),
    `ConnectionsScreen.test.ts` (4 new): on the committed web sources 23 failed (the two
    unchanged "Not created" rows passed); restored, all passed.
- **The inventory and structure suites**, one process: `test_deletion_live.py`,
  `test_keys_live.py`, `test_apikeys_live.py`, `test_migration_rules.py`,
  `test_setup_schema.py`, `test_postgres_tenancy_structure.py`,
  `test_scheduler_postgres_tenancy_structure.py`, `test_tenant_chokepoint_structure.py`,
  `test_authorization_coverage.py`, `test_ratelimit_coverage.py`,
  `test_no_tracker_calls_in_api.py`, `test_notifications_console.py`,
  `test_connection_model.py`, `test_link_status_writers.py`: **2,617 passed**.
- **The scheduler's connection, sweep and prune suites**, one process: every
  `apps/scheduler/tests/test_connections_*.py`, `test_cases_sweep_live.py`,
  `test_beat_schedule.py`, `test_detection_prune_live.py`, `test_slo_prune_live.py`:
  **228 passed**.
- **The API's case and link suites**, one process: `test_case_transition_parity_live.py`,
  `test_findings_cases_live.py`, `test_connections_webhook_live.py`,
  `test_finding_links_schema_live.py`, `test_findings_links_live.py`,
  `test_connections_live.py`, with `test_pasted_issue_address.py` and
  `test_issue_text.py`: **284 passed**.
- **Migrations 0068 and 0069** down to 0067 and up again: the table and the index went and
  came back, the index valid.
- **The whole web suite:** 169 files, **2,083 tests passed**.
- `uv run mypy .`: no issues in 928 files (after rebuilding a cache a single-file run had
  left stale, which reported two false import errors); `ruff check` and `ruff format --check` clean; Biome clean on
  `features/findings` and `features/settings`; `pnpm -r --if-present run typecheck` clean;
  `verify-tokens.js` clean.
- **Concurrent workers (this week's lesson 1).** The new scheduler tests own their tenant,
  App id and installation, pin the job's clock (2099-01-05) and set the connection's
  `paused_until` a day before it: the live stack's sweep, on the wall clock, sees a paused
  connection and adds no job for it, while the test's own run, past the pause, goes on. The
  API tests run in one rolled-back transaction each. No new assertion depends on today's
  date: the allowance and mapping tests and the scheduler tests pass pinned instants, the
  new web tests pin `Date`, and the link route tests assert no time.
- **Not run:** `just check` (the coordinator's); the judgeable check against real GitHub
  (foundation item 28); a hand check of the new screens at 375 px.

### Residual risks

- **A reference's id is learned on its first read.** Two references on one case that turn
  out to be one issue (attached by two spellings that a later transfer joined) would make
  that read's update collide with `uq_finding_links_issue_per_case`; the run's transaction
  for that link fails and the next run tries again. Not reachable by pasting, which uses
  the place's own spelling; not handled further.
- **A promoted issue that turns out gone or moved** holds the case's synced slot, as a
  refused one does (open decision 14), until a person removes it.
- **Self-review (LOW, not fixed):** a promotion refused because the connection needs
  attention reuses creation's sentence, "… so no issue can be created now", which is
  loosely worded for a sync; it belongs with open decision 17's wording review.
- **The live stack's cases sweep** may tell a test's repository's admins before the test's
  own call does; the test arranges and acts within milliseconds, against a sweep every 5
  minutes, as Step 4's sweep tests do.


## Step 5 built, 2026-11-09

Plan task T5.1, with its note in `plan.md`. **T5.2 (a real webhook delivery reaching a
real server) is not yet run: the founder runs it after Phase 2, once a reachable server
exists (foundation item 28); its result is not faked, and AC130 stays open.** No
migration: the webhook writes only `finding_links.next_sync_at`, through indexes 0061
already made. No new ADR: ADR-035 governs, and its point 2 is what was built.
**Kind-agnostic (foundation item 29):** the route, the lookup, the signature check and the
update name no tracker; each kind's signature header and issue id are one `WebhookForm`
in `connections/webhooks.py`, with GitHub's alone. Jira adds its form with Step 7.

What exists now:

- `POST /api/v1/connections/webhook/{connection_id}` (`connections/router.py`,
  `receive_connection_webhook`): no session; the raw body read as bytes and refused at the
  first byte past 1 MiB (D29), before any signature is looked at; answers 204.
- `service.ring_from_webhook`: no `CONNECTION_SECRET_KEY` is a 503; the connection is
  loaded by id alone (`queries.live_webhook_connection`, the one unscoped connection
  statement, in `test_postgres_tenancy_structure.py`'s `EXEMPT` with its reason, AC139);
  not found, disconnected, a kind with no webhook form, no secret, a secret that will not
  open (logged by connection id) and a bad signature are one refusal, 401, in the same
  words. The header comes from the connection's kind; the HMAC is
  `deploys.signatures.matching_secret`, constant time over the raw bytes. A signed body
  naming no issue is answered and does nothing. Nothing from the body sets a case or a
  link's status; no job is added (D50); nothing is stored or audited.
- `queries.ring_links`: binds the matched connection's `org_id`, brings the next read of
  every link to that issue forward to now, or to 30 seconds after its last read if that is
  later, and only if that is sooner than it was. A link with no read scheduled (not
  followed, stopped, gone, waiting for a person) and a link in backoff are left alone.
- `ratelimit/policy.py`: tier `connection-webhook`, 120 a minute per address, on the
  prefix `/api/v1/connections/webhook/`, apart from the `/connections` routes people use.
- `test_authorization_coverage.py`: `receive_connection_webhook` in `EXEMPT`, with its
  reason, beside `receive_deploys`.

### Unverified until the founder's real-GitHub run (T5.2; foundation item 28)

That GitHub signs an App's deliveries with `X-Hub-Signature-256: sha256=<hex HMAC-SHA256
of the raw body>` was read on GitHub's *Validating webhook deliveries* page on 2026-11-09
(through search results; `docs.github.com` is not reachable from the build session). That
an `issues` delivery carries `issue.id`, the REST issue's id the sync stores as
`external_id`, was **not** confirmed: the payload page could not be reached. A delivery
without it rings nothing, and polling still follows the issue. The tests build the
delivery from the authors' reading, marked so in `webhooks.py`.

### Where the code needed more than the plan named

- **A new file, `connections/webhooks.py`**, holding each kind's form, so the service and
  the route stay tracker-neutral.
- **The update's conditions beyond the design's** (open decisions 8 and 9): the 30-second
  rule schedules the read rather than skipping it, and a link with no read scheduled or in
  backoff is not revived.
- **204, not 200.** The design says "answer 200"; the route answers 204 with no body, as
  nothing is returned. GitHub treats any 2xx as delivered (general knowledge, unverified).
- **The match is on the issue's id** (open decision 11).
- **A fourth copy of the bounded body read** (`deploys`, `billing`, `checkfiles` hold the
  other three). Merging them is outside this feature's files: residual risk below.

### Validation run (read, not inferred)

Against this worktree's own Postgres (`f22s5-pg`, port 45432, Postgres 18, migrated to
0065) and ClickHouse (`f22s5-ch`, ports 48123 and 49000), torn down afterwards.

- **Seen failing first, then passing:** `test_connections_webhook_live.py`, 36 tests: all
  36 failed on the committed API code (no route, no service function); then 36 passed.
  **Negative controls on the built code:** removing `ring_links`' status, backoff,
  read-gap and "already sooner" conditions failed 8 (the replay, read-gap, five
  never-revived and already-due tests); replacing the read gap with plain `now` failed the
  read-gap test; each restored from a saved copy, 36 passed. `test_postgres_tenancy_structure.py`
  and `test_authorization_coverage.py` failed on the new lookup and route until each
  `EXEMPT` entry was added (2 failed; then 2,319 passed). `test_ratelimit.py`'s and
  `test_ratelimit_callers.py`'s new tests: an ImportError on the committed policy, and,
  with the tier built but left out of `_OWN_TIERS`, both failed on behaviour (the default
  tier; a 121st delivery answered 200); restored, 70 passed with `test_ratelimit_coverage.py`.
  The six new tests in `test_connections_sync_live.py`: on the committed API code the
  three webhook tests failed; the other three exercise Step 4's polling and lease, so
  each was proved by a control instead: the lease's held-check removed failed the
  two-jobs test, the active poll doubled failed the polling test, the resolved poll
  doubled failed the hourly reopen test; `sync.py` restored from a saved copy, 6 passed.
- **The suites that inventory or share what this touches**, together with 4 workers:
  every `apps/scheduler/tests/test_connections_*.py`, `test_connections_live.py`,
  `test_connections_webhook_live.py`, `test_findings_links_live.py`, `test_keys_live.py`,
  `test_deletion_live.py`, `test_apikeys_live.py`, `test_no_tracker_calls_in_api.py`, the
  three tenancy-structure suites, `test_authorization_coverage.py`, the rate-limit suites,
  `test_deploys.py`, the detection and SLO prune suites and `test_beat_schedule.py`:
  **2,923 passed**, none failed. This step adds no table, sealed column, audit entry,
  rule or sweep; its live tests write one tenant's rows inside a rolled-back transaction
  (API) or under per-test tenants and App ids (scheduler), so a concurrent sweep meets
  nothing of theirs half-written.
- **Every non-live suite** in `apps/api/tests`, `apps/scheduler/tests` and
  `packages/py-platform/tests`, with the webhook suite, 6 workers: **10,207 passed**, 6
  failed, 18 errors, each read: ClickHouse tables absent (this worktree's ClickHouse was
  not migrated; Step 5 touches no ClickHouse), the dev stack not running, or no DNS
  (`test_connect_guard`). `test_migration_rules.py` (39), `test_setup_schema.py` (2) and
  `test_finding_links_schema_live.py` (13) passed, one at a time. The sync and webhook
  suites rerun after formatting: 91 passed.
- `uv run mypy .`: no issues in 893 files; `ruff check` and `ruff format --check` clean;
  `pnpm -r --if-present run typecheck` clean and `verify-tokens.js` clean (no web file
  changed, so Biome and the web tests were not run).
- **Not run:** `just check` (the coordinator's); T5.2 and every real-GitHub check; a
  delivery through the real Caddy.

### Residual risks

- **Everything GitHub-facing is proved against the authors' reading of GitHub's pages**,
  and `issue.id` in a delivery is not confirmed (above).
- **A connection id is unguessable but not secret**: it is in the webhook's address,
  which GitHub holds. A caller who learns it still cannot ring without the secret, and a
  refusal reads the same whether the row exists or not; the lookup's timing differs.
- **Four copies of the bounded body read.** A shared helper is a change-work item outside
  F22's files.
- **A link in backoff is not hurried by a webhook**, so a change during a link's backoff
  arrives when the backoff ends, as FR39 allows.


## Step 4 built, 2026-11-09

Plan tasks T4.1 to T4.7, with the notes in `plan.md`. **T4.8 (the written script against
real GitHub) is not yet run: the founder runs it after Phase 2 (foundation item 28); its
result is not faked. T4.9 (memory) is not run** (below). **Migration 0061**
(`0061_finding_links.py`, `down_revision` "0055" until the coordinator re-chains after
F20's 0056, F28's 0057 and F19's 0058 to 0060); ClickHouse untouched. **ADR-035** filed
(*Trackers are reconciled, and a webhook only rings the doorbell*), with the index; the
design's copy points to it. **Kind-agnostic, as the founder asked (foundation item 29):**
the sync (`connections/sync.py`) and the comparison (`compare.py`) name no tracker; the
issue calls are the `Tracker` protocol's (`trackers.py`), and GitHub is still the one
adapter. `issue_text()` already writes Jira's ADF form beside GitHub's Markdown.

What exists now:

- **T4.1** `nexovue_platform/connections.py`: `IssueFacts` (finding id, signal, ratio,
  severity, since, strict name, link back; nothing else), built only by `issue_facts()`
  and re-checked by `from_json()` when thawed; `issue_text()` in GitHub's Markdown and
  Jira's ADF (doc, paragraph and text nodes, the name under a code mark); the fixed
  comments (`comment_text()`), and the close reason by how the case ended. The shared
  job builders `sync_job()`/`refresh_job()`; `jobs.ENQUEUE_UNLESS_WAITING`, the
  conditional insert (AC48); `cases.CLEAR_HOLD_SQL`; `SET_WANTED_STATE_SQL` now leaves
  nothing pending when the act wants what was last seen.
- **T4.2** migration 0061: `finding_links` with no address column; one synced issue per
  case and per issue (stopped links hold neither slot); one issue per case once in any
  role; strict key and id patterns; facts only while `creating`; inventories
  (`organizations/tables.py`) and the model held equal to the frozen words.
- **T4.3** `findings/links.py`, `findings/link_sync.py`, routes in `findings/router.py`:
  `GET /findings/{id}/links/preview`, `POST /findings/{id}/links` (the only writer of
  `creating`, AC88), `POST .../links/{link_id}/retry`, `DELETE .../links/{link_id}`, each
  `MayOperateCases`, in `POLICY_OF`, audited per the design's table
  (`link.issue_requested`, `link.removed`; retry none). A resolve, false alarm or new
  normal asks the issue to close with one comment, unless the person leaves it open or
  it is held; a reopen asks it to reopen; each adds one sync job. `PATCH /case` takes
  `leave_issue_open`. A finding's page carries `links` (a token's never). Disconnect
  stops every link in its transaction and frees each case's slot; `GET /connections`
  carries the real count, `synced_links`.
- **T4.4** `connections/compare.py`: the table, rows 1 to 11 (7 decided where the reopen
  meets the index).
- **T4.5** `connections/sync.py`: `sync_connection` (ids-only kwargs, `org_id` bound on
  every read, the lease by compare-and-set, the pause, 50 links oldest first, the state
  read before each call, writes a second apart, the run ending at the first timeout,
  connection failure, 5xx, rate limit or refused key, backoff 1 minute doubling to an
  hour with spread, `retry-after` obeyed, tokens narrowed to one repository with
  `issues: write` and `metadata: read` and revoked at the end); creation with
  `first_attempt_at` committed first, the public check before every create, and
  find-existing on a retry once the tracker's list is believed settled
  (`search_settles_seconds`: 0 for GitHub, by its documentation); the comment committed
  as written before it is posted; `sweep_connections` (one sync job per connection with
  a due link, and the **hourly repository refresh**); beat adds the sweep every 30
  seconds **only while a connection is live and connections are on** (AC118's beat
  clause, founder 2026-11-09); a refresh that finds a repository again resumes its
  links. `connections/store.py` holds loading and the state write (a connection back at
  `working` clears `failure_told_at`). The simulated GitHub now has issues (create,
  read, close with a reason, reopen, comment, list by creator and time with a stated
  delay, delete, transfer, a repository leaving the installation, commit-then-drop) and
  `tracker_harness.py` kills a spawned worker with `SIGKILL` at each named fault point.
- **T4.6** `nexovue_scheduler/case_emails.py`, called by `cases_sweep` on `light`: owed
  case emails (`tracker_closed`, `tracker_closed_signal_open`, `idle_warning`), each row
  deleted and committed before its send, to the assignee or to verified owners and
  admins (`_VERIFIED_ADMINS`, new beside `_VERIFIED_OWNERS`); a connection's failure
  email (needs attention, or failing 24 hours) and expiry email (14 days ahead), each
  with its own marker. The idle warning asks the synced issue for its one idle comment
  (`idle_warning_at`), which the sync writes; a case with any link never ends as
  untouched. Six new `Message` members and templates. `case_endings.py` is the
  scheduler's one ending (resolve, detach, end a hold, event, audit), run by the sweep
  and the sync alike.
- **T4.7** `features/findings/link-words.ts` (words by status, reader and age; the
  resolve and false-alarm choices naming the issue; the hold line; the status-region
  announcement; Copy my notes from the person's own notes), `LinksList.tsx`,
  `CreateIssuePanel.tsx`, `CaseSection.tsx` (Create an issue, the hold line, resolve's
  three confirmations, the links after the note box), `FindingScreen.tsx` (link moves
  in the status region; the false alarm's line and success naming the issue),
  `ConnectionsScreen.tsx` (Disconnect's real count and the step: delete the App).

### Unverified until the founder's real-GitHub run (T4.8; foundation item 28)

Everything about GitHub's issues was read from documentation and replayed by the fake:
`POST /repos/{r}/issues` taking `labels` and answering `id` and `number`; the label's
outcome under `issues: write` (AC84); `state_reason` on `PATCH` and its values,
including what a `Fixes #n` merge sets; 410 for a deleted issue, 404 for one not found,
301 with a `Location` for a transferred one, and the JSON read there; the list's
`creator=<slug>[bot]`, `since` and `state=all`, and that it sees a new issue at once (a
retry trusts it); a token narrowed by `repository_ids`, and 422 when the installation no
longer reaches one; the rate-limit headers; and how GitHub renders the issue text,
including whether `GH-12` or a hexadecimal name links in a title (AC41). The issue
fixtures say so in their `_source`.

### Where the code needed more than the plan named

- **Columns beyond the design's:** `finding_links.idle_warning_at` (one idle comment per
  link in its case's life, AC96) and `gone_at` (row 9's daily re-read for a week).
- **`finding_links.frozen_facts` is `JSONB(none_as_null=True)`**: SQLAlchemy writes
  Python `None` as JSON `null` otherwise, which the "facts only while creating" check
  refused when a disconnect stopped a link (caught by the disconnect test).
- **A stopped link may have no key** (a disconnect before its creation was known); the
  check allows `stopped` beside `creating` and `refused`.
- **A link whose repository row is gone** (`SET NULL`) is refused if being created, else
  needs a person, and the run goes on (found in review, test seen failing).
- **The sweep's untouched end also requires no link** (design step 3), not only
  `case_worked_at`.
- **`recording_site`** gained a callable default and a dropped reply; **the fake's App
  and installation ids** are per test in the sync suite, since the pair is unique across
  tenants and suites run side by side.
- **New files the plan did not name:** `findings/links.py`, `findings/link_sync.py`,
  `connections/sync.py`, `connections/store.py`, `case_emails.py`, `case_endings.py`,
  `link-words.ts`, and the test files named below.
- `test_notifications_console.py` and `test_findings_api_live.py` hold inventories the
  new messages and the `links` field had to join.

### Validation run (read, not inferred)

Against this worktree's own Postgres (`f22s4-pg`, port 45432, Postgres 18, migrated to
0061) and ClickHouse (`f22s4-ch`, ports 48123 and 49000, 39 migrations), torn down
afterwards.

- **Seen failing first, then passing:** `test_issue_text.py` (ImportError on the
  committed module; then 85 passed); the model and frozen-word tests (collection error);
  `test_deletion_live.py` (`finding_links` does not exist; then 26 passed); the index
  tests (13 failed on 0055; 13 passed on 0061); `test_connections_compare.py`
  (ImportError; 24 passed); `test_connections_sync_live.py` (ImportError without
  `sync.py`; then its first run found the lease taken on the real clock in the harness
  test, and five test faults; then 49 passed, including the placeless-link test seen
  failing with a `TypeError`); the beat tests (ImportError; 18 passed); the tenancy test
  (it named `_CONNECTIONS_DUE`, then `_OWED` and `_TO_TELL`, until each was a named
  exception); `test_findings_links_live.py` (22 of 25 failed on the committed API code,
  the three passing ones asserting that nothing happens; then a real defect, JSON
  `null`; then 25 passed); the sweep's six new tests (5 failed, the sixth asserts
  nothing happens; then 15 passed); `link-words.test.ts` (no module; 29 passed);
  `FindingLinks.test.ts` and the disconnect counts (11 failed on the committed screens;
  then passed); the two inventories (`test_notifications_console`,
  `test_findings_api_live`, failed on the new members and field).
  **Not seen failing first, checked by a negative control instead:** the AC89 seam test
  (failed when `github.py` read the environment); the parity test (failed when the
  ending's audit action changed). **The five new `test_case_transitions.py` rows, seen
  failing after the fact** (2026-11-09, at the coordinator's request): with
  `nexovue_platform/cases.py` copied aside and its new behaviour stubbed (`CLEAR_HOLD_SQL`
  without its `org_id` bind, `SET_WANTED_STATE_SQL` no longer nulling a wanted state equal to
  the seen one, `set_wanted_state_parameters` without its vocabulary checks), all five
  failed (5 failed, 15 passed); restored from the copy, 20 passed. Each guards behaviour new
  in this step, so none only restates older code.
- `apps/scheduler/tests` with 6 workers: **5,153 passed**, 11 failed, 24 errors. The
  errors are the four suites that refuse to start without Kafka, DNS or the object
  store (`test_connect_guard`, `test_detection_failure_live`, `test_detection_settle_live`,
  `test_erasure_live`); 3 failures are `test_alert_telemetry_live.py`, which needs the
  seeded dev project; the other 8 were `test_connections_github_live.py` colliding with
  the sync suite on the fake's fixed App id, fixed in the sync suite, after which both
  together: **66 passed**.
- `apps/api/tests` with 4 workers, migration suites aside: **5,075 passed**, 20 failed,
  3 skipped, 91 errors; the two failures this step caused (the inventories) were fixed
  and pass (39). The rest need the running dev stack (receiver, seeded data, object
  store, system logs): `test_traces_live`, `test_metrics_live`, `test_services_live`,
  `test_synthetics_live`, `test_seed_live`, `test_dbstats_live`,
  `test_clickhouse_system_logs_live`, `test_backups_live` and others in areas this step
  did not touch. The six migration suites, one at a time: **48 passed**, ending at 0061.
- `packages/py-platform/tests`: **272 passed**.
- Web: **1,731 passed** in 147 files (the two new test files rerun after their issue
  numbers changed: 37 passed); `pnpm -r --if-present run typecheck` clean; Biome clean on
  `features/findings` and `features/settings` and on the two JSON fixtures;
  `verify-tokens.js` clean (issue numbers in tests are two digits, since `#412` reads as
  a colour to it).
- `uv run mypy .`: no issues in 834 files; `ruff check` and `ruff format --check` clean.
- **Not run:** `just check` (the coordinator's); T4.8 and every real-GitHub check; T4.9;
  the promtool tests; any browser (layout, AC66 and AC108's hand list, are Design QA's).

### Not built in Step 4, and why

- **T4.8**: real GitHub, the founder's after Phase 2 (foundation item 28).
- **T4.9**: the measurement belongs to the built `scheduler-connections` container; this
  session built none, and a bare process's figure would not be the container's.
  `sizing.md` keeps the 384 MB guess.
- **T3.13's oldest-due-link alert** still waits: links now exist, and nothing exports
  the oldest due link's age yet. It is T3.13's remainder, not a Step 4 task.

### Residual risks

- **Everything GitHub-facing is proved against the authors' reading of GitHub's pages.**
- **An error other than a tracker failure on one link** (a database refusal while
  adopting or moving an issue, say) fails the whole run, and the next run meets it
  again; the lease is released, but that connection's other links wait behind it.
- **A retry while the connection is paused** adds a job that does nothing until the
  pause ends.
- **A comment taken and then not posted is lost** (a kill between, or a refusal): "at
  most one" is the promise, as designed.
- **An issue created while its link was removed or the connection disconnected** stays
  in the tracker with its link back and nothing in nexovue pointing to it; a creating
  link cannot be removed, which narrows this to a disconnect mid-call.
- The resolve parity (AC103) is proved row by row; reopening by person and by sync shares
  `REOPEN_CASE_SQL` and is not compared row by row.

**CI fix, 2026-11-09: a setup's state hash is hex text (migration 0065).** CI (run
42650457940) failed `test_keys_live.py::test_every_binary_column_is_a_sealed_column_the_rotation_reseals`:
0055 stored `connections.setup_state_hash` as `bytea`, and that test holds every binary
column to be a sealed one the key rotation reseals (F28 AC3). The column was wrong, not
the test: a hash is not sealed and cannot be resealed, and naming it as an exception
would make "binary means sealed" false, the property that stops a sealed column being
left out of `keys.SEALED_COLUMNS`. Every other token hash in the schema is 64 hex
characters, so 0065 converts this one with `encode(…, 'hex')` and the code compares a
`hexdigest()`. The design names the column and not its type, so it needs no change.


## Step 3 built, 2026-11-08

Plan tasks T3.1 to T3.13, with the notes below and in `plan.md`. **T3.0 is not yet run:
the founder runs it after Phase 2 (foundation item 28); its result is not faked.** Built
against a **simulated GitHub**, by the founder's decision of 2026-11-08: nothing here has
spoken to GitHub. **Migration 0055** (`0055_connections.py`); ClickHouse untouched.
**ADR-030** filed (*`scheduler-connections` makes every tracker call, on narrow settings*),
with the decisions index. The reconcile-and-doorbell ADR stays proposed in `design.md`:
nothing in Step 3 rests on it, and Step 4 builds the sync it governs.

**Kind-agnostic, as the founder asked (foundation item 29).** One `connections` table with
`kind` (`github`, `jira`, `github_fix`); the scheduler's jobs (`connections/tasks.py`) load
any tracker kind and write what they learn through one state machine (`states.py`); the
per-kind boundary is `connections/trackers.py` (`Tracker`, `tracker_for`), and GitHub is the
one adapter (`connections/github.py`). No Jira code exists; `tracker_for` refuses a `jira`
row. Nothing on a screen names Jira (T0.1).

What exists now:

- **T3.1** `nexovue_platform/connections.py`: queues `connections` (cap 2) and
  `connections-interactive`, the three task names, purposes bound to
  `org_id:connection_id`, connection states, link statuses, the failure words,
  `link_url()` (strict keys only), `is_repository_key`, `is_github_account`,
  `is_manifest_code`.
- **T3.2** `scheduler-connections`: `roles.py`; `SCHEDULER_ROLE` (`full` | `connections`),
  which decides whether Kafka and the check key are demanded; `--tasks`, `TASK_SETS`,
  `PRELOAD_OF` and `worker.use_task_set`, chosen before the first child; `QUEUE_CAPS` gains
  `connections: 2`. Compose: the service behind the `connections` profile in `nexovue.yaml`
  with an exact environment and no `control-plane-env`; entries in the quickstart and build
  overlays and the dev stack; **none in `nexovue.hosted.yaml`** (asserted). F3 had not
  merged, so this built F3's mechanism for F3 to take over.
- **T3.3** `deploy/nexovue`: `write_secrets` writes `CONNECTION_SECRET_KEY`, and
  `add_missing_secrets` adds it to older settings without touching what exists
  (`KEYS_ADDED_LATER`); `keys.py` gains the keyring; `key-rotation.md` names the key.
- **T3.4** migration 0055: `connections` (setup columns, three sealed columns, state,
  failure enum and HTTP status, a consecutive-failure count, `repositories_read_at`),
  `connection_repositories`; `UNIQUE (org_id, id)` on both; one live row per organization
  and kind; disconnected means every secret null; inventories and `SEALED_COLUMNS`
  updated; `job_results` may hold `connection_test` (check replaced `NOT VALID`, then
  validated). Upgrade, downgrade and upgrade run clean on a fresh database.
- **T3.5** `apps/api/src/nexovue_api/connections/`: `MANAGE_CONNECTIONS` (owner, admin) in
  every copy of the action list; `GET /connections` (a support view gets kinds and states
  only); `POST /connections/github/manifest` (hosted and off refused with the spec's
  sentences; an abandoned setup past its hour is marked disconnected first, AC76; one with
  an App is kept); `/github/manifest/complete` (state hash, issuing person, expiry; the code
  sealed and its conversion job added **on the same transaction**, ids only, AC74);
  `/github/refresh`; `DELETE /connections/{id}` (every secret null in that transaction,
  disconnect hooks for F5, audit).
- **T3.6** the scheduler's `connections/` package and PyJWT (`pyjwt[crypto]`, RS256):
  conversion (the code nulled in the same transaction; client id and secret dropped; a code
  past its hour never sent; GitHub down retries within the hour), binding the App's own
  installation (none or two binds nothing, AC38), repositories read page by page with a
  token narrowed to `metadata: read` and revoked after, places no longer listed marked
  unreachable, the connection test, and the state machine (`failing` only after 5 straight).
- **T3.7** `test_connections_have_no_ai.py` (the walk from `TASK_SETS["connections"]`, the
  role's two queues, and a running child from the connections preload holding no AI module,
  no `clickhouse_connect`, `confluent_kafka` or `boto3`, nor detection);
  `test_no_tracker_calls_in_api.py` (open decision 1).
- **T3.8** `POST /connection-tests`, `GET /connection-tests/{request_id}` on their own tier
  (10 a minute); the wait ends its read transaction between polls.
- **T3.9** the Caddyfile's start route, with the console's policy word for word except
  `form-action 'self' https://github.com`, set with Caddy's `>` (set and defer, per Caddy's
  `header` documentation); `GitHubStartScreen` posts GitHub's manifest form.
- **T3.10** `/settings/connections` (`ConnectionsScreen.tsx`, `connection-words.ts`), the
  return and installed pages, the rail entry (`manage_connections`), and the console-wide
  banner (`ConnectionsBanner.tsx` in `AppShell`).
- **T3.11** `POST /connections/{id}/credential`: a new App key re-sealed in place, audited,
  and tested.
- **T3.12** `NEXOVUE_CONNECTIONS` (default `yes`) in `write_choices`; `compose_profiles`
  turns on the `connections` profile unless `no` or hosted; `services_to_stop` names
  `scheduler-connections` only where it runs; the API and beat read the same setting.
  **Not built: beat's sweep clause** (open decision 2).
- **T3.13** beat exports `nexovue_scheduler_connections_active` (an `EXISTS` over live
  tracker connections, 0 while connections are off; a named cross-tenant exception);
  `NexovueConnectionsMissing` and its scrape job; `sizing.md` corrected to 13.75 GB and the
  role's 384 MB guess added.

### The simulated GitHub, and what it is shaped from

`apps/scheduler/tests/tracker_fake.py`, on `recording_site` (real sockets, the guarded libcurl
client, a private address), answers the routes the code calls: `POST /app-manifests/{code}/conversions`,
`GET /app`, `GET /app/installations`, `POST /app/installations/{id}/access_tokens`,
`GET /installation/repositories` (paged) and `DELETE /installation/token`, and 404 for
anything else. It is stateful: a code converts once; the App's JWT is verified (RS256 with
the key the conversion handed out, `iss`, `iat`, `exp` at most 10 minutes); tokens are minted
per request, record the permissions asked for, and stop working when revoked; a route's
answer can be replaced to drive a failure. Its answers come from `fixtures/github/*.json`,
each carrying a `_source` note.

**docs.github.com could not be fetched from the build session**, so the shapes were read
through web search on 2026-11-08 (GitHub's own pages, as indexed): *Registering a GitHub App
from a manifest* and the REST *Create a GitHub App from a manifest*; *Generating an
installation access token*; *Generating a JWT for a GitHub App*; the REST *Installations*
pages (*List repositories accessible to the app installation*, *Revoke an installation
access token*). PyJWT's installation page for the `crypto` extra. Caddy's `header` page for
the `>` prefix.

### Unverified until the founder's real-GitHub run (T3.0, T4.8; foundation item 28)

- The manifest flow from a private address, through the real console behind Caddy: that the
  form posts under the start route's policy, that the redirect back passes, and that GitHub
  accepts `hook_attributes.active: false` with `default_events: [issues]` (AC73).
- Whether `installation` and `installation_repositories` events need asking for. Nothing
  relies on them: binding reads `GET /app/installations`. **The hourly refresh is not
  scheduled yet** (open decision 2), so until Step 4 a refresh runs when GitHub returns a
  person to `installed`, on Test, and by `POST /connections/github/refresh`.
- **The unreachable install** (polling only): built as `active: false`, never tried.
- Who must act in GitHub (an organization owner, or someone allowed to manage Apps).
- The conversion's answer (`id`, `slug`, `pem`, `webhook_secret`, `client_id`,
  `client_secret`; 201); the JWT's `iss` as the App id (GitHub's current page recommends the
  client id and accepts the App id, by search); `GET /app/installations`' shape; that a
  private App installs only on its owner's account (so one installation is expected); a
  token request narrowed by `permissions`; `GET /installation/repositories` carrying
  `full_name` and `private` (general knowledge, not confirmed by the search); revocation
  answering 204; GitHub's rate-limit 403 headers.
- The App's install page (`https://github.com/apps/<slug>/installations/new`), used also for
  *Choose repositories on GitHub*; the 34-character App name limit and its uniqueness.
- Calls through a company egress proxy (D83).

### Where the code needed more than the plan named

- **`job_results` kinds** were frozen (`check_run`, `channel_test`); 0055 adds `connection_test`.
- **`recording_site`** gained a configurable `default` reply, so the fake answers 404 for an
  unknown path, as GitHub does (it answered 200 before, which hid a refused code).
- **`test_detection_coordinator.py`** and **`test_billing.py`** pinned the old queue caps and
  the old set of production keys; both updated.
- **The dev stack is the hosted product** (`NEXOVUE_DEPLOYMENT: hosted`), where connections
  are refused by design; trying a setup locally needs the API run self-hosted.
- **Two lines of the design were not built as written** and are open decisions 3 and 4.

### Validation run (read, not inferred)

Against a private Postgres **16** at port 26543 (`/var/lib/postgresql/f22-pg`, outside the
scratch folder because the session's `/tmp` tree became unreadable to the `postgres` user),
because the Docker daemon was not running and starting it was refused. Postgres 16 lacks
18's `uuidv7()`, so a SQL stand-in was added to that database and `template1`, for the
test store only. No Kafka, ClickHouse or object store.

- **Seen failing first, then passing:** T3.1 vocabulary (30 failed, AttributeError);
  T3.4 deletion and keys suites (collection errors, then a real `KeyError: 'connections'`
  in the deletion statements, fixed); the model/migration test (seen failing against a
  mutated migration); T3.3 install secrets (2 failed, KeyError); T3.2 settings and role
  (8 failed); AC34 suite (5 failed); T3.6 states and GitHub suites (ImportError), then two
  real defects: the `job_results` check and the fake's 200 for unknown paths; T3.5 API
  suite (ImportError); AC32 (failed until the routes existed, then found the object store,
  open decision 1); the connection-test tier (1 failed); the compose contract (11 failed);
  T3.12 install functions (11 failed); `connections_active` and its tenancy exception
  (2 failed); the Caddyfile route (2 failed); the rail entry (1 failed); the console suite
  (import failure); after review, the hosted screen's description (failed against the old
  description) and the poll's transaction (failed with a rollback, passes with a commit).
  **Not seen failing:** the cross-service contract for connection jobs, written after the
  tasks; the restore stop list's Compose test (checked by a negative control instead).
- `apps/scheduler/tests`: **4,932 passed, 0 failed**; 13 suites refuse to start without
  Kafka, ClickHouse or DNS, as designed (52 collection errors across 4 workers).
- `apps/api/tests`, every suite but the six migration suites: **4,766 passed, 1 failed,
  3 skipped**; 26 suites refuse to start without ClickHouse. The one failure,
  `test_apikeys_live.py::test_an_organization_cannot_revoke_another_organizations_key`,
  orders audit rows by their `uuidv7` id: Postgres 18's function is monotonic within a
  millisecond, the test store's Postgres 16 stand-in is not, so rows written in one
  millisecond sort either way. It passed before the database was seeded, and touches nothing
  this step changed; it should be rerun on Postgres 18. The six migration suites, one at a
  time (they downgrade the database they share): **48 passed**. The seed suites, after the
  development seed: **14 passed, 1 skipped**.
- `packages/py-platform/tests`: **135 passed**. Web: **1,660 passed** in 142 files;
  `pnpm -r --if-present run typecheck` clean; Biome clean on the changed files;
  `verify-tokens.js` clean.
- `uv run mypy .`: no issues in 801 files; `ruff check` and `ruff format --check` clean.
- **Not run:** `just check` (the coordinator's); the promtool rule tests (no Docker, and
  downloading promtool was refused); the real-Caddy header check; any browser; T3.0 and every
  real-GitHub check; `shellcheck` (not installed); the 13 scheduler and 30 API suites that
  need ClickHouse, Kafka or DNS.

### Residual risks

- **Everything GitHub-facing is proved against the authors' reading of GitHub's pages,**
  through search snippets, not the pages themselves. The fake can be wrong in the same way
  the code is.
- **The role's 384 MB is a guess;** T4.9 measures it.
- **A compromise of `scheduler-connections` still reaches all of Postgres** (one shared
  account) until T8.7.
- **The connection test's result waits up to 30 seconds** in the console (three 10-second
  waits); a slower test reads as "has not finished".


## Step 2 built, 2026-11-07

Plan tasks T2.1 to T2.8, T2.10 and T2.11, as written, with the notes below. T2.9
waited on F3 (founder, 2026-11-07): F3's `evidence/gather.py` was not on the
branch, and Step 2 was committed without it. **Built 2026-11-22 with Step 8** (above). **No migration:** every
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

The coordinator commits Step 8. The founder answers open decisions 12 to 24 below, with Steps
4's and 5's, and runs the Design QA hand list (below). Step 7 (Jira) is out of Phase 2. T4.9
measures the built container. After Phase 2, the founder runs T3.0, T3.9's hand
checks and T4.8 against real GitHub and a real browser (foundation item 28). Step 7 waits on
Atlassian.


## Open decisions

**From Step 8 (2026-11-22).** None blocks the commit; each is built as recommended.

**20. The timeline read scans every organization's events.** Measured at D65's worst shape,
its median is 20 to 37 ms, under the 200 ms guess, so D65's "store a root on each event" is not
triggered. But the plan is a sequential scan of all of `finding_events`: its cost grows with the
install's total events, not the case's. *Option A (recommended):* keep D65 as decided, and
re-measure on the test bed at a realistic event count before the first customer; if it slows,
rewrite the read as two index scans joined by `UNION ALL` (no schema change). *Option B:* make
that rewrite now, unmeasured at scale.

**21. AC56's "the whole gate" runs beside an idle `scheduler-insight`.** The dev stack `just
check` uses starts `scheduler-insight` (idle, no AI key). Built: a separate CI job, `ai-off`,
with Postgres alone and no AI account, runs the AI-off case suite and refuses any AI variable,
container or account; `check` is untouched. *Option A (recommended):* keep it, and amend AC56's
"the whole gate must run with no AI configured and no `scheduler-insight`" to "a full case must
run, in CI, with no AI configured and no `scheduler-insight`", since F3's and F4's own suites
need the AI account; making `ai-off` a required check is a GitHub settings change, the
founder's. *Option B:* take `scheduler-insight` out of `check`'s stack, which F3's and F4's
suites would then have to start themselves.

**22. Row security names the tracker account by text.** 0085's two policies admit
`current_user = 'nexovue_connections'`, because a migration runs before `setup` creates the
role, so `TO nexovue_connections` cannot be written there. Only an account allowed to create
roles could make another account of that name, and the install's is the only one. *Option A
(recommended):* keep it. *Option B:* have `setup` create the policies `TO nexovue_connections`
after the role, which puts schema objects outside Alembic.

**23. What the `explained` event records (T2.9).** FR16 lists `explained` and says nothing of
when it is written or what it carries. Built: one event, for the system, whenever
`explained.write` wrote at least one follow-up row, carrying the number of the explanation
version the follow-up rests on (`explanation_version`, already in the event's frozen fields). So
a second channel owed its follow-up by a later read adds a second event. *Option A
(recommended):* keep it: the timeline shows each time a follow-up went out, and it never shows
one that did not. *Option B:* one `explained` event per finding however many channels, which
needs a check for an earlier one. *Option C:* write it whenever a qualifying cause stands, even
when every channel is off or capped, which says something that was not sent.

**24. `design.md`'s audit table disagreed with the code in two lines (T8.8).** A note on a
finding with no case starts one, and starting a case is audited (`case.started`), though the
table said a note writes none; and `connection.place_changed` is not defined, since the places
route changes only the public allowance. Built: the table is corrected to the code. *Option A
(recommended):* keep the code. *Option B:* stop auditing the case a note starts, which loses a
record of who started a case. Jira's issue type and transitions (Step 7, out of Phase 2) would
bring `place_changed` back if built.

**From Step 6 (2026-11-11).** None blocks the commit; each is built as recommended.

**12. A promotion adopts the issue's status as it reads it.** The spec says promotion
"runs as a job that makes the public check" and ends `in_sync` or `refused`; it does not
say what an already-closed issue does to an active case. Built: the first read is
recorded as seen, so only a later change in the tracker moves the case (a closed issue
promoted onto an active case does not resolve it then). *Option A (recommended):* keep it:
a second click should not end a case by surprise. *Option B:* treat the first read as a
change from `open`, so a closed issue holds or resolves the case at once (rows 5a, 5c).

**13. Only a reference nexovue has read can be promoted.** A pasted address carries no
tracker id, and one-case-per-issue is enforced on that id. Built: a reference not yet
read is refused, "nexovue has not read this issue yet … Try again in a minute."; the first
read follows the attach at once. *Option A (recommended):* keep it: the database's index
then decides at the click. *Option B:* allow it, and let the job refuse a collision,
which needs a new link failure word and a migration.

**14. A refused promotion keeps the case's synced slot.** As a refused creation does today
(the index frees only `stopped`), so the person removes it before creating or promoting
another. *Option A (recommended):* keep it, consistent with creation. *Option B:* a
refused promotion falls back to a reference, still read hourly; a refused creation would
then deserve the same, which is a change to Step 4.

**15. Only a public repository can be allowed, and turning private clears the
allowance.** The design says an admin "may allow" a public repository. Built: allowing a
private one is refused ("nothing to allow"), and the refresh clears an allowance when its
repository turns private, so a later turn to public needs a fresh one after the warning.
*Option A (recommended):* keep it: an allowance given ahead of time would let a repository
turn public with nobody warned. *Option B:* allow ahead of time and keep allowances.

**16. A reference on a resolved case leaves it resolved.** The spec lets a link start a
case and says a reference never changes one; it is silent on a resolved case. Built:
allowed, like a note (FR14), and the case stays resolved. *Option A (recommended):* keep
it. *Option B:* refuse it, as creating is refused (open decision 1).

**17. Where Attach lives, and new words.** The design names the paste but not its place.
Built: **Attach an issue** among the case's actions, for anyone who may operate cases,
opening a panel. New fixed text needing the founder's eye: the attach panel's sentence;
"Sync with this case" and its warning; "Closed in GitHub, read … It does not change this
case"; "Not synced: the repository is public …"; "acme/api is public: anyone can read this
issue."; the settings screen's allow warning, "Public. nexovue creates no issue here unless
an admin allows it.", the mapping's empty line; the repository-public email; and the
timeline's "nexovue did not write to the issue: the repository is public", replacing "The
issue was not created: …" because a refused promotion shares that event. *Recommended:*
review once on the built screen.

**18. The mapping's service is typed, not chosen.** The settings form takes the service
name as text, because the screen holds no list of the project's services and reading one
is a telemetry read. *Option A (recommended):* keep it; F5 or a later step may add a
picker. *Option B:* read the project's services there.

**19. A repository first seen public owes no notice.** FR42 tells admins when the refresh
sees a repository *turn* public. Built: one already public when first listed is shown as
public and refused, with no email. *Option A (recommended):* keep it. *Option B:* tell
admins about every public repository the first refresh finds.

**From Step 5 (2026-11-09).** None blocks the commit; each is built as recommended.

**8. The 30-second rule schedules the read; it does not skip it.** The design's update
leaves alone a link read in the last 30 seconds, so a replay cannot cost more than two
reads a minute. Built that way, an issue closed within 30 seconds after a read waits for
the 5-minute poll, which breaks AC46's minute. Built: the read is set to the later of now
and 30 seconds after the last read, and only when that is sooner than it was; replays
then write nothing, and reads stay at most two a minute (tested both ways). *Option A
(recommended):* keep it, and amend the design's line. *Option B:* the design's rule, and
loosen AC46 to "within 60 seconds unless read in the last 30".

**9. A webhook revives no link that has no read scheduled, and keeps a link's backoff.**
The design excludes only `not_followed` and `stopped`. A link waiting for a person after a
refused write, or `gone` for good, also has no read scheduled; ringing it would retry a
write the design says waits for a person's Retry. Backoff is FR39's to lengthen. *Option A
(recommended):* keep it: a webhook only makes a scheduled read sooner. *Option B:* the
design's two exclusions alone.

**10. No handler seam for F5's `pull_request` events.** The plan and design name one;
F5's own design removed its use ("F22's webhook carrying a `pull_request` event", F5 D33:
poll only), and a seam no feature registers is unused code. *Option A (recommended):*
leave it out, and strike it from T5.1 and the design. *Option B:* build the registry now,
empty.

**11. A GitHub delivery is matched by the issue's id.** The design's parse names the
"repository id and issue number", while its update matches `external_id`, which the sync
stores as GitHub's REST issue `id`. Built: `issue.id` against `external_id`, the update's
reading, in one indexed comparison. Whether a delivery carries `issue.id` is unverified
until T5.2. *Option A (recommended):* keep it, and amend the design's parse line; if T5.2
finds no `issue.id`, match the repository's `external_id` and the number instead.

**From Step 4 (2026-11-09).** None blocks the commit; each is built as recommended.

**1. Creating an issue on a resolved case.** The spec does not say. Built: refused
with "This case is resolved. Reopen it to create an issue for it." *Option A
(recommended):* keep it: an issue for finished work would be closed at once. *Option B:*
allow it and close it straight after creation.

**2. Removing a link while its issue is being created.** Built: refused ("This issue is
still being created. Remove it once it has been."), so the job never writes an issue
nothing points to. *Option A (recommended):* keep it. *Option B:* allow it and accept an
unlinked issue.

**3. The member's line with nothing connected.** The design says "Ask an admin to
connect GitHub or Jira."; the founder's answer 4 of 2026-11-09 keeps Jira off screens
until Atlassian answers. Built: "Ask an admin to connect GitHub." *Option A
(recommended):* add Jira with Step 7, as for the rail.

**4. Words the spec does not give.** New fixed text, built and needing the founder's
eye: the close comments ("The nexovue case for this issue was resolved, so nexovue
closed it." and the false-alarm and new-normal forms); the three case emails and three
connection emails (`email/templates.py`); the link words for each failure by reader
(`link-words.ts`); "Asked for the issue. The link below shows when it exists."; and
Disconnect's count ("12 issues stop syncing. The issues stay in GitHub."). *Recommended:*
review the wording once on the built screen; nothing structural rests on it.

**5. AC4's "make no tracker call".** A recovery itself makes no call and changes no link
(tested); the active case's link is still read on its 5-minute poll, which is a call.
*Recommended:* read AC4 as "the recovery makes no call", as built; the alternative,
pausing reads while recovered, would hide a close made in the tracker.

**6. A `failing` connection keeps calling.** The design's step 5 says a run stops "if
not working". Built: a run goes on in `working` and `failing`, and stops in
`needs_attention`, `setting_up` or `disconnected`; otherwise a failing connection could
never recover by itself. *Option A (recommended):* keep it, and amend the design's
line. *Option B:* stop in `failing` and recover only by a person's test.

**7. A rate limit counts toward `failing`.** Five rate-limited runs in a row make the
connection `failing` (the state machine counts every passing failure). *Recommended:*
keep it: five straight limits is a real problem worth showing; the pause already obeys
`retry-after`.

**Step 3's, answered by the founder on 2026-11-09:** all four from Step 3 as recommended (option A each), kept below for the record. AC32 amended in `spec.md`; the beat clause and hourly refresh moved to T4.5 in `plan.md`; the *Session ended* row deleted and `jira` deferred to Step 7 in `design.md`.

**1. AC32's list of API modules allowed a network client.** AC32 says the list "holds
`billing/razorpay.py` alone". Evidence: `apps/api/src/nexovue_api/data/objectstore/store.py`
and `apps/api/src/nexovue_api/backups.py` import `boto3`, which the shared walker
(`nexovue_platform.import_walk.NETWORK_CLIENTS`) counts as a network client; both talk to
the install's own S3-compatible store, never a tracker. As written, the test fails on
today's code.
- *Option A (built, recommended):* the allowlist names all three, each with its reason, and
  any new client still fails the test. Amend AC32 to "a named list, each with its reason".
- *Option B:* narrow the test to HTTP libraries and leave `boto3` out, keeping AC32's words.
  It stops seeing a future `boto3` user that calls somewhere else.

**2. T3.12's beat clause ("no sweep job with none live") and the hourly refresh.** Beat has
no connection sweep to withhold until T4.5 builds `sweep_connections`, which the design also
makes the scheduler of each connection's hourly repository refresh. Built in Step 3: beat's
`connections_active`, which already follows `NEXOVUE_CONNECTIONS` and the live rows.
- *Option A (recommended):* move AC118's beat clause, and the hourly refresh, to T4.5, where
  the sweep exists; Step 3 refreshes on GitHub's install return, on Test and on request.
- *Option B:* build a refresh-only `sweep_connections` now and extend it in T4.5: two steps
  of work on one function, and a design detail (refresh inside `sync_connection`) decided
  early.

**3. The setup row "Session ended".** The design says: the sign-in screen, then "Sign in
finished, but setup must start again." As built, the shell's sign-in returns the person to
GitHub's return address, and the setup completes if it is the same person within the hour,
so that sentence would be false.
- *Option A (recommended):* keep it as built; delete the row from the design's table.
- *Option B:* force a restart after any sign-in, discarding a valid return.

**4. The rail's search words.** The design gives the Connections entry the keywords `github`,
`jira`, `issues`, `tracker`; T0.1 says nothing public mentions Jira until Atlassian answers,
and the console is published source. Built without `jira` (asserted by a test).
- *Option A (recommended):* add `jira` with Step 7, if it goes ahead.
- *Option B:* add it now.


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

- **Founder, 2026-11-08: T0.1 sent.** The founder emailed Atlassian's developer support the T0.1 question; no reply yet. Jira (Step 7) waits for a written yes (foundation item 17; at most 4 weeks, then GitHub only). **GitHub ships now.** Steps 3 to 6 must keep the connection layer kind-agnostic (one `connections` table with `kind`, shared sync machinery, per-kind adapters, foundation section 6), so Jira is an added adapter, not a rework.
- **Founder decision, 2026-11-08:** Steps 3 to 6 are built against a simulated GitHub; T3.0's manifest-flow trial and T5.2's real webhook are run by the founder after Phase 2 (foundation item 28). Step 7 (Jira) still waits on Atlassian's written answer.
- T3.0 and T3.9's hand checks (AC73) wait for the founder's real-GitHub run after Phase 2; T5.2 (a real webhook delivery, AC130) for the Oracle deployment.

## Risks

- **Nobody has asked a user.** Every need here is inferred. Whether status-only sync is enough is a guess. It should be in the founder's five to eight conversations.
- **F22 is a reason to stay, not to arrive,** by the research's own reading. Steps 1 and 2 are the value. A tracker is what makes it stick.
- **Jira's API details are second-hand** until T7.0.
- **The manifest flow from a private address is untried.** Step 3 was built without it, against a simulated GitHub (foundation item 28); the founder runs T3.0 after Phase 2.
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
- ~~A compromised `scheduler-connections` reads and writes all of Postgres until T8.7.~~ **Closed by T8.7 (2026-11-22):** it signs in as `nexovue_connections`; what it still reaches is in ADR-030.
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
