# Status: ai-solution (F5)

## Lifecycle status

**Proposed.** Specification, design and plan are written, reviewed by eight personas and changed as a result, and awaiting the founder. Nothing is approved, and no code exists.

## Current task

The founder answered [`spec.md`](spec.md)'s founder decisions on 2026-11-02. Waiting for the founder's review of [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md).

## Completed

- **2026-11-02:** [`research.md`](research.md): attacker view, competitors, methods compared, GitHub's documents.
- **2026-11-02:** spec, design, plan and this status, read against the code on `feature/anomaly-detection`.
  - The reading **settled two things the research left open:** the demo's faults are coded flags (`tools/otel-demo/upstream/src/payment/charge.js:39-47`), so the fix run needs its own fixture repository; and stored span and log rows appear not to record whether a browser key sent them (a search of migrations, not a read of every table), so every stack is treated as attacker-written.
  - **It found three things nobody had asked about:** a compromised AI process could store a harmful proposal, so the checks run again in the writing room (D18). The copyable brief goes straight into an agent that can run commands, so it holds no attacker-written text (D19). A member with no access to a repository can read quoted code through Nexovue, unless the owner says otherwise (D20).
  - **It found two errors in the foundation:** the room table and rules disagree about who holds `AI_SECRET_KEY`, and a test it calls unbuilt exists (`test_scheduler_postgres_tenancy_structure.py`).
- **2026-11-02:** eight persona reviews, each finding checked, and the documents changed (*Reviews*, below). `plan.md` was rewritten from the current design, and `research.md` gained a *Superseded by the spec* list and sourced rival rows.

## Next action

F5's questions go into the shared buyer round (foundation decision 8; spec, D57). The founder's decisions were answered on 2026-11-02. With that approval, in the free test organization shared with F22, run the GitHub trial on a scratch repository, about two hours (a guess) and 21 questions (`design.md`, *The GitHub trial*), and write its results into the design.

## Open decisions

**None for the founder.** All five were answered on 2026-11-02 (*Founder answers, 2026-11-02*, below). The write-access go is still given at T6.5, against the approved rule.

**Not decisions, but still to happen:** the shared buyer round (approved, foundation decision 8) and the GitHub trial (approved 2026-11-02). Both block design approval.

**Removed from this list, with where each went:** the buyer conversations (foundation decision 8; D57); F5 and the hosted service (foundation decisions 1 and 2, FD19; D59); when a draft opens (D58); publishing verdict and refusal numbers (foundation decision 5 names them); the bed's money as a process (foundation decision 4; now item 3's amount); D1 to D56 (decided, approved with the spec, not open questions); the foundation's 15 differences (checked, none open: `design.md`, *Where this differs*); the finding page's order (D54; F22 carries the order at `case-management/design.md:91`, but not yet the *Suggested fix* jump link, see `design.md`, *What other features must carry*).

## Blocked

- **Everything in the plan is blocked** on F6 merging, then F28, F3, F22 and F4 being built.
- **Design approval is blocked** on the GitHub trial, which has not been run.
- **Step 3 is blocked** on F3's evidence holding a group id and window.
- **Step 7 is blocked** on T6.5: the safety gates, the founder's go, and the buyers' answer.

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
- **Tested only on free models (foundation items 4 and 10, 2026-11-02).** A call of about 25,000 tokens (a guess) is above some free tiers' per-minute limit, and 240 or more calls per option at daily caps take days. Free tiers may train on inputs and let people read them, so only fixtures and nexovue's own public code are sent.
- **"Named option" covers only what was run.** OpenAI, Anthropic and Bedrock have no free API tier, so they stay "not yet tested", and the write-access gate (T6.5) can only pass on a free-tier or local model. A small local model on the 24 GB Mac may be weak at code, and competes with the dev stack for memory (decision 0047).
- **No server measurement before deployment** (foundation item 9). F5's memory share of `scheduler-insight` (768 MB, a guess) is read on the Mac's Docker; the 16 GB server waits for Oracle.

## Validation

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

2026-11-02

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

**F5's questions for the shared round (D57).** Screen: "Would you run this yourselves, and do you use GitHub.com?" Past behaviour first: "Describe your last three incidents: was there a stack, and what did you do first? How often do you deploy? Which GitHub plan? Which git host and CI? Who approves a new GitHub App, and did they last time? Do you already use an agent on incidents?" Then the real consent screen wording: "Would you send source to your AI provider, per repository? Would you use a suggestion nobody compiled? Would you grant write access? Would you want a draft pull request from an error stack alone, with no deploy behind it? Would you run this self-hosted or want it hosted? What would you pay, and for what?"

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
