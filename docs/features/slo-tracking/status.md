# Status: slo-tracking (F19)

## Lifecycle status

**Approved; plan approved, ready to implement.** The founder approved the specification, design and plan on 2026-11-02. No code is written.

## Current task

None. Waiting for F6 (pull request #24) to merge.

## Completed

- **Research** ([`research.md`](research.md)), 2026-11-02: code, competitors, personas, methods. Rival rows corrected after review.
- **Specification, design and plan**, 2026-11-02, written against foundation and the code on `feature/anomaly-detection`.
- **Three things research left untraced are now traced by reading:**
  - A self-hosted organization is on Free's row, so it keeps 7 days raw and 30 days derived. This changed the history design from an hourly table to daily counts in Postgres (spec D2).
  - Check readings use derived retention.
  - Browser-door spans do reach the rollups. Nothing filters them.
- **Eight persona reviews,** then a full rewrite of the design and plan against the spec, and changes to the spec and research. See *Reviews*.

## Next action

Start per [`phase-2-tracker.md`](../../product/phase-2-tracker.md) once F6 (pull request #24) merges: Step 1, after re-reading every line reference (plan P1). File the design's four proposed ADRs in `docs/decisions/` at the start of implementation, numbered then. T1.5 needs F3's floor (plan P3): build it there if F3's T1.7 has not merged.

## Open decisions

**None for the founder.** Both were approved on 2026-11-02, as recommended (*Founder answers, 2026-11-02*, below). The gate's bar and P2's span-kind question are decided (spec D62, D63).

## Blocked

- **F19's build** waits for F6 to merge, and for F28, F3, F22, F4 and F5 in the plan's order.
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

**Almost nothing was run.** No test, query or measurement of the product. One read-only run of the no-AI test's own import walk, from a scratch script, gave the evidence for D55. Documents only. Code was read, on a branch with uncommitted work, so line numbers will drift. The plan requires reading them again at start. A script checked that every FR and AC in the spec has a plan task and test, and that every T-number and D-number cited exists. That is a check of the documents, not of the design.

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

2026-11-02

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
