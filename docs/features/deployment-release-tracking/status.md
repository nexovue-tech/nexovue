# Status: deployment-release-tracking (F28)

## Lifecycle status

**Approved; plan approved, ready to implement.** The founder approved the specification, design and plan on 2026-11-02. No code exists yet.


## Current task

None. Waiting for F6 (pull request #24) to merge.


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

## Next action

Start per [`phase-2-tracker.md`](../../product/phase-2-tracker.md) once F6 (pull request #24) merges. Start at plan Step 1a (and the browser-door change it owns), after re-reading line references. File the design's 3 proposed ADRs in `docs/decisions/` at implementation start, numbered then.


## Open decisions

**None.** The customer conversations (foundation item 8) and the browser-door change (item 7) were settled by the foundation
on 2026-11-02, and D1 now matches the foundation's section 9. The browser-door owner (F28) and the conditional gate after
Step 1a were answered on 2026-11-02 (*Founder answers, 2026-11-02*, below).

## Blocked

- **The build is blocked on F6 merging** (findings, `service_stats_1m`, `detection_sweep`).
- **Blocked on founder approval** of specification, design and plan.
- **Step 1b waits for the one round of customer conversations** (foundation item 8).
- **Step 4 is blocked on the shared browser-door change** (below; foundation item 7, D58). **F28 owns it** (foundation item 22,
  2026-11-02). The plan holds no task for it yet; they are added when the plan is next revised.

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
| How much more disk? | Not known yet. The cost report will give figures per million requests. A self-hoster pays for that disk |
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

2026-11-02

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
