# Status: deployment-release-tracking (F28)

## Lifecycle status

**In development. Step 6 built 2026-11-22, awaiting commit.** (Step 5 built 2026-11-12, committed 2026-11-12.) The founder approved the specification, design and
plan on 2026-11-02. Steps 1a, 1b, 2 and 3 are on `feature/phase-2`; T2.10's timed new person is not yet run (founder
tests after Phase 2). Step 4 (the whole release page) and Step 5 (what other features need, and telling people) are
committed 2026-11-12, locally, not pushed. Step 6 (launch claims, and the deferred hardening) is built 2026-11-22 and awaits the coordinator's commit; every F28 step is now built.


## Current task

**Step 6 built 2026-11-22, awaiting commit** (*Step 6, built 2026-11-22*, below). No migration, no ADR. One defect fixed
(the `#deploys` links), the launch sentences written and checked, Playwright and the 375px check run, the errors read and
the 2-second target measured, a labelled demo recording made; judging the recording is the founder's.

**Step 5 built 2026-11-12, committed 2026-11-12** (*Step 5, built 2026-11-12*, below). Postgres migrations `0074`
and `0075`, on F4's `0073`; no ClickHouse migration; ADR-066.

## Step 6, built 2026-11-22

Launch: say only what is true (plan T6.1 to T6.3), and F28's deferred hardening (Playwright, Design QA at 375px, the
errors read at 24 hours, the 2-second target, the `tools/release-demo` judging), in `wt/f28s6` on `feature/phase-2`
(b4d7c87). No migration, no ADR. **Awaiting commit.** One code defect found and fixed (below).

### What was done, per task

- **T6.1, competitor rows** (`research.md`, new section 10). Every row re-read on 2026-11-22 from the vendor's own page or
  repository, each row saying *opened* or *extract*. This container could reach `docs.datadoghq.com`,
  `www.datadoghq.com`, GitHub's raw files and `ghcr.io` only; 12 other vendor hosts were refused by the proxy.
  - Opened: Datadog (deployment tracking, faulty deployment detection, price list: APM Pro is now $35 annual, $42
    monthly, was $31 and $36; "rollbacks detected" not found on the page), Sentry's documentation source, the SigNoz
    and Coroot repositories, the HyperDX changelog.
  - Extracts only (page not opened, host refused): ClickStack, Observe, Honeycomb, New Relic, Grafana, Dynatrace,
    OpenObserve. **Not refreshed at all: every price except Datadog's, Coroot's 30-minute and 3-minute figures.**
  - **SigNoz, settled by reading, not by installing:** deployment markers are an open, unmerged draft pull request
    (#13340, commits 2026-11-11) adding `POST /api/v1/annotations`. No released SigNoz found draws them, so no edition
    holds them yet. **Not installed:** an install of a release could only show nothing; remaining is a re-read when
    that pull request merges or closes.
  - **Coroot, installed once:** the Community Edition image (1.29.4, Apache-2.0, built 2026-11-18) was pulled and
    started; its binary contains deployment tracking, its screen and its Slack, Teams and webhook notices. **Not
    seen working:** it needs Kubernetes metadata through Prometheus, and this container has neither. Remaining: run
    it against a cluster, or read its licence manager for what it gates. Container and image removed.
  - **Second pass, 2026-11-22, `wt/f28fix`, after the founder allowed the vendor hosts:** the rows were opened from the
    vendors' own pages with `curl` (the session's page-reading tool still refused those hosts). **Ten of eleven rows are now
    opened**; Observe is not (`docs.observeinc.com` refused; `www.observeinc.com` redirects to
    `www.snowflake.com/en/product/observe/`, also refused). What changed is in `research.md` section 10, the short list:
    Datadog rollback detection exists but in a limited-access Preview product; SigNoz's own guide says it "detects a
    deployment each time this value changes" under "Deployment Markers", so the first pass's "no markers" statement is
    withdrawn; Coroot's pricing page lists deployment tracking as paid while its Community Edition binary contains it
    (not settled); Dynatrace's current page settles that OpenTelemetry-only versions do not reach its release inventory;
    OpenObserve has generic dashboard annotation sources. The claims check in `plan.md` stays unticked for Observe.
- **T6.2, the allowed and banned sentences** (`phase-2-foundation.md`, section 12, new subsection *F28: releases and
  deploys*): 11 allowed sentences, each with its source in the code and its limit; 15 banned, each with what in the code
  makes it false. Checking them against the source changed three things the plan's example would have said:
  - "A webhook adds same-version redeploys" is only true as a *deploy*: `_place_one` opens no new period for a version
    still in service, so a redeploy is stored, drawn as a marker and listed on the release, but is **not a new release and
    is not compared**. Sentence 2 says so, and "a redeploy is a release" is banned.
  - "Rollbacks detected" (Datadog) came off the competitor row as unconfirmed.
  - The demo answer "How much more disk? Not known yet" was stale: the cost report exists (about 10% on demo data), and
    sentence 11 carries it with its label. The table below is updated.
  "Seen is not a deploy" is kept (sentence 6 and the banned list).
- **T6.3, launch text and demo recording.** *The setup page* (`docs/product/setup/deploys.md`) was read in full against
  the code and needed no change; the release summary's sentences there stay true, and its real-channel sends are not
  run (below). *The changelog entry* has no file to go in (open decision 1): its text is under *Launch text*, below.
  *The demo recording:* see *The demo recording*, below.

### Playwright

F28 had **written no Playwright spec** (the earlier steps' notes said "deferred", and nothing was in `tools/e2e`), so
there was nothing "written but not run". `tools/e2e/tests/releases.spec.ts` and `support/releases.ts` are new.

- **The spec:** a new organisation opens `/releases` (empty state), exports two versions of one service through the real
  receiver, waits for the sync, reads the list ("Seen in traffic", not "Reported by your CI"), opens the release page
  (comparison, share of traffic, facts, no verdict words), mints a secret in Settings, sends a **signed deploy** as the
  setup page's recipe does and reads "Last deploy received"; then the same three screens at **375px**, asserting that
  no element sticks out past the right edge unless it sits in a scroller made for it (a check shown to flag a 600px
  element and to ignore one inside `overflow-x: auto`).
- **First run failed at the Deploys step, a real defect:** the two links that end in `#deploys` (the releases list's
  coverage strip, and the finding's deploy note) landed on Settings → Projects with
  every project **closed**, so the section the link named was not on the page. Root fix: `ProjectsScreen` opens the
  selected project when the address says `#deploys` and scrolls the section into view
  (`useOpenDeploysWhenLinked`); the anchor is one constant, `DEPLOYS_ANCHOR`. **Test seen failing first**
  (`ProjectsScreen.test.ts`: `expected ['false','false'] to deeply equal ['false','true']`), then passing; the second
  test shows nothing opens without the link. Two later failures were the spec's own (a wrong selector for the minted
  secret, which gave a 401; an expectation of loaded sections), fixed in the spec.
- **Results, last runs:** `releases.spec.ts` **1 passed** (57.9 s, with the stack's setup test), including 375px on the
  list, the release page and the Deploys section. Two specs that read F28's markers and an F28-fed screen:
  `dashboards-marks.spec.ts` and `errors.spec.ts`, **2 passed** (1.2 min). Run with `--trace off`, one or two workers,
  Chromium from the container (a symlink directory made the installed revision 1243 answer to the revision the project's
  Playwright wants, 1248; nothing in the repository changed for it).
- **375px, looked at, not only measured:** the list, the release page (opening sentence, tabs, comparison lines) and
  the Deploys section (webhook address wraps, the copy button holds, the secret row keeps its button) were read as
  screenshots at 375 by 812. **Not covered:** the finding section at 375px (it needs a finding, F6's detector to fire on
  seeded data, which was not set up) and the markers' hover and pinned box on a touch screen. **Design QA stays unticked** for them.

### Measurements, on demo data, on this machine

**Machine:** one Linux container, 4 vCPU (Intel Xeon, 2.1 GHz), 16 GB, shared with another build running its own stack at
the same time. ClickHouse 26.3.20.7 in Docker (5 GiB cap), Postgres 18, the API in Docker, one run per row below with
**cold** meaning ClickHouse's mark, uncompressed and query caches dropped before the call (the operating system's page
cache was not) and **warm** the median and slowest of 10 repeats straight after. **Seeded data:** one service, 49 hours
of spans ending now, versions `build-2001` for the first 24 hours and `build-2002` after, 1% of spans failing on the
first and 2% on the second, 6 exception types and one new on the second, plus logs at a tenth of the span count with 30%
exceptions, plus per-minute rollup rows that agree with them. The release's period starts 25 hours back. Inserted by
`INSERT … SELECT` into `spans`, `logs`, `service_versions_1m` and `service_stats_1m`, not through the receiver (the
sync needed a cursor set back 49 hours to walk that far, which was done in Postgres). **Not a server figure; demo data.**

| Read (24-hour window unless named) | 1 M spans + 0.1 M logs, cold / warm median (slowest) | 10 M spans + 1 M logs, cold / warm median (slowest) |
|---|---|---|
| The release, `GET /releases/{id}` | 22 / 22 (26) ms | 22 / 16 (24) ms |
| Comparison, 1 hour | 125 / 59 (268) ms | 53 / 57 (63) ms |
| Comparison, 24 hours | 226 / 152 (242) ms | 278 / 147 (513) ms |
| Rollout share, 24 hours (150 KB answer) | 458 / 140 (455) ms | 166 / 147 (489) ms |
| **Errors by version, 1 hour** | 146 / 71 (111) ms | 573 / 157 (251) ms |
| **Errors by version, 6 hours** | 160 / 96 (132) ms | 297 / 270 (352) ms |
| **Errors by version, 24 hours** | 301 / 135 (154) ms | **1,594 / 949 (1,224) ms** |

- **The errors read at 24 hours:** under a second warm and 1.6 seconds cold at 10 million spans for one service (about 57
  requests a second sustained), growing about with the rows read (10 times the rows, about 7 times the time warm).
  Against the reader account's **30-second limit** that leaves room, by straight-line arithmetic from these two points, up
  to a service of roughly 300 million spans in 49 hours (about 1,800 requests a second): an extrapolation, **not measured**. **A
  third point was attempted and failed:** a 50-million-span seed filled the shared disk (the `span_trace_lookup` view stores
  about as much again as `spans`), the insert did not finish and its data was dropped. The design's line ("if the
  measurement is poor, the section defaults to the 1-hour window") is **not triggered**: the default stays as built (the
  page's default is already one hour).
- **The 2-second target (AC67):** the page's first numbers come from the comparison. At 10 M spans the comparison read took
  53 to 278 ms cold and about 150 ms warm, so the read is far under 2 seconds. **Browser, first numbers after clicking
  a release row: 631 ms** (one run, from the click to the comparison's opening sentence being on screen) (on the demo-recording data, small, same machine; not on the 10 M data, which had
  been removed by then). So **the target is met on the demo, on this machine**; it is not a statement about a customer's
  data or a server.
- **Not measured:** more than 10 M spans for one service; many services at once; the other tables' pressure on the same
  ClickHouse; any figure on the Oracle server (foundation item 9).

### Found and fixed, and left as it is

- **Fixed:** the `#deploys` links (above). Files: `ProjectsScreen.tsx`, `DeploysSection.tsx` (`DEPLOYS_ANCHOR`),
  `ProjectsScreen.test.ts` (new).
- **Left, low:** `ReleasesScreen` and `FindingScreen` still spell the anchor as the string `"deploys"`: features may not import
  each other's modules, so the constant is not shared; a change of the anchor would need three edits and the new test names it.
- **Left, medium, the founder's to weigh:** the release page's *Share of traffic by version* answer is **150 KB** for 24
  hours of one service (about 1,440 minutes by 2 versions). It is read whole on every open. Fine here; worth a look at
  50 versions. Recorded, not changed.

### The demo recording

`release-demo-2026-11-22.webm` (1.2 MB, 1280 by 720, 28 seconds, no sound), kept outside the repository (the
repository holds no video) and sent to the founder to judge. **It is demo data and says so in every frame:** a bar along the bottom reads "DEMO DATA: traffic from
tools/release-demo (rolling v1 to v2), local development stack, 2026-11-22. Not a customer's traffic."

- **Traffic:** `tools/release-demo/send.py send rolling --minutes 16 --start-minute 5 --rollout-minutes 6 --rpm 120
  --old-failure 0.002 --new-failure 0.03`, through the real receiver, Kafka, the worker and ClickHouse: 960 requests and 4
  errors on `v1`, 943 and 42 on `v2`, 0 exports refused (`send.py`'s own ledger).
- **What it shows:** `/releases` with two rows, both "Seen in traffic", `v2` flagged "Failure share higher: 4.2% (23 of 553)
  against 0.6% (3 of 523)" (the stored reading the job wrote); opening `v2`; the comparison with its two blocks (*During the rollout*, *Since the rollout finished*) and the line "This compares numbers. It does not say why they moved."; the rollout share chart and
  its table; errors by version; the finding section ("No finding on this service started while this version served");
  the facts; the 6-hour window. Recorded with Playwright and Chromium against the stack's web server, signed in as the seeded
  development owner. The script is not committed (a throwaway in the scratchpad): the setup is the traffic command above
  and the page loads in `status.md`'s *Playwright* notes.
- **Read it with this in mind (found while recording, not changed):** `rolling` mode sends failures **without
  exceptions**, so *Errors by version* reads "No errors in either version during this window" beside a failure share of 4.2%.
  That is true of what that section counts (exception groups, like the Errors screen) and reads as a contradiction to a
  newcomer. `send.py ... exceptions` mode sends a `v2`-only exception and would fill the section; it was not recorded (16
  more minutes, and the same service name would have mixed the two runs). **Open decision 2, done 2026-11-22: the empty
  list now reads "No exceptions were recorded in either version during this window."**
- **Judging it is the founder's.** Nothing here claims it is good: it shows the screens read as built.

### Launch text

The changelog entry, as a draft for the release notes that `docs/changes/ready-to-deploy` generates at release. It says only what
section 12's allowed sentences say. **Not published, and not to be quoted before the release.**

> **Releases and deploys.** nexovue now shows each release of each service: when it started, how far it rolled out, and
> how its failure share and latency compare with the version before it. Releases appear on their own when your spans carry
> a build id in `service.version`. Reporting deploys from your CI adds what traffic cannot show: failed and rolled-back
> states, a commit link, and redeploys of a version that was already running. Request and error counts are taken before
> nexovue's own sampling; p95 is an estimate. Every comparison says how it was made, and says so when there is too little
> traffic to compare. A release's page compares numbers and does not say why they moved. You can switch on a summary of each
> release to one channel your team reads. Setup: *Releases and deploys* in the setup guide.

- **Found while recording, MEDIUM, fixed 2026-11-22 (open decision 3, done: see *Decisions 2 and 3, built 2026-11-22*):** the comparison's *Requests a
  minute* was the new version's count divided by the whole window, silent minutes included. The demo's sender stopped at
  08:04 and the page was read at 08:27, so `v2` shows 20.5 requests a minute and `v1` 19.4 when each took about 120
  a minute while traffic flowed. For a service that never goes quiet the figure is right; for one that does (a worker, a
  nightly job, the demo) it understates both and the pair still compares fairly. Recommendation: divide by the minutes
  that had any traffic, and say so, or drop the row when the version was idle for more than a tenth of the window.
- **Self-review (`/review-diff`), LOW, left:** `useOpenDeploysWhenLinked` scrolls again whenever the opened project changes
  while the address still ends in `#deploys` (opening another project by hand scrolls to its Deploys section too); a
  member who cannot manage credentials gets the project opened and no section (it is not rendered for them). Neither
  loses anything. No other unintended files: the diff is the three web files, the new spec and support file, the demo
  video, and documents.

### What is the founder's

- **Judge the demo recording** (T6.3 says "proved by review"; the review is the founder's).
- **T2.10's timed new person** (the five-minute setup), and **Steps 3 and 4's Design QA** with a real finding: both as
  before, after Phase 2.
- **A real send of the release summary** through Slack, Teams, Discord and email: only the delivery path's fakes ran.
  Until then section 12 forbids saying a channel works.
- **Whether the released SigNoz and Coroot rows still stand at announcement:** re-read, if the announcement is after
  2026-12-06 (two weeks from this refresh), and run Coroot against a cluster if a claim turns on its gating.
- **Real GitHub and a hosted runner running a recipe** (as before, foundation item 28).

### What did not run here, and why

- `just check`, the Go suites, and the live Python suites: the coordinator's, and no F28 Python or Go code changed. The
  Python and TypeScript checks named in the brief ran: `uv run mypy .` (1,287 files) clean; `ruff check .` and
  `ruff format --check .` clean (1,700 files); `pnpm -r --if-present run typecheck` clean; `biome check` on the touched
  folders clean at error level (four older warnings in other specs, not touched); the web suites for releases, projects
  and findings: 28 files, 531 tests passed.
- **The environment** was unkind: this container's one disk (about 39 GB in all) was shared with another build and was
  filled to 0 MB free four times; once by this step's 50-million-span seed, which was then removed, and the rest by what
  another build held. It broke one write of `research.md` (restored from git and written again), stopped Kafka, and lost
  the image store partway, so the images could not be rebuilt (the container's TLS certificate authority is not trusted by
  the image builds). The receiver, the worker, the API, the scheduler and the web server were then run from source as host
  processes against Docker's stores, as the Step 1a cost run was. All figures above came from the first, container, stack,
  except the browser figure and the recording.

## Step 5, built 2026-11-12

What other features need, and telling people (plan T5.1 to T5.7), in `wt/f28s5`. What exists now, read from the code:

- **One deploy fact, one history** (T5.1): `nexovue_platform.releases` holds `DeployFact` (ids, the sender's time and
  ours, state, commit, `basis` always `reported`; no `metadata`, `url`, `source` or `delivery_id`) and `DeployHistory`
  with `EMPTY_DEPLOY_HISTORY`, moved from `deploys/service.py`. The API's `history_for_window` builds facts; errors,
  F20's panel scans (one import line) and the scheduler's facts use the one type. The comparison's ClickHouse column
  lists and environment expression are shared fragments (`VERSION_MINUTE_COLUMNS`, `SERVICE_MINUTE_COLUMNS`,
  `SPAN_ENVIRONMENT`) with their row converters; the API's statements and the scheduler's are equal word for word.
- **The comparison for the scheduler** (T5.2): `facts.release_comparison(store, connection, ...)` reads the period, its
  previous version's start, the settled minute and the plan's derived retention (`project_retention`) from Postgres,
  and the version minutes, totals and environment labels through `releases/store.py`'s new project-bound reads; then
  the shared `choose_method`. `ComparisonFact` carries no other version names and no sender text.
- **No AI** (T5.3): F28's packages in every process are walked by `test_detection_has_no_ai.py`.
- **MCP and `GET /deploys`** (T5.4): `list_deploys` sends `DeployRow`, exactly `id`, `version`, `service_name`,
  `deployed_at`, `commit_sha`, `state`, `received_at`, and the envelope says `partial` when the read was capped
  (`bounded(..., partial=)`). `DeployEventSummary` lost `source` and `metadata` and gained `received_at`, `state` and
  `commit_sha`.
- **The hostile fixture** (T5.5): `apps/scheduler/tests/hostile_release_names.py`, versions that pass the name check
  and free text a sender can write; stored live and never read back into a fact.
- **The stored reading and the summary** (T5.6): `releases/summary.py`'s job, on the `releases` queue, added by the
  sync for each period it opens (timed from the newest minute read, not a clock) and by the webhook for each period a
  report opens; a start that moved later, or a comparison under 5 settled minutes (for at most 30 minutes more), puts
  it back. It stores `last_reading` (the leading block's method, words and counts, or the reason; never a name) and
  `mixed_environments`, and with `projects.release_summary_channel_id` set writes one `release_summary` notice on that
  channel, joined by organization and only if people read it, `sequence` 0, `ON CONFLICT DO NOTHING`. A period
  `already_running` gets neither. Delivery reads the release, versions and reading after the claim on its own
  connection and retries the batch if that read fails; the words are `nexovue_platform.releases.summary_lines` with
  "Order only, not cause", coloured neutral; "3 release summaries" when only summaries are due. `PUT
  /projects/{id}/release-summary` under `CONFIGURE_PROJECTS` refuses another organization's channel and PagerDuty
  and webhook channels (422), audits `project.release_summary_set`, and `GET /projects/{id}` returns the value. The
  list shows *Failure share higher* and *p95 higher* with counts in critical text, and "Versions were combined on X:
  too many in one minute", from Postgres alone. The Deploys section shows FR45's warning from the stored readings and
  the opt-in, listing only channels people read.
- **Documents** (T5.7): `database.md`, the deploy-events spec, IA3's note, the catalogue, the setup pages
  (`deploys.md`: the summary and who sees sender text; `api.md`), design section 6's *as built* rows, ADR-066.

**Readings made while building** (none blocking; each open to overrule, also under *Open decisions*):

- **The claim reads no release table; delivery does.** Design section 6 joined `release_periods` and `releases` in
  `_TAKEN`. Detection imports the outbox, and T1.12's isolation test (AC42) refuses any module it reaches that names a
  release table; it failed on that join. The read moved to `delivery.py`, after the claim, as FR44's is (ADR-066).
- **The time method's reading is stored and summarized, but never flagged.** FR55 reads the flag from the two version
  methods; a by-time summary carries the design's "a rolling deploy mixes old and new" sentence.
- **The job is added by the webhook as well as the sync.** Design section 6 names the sync; a period a report opens is
  not opened by the sync, so without the webhook's job a reported-only release would never be summarized (AC82: every
  period).
- **A failed job is retried twice, then gives up silently**: that period has no reading and no summary.

**Tests, each seen failing first** (the behaviour removed, the test run and seen failing, the code restored and checked
byte-equal by a script; outputs in this session's scratch space, `seen-failing*.log`):

- `test_deploys.py`, `test_errorgroups.py` (re-pointed): a deploy at the instant no longer preceding it failed two;
  the `known_from` guard removed failed the overflow test only once it held a deploy older than the cap's reach (it
  passed before: strengthened).
- `test_release_facts.py` (new, 8): a `metadata` field on `DeployFact` failed the allow list and the free-text test;
  `source` selected by `_DEPLOYS_IN_WINDOW` failed the statement test; versions combined forced false, and two labels
  read as one, each failed; the store handed another project failed.
- `test_releases.py` (+1): the scheduler's totals statement written without the shared fragment failed.
- `test_release_facts_live.py` (+3): the window read without its organization and project failed AC77's; seen
  starts with `already_running` failed; `source` read as the commit failed the hostile test.
- `test_detection_has_no_ai.py` (+1): `import mcp.server` in `summary.py` failed.
- `test_mcp_tools.py` (+3): a `source` key added to the row, `partial` ignored by `bounded`, and `partial=False` in the
  tool each failed. `test_mcp_injection.py` judges `DeployRow`.
- `test_releases_live.py` (+1 test, AC76 extended): `source` put back on `GET /deploys` failed; flags from the time
  method failed AC81's list test.
- `test_platform_releases.py` (+8): a version name in the stored reading, flags by time, and reason words replaced
  each failed.
- `test_alert_messages.py` (+6): no "Order only, not cause", no `one_line`, amber in place of neutral, and no
  summaries-alone shape each failed.
- `test_release_summary_live.py` (new, 11): the summary written for an `already_running` period, to a PagerDuty
  channel, twice on a retry (no `ON CONFLICT`), before its 30 minutes; a name stored; the release not named at
  delivery; an unread release sent anyway; the outbox's order without the period (with the second summary's
  `created_at` later: an equal one passed, strengthened); the comparison without its tenant binding; each failed. In
  the database, `release_summary_names_its_period` dropped failed the refusal test (an `opened` row naming a period;
  the first version, which `names_one_subject` also refused, passed: rewritten), the project key without `release_period_id
  IS NULL` failed "told once", and the channel's foreign key without `SET NULL` failed the deletion test; each put
  back and checked with `\d`.
- `test_release_sync_live.py` (+1), `test_deploys_live.py` (+1): the sync's and the webhook's job left out each failed.
- `test_projects.py` (+11): the people-read check, the audit row, the policy (`CurrentIdentity`) and the detail's value
  each failed; `test_authorization_coverage.py` failed with the policy changed; `test_alert_vocabulary.py` failed
  with the kind left out of `0074`'s copy; `test_cross_service_contracts.py` failed with the task module unregistered.
- `test_detection_isolation.py` failed on the design's `_TAKEN` join (the reading above); `test_release_words.py`
  failed on the scheduler's environments read (now named in its allow list); the scheduler Postgres tenancy test
  failed on a read of `plans` (now `project_retention`).
- Web: `DeploysSection.test.ts` (+4) failed with FR45's warning hidden, machine channels offered, and "Off" saved as
  "off"; `ReleasesScreen.test.ts` (+3) failed with no flags, dim flags, and no combined state. Clocks pinned
  (`vi.useFakeTimers({ toFake: ["Date"] })`); Python decisions take `now` built from the rows.

**Counts.** Offline, every store at a closed port (`apps/api`, `apps/scheduler`, `packages/py-platform`,
`packages/contracts`, `tools/release-demo`, `tools/detection-seed`, `-n 2`): 11,876 passed, 125 skipped, 0 failed.
Live, on this session's own Postgres and ClickHouse (containers `f28s5-pg`, `f28s5-ch`, ports 35432 and 38123, each
port checked before every run): `test_releases_live`, `test_deploys_live`, `test_errorgroups_live`,
`test_findings_api_live`, `test_deletion_live`, `test_projects_live`, `test_channels_live`, `test_release_facts_live`,
`test_release_summary_live`, `test_release_sync_live`, `test_alert_delivery_live`, `test_alert_outbox_live`,
`test_outbox_golden_live`, `test_detection_release_neutrality_live`, `test_detection_prune_live`,
`test_slo_prune_live`, `-n 2`: 270 passed; the three suites that write the settled minute, twice more in parallel:
67 passed each. A first parallel run failed one summary test: two suites committed the install-wide settled minute
at once; both now hold one advisory lock while they own it (`apps/scheduler/tests/settled_minute.py`). Migrations
`0074` and `0075` (then numbered `0078` and `0079`) applied, `0075` reverted and reapplied, `0074`'s downgrade refused as written. Web, whole suite:
2,301 passed (183 files). `uv run mypy .` (1,009 files), `ruff check .`, `ruff format --check .`,
`pnpm -r --if-present run typecheck`, `biome check` on the touched folders and `verify-tokens.js`: clean. Go not
touched.

**Not run (deferred to hardening):** Playwright, a browser or phone check of the list's flags and the Deploys section,
Design QA at 375px, the *Judgeable* run with `tools/release-demo` (one notice after a demo rollout), a real send to
Slack, Teams, Discord or email (only the delivery path's fakes ran), and `just check` (the coordinator's).

**Found in the self-review, left as they are** (none blocking):

- **Low:** the summary job and the sync hold a Postgres connection open while ClickHouse answers (up to 10 seconds a
  read, three reads), as the sync already did.
- **Low:** the outbox's PagerDuty and burn statements still match subjects by incident and finding alone; a summary
  never reaches PagerDuty and is never news, so none of them sees one.
- **Low:** a project whose chosen channel is muted gets its summaries dropped as any alert is, and recorded so.

## Step 4, built 2026-11-12

The whole release page (plan T4.1 to T4.7), in `wt/f28s4`. What exists now, read from the code:

**The precondition, checked in the code.** The browser-door change (`docs/changes/browser-door`, done 2026-11-07)
holds: `traceassembly/stages.go` `countsInRollups` keeps a `DOOR_RUM` span out of `counters.Observe`, the one point
that feeds `service_stats_1m`, `endpoint_stats_1m` and `service_versions_1m` (the version map is fed from
`observeService` behind it); `traceassembly/browserdoor_test.go` asserts all three tables, and
`apps/api/tests/test_browser_door_live.py` names `service_versions_1m` among its rollups (`ROLLUPS`). The interim
`countedVersion` branch in `flatten.go` is gone (`derive.countedVersion` is an unrelated map key).

- **The comparison** (T4.1): `GET /releases/{id}/comparison` gains `incomplete` and `minutes_differing` (FR28, per
  minute over every compared stretch, a minute only one table holds counting too), `versions_combined` (FR17,
  `__other__` in a compared minute), `mixed_environments` (FR45, `ENVIRONMENTS_SQL` over stored spans, reading
  `deployment.environment.name` and falling back to the older `deployment.environment`, as the profile writer does),
  and `finding` (FR22). Each block names the previous side's stretch (`previous_from`, `previous_to`) and, for the two
  before-and-after methods, `last_week` (FR25) from `service_stats_1m`, null where expired. The time method now reads
  `service_stats_1m` (`SERVICE_TOTALS_SQL`), as design 4 step 6 says. Pure parts are in
  `nexovue_platform.releases`: `ServiceMinute`, `minutes_differing`, `versions_combined`, `last_week_of`,
  `compared_spans`, `window_for_finding`, `reaches`.
- **The window from a finding** (T4.1, FR22): `?finding=` (a finding of the release's project, else 404) without a
  `window` takes the smallest offered window reaching the finding's start, and the answer says whether its window
  reaches it. The web's own copy of that rule (`windowForFinding`) and the link's `finding_at` are deleted, so one
  rule decides.
- **FR46** on `GET /releases`: `naming_differs`, one grouped Postgres statement over the last 24 hours; the list
  replaces a `not_seen` row's words with "Your CI and your traffic name versions differently: CI sent X, traffic
  sends Y".
- **The door on raw rows** (T4.2): ClickHouse `000041_door_on_spans` and `000042_door_on_logs` (a migration here is
  one statement), `LowCardinality(String)`, outside the sorting keys. The worker writes `consume.StoredDoor`:
  `browser` for `DOOR_RUM`, `server` otherwise, the words `service_edges_1m.door` already uses; `''` is a row written
  before. Applied, reverted and reapplied on this session's ClickHouse.
- **Errors by version** (T4.3, FR29 to FR31, FR51): `GET /releases/{id}/errors` over `ERRORS_BY_VERSION_SQL`, built
  in `errorgroups/queries.py` from the errors list's own occurrence rows: this version from the period's start, the
  previous version over the same length before, split at the start; per-table version predicates; a door predicate
  that leaves browser rows out for a release counted in server traffic (one never seen reads them, labelled "from the
  browser"); up to 100 groups. A timeout (ClickHouse code 159) answers `timed_out`; raw rows older than the project's
  raw retention answer `aged_out_on`; `keep_errors: false` answers `may_be_incomplete`.
- **Rollout share** (T4.3, FR26): `GET /releases/{id}/rollout`, the four busiest series by name then "other
  versions", with no version and `__other__` as labels.
- **Sender details** (T4.3, FR54): `GET /releases/{id}/sender-details` takes `CurrentIdentity`, so a token or a ticket
  is refused (403 for a token); `metadata` only for owners, admins and members (`READS_SENDER_METADATA`, D46), a
  viewer reading `metadata_withheld`.
- **The release** (T4.4): `GET /releases/{id}` gains `deploys` (id, the sender's time, our arrival time, state,
  commit) and, per period, `later_findings` on its service from the start's own minute to its end, up to 50 (AC41).
- **The page** (T4.5): the sentence, warnings, the window's sentence, blocks with a last-week column (a line of its
  own on a phone) and FR49's caution with links to errors by version and the service's page for the same minutes,
  the rollout chart (`TrendChart`'s existing `distinctColours` mode, F20's, so `TrendChart` did not change) with a
  table of the latest shares, errors by version with the caution beside the list, "Started after this release", the
  facts led by "Link from your CI" (https only, `noreferrer`, `no-referrer`), each deploy's two times when over 15
  minutes apart, and what the CI sent, metadata in a closed `<details>`. Each section loads and fails on its own; a
  missing release reads "No such release". `/releases` gains the coverage strip (services with coverage `none`).
- **Retention** (T4.6): `releases/retention.py`: deploys by `created_at` and releases by `last_activity_at` past the
  project's derived retention, the shortest plan's first; both for a project or organization deleted longer ago than
  the shortest plan's derived retention. `jobs/expiry.sweep` now guards each table: a failing one is logged by name and
  the rest go on.
- **The words and raw reads** (T4.7): `apps/api/tests/test_release_words.py`.

**Readings made while building** (none blocking; each open to overrule):

- **Errors are compared over equal lengths:** this version from the start to the window's end or now, the previous
  version over the same length before the start. The design says "the same durations" without placing them.
- **The rollout read spans the comparison's read** (an hour, or the window, before the start, to the settled minute).
- **A finding that started before the period** gives no window and no sentence; its "Back to the finding" link stays.
- **The version predicates in `ERRORS_BY_VERSION_SQL` only narrow the read:** each side's count already requires its
  version, and `HAVING` drops empty groups, so removing them changes no answer (seen in the removal run, below).

**Tests, each seen failing first.** All were written after the code and checked by removal: the behaviour taken out,
the test run and seen failing, the code restored (outputs in this session's scratch space):

- Live, `test_releases_live.py` (+16): without per-minute completeness, AC36's failed `(False, 0) != (True, 1)`;
  without combined versions or the environment read, AC67's failed; without last week, AC29's; with the finding
  ignored, AC64's `(1, False) != (6, True)`; with FR46's tally at one, AC78's listed `payments`; without the door
  predicate, AC69's listed `FromAPage` as new; without the sampling flag or the aged-out check, FR31's and FR30's;
  without the metadata gate, AC76's (a viewer saw it); without the minute floor or the period's end, AC41's; without
  grouping the rest, AC65's named v3 and v4; with no deploys, the facts test; without step 5, AC32's read
  `no_version_no_deploy`; without the serving window, AC23's read `idle`; with every read error treated as failure,
  AC68's timeout test. **Not seen failing:** removing the version predicates (above): they are pruning only.
- `test_platform_releases.py` (+9): the time method on version rows, the previous stretch, the smallest window and
  last week on side by side each failed.
- `test_release_sync_live.py` (+4, AC74): release retention left out of the sweep, deploys lapsing by the sender's
  time, the deleted-project clause, the per-table guard, and the project's own retention (replaced by 30) each failed.
- `test_release_words.py` (6): "impact" in a screen string, "Caused by" in a response, "Due to" in the setup page, a
  scheduler statement reading `spans`, and a duration in `ENVIRONMENTS_SQL` each failed.
- Go: `flatten_test.go` and the log `flatten_test.go` failed with the door left unset (`""` for every door);
  `block_test.go` failed with the column left out (`30 columns, the table has 31`); `consume/door_test.go` failed
  with the browser word changed.
- Web: `ReleaseScreen.test.ts` (new, 19) and `ReleasesScreen.test.ts` (+2): every reading dim (AC84), the caution
  always shown (AC79), no last-week line (AC65), the environments warning dropped, the finding not sent (FR22), the
  link's `rel` and `referrerPolicy` dropped (AC44), any url linked, the timeout ignored (FR51), FR46's words unused
  (AC78), `__other__` labelled as other versions (FR26), the viewer's withheld metadata shown, later findings not
  listed, and the coverage strip unfiltered each failed. `comparison.test.ts` lost the window-choice tests with the
  rule it held, and gained the warnings, FR49 and last-week tests. The old page tests in `ReleasesScreen.test.ts` moved
  into `ReleaseScreen.test.ts`. Every test pins the clock (`vi.useFakeTimers({ toFake: ["Date"] })`).
- `test_releases.py` (+8): the error kinds, the rollout's labels, the metadata roles, and no sender text on the new
  models, written after the code. `test_read_surface_structure.py` failed naming `get_errors`, then passed with them
  listed and in `setup/api.md`; `sender-details` is not on the surface, as a person-only route.

**Counts.** Offline, every store pointed at an unreachable port so live suites skip on purpose (`apps/api`,
`apps/scheduler`, `packages/py-platform`, `packages/contracts`, `tools/release-demo`, `tools/detection-seed`, `-n 2`):
11,817 passed, 124 skipped, 5 failed. `test_read_surface_structure.py` failed on the two new reader routes (its
seen-failing record); with `get_errors` and `get_rollout` added it passed. `test_install_secrets.py`'s 4 read the
offline run's own `NEXOVUE_KAFKA_BROKERS`; without it, both files: 114 passed. A first run with this session's stores
reachable also ran other features' live suites against these bare stores (no gate seed, no Kafka): `test_services_live`
and `test_llm_observability_live` errored for that reason, not run further. Live, on this session's own Postgres and ClickHouse (containers `f28s4-pg`, `f28s4-ch`,
ports 35432 and 38123): `test_releases_live.py`, `test_errorgroups_live.py`, `test_findings_api_live.py`,
`test_deploys_live.py`, `test_deletion_live.py`, `test_release_sync_live.py`, `test_release_facts_live.py`,
`test_detection_prune_live.py`, `test_slo_prune_live.py`, `test_detection_release_neutrality_live.py`: 189 passed. Go,
`apps/workers`: 28 packages ok; `go vet` and `golangci-lint` clean. Web, the whole suite: 2,294 passed (183 files),
browser SDK 124. `uv run mypy .` (1,002 files), `ruff check .`, `ruff format --check .`,
`pnpm -r --if-present run typecheck`, `biome check` on the touched folders and `verify-tokens.js`: clean.

**Not run (deferred to hardening, as the brief says):** the errors read's cost at 24 hours on seeded data, and the
2-second first-numbers target (AC67): both measurements; Design QA at 375px and in a browser (AC75 beyond what jsdom
can show); Playwright; the *Judgeable* run with `tools/release-demo` and the full dev stack; `just check` (the
coordinator's); `test_erasure_live.py` (no table added).

**Found in the self-review, left as they are** (none blocking):

- **Low:** `releases/service.errors_by_version` proves the project readable twice (once through the release), one
  extra Postgres read per request.
- **Low:** opened from a finding, the window tabs show "1 hour" selected until the comparison answers with the window it
  chose.
- **Low:** the token-refusal live test reaches the app's dependency overrides through the client's private transport.
- **Not measured:** the comparison now reads up to four statements (versions, totals, last week, environments), and the
  environments read scans one service's stored spans over the compared stretch, up to about two days.

## Step 3, built 2026-11-09

Markers that can be read, on every chart, including the finding (plan T3.0 to T3.9). What exists now, read from the
code:

- **Two reads** (T3.1): `GET /releases/markers` (`project_id`, `from`, `to`, optional `service`; the newest 500 with
  `truncated` and `cap`) behind `releases.service.markers_in_window`, the function F20 calls (D30). Postgres only. A
  mark is each reported deploy (with its state, same-version redeploys and failed ones included) and each counted
  period's start, except an already-running one; a reported period's start is its deploy's, so it is not drawn twice.
  Fields exactly `release_id`, `period_id`, `deploy_id`, `service_name`, `version`, `at`, `basis`, `state` (AC45).
  `GET /releases/coverage` (`at` defaults to now; optional `service`): each service that sent telemetry
  (`service_last_seen`) or has a release, with its value, last report at or before `at`, and `first_heard_at`. Both
  call `require_readable_project`, are on the read surface and in `setup/api.md`.
- **One coverage rule** (T3.1): `nexovue_platform.releases.coverage_of(first_reported, first_seen, *, at,
  last_reported_at)`, used by the API (`releases.service.coverage_at`, `CoverageBook`) and the scheduler
  (`releases/facts.coverage`). Any report counts, a failed one included: it is still deploy information.
- **Charts** (T3.2 to T3.4): `shared/deploy-markers.ts` (`releaseMarkers`, `markerTag`, `clusterMarkers`, the
  group's line and the cap sentence); `TrendChart` keeps one neutral dash per mark and adds `ReleaseStrip`, HTML tags
  above the plot placed by `valToPos` after each draw ("failed", "rolled back", "seen", "3 releases" within 8 px, a
  short mark otherwise); hover or focus shows `ReleaseHoverBox` in a portal, a click pins it with its links, Escape or
  the close control closes it; drag still selects. `ChartReleases` under each chart group: the always-visible line, the
  cap sentence, "Releases could not be loaded for this chart" with a retry, and the text list (closed by default, open
  on the finding page). Overview, the service page and the finding's charts read the window on screen through
  `shared/releases/api.ts`; the old `GET /deploys` hooks are deleted.
- **The finding** (T3.5, T3.6): `FindingDetail.release_context` (`before` at any age, the service's coverage as of
  the start with the last report and `first_heard_at`, `nearby` in the 2 hours before with this service first and
  `called` from `service_edges_1m`, `called_known`, and a project finding's `affected`, the latest per service in 24
  hours). The edges read failing leaves the list unmarked. *Release before this* under the header follows the wording
  table, with "It came first. That is not evidence that it caused this." exactly on the rows naming an earlier release,
  the setup link for `manage_credentials` and the sentence for others, the docs link for all; the link carries
  `finding` and `finding_at`, and the release page then compares over the smallest window reaching the finding and
  says so, or says it ends before it (FR22, AC64), with "Back to the finding". MCP `get_finding` labels the six new
  name fields untrusted (`assistant/tools.py`).
- **Errors** (T3.7): `ErrorGroupSummary.deploy_coverage`, from the group's services as of `first_seen` (the
  better-heard service wins); the cell reads `after v2.1`, "none before", "none reported" or "not told", the unknown
  mark only when unread; the panel always states the deploy line and the coverage words.
- **FR44** (T3.8): `nexovue_scheduler.releases.facts` (`coverage`, `release_before`, `releases_near`, `PeriodFact`,
  D56). `alerts/delivery.with_release_line` reads them after the claim commits, on its own connection, under
  `asyncio.timeout(0.5)` including the wait for a connection, only for a batch of one `opened` service finding; on any
  failure it logs "release line left out" and sends without it. `messages.RELEASE_FACTS` carries plain values, and
  `_compose_finding` renders "Most recent release on this service: v2.1, 6 min before this. Order only, not cause." or
  "We receive no deploy information for this service." in `details`, so every channel, PagerDuty included, carries it.
  `messages.py` imports nothing from `releases` (`test_detection_isolation.py` unchanged and passing).
- **Neutrality** (T3.9): `test_detection_release_neutrality_live.py`.
- **The seed** (T3.0): `tools/detection-seed/seed.py` writes `service_versions_1m` split from each service minute
  (rollouts of 10 minutes from a counted version), and the releases, periods, reported deploys and sync position.

**The carried-over fix (Step 2's low finding 1; founder answer of 2026-11-09).** `first_reported_at` stays the
release's reported fact (any state; the record's `seen_or_reported` check and the page's facts rest on it). Its two
readers changed: the sync's "already running" now asks for the earliest report of a deploy that went out
(`_KNOWN_RELEASES` reads `min(deployed_at)` over `started` and `finished` deploys, `KnownRelease.first_went_out_at`),
so a version reported only as failed before its first counted minute is still already running; and the comparison's
time method takes its moment from the compared period's reported start (`releases.reported_moment`), not the
release's first report, so "failed at 10:00, finished at 11:00" compares from 11:00. The second also fixes a return's
period, which was compared from the release's first report. The states that go out are one constant,
`nexovue_platform.releases.OPENING_STATES`, used by the API and the sync (the third low finding's two copies are now
one, beside `DEPLOY_STATES`).

**Readings made while building** (none blocking; each open to overrule):

- **The design's `PeriodFact` gains `already_running`.** FR44's line must not call a version already running when
  counting began "the most recent release". It is a boolean fact, no text; F3's T1.4 sees it.
- **FR44's failure is a log line, not a metric.** Delivery runs in a job's child process, whose metrics no scrape
  sees (Step 1b's reading for the sync).
- **The service page's chart axis now reads in the project's zone**, as its hover box and its detection strip do; it
  was the browser's. The finding page already did.
- **Error groups read every release's first times for the project on each list and detail request** (one grouped
  statement over at most 50,000 rows), since the services are known only after the ClickHouse read. Not measured.
- **The hover box opens under its tag in viewport pixels**; a pinned box does not follow the page when it scrolls.
  Design QA should judge it.
- **`fault.py` still writes `service_stats_1m` only**, so its minutes have no version rows: the comparison will read
  them as incomplete once T4.1 builds that check. The seed's period ends approximate the sync's rule (the next rollout's
  end), which is enough for demo data.

**Tests, each seen failing first** (outputs in the session's scratch space):

- Carried-over fix: `test_release_sync_live.py` (+3): the `failed` and `rolled_back` cases failed (`already_running`
  false), then passed; the `finished` case guards the other side. `test_releases_live.py`: the no-version comparison
  failed (`too_little_traffic`, its "before" side outside what is read), then passed. `test_platform_releases.py` (+9,
  coverage and `reported_moment`): collection failed on the missing names, then passed.
- `test_read_surface_structure.py` failed on the two new routes, then passed with `setup/api.md`. `test_mcp_injection.py`
  failed on the six unlabelled `get_finding` fields, then passed.
- `test_errorgroups.py` (+7), `test_alert_messages.py` (+7), `test_seed.py` (+5), `deploy-markers.test.ts` (11),
  `finding-page.test.ts` (+14), `comparison.test.ts` (+3), `error-groups.test.ts` (+3): each failed on the missing
  functions, then passed.
- Written after the code, then checked by removal (each failed with the line taken out, passed with it back):
  `test_releases_live.py` markers and coverage (+5: the already-running filter, the time bound on the last report,
  the cap, `service_last_seen`); `test_findings_api_live.py` (+5: this-service-first, the callee mark, latest per
  service, the `<= at` bound); `test_release_facts_live.py` (10: the delivery call, the timeout and the catch);
  `test_detection_release_neutrality_live.py` (1: a rollup row added with the release made it fail);
  `OverviewView.test.ts` (5), `ChartReleases.test.ts` (5: the pin, Escape), `ReleaseHoverBox.test.ts` (2: markup
  instead of text), `FindingScreen.test.ts` (+4: the section, `open`, the permission), `ReleasesScreen.test.ts` (+1:
  the finding's window), `ErrorsView.test.ts` (+1: the cell).

**Counts.** Offline (`NEXOVUE_SKIP_LIVE_TESTS=1`), `apps/api`, `apps/scheduler`, `packages/py-platform`,
`packages/contracts`, `tools/detection-seed`, `tools/release-demo`: 10,105 passed, 107 skipped, and the one MCP
failure above, fixed (`test_mcp_injection.py` and `test_mcp_tools.py` then 106 passed). Live, on this session's own
Postgres and ClickHouse (ports 25432 and 28123, since removed): `test_releases_live.py`, `test_findings_api_live.py`,
`test_deploys_live.py`, `test_errorgroups_live.py`, `test_deletion_live.py`, `test_release_sync_live.py`,
`test_release_facts_live.py`, `test_detection_release_neutrality_live.py`, `test_alert_delivery_live.py`,
`test_alert_senders_live.py`, `test_alert_outbox_live.py`, `test_detection_project_job_live.py`,
`test_detection_notify_live.py`: 240 passed. Web, the whole suite: 1,798 passed (151 files). `uv run mypy .` (870
files), `ruff check .`, `ruff format --check .`, `pnpm -r --if-present run typecheck`, `biome check` on the touched web
folders and `verify-tokens.js`: clean. The seed run against the same stores: 76,594 service rows and 77,019 version
rows over two weeks and four services, no minute differing; 67 releases.

**Not run:** `just check` (the coordinator's); Design QA at 375px and in a browser (the strip, the hover box, the
finding section; one instant reading the same on the finding and release pages, AC75): not yet run, founder checks
after Phase 2; the full dev stack (the judgeable "trigger a seeded finding and read the line in its notice" needs the
receiver, worker and scheduler running); Go (no Go file changed); `test_erasure_live.py` (no table added).

## Step 2, built 2026-11-08

A person can set it up (plan T2.1 to T2.9; T2.10 below). What exists now, read from the code:

- **The record** (T2.1): Postgres migration `0057_deploy_envelope_and_sealed_secrets` (revision "0057", down
  revision "0054" for now: 0055 and 0056 are held by parallel work, so the coordinator re-chains it). `deploy_events`
  gains `state`, `commit_sha`, `url`, `release_id` and `webhook_secret_id` (both `ON DELETE SET NULL`), `delivery_id`
  NOT NULL at 200 with a plain unique key, and indexes on `created_at`, `release_id` and `(webhook_secret_id,
  created_at)`; `project_webhook_secrets` swaps `secret` for `sealed_secret`, `secret_hint`, `last_used_at`;
  `deploy_intake_state` is new, in `organizations/tables.py`. Indexes are built concurrently and the new keys and
  check validated after `NOT VALID`. Existing secrets and id-less deploys are deleted (pre-deployment). Roll
  forward only, listed in `test_migration_rules.py`.
- **The webhook** (T2.2): `POST /deploys/webhook/{project_id}` takes `{"deploys": [...], "dry_run": false}`, 1 to
  100 items, 512 KiB ("at most 512 KiB; send fewer items"). Order: open the live secrets, verify over the raw bytes
  (`signatures.matching_secret`, constant time over all), mark the secret used, then parse. Every FR9 check, with
  one 422 naming item and field; a signed refusal is recorded in `deploy_intake_state` and committed before the 422
  unwinds (the precedent is sign-in's refusal). Repeats answer with what they stored. New items are charged to the
  project's exact hourly quota (`DEPLOYS_ACCEPTED`, 1,000 per 3,600 s) by a conditional update, so a refused batch
  charges nothing; then evict past 50,000 (now `nexovue_platform.releases.RELEASES_PER_PROJECT`, shared with the
  sync), insert in (service, version) order `ON CONFLICT DO NOTHING`, file each under its release by the start
  rule, place periods, one `deploy.batch_recorded`. `metadata` is masked by `nexovue_platform.masking`, the port of
  the worker's default rules, held with Go to `packages/contracts/fixtures/metadata-masking.json`. Each answer:
  `deploy_id`, `release_id`, `repeated`, `known`, `version_seen`, `recent_versions`.
- **Secrets** (T2.3): sealed under `CREDENTIAL_ENCRYPTION_KEY`, purpose `deploy-webhook`, owner
  `project_id:secret_id` (the id read from `uuidv7()` first); in `SEALED_COLUMNS`; shown once in
  `MintedWebhookSecretResponse`; at most 5 live, under a project row lock. A seal that will not open is no match,
  logged by id. Without the key, minting and the webhook answer 503.
- **Audit** (T2.4): `deploy.webhook_secret_viewed` and `deploy.recorded` are gone; `deploy.batch_recorded` (secret,
  count, first deploy) and `deploy.removed` (who, and the deploy or the secret and time) are new.
- **Rate limit comment** (T2.5) corrected: the address tier bounds requests; the new quota bounds deploys.
- **The console** (T2.6): *Deploys* in project settings for `manage_credentials` only: URL, secrets with ending,
  created and used, mint (disabled at 5, with why), a minted secret shown once with `UnsavedChanges`, withdraw with
  inline confirmation and "also remove what it sent since", numbered rotation steps, `Tabs` of recipes, a
  `role=status` intake line polled every 5 seconds for 10 minutes, the release-cap sentence, current versions. The
  non-admin sentence names deploy secrets.
- **Setup** (T2.7): `docs/product/setup/deploys.md` (both ways in, `service.version` for Helm, Kustomize, Docker and
  systemd, one project per environment, the three recipes, the body's rules and the 7-day limit, clear text,
  reachability, sampling, rotation and removal, a post-deploy comparison read), with entries in `index.md`,
  `api.md` and `troubleshooting.md`; Kubernetes, EC2, the four language pages, `browser.md` and onboarding now set
  `service.version` from a build variable. The Kubernetes block also declared its variables after the line using
  them, against its own ordering rule: fixed.
- **The demo** (T2.8): `tools/otel-demo/doors.py` sends one batch for both services, minting a secret for the run and
  withdrawing it after.
- **Removing** (T2.9): `DELETE /deploys/{id}` and `DELETE /deploys/webhook-secrets/{id}?remove_since=`, both
  `MayManageCredentials` (in `POLICY_OF`), each recalculating: a release's reported facts are worked out again from
  the deploys left, one nothing supports is deleted, and report-decided periods are rebuilt.
- **ADR-033** filed (the design's third proposed ADR).
- **T2.10, the timed new person: not yet run, founder tests after Phase 2** (foundation item 28). The five-minute
  claim is not withdrawn here: AC1 makes that the outcome only if nobody is found, which is the founder's to
  decide.

**Readings made while building** (none blocking; each open to overrule):

- **On a service that sends no version in traffic, a newer report ends the period it replaces**, at the new start
  (a `started` or `finished` one only: *Founder answer, 2026-11-09*);
  a report arriving late for an earlier time opens a period ending where the next one starts; periods are placed in
  claimed-time order within a batch. The design names only the return rule; without these, AC59's "v1, v2, v1" had no
  v1 period to end. A service with versions in traffic is still the sync's alone to end and return.
- **Names are stored exactly as sent, not stripped.** The old schema stripped whitespace; Python counts U+0085, a C1
  control, as whitespace, so `v1\u0085` was accepted as `v1` while the worker folds it. Found by the new test holding
  the door to `release-names.json`.
- **An unknown field is refused** (named as "unknown field"), not ignored as before, so a misspelt `commit_sha`
  shows in CI's output.
- **The webhook answers 200**, with `stored` and per-item `repeated`, rather than 201: a batch can store some, all or
  none.
- **Signup no longer mints a deploy secret**: shown once, one minted there could never be read. `first-accounts.md`
  and two billing comments that described door 4a's old header id are corrected.
- **The section's current versions come from `GET /deploys/webhook-secrets`**, the one endpoint it polls; the design
  names the list, not its route.
- **Recipes sign with `python3` reading the secret from the environment**: `openssl dgst -hmac` takes the key as a
  process argument, which FR14 forbids. They `set +e`, give each attempt 10 seconds and the retries 30, warn, and
  exit 0, and leaving out `NEXOVUE_DRY_RUN` means a real deploy.
- **A charge is spent before the insert**, in its own transaction: two copies of one body racing can each be charged,
  and a deploy transaction that fails afterwards does not refund. Bounded by the batch size.

**Tests, each seen failing first** (outputs kept in the session's scratch space):

- `test_platform_masking.py` (26): collection failed on the missing module, then passed. Go
  `mask/metadata_fixture_test.go` passed against the worker as written; with one fixture case altered it failed
  naming the case, then the fixture was restored.
- `test_deletion_live.py` (25): with `deploy_intake_state` left out of the inventory it failed naming it; restored,
  passed.
- `test_ratelimit.py` (+3), `test_keys_live.py` (+1, and the reseal case now covering deploy secrets): each failed
  against the previous `limiter.py`/`policy.py`/`keys.py`, then passed.
- `test_audit.py` (+3): failed on the missing functions, then passed.
- `test_deploys.py` (134): the previous suite failed against the new code as expected; the new one was checked by
  removal: without the time bound 2 failed, without the refusal's commit 1, without masking 1. Its name-fixture
  test failed twice on U+0085 (above) before the fix.
- `test_deploys_live.py` (30): written after the service, passed first run; checked by removal: the limiter's
  condition (1 failed), the return rule (2), ending the previous period (1), recalculation (3), claimed-time order
  (1).
- **Founder answer of 2026-11-09, built the same day** (`releases/service.place_periods`, the one place every report
  reaches a period, from the webhook and from recalculation after `DELETE /deploys/{id}` and a withdrawn secret's
  removal): `test_deploys_live.py` now 39. Eight new cases, `failed` and `rolled_back` each: no period and the open one
  left open on a service with no version in traffic and on one with versions in traffic; no return; recalculation
  after a removal skips it. The opening case runs for `started` and `finished`. With the existing removal case's
  expectation corrected (a lone failed report keeps no period), 9 failed against the code as built and 39 passed
  after, on this session's own Postgres (port 25432, since torn down).
- `test_authorization_coverage.py`: failed on the unplaced `delete_deploy` and the renamed `receive_deploys`, then
  passed. `test_postgres_tenancy_structure.py`: failed on three period statements whose `org_id` sat in a helper,
  then passed with it in each statement. `test_cross_service_contracts.py` failed on the eviction written as raw
  SQL in a module that also holds ClickHouse SQL; rewritten in SQLAlchemy Core, passed.
- `test_deploy_recipes.py` (53): against the original Kubernetes page the version check failed; with `exit 0`
  replaced in the curl recipe, its three exit tests failed.
- Web: `DeploysSection.test.ts` (15): with the section ungated 2 failed, with the guard off 1, with the secret as an
  argument 3. `DetectionSection.test.ts` gained the Deploys stub its owner render now needs.

**Counts.** Offline, with live suites left out (`NEXOVUE_SKIP_LIVE_TESTS=1`): `apps/api`, `packages/py-platform`,
`apps/scheduler`, `packages/contracts`: 9,347 passed, 4 skipped. Live, on this session's own Postgres (port 25432,
since torn down): `test_deploys_live.py`, `test_keys_live.py`, `test_deletion_live.py`: 59 passed. Go: the `mask`
package's new test passed. Web: `features/projects`, `shared/components`, `features/onboarding`: 227 passed.
`uv run mypy .` (798 files), `ruff check`, `ruff format --check`, `pnpm typecheck`, `biome check` on the changed web
files and `verify-tokens.js`: clean. HMAC cost: 5 secret openings and 5 HMAC-SHA256 over 512 KiB, 1.82 ms median
(3.06 ms max) per request on this machine (4 vCPU Xeon at 2.1 GHz), about 0.11 s of CPU a minute at the 60-a-minute
address limit.

**Not run:** `just check` (the coordinator's); `test_releases_live.py` and `test_release_sync_live.py`, which need
ClickHouse (the sync's only change is reading the shared cap constant); the recipes on a hosted runner (no reachable
address); the full dev stack (T2.8 ran against an API started locally on this session's Postgres); Design QA at 375px;
T2.10.

**Residual risks** (recorded, not fixed):

- **Two copies of the recipes**: `deploys-api.ts` renders them for the console and `deploys.md` holds them for the
  page. `test_deploy_recipes.py` runs the page's, and `DeploysSection.test.ts` checks the console's; a change to one
  that misses the other is caught only where it breaks a check.
- **AC51 races a sync-shaped writer**, the sync's own upsert statement in its order, not the scheduler job itself,
  which the API's tests cannot import.
- **"Remove since" is entered in the browser's time**, labelled so; the design's rule is the project's zone. Design
  QA should judge it.
- **A recipe's JSON is built with `printf`**: a service or version containing `"` or `\` makes an invalid body,
  refused with a warning. Telemetry names rarely do; the page does not yet say so.
- **Deploys that fail on the server after charging** do not refund the hour's allowance (above).
## Step 1b, built 2026-11-08

A release compared by version, on real traffic (plan T1.8 to T1.16). What exists now, read from the code:

- **The record** (T1.8): Postgres migration **`0054_releases`** (the next free number after F19's 0053; F22 may also
  take numbers in parallel, so the coordinator renumbers at commit if they collide), `releases`, `release_periods` and
  `release_sync_state`, with models and `organizations/tables.py`. One open period per release and one period per
  release and start are unique; '' and `__other__` are refused by the database. Roll forward only, like F19's.
  **ADR-029** filed (the design's second proposed ADR). The third, the deploy envelope, is Step 2's.
- **The comparison** (T1.9): `nexovue_platform/releases.py`, pure: the one name check (held to
  `release-names.json`, as the Go side is), `storable`, `bucket_p95` (moved from detection's `method.py`, which
  re-exports it), `compare_failure_share`, `compare_p95` and `choose_method`, the design's ordered list in full.
- **The sync** (T1.10 to T1.13): `nexovue_scheduler/releases/` with `sweep:releases` on beat every minute, on its own
  `releases` queue, capped at 2 under `scheduler-light`'s floor, in `worker_queues`' default and both compose files'
  comments; `observe_project` under the project's session lock; the per-minute walk (`sync.walk`); writes sorted by
  service and version in one transaction; `synced_through` forward only; 200 new releases a run; eviction past 50,000.
  The store reads through `alerts.telemetry`'s reader, so it holds no driver. `test_detection_isolation.py` walks
  detection and the alert roots and finds no path to the sync. The one cross-tenant statement, the sweep's
  `_MOVED_SINCE_SYNC`, is named in the scheduler Postgres tenancy test.
- **The API** (T1.14): `GET /releases` (window overlap, service and version filters, keyset page, the window's
  totals, `has_any` and `latest` for the three empties), `GET /releases/{id}`, `GET /releases/{id}/comparison`
  (`period`, `window` of 1, 6 or 24 hours, to the settled minute). On the read surface and `setup/api.md`; the
  `/releases` prefix is on the telemetry rate-limit tier, as the design says, though only the comparison reads
  ClickHouse.
- **The console** (T1.15): `/releases` in Signals after Errors, and `/releases/$releaseId` with the opening sentence
  (`role=status`), the two blocks with a table from `md` and stacked lines on a phone, the window tabs, the facts and
  periods, each section failing on its own; `shared/format/version.ts` for long versions.
- **AC22** (T1.16) and **the gate's alert evaluation start delay** (added to T1.10 by the founder on 2026-11-08):
  both measured and passed on demo data (cost report, *Addendum, Step 1b*): about 60 seconds from the settled
  minute to the release row, against 3 minutes; and an alert sweep claimed in 0.049 s (median) with 51 release
  jobs queued and one on a 360,000-row first read, against 0.071 s with none.

**AC28, recorded as a fact.** F6's project job over four weeks at 0.2% failing, then 40 minutes at 0.8% mixed (3% on a
fifth of traffic), **opened no finding**: its minimum change at normal sensitivity is 2 percentage points
(`detection/method.py`, `SENSITIVITIES`). A 30% control opened a critical one, so the harness can open one. The
release page reads the same rollout side by side, 3.0% against 0.25%, "higher". `test_releases_live.py` asserts both,
so a change in F6 shows there.

**Readings made while building, against the plan's or design's wording** (none blocking; each open to overrule):

- **`LOG_EDGES` had already moved** to `nexovue_platform/slo.py` with F19, so T1.9 moves only `bucket_p95`.
- **`releases.store` is not added to the raw-client list** (T1.11): it reads through `alerts.telemetry`'s reader, as
  F19's `slos/store.py` does, so one module fewer holds the driver. `PROJECT_BOUND_PACKAGE` became
  `PROJECT_BOUND_PACKAGES = ("detection", "releases")`.
- **The sync reads the hour before its new minutes again, not five minutes.** The 60-minute rule needs that hour, so
  a version first counted in rows that arrived late within it also gets its true first minute. The design's stated
  risk now reads "more than an hour late". A first run reads exactly 6 hours.
- **How an end is dated** (the design names the rule, not the minute): a takeover ends a period at the first minute
  of the other version's 10-minute run; a quiet end at the minute after its last request. "Named requests" leaves out
  '' and `__other__`.
- **Every version first counted in a service's first counted minute is already running** (connecting mid-rollout).
- **Every request failing on both sides** has a spread: the pooled share is held one request from 1, as the design
  holds it one from 0.
- **The comparison response leaves out** `incomplete`, `minutes_differing`, `versions_combined` and
  `mixed_environments` until T4.1 computes them, rather than answering `false`. `time_before_after` is computed from
  the version rows when every row has no version (those rows are the service's totals, FR16); T4.1 adds last week
  and completeness. No release has a reported time before T2.2, so it cannot occur yet.
- **The sync's figures are log fields, not Prometheus metrics** (`release_sync`: `duration_ms`, rows read, new
  releases and periods; `release_sync_bounded`: rows skipped, evictions, the cap). Jobs run in child processes, which
  export no metrics in this codebase; the `releases` queue's age is the existing per-queue gauge.
- **The list's empty state links the setup documentation only.** The Deploys section it would also link is T2.6.

**Tests, each seen failing first** (outputs kept in the session's scratch space):

- `test_platform_releases.py`: collection failed on the missing module, then 47 passed.
- `test_deletion_live.py`: failed naming the three unclassified tables, then 25 passed.
- `test_release_sync.py`: failed on the missing package, then 21 passed (one fixed in the code on the way: the cap
  stopped a run on a minute bringing nothing new).
- `test_beat_schedule.py` and `test_compose_contract.py`: failed on the missing beat entry and queue, then 71 passed.
- `test_tenant_chokepoint_structure.py`: with the project predicate removed from the store, failed naming
  `releases.store`; restored, 18 passed. `test_detection_isolation.py`: with an import of the sync placed inside a
  function in `alerts/silence.py`, failed naming the chain; removed, 5 passed.
  `test_scheduler_postgres_tenancy_structure.py`: failed naming `_MOVED_SINCE_SYNC`, then 14 passed.
- `test_release_sync_live.py` (13) was written after the walk: its first run failed on a fixture mistake (a version
  name colliding with a junk one); with eviction removed from the code, the 50,000-release case failed; restored, 13
  passed.
- `test_read_surface_structure.py`: failed on the three new reader routes, then passed with `setup/api.md` updated.
- `test_detection_coordinator.py`: failed on the worker's default queues, then passed.
- Web: `comparison.test.ts` failed with no module, then 18 passed; `route-manifest.test.ts` failed on the missing
  Signals entry, then passed; `ReleasesScreen.test.ts` (9) failed 2 with the empty and zero-result branches broken,
  then passed. `version.test.ts` (3) was written with its formatter and not seen failing.
- `test_releases.py` (14) and `test_releases_live.py` (13) were written after the routes; they passed on first run.

**Counts.** Offline, `packages/py-platform`, `apps/scheduler`, `apps/api` and `packages/contracts` tests with live suites
left out: 9,142 passed and 1 failed, the worker-defaults test above, then fixed. Live, on this machine's stores:
`test_release_sync_live.py`, `test_releases_live.py`, `test_deletion_live.py`, `test_jobs_live.py`,
`test_detection_project_job_live.py`, `test_detection_shifts_live.py`: 106 passed. Web: the releases folder, the
route manifest and the version formatter, 38 passed. `uv run mypy .`, `ruff check .`, `ruff format --check .`,
`pnpm -r typecheck`, `biome check` on the changed web files and `verify-tokens.js`: clean.

**Not run:** `just check` (the coordinator's); the full live suites beyond those named; Go (no Go file changed);
Design QA in a browser at 375px (Gates); `test_erasure_live.py` (unchanged, needs SeaweedFS).

## Step 1a, built 2026-11-07

What exists now, read from the code:

- **`service_versions_1m`**, ClickHouse migration `000038` (the next free number on the branch), `000035`'s shape plus
  `version`, in the erasure inventory (`erasure/tables.py`, `erasure/statements.py`, `test_erasure_live.py`). T1.1.
- **The worker's version map** (`derive/versions.go`): fed from `observeService`, so it counts exactly the spans
  `service_stats_1m` counts; the drain cap of 20 per service per drain, the arrival caps of 64 per partition, service and
  minute and 512 per tenant, partition and minute; folding into `__other__` for over-long or failing names; a service
  the per-tenant cap leaves out leaves its versions out; `Disown`, `PendingVersions`, the fold report. The Go side of the
  name check is `derive.NameAllowed`, held to `packages/contracts/fixtures/release-names.json`, which the webhook's
  Python check reads in T2.2. T1.2.
- **The browser door counts under no version** (`traceassembly/flatten.go`, `countedVersion`), until the browser-door
  change keeps such spans out of both rollups and deletes that branch (D55). This matches that change's plan, which
  filters before `counters.Observe` (`docs/changes/browser-door/plan.md`, T1.1): no conflict. T1.3.
- **The writer** (`derive/versionwriter.go`) with its deduplication token, inserted last in `insertDerived`, never an
  error and never holding progress: a refusal is counted in `derived_rows_refused`; a transient failure has its own
  30-second budget (`consume.RetryWithin`, new), then is stepped over and the insert paused for 5 minutes, counted in
  `version_rows_stepped_over` and `version_rows_skipped`. Heartbeat and release log lines carry `version_series` and
  `versions_folded`. T1.4.
- **`tools/release-demo`**: `send.py` (every named mode; a ledger of what the receiver accepted; `verify` against the
  store) and `release_measure.py` (the timed runs and the gate's table), with `just release-demo` and
  `just release-measure`, and both in `pyproject.toml`'s `testpaths` and `pythonpath`. `--deploys N` is not built: the
  task gives it "from T2.2 on", when the new deploy body exists. T1.5, T1.6.
- **`tools/detection-measure/scale.py`** reads `service_versions_1m` and each table's index memory in its storage census,
  so `just detection-scale watch` at AC29's volume reports the version table beside `service_stats_1m`.
- **ADR-025** filed (the first of the design's three proposed ADRs). The other two, the releases record and the deploy
  envelope, are filed when Step 1b and Step 2 start, since nothing they decide is built yet.

**Tests, each seen failing first.** Go: `derive/versions_test.go` (14 tests: AC15 for any mix over 40 seeds, AC18's
25 versions, the two-drain fold, the 64-name junk burst, the per-partition and per-tenant arrival caps, long and
control-character names, the shared name cases, the service cap, the empty version, a reproducible drain, the revoke
with its arrival room, the arrival room freed with its minute), `versionwriter_test.go`, `consume/permanent_test.go`
(the budgeted retry), `traceassembly/flatten_test.go` (AC17 before the change), `refusal_test.go` (AC19 for this table,
AC54), `counterhandover_test.go` and `restart_test.go` (AC16), and `counters_order_test.go`, `disown_test.go` extended.
A store probe, `derive/versionwriter_storeprobe_test.go`, writes one drain to both tables in a real ClickHouse 26.3 and
reads the version rows summing to the service rows bucket by bucket, with a retried insert deduplicated: passed.
Python: `tools/release-demo/test_send.py` (12), `test_release_demo_measure.py` (19: 9, then 10 more for T1.7, of which
the folded-churn, GOMEMLIMIT and compose-limit tests failed against the earlier tool first; the five "totals differ"
cases passed against it too, since it was stricter), `test_erasure_inventory.py` (failed on the missing table, then
passed). `uv run pytest tools/release-demo -q`: 31 passed; `ruff check`, `ruff format --check` and `mypy .` clean.

**End to end, on this machine's own stores** (ClickHouse, Postgres, Kafka on non-default ports, receiver and worker
from source): `release-demo send rolling` then `verify` printed 240 and 239 requests sent on v1 and v2, the same stored
in `service_versions_1m`, and the version rows summing to `service_stats_1m`.

**Not run:** `just check` (the coordinator runs it); `test_erasure_live.py`, which needs the full dev stack with
SeaweedFS; the Step 1b, 4 and 5 tasks.


## Completed

- [`research.md`](research.md), written on 2026-11-02, with its competitor rows and claim corrected after the reviews.
- [`spec.md`](spec.md), [`design.md`](design.md), [`plan.md`](plan.md), written on 2026-11-02 and revised the same day.
- Decisions D1 to D58 recorded in `spec.md`, each open to the founder's overrule (D56 to D58 added 2026-11-02, below).
- Eight persona reviews, 131 findings, each checked and recorded below.

**What changed from the research, on reading further**

- A release is a stored row, and each stretch in service is its own period (D2, D15).
- Secrets are shown once rather than re-readable (D7).
- The replay fix signs the id inside each item, and requires a signed time (D6).
- The browser-key question is answered from the code, not yet run: a browser key is expected to be able to send a SERVER
  span. F28 excludes it (D12, D38).
- The cost gate moves into plan Step 1a, since measuring needs code (D27).

### CI fix 2026-11-22: the release summary suite raced the stack's own deliveries sweep

**What failed.** A local full CI run (`pytest -n 6` beside the dev stack) failed `test_release_summary_live.py::test_each_release_is_told_once_on_its_channel_whatever_its_reading`: the notices read back `failed`, not `pending`. **The product is not at fault; the suite was.** `summarize_period` writes a summary due at the `now` it is given, and the suite gives it `DUE`, hours in the past, so the notice is due the moment it commits. The stack's `scheduler-deliveries` worker runs the delivery sweep every 15 seconds and claims every due notification in the database, whichever organization it is for. It took the test's notice, could not deliver it, and marked it `failed` before the test read it. The suite's other three tests that commit a summary (delivered with its counts, retried when unreadable, a retry never holding the next release's) had the same exposure and read the stack's outcome, not their own.

**Reproduced,** on this change's own Postgres and ClickHouse with the real `scheduler-deliveries` worker process on the host and a sweep enqueued every second. A scratch harness waited up to 4 s after each commit, to stand for a slow CI machine. The unchanged suite failed 4 of 4 tests, with CI's own assertion for the first (`'failed'` against `'pending'`); the worker's log shows the attempt on the test's channel. With the worker stopped, the same harness passed 4 of 4. Without the harness the unchanged suite passed 3 of 3 beside the worker, because the gap is milliseconds on an idle machine, which is why it first showed in about 10 full runs.

**The change.** The suite now runs on a database of its own on the stack's server (`private_database`, as `test_scheduler_metrics_live.py` does), so no stack sweep ever sees its notices. *After:* the same harness, with the worker live, passed 4 of 4, and the worker logged no delivery. This also takes the install's settled minute, which the stack's detection sweep writes too and which the suite's `settled` fixture sets, out of the stack's reach. *Rejected:* deciding a month from now, as the alert suites do, because the summary's `now` also chooses the minutes its comparison reads and the 35-day rollup; `jobs_held`, because it holds jobs, not notifications, and the suite's own `deliver_due` must find its rows due; a rolled-back transaction, because the job runs on its own connections and commits.

**Sweep of the other suites that commit notifications** (all clear): the alert suites decide at `future()`; `test_detection_stopped_live`, `test_release_facts_live`, `test_slo_regression_live`, `test_alert_senders_live`, `test_evidence_explained_live` and `test_finding_event_kinds_live` use a future time; `test_outbox_golden_live` is dated 2099; detection suites dated `ONSET` (tomorrow); `test_slo_job_live`, `test_slo_one_message_live`, `test_scheduler_metrics_live` and `test_connections_end_to_end_live` have databases of their own; `test_findings_actions_live` writes a due row but reads only its kind and channel, which a claim never changes.

### Decisions 2 and 3, built 2026-11-22 (`wt/f28fix`, founder: foundation item 36 (a) and (b))

- **Decision 2, done.** `version-errors.ts` `errorsAbsence` now returns `NO_EXCEPTIONS`, "No exceptions were recorded in
  either version during this window.", for an empty list, always rather than only when the failure share is above zero:
  the section counts exception groups, so the sentence is true whatever the failure share, and it needs no figure from the
  comparison (a second read). Regression test `ReleaseScreen.test.ts`, "says no exceptions were recorded, not no errors"; seen
  failing before the change. Spec FR29 and design's states table name the sentence.
- **Decision 3, done at its root.** The division was in the web (`comparison.ts`, `side.requests / block.minutes_compared`),
  and `minutes_compared` is the *stretch* (it also words "over 40 minutes"). The rule now lives once, in the platform:
  `Side.requests_per_minute` is requests over the minutes in which that side had any, `None` when none did
  (`nexovue_platform/releases._side`, which also builds last week's side). The API sends it as `requests_per_minute` on
  every side, last week included; the web shows it and a dash for `null`. Tests seen failing before the change:
  `test_requests_a_minute_divides_by_the_minutes_that_had_traffic` (a 60-minute stretch with traffic in 20 minutes: 120.0, not
  40.0), `test_a_side_with_no_traffic_has_no_requests_a_minute`, and `comparison.test.ts` "shows the API's rate, not requests
  over the whole stretch" (read 100.0 where the rate is 300.0).
  - **Each side divides by its own busy minutes**, not by the minutes both had. The alternative (shared busy minutes) is not
    built; say if the founder wants it.
  - **Every caller checked.** The comparison table and phone lines (the only place a per-minute figure was shown): fixed.
    The opening sentence and the flags use failure share and p95, which are ratios of counts and have no per-minute
    denominator. The stored reading and release summary carry counts and `minutes_compared` as the stretch's length, no rate.
    MCP `list_deploys` returns no counts; the MCP comparison tool returns requests and `minutes_compared` and no rate, and
    its field list was left as it was, so the new field does not reach an assistant (it would need a unit in
    `assistant_tools.Unit`, a larger change, not asked for).
  - **Open decision (new, 6): failure share's denominator.** Failure share is errors over *requests*, not over time, so
    silent minutes do not touch it. The other measure that sees a window is the traffic-floor test ("too little traffic"),
    which counts requests over the whole stretch: a bursty version can read "too little" where its busy minutes would
    clear the floor. Options: leave (recommended: the floor guards statistical power, which counts requests, not minutes);
    or none. No change made.

## Next action

Step 6 is built 2026-11-22 and awaits the coordinator's commit. What is left is the founder's: judge the demo recording;
T2.10's timed new person; Steps 3 and 4's Design QA and the finding section at 375px with a real finding; a real send of
the release summary; decide the open decisions below (2 and 3 are done, 4 is mostly done, 1 and 5 remain). The competitor
reading was repeated on 2026-11-22 except for Observe; it must be repeated within two weeks before announcing (research
section 10). The production-readiness assessment and the
review gate in `plan.md` are the lifecycle's, not done here.


## Open decisions

**From Step 6, none blocking; each recommended, open to overrule.**

1. **Where does the changelog entry live?** No changelog file exists; release notes are to be generated at release
   (`docs/changes/ready-to-deploy`). The draft is under *Launch text* in this file. Options: keep it here until the generator
   exists (recommended: no new convention, nothing to drift); or create `CHANGELOG.md` now. **Recommendation: keep it here.**
2. **Errors by version reads "No errors in either version" beside a higher failure share,** when the failing spans carry no
   exception (the demo's `rolling` mode). It counts exception groups, as the Errors screen does. Options: leave (recommended
   only if the founder is content that "errors" means "exceptions" everywhere); or say "No exceptions were recorded in either
   version" when the failure share is above zero, so the two lines agree. **Recommendation: the second, a one-sentence change in
   `ReleaseScreen.tsx` and its test.** It is the first thing a newcomer to the recording would ask about.
   **Done 2026-11-22 (founder, foundation item 36a).** Built always rather than only above zero failure share; see *Decisions
   2 and 3, built 2026-11-22*.
3. **"Requests a minute" divides by the whole window, silent minutes included** (*Step 6*, MEDIUM). Options: leave; divide by
   the minutes that had traffic (recommended); or drop the row for a version idle more than a tenth of the window. It
   changes the comparison's rule (`nexovue_platform/releases.py`), so it is the founder's, not built here.
   **Done 2026-11-22 (founder, foundation item 36b).** See *Decisions 2 and 3, built 2026-11-22*.
4. **Allow the vendor sites for the competitor reading.** **Mostly done 2026-11-22:** the hosts were allowed and ten of eleven rows
   opened (research section 10). **Left:** Observe (`docs.observeinc.com` refused; `www.observeinc.com` now redirects to
   `www.snowflake.com`, refused); allow `docs.observeinc.com` and `www.snowflake.com`, or drop the row. Original text: 12 hosts were refused in this build (research section 10). The
   rows marked *extract* and every price except Datadog's need a second read before any public claim. Allow `signoz.io`,
   `docs.coroot.com`, `coroot.com`, `sentry.io`, `docs.sentry.io`, `clickhouse.com`, `docs.honeycomb.io`,
   `docs.newrelic.com`, `docs.observeinc.com`, `grafana.com`, `docs.dynatrace.com`, `openobserve.ai` in the build's network
   settings, then repeat T6.1 once, within two weeks before announcing.
5. **Two builds on one disk.** This container's disk (about 39 GB) was shared with another build and was full four times in
   one step, once from this step's own seed. Not F28's to fix; recorded because it costs hours. Options: a disk each, or a
   rule that a seed over a few million rows asks first. No F28 decision depends on it.
6. **The traffic floor counts requests over the whole stretch** (*Decisions 2 and 3, built 2026-11-22*). Failure share has no
   per-minute denominator, so item 36b leaves it. A bursty version can read "too little traffic" where its busy minutes
   would clear the floor. Options: leave (recommended: the floor guards statistical power, which counts requests); or count
   the floor over busy minutes. Nothing changed.

**From Step 5, none blocking; each built as recommended, open to overrule.**

- **The release summary's words are read by delivery, not the claim** (ADR-066). Design section 6 joined the release
  tables in `_TAKEN`; AC42's isolation test refuses it, since detection imports the outbox. Options: keep (recommended:
  both approved rules hold, and a failed read retries the batch instead of holding the claim); or exempt the outbox
  from the isolation test, which weakens AC42.
- **A by-time reading is stored and summarized, never flagged.** Options: keep (recommended: FR55 flags from the two
  version methods, and the summary names the method's limit); or store nothing for it.
- **The webhook adds the summary job too.** Options: keep (recommended: otherwise a reported-only release is never
  summarized, against AC82); or a sweep that finds due periods, which needs one cross-tenant statement.
- **A job that fails three times leaves the period with no reading and no summary.** Options: keep, logged
  (recommended for now); or a later sweep for periods past due with no reading.
- **The list's flag and combined state come from the reading stored 30 minutes in**, so a later change in the
  release's numbers is shown on its page, not on the list. Options: keep (FR55); or store again at a later time.

**From Step 4, none blocking; each built as recommended, open to overrule.**

- ~~**The list's "Versions were combined on `checkout`" state is not built.**~~ Built in Step 5 from the stored
  reading, as recommended.
- ~~**FR45's warning in the Deploys section is not built.**~~ Built in Step 5 from the stored `mixed_environments`,
  as recommended.
- **A section moving from skeleton to numbers is not announced** (spec, *Accessibility*); only the opening sentence is
  (`role=status`). Options: `aria-live="polite"` on each section's body (recommended, small), for Design QA to judge.
- **The errors-by-version window is this version's start to the window's end, and the same length before** (reading
  above). Options: keep (recommended: equal lengths, as FR29 says); or both versions over the same minutes, which
  would read a rollout's old version as "in both".

**From Step 3, none blocking; each built as recommended, open to overrule.**

- **A versions-only service's errors cell reads "none reported".** The design names `after v2.1`, "none before" and
  "not told". "None before" would be false there: the deploy before a group is read from signed deploys only (until
  T5.1), so a version seen in traffic before the group would not be named. Options: keep "none reported" (recommended:
  true, short, and the panel says "Your CI reports no deploys"); or read seen releases as the deploy before too, which
  is T5.1's `DeployHistory` move and a wider change.
- **A release reported only as failed or rolled back is never "the release before this".** It has no period (founder,
  2026-11-09), and FR32 names the period before. It still shows on the finding's charts as a "failed" mark and in the
  list under them. Options: keep (recommended: it did not replace what was running); or name the latest report of any
  state in the section as well.
- **A project finding with no release in its services' 24 hours reads "No release of these services in the 24 hours
  before this started, from what we can see."** The wording table has no row for it. Overrule with other words.

**One reading from Step 1b, not blocking.** **The per-run cap of 200 new releases never splits a minute.**
`synced_through` is kept by the minute, so a strict cap would stop the sync for good on any minute holding more than
200 new versions (500 services each first counted in one minute is enough: the measurement saw 1,000 in one run). The
walk therefore takes a whole minute even past the cap, and stops only before a later minute that would cross it
(`sync.walk`, `test_one_minute_over_the_cap_is_still_taken_so_a_run_always_moves`). Options: keep this (recommended:
the cap still bounds every later minute, and a first run is bounded by its 6 hours and the arrival caps); or keep the
minute but split its writes across runs, which needs a second position inside a minute. Overrule by saying which.

**From Step 1a, none blocking.** Readings made while building Step 1a. **Founder answer, 2026-11-08:** the last three below (the gate's
rows, churn run once, the alert-delay row) are **accepted as recommended**: the gate stands as passed on demo data, and the
alert evaluation start delay is added to Step 1b's sync-job task when the plan is next revised and measured before Step 1b
is called done. The first two stay open to overrule:

- **The name check refuses DEL (U+007F).** FR9 says "C0 and C1 control characters"; DEL is in neither range strictly, but
  it is Unicode category Cc with them. The check uses Cc, and the shared fixture states it. Overrule by removing the DEL
  case from `release-names.json` and narrowing `derive.NameAllowed`.
- **The empty version takes arrival room like any version.** The design does not say whether `''` is exempt from the
  arrival caps. Treating it as a version means a junk burst can fold "no version" traffic into `__other__` too, which the
  "versions were combined" label already covers.
- **The gate applies the four ratio rows to the demo traffic and the churn row to churn** (design, *The gate*; founder
  decision 24 splits cost checks from correctness checks the same way). "Totals exact" means nothing lost and every
  service's versions, `__other__` included, holding what was sent, with no named version above it; folding a named
  version short is allowed (FR17, AC18). Overrule by making churn meet the ratio bounds too, which it does not (21× rows,
  +15.8% memory, 2.065× disk; a cost failure under decision 24, which keeps version comparison if it fits 16 GB).
- **Churn ran once for 15 minutes,** not the design's 3 repeats of 30 minutes. Its margin is wide (151 of 1,088 MiB),
  so the gate is called passed; the founder may ask for the full repeats.
- **The gate's alert evaluation start delay row has no task.** It needs Step 1b's sync job. Proposed: add it to Step 1b's
  sync-job task when the plan is next revised, and measure it before Step 1b is called done.

The customer conversations (foundation item 8) and the browser-door change (item 7) were settled by the foundation on
2026-11-02. The conditional gate after Step 1a was answered on 2026-11-02 (foundation item 24).

## Blocked

- **Founder decision, 2026-11-08:** Step 1b no longer waits on the buyer round (foundation item 27). Built 2026-11-08.
- **Step 4 is blocked on the shared browser-door change** (below; foundation item 7, D58). **F28 owns it** (foundation item 22,
  2026-11-02). The plan holds no task for it yet; they are added when the plan is next revised.
  **2026-11-07:** the browser-door change is built (all four steps; its live test asserts `service_versions_1m` too,
  and it deleted `countedVersion`, D55) and is on `feature/phase-2` (2026-11-07) (`docs/changes/browser-door/status.md`).
  **2026-11-12:** checked in the code and Step 4 built (*Step 4, built 2026-11-12*). Nothing blocks Step 5.

## Reviews

Eight personas reviewed `spec.md`, `design.md` and `plan.md`. Each finding was checked against the code or the vendor's page
before anything changed. **Nobody re-reviewed the result.** The verdicts below are what the reviewers said before these
changes, not a judgement of the documents as they now stand.

**Result: 131 findings. All 131 led to a change, and none was rejected whole. Twelve had a part rejected, listed below.** A finding counts as fixed
when the documents now answer it. That means a requirement, design and test are written. It does not mean anything works,
since nothing is built and nothing was run.

Reviewers' line numbers point at an earlier revision and have moved. Where a claim was about the earlier text of these
documents, it could only be checked as the reviewer quoted it. Those rows say *quoted*.

### Verdicts

| Persona | Verdict at review | Findings | Blockers |
|---|---|---|---|
| Architect | Not ready | 21 | 1 |
| Developer | Ready with fixes | 19 | 0 |
| UX | Ready with fixes | 17 | 0 |
| Sales | Ready with fixes | 14 | 0 |
| Marketing | Ready with fixes | 9 | 0 |
| Customer | Not ready | 17 | 1 |
| Security | Ready with fixes | 15 | 0 |
| QA | Not ready | 19 | 2 |

### Conflicts between personas, and how they were decided

The task named a decision rule without stating it, and none was found in the repository under that name. The authors used this one, drawn from the run's standing rules: the correct end state first, then what a tired engineer can trust, then cost. The founder may overrule any row.

| Conflict | Decided | Why |
|---|---|---|
| Customer wants the CI link first on the page, including for viewers; Security wants all sender text limited to members | `url` and `source` for any signed-in person, `metadata` for members and above (D46) | A viewer on call needs the commit. A `url` now holds no query or fragment, and `metadata` is the free text |
| Conversations before Step 1 (Sales), before Step 4 (Marketing) | Before Step 1b, as the founder's choice | Step 1a is a cost measurement and needs no buyer. Step 1b is where the big spend starts |
| Worker counter for environments (Customer); stated limit only (Sales's option) | Read stored spans once per comparison window (D39) | It asks which labels appear, not a rate, so sampling does not matter. It adds nothing to the worker's hot path |
| Summary on `higher` readings (Marketing's example); every release (Customer) | Every release, when opted in (D40) | Sending only on `higher` is an alert from a second detector, which F6 D1 refuses |
| `release_spells` (Architect), `release_periods` (Customer, QA) | `release_periods` | Plainer word |
| Body cap of 256 KiB (Architect), 512 KiB (Developer) | 512 KiB with 1 KiB `metadata` (D32) | The worst plain batch is about 395 KiB, by arithmetic |
| Deleting a release's events on lapse: `CASCADE` (Architect), `SET NULL` (Developer) | `SET NULL`, and retention by arrival time | Arrival time removes the disagreement. `SET NULL` covers eviction |
| Dashboards: shared route (Architect), direct function (F20's current D14) | One function behind the route (D30) | Keeps F20's reasons against HTTP and one set of markers |

### Parts of findings rejected

No finding was rejected whole. These parts were, and nothing else was dropped.

| Finding | Part rejected | Why |
|---|---|---|
| Marketing 1 | Datadog has a `PATCH` route marking a change failure | Not on either DORA setup page read on 2026-11-02, both of which describe failures as detected automatically. Left out of the table, and listed as not established |
| Architect 13 | F20 uses `deploy_events_between` with a cap of 200 | Out of date. F20's D14 now calls `deploys_in_window`, cap 500 (`dashboards/spec.md:531`). The conflict is real but different: D29 makes that read signed-only, so dashboards would lose seen releases |
| Customer 13 | Measure per-endpoint cost in the gate | Measuring means building the table for a feature D18 defers. The report gives an arithmetic estimate instead, labelled as such |
| Customer 12 | A counter in the worker's version rollup | D39. It adds work to the hot path, when a read of stored spans answers it |
| Security 2, Security 9 | Members and above for all of `sender-details` | D46, above |
| QA 15 | Rename the sender's field to `sender_source` | The confusion is fixed by the new word `basis`, and AC39 now says it. F3 and F4 already name `source`, and renaming adds churn with no further safety |
| Marketing 5 | A summary carrying only the `higher` reading | D40, above |
| Customer 15 | A new customer setting for how long `metadata` is kept | It follows the project's derived retention (D20). A separate setting adds a promise nobody asked to make. Masking and the members-only read answer the risk |
| Customer 17 | An optional `deployed_by` field | D42: personal data on a long-lived record, when the commit link already names the author. The finding allowed recording a reason instead |
| Customer 9 | A warning when a version has not changed for an unusual time | No sound definition of "unusual" exists yet. Settings list each service's version and age, which the finding allowed as the minimum |
| Marketing 9 | Fill in the price column, and install Coroot now | Prices stay "not read", with no cost comparison allowed. No install can happen in a documents-only run, so T6.1 owns it |

### What was changed, finding by finding

Checked against: **code** (read at the cited path), **arith** (redone by hand), **web** (vendor page read 2026-11-02), **doc** (another
repository document), **quoted** (earlier text of these documents).

**Architect**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Moving `started_at` erases history (blocker) | quoted | `release_periods`, FR19, FR42, D15, AC25, AC59 |
| 2 | Sweep on the `light` queue can crowd out alerts | code | Own queue, cap, expiry, lock, skip; D31, AC72, gate row |
| 3 | Sync can miss a version or misread order | code | Per-minute walk, `synced_through` rule; AC55. Residual risk below |
| 4 | Batch cannot fit the body cap | code, arith | 512 KiB, 1 KiB `metadata`, new words; FR6, D32, AC8 |
| 5 | Four requirements with no storage | code | `deploy_intake_state`, `known` and `none` from `service_last_seen`, counted limit |
| 6 | Cap of 20 is per drain | code | FR17, D14, AC18 restated; label fires on any `__other__` |
| 7 | "About double" memory does not follow | code, arith | 512 per-tenant arrival cap, worst-case arithmetic, AC18 and gate row |
| 8 | Transient failure stops the worker | code | Own 30-second budget, skip, step-over; D13, AC54 |
| 9 | The 99% check hides lost minutes | arith | Per-minute check; FR28, D33, AC36 |
| 10 | Algorithm gives wrong answers | quoted | Ordered list, `new_version_not_seen`; FR21, AC30, AC32 |
| 11 | Flat 395 days breaks retention | code | Project's derived retention; FR40, D20, AC74 |
| 12 | F3 contract undefined | doc | Signatures, two reads, no blanket "measured"; D29. F3's own document changes at approval |
| 13 | F20 draws different markers | doc | `markers_in_window`; FR35, D30. One part rejected. F20's own document changes at approval |
| 14 | Cost gate comes last, fallback understated | quoted | Step 1a and 1b; the design lists what disappears |
| 15 | Batch upserts can deadlock | code | Sorted writers, including the sync (added on checking); AC51 |
| 16 | `LEAST` and `GREATEST` conflict with "measured beats told" | quoted | One start rule; FR42, AC57 |
| 17 | `TenantScopedRepository` missing in the scheduler | code | `releases/store.py` allowed, tuple; T1.11 |
| 18 | Foreign key and retention clocks disagree | code | `SET NULL`, arrival time; AC74 |
| 19 | Step 3 cannot run with the seed tool | code | T3.0 |
| 20 | Errors read has no cost estimate | code | Measured in T4.3, timeout words; FR51 |
| 21 | FR39 contradicts itself | quoted | `basis`; FR39 |

**Developer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Version numbers can be silently wrong | code | Caps stated truthfully, `__other__` label, Go tests; FR17, D14, AC18, AC67 |
| 2 | Algorithm's order is wrong | quoted | Ordered list, `''` and `__other__` excluded from previous; AC65 |
| 3 | Largest risk tested last; gate cannot fail | quoted | Step 1a, `measure.py`, baseline, 3 repeats |
| 4 | Body cap makes batches impossible | code, arith | As Architect 4; HMAC cost measured in T2.2 |
| 5 | Limiter cannot count a batch | code | `cost`, exact quota, `app.state`. On checking: a refused batch must charge nothing, since today's statement adds first (`limiter.py:169-186`) |
| 6 | "Last check received" has nowhere to live | quoted | `deploy_intake_state`; AC13 |
| 7 | Nothing defines who writes the start | quoted | FR42, AC57 |
| 8 | Return rule misfires, rollback compares wrong | quoted | Share-based rule; FR19, FR20, AC25, AC55. On checking, the new rule had two holes: a quiet service's swap never ended a period, and one stray request could return. Both are closed |
| 9 | One bad name stops a project's sync | code | Per-row skip; FR18, AC56 |
| 10 | Coverage flips falsely at 30 days | quoted | As of a moment, last report's age; AC73 |
| 11 | Structure test does not cover the package | code | T1.11, T5.2 |
| 12 | Step 3 and 4 lines cannot be run | code | T3.0, `--deploys N`, T1.5 modes |
| 13 | Retention can break the expiry sweep | code | Arrival time, `SET NULL`, `ix_deploy_events_created_at`; AC74 |
| 14 | Files and tasks missing | code | T2.3 names, full sealing path, T4.4, T4.1 |
| 15 | Sealing needs the id before insert | code | `uuidv7()` read first; T2.3 |
| 16 | Proofs that cannot stay unchanged | code | T5.1 re-pointed, T5.4 adds `partial`, AC6 |
| 17 | Spec contradictions, FRs with no AC | quoted | FR39, AC23, AC26, AC65, AC69, AC74 |
| 18 | Work that could be deleted | code | No window on `GET /deploys`, one coverage route, per-table predicates |
| 19 | F28 deletes code F22 is told to reuse | doc | Contradictions recorded; design's impact list |

**UX**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Default window gives false reassurance | quoted | FR22, `?finding=`, "Back to finding"; AC64 |
| 2 | Default `/releases` is usually empty | code | Overlap window, new state; AC27 |
| 3 | No phone layout | code | Phone rows and blocks; NFR, AC75 |
| 4 | Failures fall to a toast | code | Per-section `InlineFailure`, chart failure line; AC38, AC68 |
| 5 | Rollout chart cannot be read | code | Categorical mode, top four, labels, share table; FR26, T4.5 |
| 6 | Marks not legible | code | One dash, HTML strip, visible line; FR36, FR37, D49 |
| 7 | Hover link cannot be reached | code | Click to pin, one list per group; AC39 |
| 8 | Setup signal has no data | quoted | Times, polling, `role=status`, words, `UnsavedChanges` |
| 9 | Errors list hides the difference | code | Words in cell, panel always states it; FR38, D23, AC73 |
| 10 | Finding sentence can mislead | code | Wording table; FR32, AC40 |
| 11 | Opening sentence written for one method | quoted | Sentence table; AC66 |
| 12 | Times have no clock | code | Project's zone; NFR, AC75 |
| 13 | Long versions have no display rule | quoted | Shared formatter; AC75 |
| 14 | Colour undefined, threshold differs | code | FR24, D50 |
| 15 | No-permission paths incomplete | code | `useCan`, words, anchor; AC75 |
| 16 | Sidebar entry contradicts IA3 | doc | Contradiction recorded; D25, T5.7 |
| 17 | Screen-reader and refresh missing | quoted | `role=status`, `Tabs`, 60-second refetch, descriptions |

**Sales**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | "No CI work" cannot be defended | code, doc | Claim withdrawn below; FR41, AC48, T2.7 |
| 2 | "Every", "exact", "own server" fail | doc | Claim withdrawn; FR50; T1.7 reports `side_by_side` reach |
| 3 | "About the same" on a worse release | arith | Four readings; FR24, D17, AC33 (z is about 2.39) |
| 4 | "Same traffic" overclaims | quoted | Wording, warm-up; FR48, AC63 |
| 5 | "Will you tell me?" has no answer | quoted | D3 partly met, AC28 records F6, FR14 recipe, lag list below |
| 6 | One rollout, two releases | quoted | `version_seen`, one variable, FR46, error row |
| 7 | Environment mixing | code | BR11, FR45, FR14, FR41, error row |
| 8 | Self-hosted CI may not reach us; "five minutes" unmeasured | doc | FR14, error row, AC1, T2.10 |
| 9 | Silent release cap | arith | Evict, stated sentence; FR56, D36, AC60 |
| 10 | BR9 is untrue | code | BR9 rewritten with its table, the 35-day answer |
| 11 | First-day facts can be wrong | quoted | `already_running`; FR43, D47, AC58 |
| 12 | Largest step rests on unknowns | doc | Founder decision, with questions and what changes the plan |
| 13 | Rival lag list incomplete | doc | List below; T5.7, recipes fill `url`, D43 |
| 14 | Three buyer questions unanswered | doc | Demo answers below; absolute figures in T1.7; design's threat row; T6.2 |

**Marketing**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Competitor table misstates rivals | web | Rows corrected, Observe added, conclusions rewritten. One part rejected |
| 2 | Approved claim is false | code, doc | Replaced here and in `research.md`; "exact p95" banned |
| 3 | "No CI work" is untrue | web, code | Wording changed; Seen says "if they send it"; T2.7 |
| 4 | No true reason to choose F28 | web | Spec's *Positioning*, with the candidate story, marked unproven |
| 5 | D3's promise can go unannounced | quoted | D3, AC28, FR55, lag list. One part rejected |
| 6 | No launch step | doc | Step 6, Claims check gate |
| 7 | "Five minutes" is unmeasured | quoted | Marked a target; AC1, T2.10 |
| 8 | "Rule" and "impact" are the wrong words | code | Release marker, comparison; D44, AC37 |
| 9 | Rows too narrow | web | Grafana and Coroot corrected. One part rejected |

**Customer**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Overlap-only comparison misleads forever (blocker) | quoted | Two blocks; FR47, D34, AC62 |
| 2 | CI and telemetry name versions differently | quoted | FR12, FR46, recipes, AC52 |
| 3 | Moving start rewrites history | quoted | As Architect 1; AC25 |
| 4 | Finding answers only for its own service | code | Project list, callees marked; FR52, D41, AC71 |
| 5 | The page carries no release | code, doc | FR44, D24, AC70. On checking, a grouped message cannot carry the line, which is now stated |
| 6 | Depends on remembering to look | quoted | `release_id`, version filter, FR55, D40 |
| 7 | Broken hookup looks healthy | quoted | Signed refusals recorded, last report's age; FR10, AC9, AC73 |
| 8 | CI may be unreachable; recipes could break deploys | doc | FR14, BR13, AC49 |
| 9 | "No CI work" overstated | code | FR14, FR41, AC48, current versions in settings. One part rejected |
| 10 | Cold starts read as `higher` | quoted | FR48, method words, `cold-start` mode, AC63 |
| 11 | Teams with little tracing | code | `no_request_traffic`, *Not served*; FR21, FR27 |
| 12 | Two environments in one project | code | FR45, D39, AC67. One part rejected |
| 13 | Service level hides one broken endpoint | quoted | FR49, arithmetic estimate. One part rejected |
| 14 | Connecting creates fake releases | quoted | `already_running`; AC58 |
| 15 | `metadata` is unmasked and widely shown | code | Masked, members only; FR9, FR54, AC53, AC76. One part rejected |
| 16 | MCP still leaks `source` and `url` | code | Explicit field list; D28, AC46 |
| 17 | "What's in it?" has no required answer | quoted | Recipes fill `url`, shown first; D42. One part rejected |

**Security**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | MCP removes only `metadata` | code | As Customer 16 |
| 2 | Sender text reachable by F4's ticket | code | `sender-details`, person only; FR54, D45, AC76. One part rejected |
| 3 | No way to remove what a stolen secret wrote | code | `webhook_secret_id`, delete routes; FR53, D37, AC61 |
| 4 | Release cap allows a lockout | arith | One cap, eviction order; FR56, AC60 |
| 5 | Flat retention outlives telemetry | code | As Architect 11 |
| 6 | Tenancy tests leave gaps | code | `require_readable_project`, AC26, deleted-project clause |
| 7 | Scheduler statements unenforced | code | T1.13, D48, AC77 |
| 8 | Browser key reaches F28 by other paths | code | `door` column, precondition; D38, AC69. Risk below |
| 9 | Webhook text skips masking | code | FR9, masking port, setup warning. One part rejected |
| 10 | F3 guard is a deny list | quoted | Exact allow list; AC45 |
| 11 | Name checks differ by door | code | One name check; FR9, AC12 |
| 12 | Hover box has no safeguard | code | React portal, direct test; AC43 |
| 13 | One audit row per deploy floods the log | code, arith | `deploy.batch_recorded`; AC8. Risk below |
| 14 | Recipes may leak the secret | quoted | FR14, AC49 |
| 15 | F22 would copy a known hole | doc | Contradictions recorded; impact list |

**QA**

| # | Finding | Checked | What changed |
|---|---|---|---|
| 1 | Rollup fault can stop detection (blocker) | code | As Architect 8; AC54, T1.4 |
| 2 | Requirements with no AC (blocker) | quoted | AC48 to AC84 added. Checked in both directions, by script and by hand: every AC is cited by a plan task, and every FR is covered by an AC. See *Second review* below |
| 3 | Algorithm cannot produce the ACs | quoted | Ordered list; failure floor selects the method |
| 4 | Time comparison fires falsely | quoted | Only when every row has no version; AC30 |
| 5 | History rewritten, no AC | quoted | AC25 with the sequence |
| 6 | AC18 cannot be met | code | AC18 restated; two-drain and junk-burst tests |
| 7 | Test bed cannot produce the claims | code | T1.5 modes, T3.0 |
| 8 | AC42 tested in the wrong place | quoted | T1.12, T3.9 |
| 9 | Alert timing not gated | code | Gate row, AC72 |
| 10 | `synced_through` undefined | quoted | Rule stated; AC55, timed check T1.16 |
| 11 | ACs test what cannot be stored | quoted | `deploy_intake_state`; AC13 |
| 12 | Limit and repeats under-specified | code | FR7, FR13, AC10, AC14, AC51 |
| 13 | Text can stop the sync; `__other__` can be sent | code | AC12, AC56 |
| 14 | Scheduler Postgres untested | code | T1.13, AC77 |
| 15 | "Source" means two things | quoted | `basis` defined and used. One part rejected |
| 16 | Gate may not prove its claim | quoted | Baseline, repeats, spread, floor label, one stopping rule |
| 17 | What cannot be tested is unstated | quoted | AC49 contract test, AC2 API test, wider *Not provable here* |
| 18 | ACs too loose | quoted | AC37, AC34, AC23, AC41, AC40 |
| 19 | AC28 assigned too early | quoted | T1.14 computes it directly; AC66 asserts the sentence; gate first |

### Found while checking, beyond what was reported

- **The comparison's "about 360 requests" was mislabelled.** It holds near 0.2%, and near 3% it is about 5,200 (arithmetic).
- **F20's D14 has moved, and F3's and F20's documents cite this design's old line numbers.** Both are in the impact list.
- **`coverage` needs no ClickHouse,** since `releases` stores the times. F20 had assumed it did.
- **FR44's line had the wrong file.** The composer sees only `Subject.detail`. It was first placed in `alerts/outbox.py`, and the second review moved the read to `alerts/delivery.py` (D51).
- **FR41 assumed language pages teach a literal.** They do not mention `service.version` at all.

### Second review: 14 problems, each verified before the fix

A second pass reported 14 problems after the changes above. Each was read against the code and these documents first. All 14 held, and all 14 are fixed in the documents. Nothing was built or run, apart from the coverage script below and the reviewer's own reproduction of the collection error.

| # | Severity | Problem | Verified by | What changed |
|---|---|---|---|---|
| 1 | Blocker | FR46, FR49, FR50 and FR55 had no AC, while QA 2 read as fixed | Script, spec | AC78 to AC83, cited by T4.1, T4.5, T5.6. QA 2's row corrected. The full check also found FR24's colours untested, which AC84 now covers |
| 2 | Major | The summary could not be delivered | `models.py:1454-1458`, `outbox.py:272-287,708-719`, `messages.py:365-381` | `notifications.release_period_id`, design section 6, T5.6, D52 |
| 3 | Major | The opt-in had no route, storage or permission | Design, `projects/router.py:91`, `permissions.py:56` | `projects.release_summary_channel_id`, the `PUT` route, `POLICY_OF`, T5.6, AC83, D53 |
| 4 | Major | Three test filenames repeated, stopping collection | `pyproject.toml:103-112`, no `__init__.py` in those folders | `test_scheduler_postgres_tenancy_structure.py`, `test_platform_releases.py`, `test_release_demo_measure.py` |
| 5 | Major | `tools/release-demo` tests would never run | `pyproject.toml:107,112`, `justfile:205` | `pyproject.toml` in T1.5 |
| 6 | Major | AC42's promise conflicted with T3.8 | `detection/notify.py:52`, `delivery.py`, `jobs/registry.py:22` | The read moved to `delivery.py`, T1.12 is transitive, D51 |
| 7 | Major | "It came first" on the wrong rows | Design table, FR32 | Rows named in the design, FR32 and AC40 |
| 8 | Major | Rule U6 conflict unrecorded | `phase-2-foundation.md:204-205` | Contradiction recorded, mapping table in the design, both impact lists, D54 |
| 9 | Major | AC17 and FR16 conflicted with the F6 change | `services.go:142`, FR16, AC17 | End state stated, the F6 change's test named, D55 |
| 10 | Minor | AC66's "about the same" with 50 requests each cannot occur | Arithmetic: SE about 0.0199, 3·SE about 6 points. Equal sides need about 300 each | The row now reads "not clearly different" |
| 11 | Minor | FR44's read could abort the claim, and N reads could pile up | `delivery.py:53-56`, `outbox.py:534-544` | Own connection after commit, one read per batch, D51, AC70 |
| 12 | Minor | AC18 and AC1 could not fail | Spec, T2.10 | AC18 asserts the outcome. AC1 states what happens if nobody is found |
| 13 | Minor | Datadog "gates" had no source | `research.md` has no such row. That shows it is unsourced here, not that it is false | Deleted |
| 14 | Minor | Two signatures for `coverage_of` | Design | One signature, the pure one |

**Corrections to the reports themselves.** Problem 4 said no duplicate test name exists today. `test_logs.py`, `test_metrics.py` and `test_traces.py` do repeat, because of `tools/otel-demo/upstream`, which is outside `testpaths`, so the point stands for what the gate collects. Problem 2 suggested only a `_TAKEN` join and composer changes. On reading, the partial unique index would have dropped every summary after the first on a channel, and `_IN_ORDER` would have treated all of them as one subject. `colour` would also have shown a report in amber. All three are in the design.

**Found while fixing, not reported.** `tools/detection-measure/measure.py` already holds the module name `measure` on `pythonpath`, so a second `measure.py` would have imported the wrong file. The new tool's is `release_measure.py`. FR44's read also now names what it leaves out: a project finding has no service.

**FR-to-AC check.** A script over the *Acceptance criteria* section found 18 of the 56 FRs named by number. The other 38 were matched by reading, as follows. FR24 is named too, and is listed because of AC84. A match means an AC tests the requirement's main promise, not every clause in it.

| FRs | ACs | FRs | ACs |
|---|---|---|---|
| FR1 | AC2 | FR24 | AC33, AC84 |
| FR2 | AC4 | FR32, FR33 | AC40, AC75 |
| FR3 | AC3 | FR34 | AC41 |
| FR4 | AC5 | FR35 | AC38, AC45 |
| FR5 | AC6 | FR36, FR37 | AC39 |
| FR6 | AC8 | FR38 | AC73 |
| FR7 | AC10 | FR39 | AC45 |
| FR8 | AC11 | FR41 | AC48 |
| FR9 | AC12, AC53 | FR42 | AC57 |
| FR10 | AC9 | FR43 | AC58 |
| FR11 | AC13 | FR45 | AC67 |
| FR13 | AC14 | FR47 | AC62 |
| FR14 | AC49 | FR48 | AC63 |
| FR15, FR16 | AC15, AC17 | FR51 | AC68 |
| FR17 | AC18, AC67 | FR52 | AC71 |
| FR18 | AC24, AC56 | FR53 | AC61 |
| FR19 | AC25, AC55, AC59 | FR54 | AC76 |
| FR23 | AC31 | FR56 | AC60 |

**Still not re-reviewed.** These fixes were checked by their author and the script above, not by a separate reader.

### What remains as a risk after the reviews

- **Nothing was run.** Every limit is a guess, and every fix above is a written requirement.
- **No one re-reviewed the changes.** Scope grew by 16 requirements (FR41 to FR56) and 37 acceptance criteria (AC48 to AC84), including a stored reading, a summary notice and a new raw-table column. It is a much bigger feature than the first draft. D57 keeps D40 and FR52 (2026-11-02); the founder may still cut either at approval.
- **A version seen only in a span more than 5 minutes later than its settled minute is missed until it is counted again.**
- **Folding can understate a real version.** The screen says so. It cannot say which.
- **A stolen deploy secret can still plant a believable marker,** on a version never seen. F3 labels the basis, and removal exists, but only after someone notices.
- **Audit rows do not expire,** and single-item requests at the limit reach about 8.76 million a year (arithmetic).
- **F6's browser-door exposure stays until the separate change lands.** See below.
- **Period thresholds can misread blue-green flips and long canaries.** All guesses.
- **Other features' documents still disagree with this one** until changed at approval. F20 now agrees (`dashboards/design.md:967`); F3's T1.4 must call, not add, D56's reads.
- **A grouped alert message does not carry the release line.**
- **Whether anyone wants this is unknown.** No buyer was asked.

## Risks

**A risk found here that belongs to F6, and that the founder should hear about.** In plain terms: a website's public key is
in its page, so anyone can read it. From the code, nothing stops that key sending a span labelled as server traffic
(`receiver/internal/otlp` reads no span kind; `traceassembly/flatten.go:185` takes it from the payload; the door is read only
for billing, `usage/usage.go:281,291`). So anyone could push made-up request counts into `service_stats_1m`, and so into
detection, which could raise a false finding or teach a false normal. **Expected from reading; not run.** `alerts/payloads.py:204`
already treats names from a browser key as untrusted, and this is about counts. F28 excludes that door from its own table, and
its `time_before_after` and completeness check inherit the exposure until the change lands.
Recommendation: a separate change record before Stage 1 ships, and required before F28's Step 4. Approved by the founder
on 2026-11-02 (foundation, *Founder decisions*, 7), and F28 owns it (item 22). Not done here: this run
changes only F28's documents, and F6 is outside it.

**Assumptions not validated**

- Rollup cost: rows, disk, worker memory. Unmeasured; the gate in plan Step 1a. If it fails, the fallback is comparison by time
  with F6's readings. That also loses seen releases and the no-webhook route, as the design lists.
- How many customers set `service.version`, and what they put in it.
- All limits and thresholds marked *starting guess* in the spec.
- **The whole 16 GB server is not measured before deployment** (foundation item 9: no rented test server). Step 1a's per-container
  figures come from the Mac's Docker; the whole-server fit waits for Oracle after deployment. Oracle's free allowance is 2 Arm CPUs
  and 12 GB, below the 16 GB minimum, so that measurement needs a paid shape, which returns to the founder then.
- Whether people want a release screen at all rather than the fact on the finding. No interview data.
- Whether F6 opens a finding when a release fails 3% at 20% of traffic (0.76% mixed). AC28 records it.
- GitHub's own delivery payload was never confirmed, and that route is deferred.
- Competitor facts were read through a summarizing tool on 2026-11-02, and must be checked before any public claim. **We must
  not claim to be more accurate than anyone.**

**Known to lag rivals, plainly**

- Datadog: per-endpoint comparison, any two versions, automatic faulty-deployment detection.
- Sentry: suspect commits, commit and author lists, deploy emails.
- Coroot: an automatic post-rollout audit across restarts, CPU, memory and log errors, with a Slack notice.
- Commits appear as plain text with a link from CI, until F22.
- A release comparison opens no finding. Only F6 can, so the catalog's "regression surfaced as a finding" is only partly met.

**What may be said once built, and true**

"When your spans carry a build id in `service.version`, Nexovue compares old and new from counts taken before its own sampling,
and every comparison says how it was made. Request and error counts are exact for the spans Nexovue receives; p95 is an estimate
from latency buckets. If your deploy already passes a build id into `service.version`, releases appear with no new webhook."

Only in the future tense until shipped, and re-checked in plan Step 6. **The earlier line is withdrawn.**

**Re-checked in Step 6, 2026-11-22:** the list of allowed and banned sentences, each checked against the code, is now in `phase-2-foundation.md` section 12 (*F28: releases and deploys*) and is the one to use. The block above is kept as what was approved in 2026-11.

**Do not say:** "every release is compared", "exact p95", "no CI work", "on your own server" to a hosted buyer, "no extra charge"
to anyone paying for their own disk, "automatic deploy detection", "more accurate than" anyone, or a rival's name in public.

**Demo answers, for when someone asks**

| Question | Answer |
|---|---|
| Will you tell me when a release goes bad? | Not by itself. F6 tells you if the service as a whole looks wrong. A release can be compared on its page, by your pipeline through the API, or in an optional summary. Whether F6 fires on a small rollout share is being measured |
| Do I need to change CI? | One step, or one change per service to pass a build id into `service.version`. Comparisons start from your next rollout |
| Will it compare my small service? | Not always. Low traffic reads "too little traffic" or "not clearly different" |
| Is old against new fair? | They are the same minutes. They may not get the same mix of requests, and new instances start cold, so p95 leaves out each version's first 5 minutes |
| I sample before sending | Counts are short by that factor, and nothing in the data shows it. The page says counts assume no sampling |
| How much more disk? | On demo data on a development machine, about 10% more rows and disk than the per-service table when every service rolls out once an hour. Not a figure for your traffic or a 16 GB server. A self-hoster pays for that disk |
| Can I import past deploys? | Up to 7 days back |
| Why 35 days on a 395-day plan? | Per-minute version counts are kept 35 days at most, as detection's are. The screen states your project's limit |
| Can a public browser key fake my numbers? | Not version numbers or a server release's errors. Totals in the time comparison can be, until the separate change lands, which is required before that page ships |
| My CI cannot reach a private install | Use a self-hosted runner, or the `service.version` route |

## Validation

**Nothing was run:** no test, query, request or measurement. The documents rest on reading the code on branch
`feature/anomaly-detection`, with uncommitted work present, and vendor pages read on 2026-11-02. "Expected" throughout means read,
not reproduced. Scripts confirmed that every acceptance criterion is cited by a plan task, that FR and AC numbers have no gaps, and which FRs the ACs name. The rest were matched by hand (*Second review*).

## Specification impact

These documents are the specification. Other documents affected at approval, none changed in this run. The design's
*Specification impact* has the detail.

- `phase-2-foundation.md`: sections 3 (rule U6, D54), 8, 9 and 12, and line 495.
- `feature-catalog.md:91,104,371-372`.
- F3: two reads, `release_comparison`, release lag, `basis`, fixture, line citations.
- F20: D14 moves to `markers_in_window`, and coverage to `coverage_of`.
- F22: its own header-id check and dedupe; the savepoint it cites is rewritten.
- F4: `sender-details` refuses its ticket; `list_deploys` has an explicit field list.
- F21: a new line in `_compose_finding`, and a new frozen kind.
- Console redesign: IA3.

## Documentation impact

None updated yet, since nothing is built. At build: `docs/product/setup/` (a new page, corrected platform pages, language pages
and onboarding), `docs/architecture/database.md`, `docs/features/integrations-deploy-events/spec.md`, and
`docs/self-hosting/sizing.md` if the gate shows growth. `000035`'s comment "nothing else reads this table" will go stale, and
`database.md` carries the correction.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- The scheduler Postgres tenancy test: corrected from "does not exist, F28 builds it" to "exists at `apps/api/tests/test_scheduler_postgres_tenancy_structure.py`, F28 extends it" (design, spec D48, plan C7 and T1.13). Read from the code.
- Bases: F28's table now uses F3's `verified`, `telemetry`, `telemetry_text`, and a seen version is `telemetry_text` everywhere (design, spec D54).
- The cap on `releases` now states F3's floor on `scheduler-light`, which F3 and F19 asked for (design).
- The finding page: F28's section is folded into F3's as its first row, and F5 extends it (design). F28's AC40 is proved through it.
- F22's webhook has no delivery table, so F28's ask for a dedupe is withdrawn; `signatures.py` stays a plain verifier (design).
- The browser-door change is now one record shared with F3 and F19 (plan, foundation section 9).

## Last updated

2026-11-22: Step 6 built in `wt/f28s6`, awaiting commit: competitor rows re-read (research section 10), the launch sentences checked against the code (foundation section 12), Playwright and Design QA at 375px, the errors read and the 2-second target measured, a labelled demo recording, and the `#deploys` fix.

2026-11-12: Steps 4 and 5 committed, Step 5's migrations re-chained to `0074` and `0075` on F4's `0073`. Earlier the same day: Step 5 built in `wt/f28s5`, and Step 4 in `wt/f28s4`.

2026-11-09: Step 3 committed to `feature/phase-2`, with the carried-over fix to `first_reported_at`'s two readers.
Earlier the same day: Step 2 committed to `feature/phase-2`; the founder's answer on failed and rolled-back reports
built into it.

## Founder answer, 2026-11-09

**Does a report of a `failed` or `rolled_back` deploy open a period?** The design's
step 6 opened one for a version with none, whatever its state, and that was built first; AC57 says only that
`rolled_back` on a *seen* version sets its state. So a version first reported as `failed`, never seen, gets a
reported period, appears on `/releases` as starting then, and on a service with no version in traffic ends the
period before it. Options: keep it (as built: the sender said it was deployed, and the release page shows the
state); or open periods only for `started` and `finished`, keeping the release and its state with no period, so a
failed deploy never reads as serving. **Recommendation: the second**, since a failed deploy did not replace what was
running; it is a small change in `releases/service.place_periods` and its live tests. **Founder answer, 2026-11-09:
started and finished only.** Built: such a report is stored and its state shown, but opens no period, ends none and
brings no version back (design, *From the webhook*, step 6; tests under *Step 2, built 2026-11-08*).

Left as they are, found in the self-review, none blocking (each open to overrule):

- **Fixed in Step 3 (2026-11-09)**, see *Step 3, built 2026-11-09*: **`first_reported_at` still counts a failed
  report.** It is the release's reported fact, so it stays, but two
  readers lean on it: the sync's `already_running` (`_first_on_service`: a version reported, even as failed, before
  its first counted minute is not "already running"), and the comparison's time-based fallback on a service with no
  version in traffic (`choose_method`'s `reported_at`: a version reported failed at 10:00, then finished at 11:00,
  is compared from 10:00 while its period starts at 11:00). Neither opens or ends a period. The second is the one
  worth changing (compare from the period's start); not done here, since it is the comparison's rule, not the
  founder's question.
- **A `started` report then a `failed` one for the same version** keeps the period `started` opened: the answer
  says which reports open periods, not that a later failure closes one.
- **`OPENS_A_PERIOD` names the states as plain strings** beside `deploys/schemas.DeployState`; a new state added
  there would open no period until listed here. *Step 3:* now `nexovue_platform.releases.OPENING_STATES`, beside
  `DEPLOY_STATES`, used by the API and the sync.

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in this feature's documents was checked against the code, the foundation and fresh reading.

- **Settled by the foundation, removed from the open lists:** customer conversations before Step 1b (item 8: one round,
  before Step 1b); the browser-door change before Step 4 (item 7, so the "accept the exposure" path is withdrawn, D58); and
  D1's difference from the foundation, which no longer exists (`phase-2-foundation.md:547`).
- **D56, decided:** F28 builds the two scheduler reads F3 found missing, `release_before` and `releases_near`, in T3.8. F3 calls them.
- **D57, decided:** the opt-in release summary and the project release list stay. Coroot and Sentry both notify on a release,
  and F3 already relies on the list. The summary is built last, so it can still be dropped cheaply.
- **D58, decided:** Step 4 waits for the browser-door change.
- **Still the founder's, only if it happens:** if the Step 1a cost gate fails after one tightening, whether to pay the extra disk
  or drop version comparison and its "no new webhook" promise. Recommendation recorded in `spec.md`.

**Review, 2026-11-02.** A second pass checked each answer against the code, the foundation and the cited pages.

- **Held:** the customer conversations (foundation item 8), D1 (`phase-2-foundation.md:547` reads as stated), D56 (no
  `nexovue_scheduler/releases/` exists yet, and F3's `design.md:300` and `plan.md:125` ask for exactly these two reads), and
  D58 (foundation section 9 says F28's Step 4 waits). D57 stays the agent's call: it keeps written scope and changes nothing sold.
- **Fixed, design:** `releases_near` had no `service` argument, so it could not put "the same service first" (FR52). Added,
  with a note that marking callees reads `service_edges_1m` and is the caller's part.
- **Fixed, spec D57:** Sentry's deploy notice goes to the people who committed to that release, not to everyone, and Coroot's
  goes only on notable changes. Both pages were re-read directly on 2026-11-02.
- **Fixed, the conditional founder question:** the old recommendation said "after one more tightening", but the plan allows
  one, and it used the 2x disk bound, which is one of the checks that would have failed. It also called "no new webhook" the
  buyer's main promise, while the spec calls it table stakes. The recommendation now splits by which check fails: a cost
  check alone means keep and pay if it fits the 16 GB server (`docs/self-hosting/sizing.md`); a correctness check means time
  comparison only. It stays the founder's, and only if the gate fails.
- **Still outside F28:** F3's `plan.md:125` (T1.4) must say it calls D56's reads instead of adding them.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** F28 owns the shared browser-door change (item 22). The later gate after Step 1a (item 24): if only a
  cost check fails, keep version comparison and pay, if it fits the 16 GB server; if a correctness check fails, time comparison only.
- **Changed, A: no rented test server.** Long runs and server measurements happen on Oracle Cloud after Phase 2 is deployed. For F28,
  Step 1a still runs as planned, on the Mac's Docker: its thresholds are shares and per-container ceilings. **What now waits for Oracle:**
  the whole-server fit on 16 GB, so `sizing.md`'s new figures are labelled "measured on a development machine" until then.
- **Changed, B: no paid model calls.** F28 calls no model, so nothing changes here.
- **Open decisions:** none for the founder. The Oracle shape to measure on comes back at deployment (foundation, *What A and B change*).

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 3 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.
