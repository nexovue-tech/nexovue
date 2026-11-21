# Phase 2 tracker

> **Type:** Progress tracker · **Started:** 2026-11-02 · **Owner:** the founder
> **Plan:** [`phase-2-plan.md`](phase-2-plan.md) · **Shared decisions:** [`phase-2-foundation.md`](phase-2-foundation.md)

This page answers one question: **how far along is Phase 2?** Each feature's own `status.md` holds the detail.
When a step's state changes, update its row here in the same commit.

States: **Docs** (spec, design and plan written) · **Approved** (the founder approved the plan) ·
**Building** · **On the branch** (committed to `feature/phase-2`) · **Merged** (in `main`, after the final pull request) · **Measured** (checked on the deployed
server; see foundation, *What A and B change*).

## Where it stands, 2026-11-02

| Stage | Feature | Docs | Spec approved | Plan approved | On the branch | Merged | Measured |
|---|---|---|---|---|---|---|---|
| 1 Detect | [F6 Anomaly detection](../features/anomaly-detection/status.md) | Done | Yes | Yes | Yes | Yes, PR #24 | After deploy |
| — | [Browser-door change](../changes/browser-door/status.md) (owned by F28) | Done | — | Yes, 2026-11-07 | Built 2026-11-07, all four steps, live test passed on a local stack; on the branch 2026-11-07 | No | After deploy |
| 2 Explain | [F28 Deploy and release tracking](../features/deployment-release-tracking/status.md) | Done | Yes | Yes | Step 1a (2026-11-07); cost gate passed on demo data ([report](../research/release-rollup-cost-2026-11-07.md)), its alert-delay row measured in Step 1b. Step 1b built 2026-11-08 (releases from traffic, the sync, `/releases` and the comparison by version; migration 0054, ADR-029), on `feature/phase-2`. Step 2 (2026-11-08: the batch deploy envelope signed per item, sealed shown-once secrets, the Deploys section, the setup page and its tested recipes, removing deploys; migration 0057, ADR-033). T2.10's timed new person: not yet run, founder tests after Phase 2. Step 3 (2026-11-09: markers on every chart with words, hover, pinned box and text list; `/releases/markers` and `/releases/coverage`; the finding's *Release before this*; errors' coverage words; FR44's line in a finding's notice; `releases/facts.py` for F3; the seed's releases; no migration). **Step 4 built 2026-11-12, committed 2026-11-12** (the whole release page: completeness, combined versions, two environments, last week, the window from a finding, every reason; errors by version, rollout share, sender details for a person only; later findings and deploys on the release; FR46 and the coverage strip on the list; retention of deploys and releases with a per-table guard on the expiry sweep; the words and raw-read structure test; `door` on `spans` and `logs`, ClickHouse 000041 and 000042; no Postgres migration, no ADR). **Step 5 built 2026-11-12, committed 2026-11-12** (what other features need, and telling people: `DeployHistory` and a plain `DeployFact` in `nexovue_platform.releases` with the comparison's shared SQL fragments; `release_comparison` in the scheduler's facts; F28's modules in the no-AI walk; MCP `list_deploys` by an explicit field list with `partial`, and `GET /deploys` without sender text; the hostile fixture for F3; the stored reading 30 minutes in, the list's *failure share higher* and *p95 higher* flags and combined-versions state, FR45's warning in Deploys, and the opt-in release summary to a channel people read, `PUT /projects/{id}/release-summary`; Postgres 0074 and 0075, ADR-066). Errors-read cost at 24 h, the 2-second target, Design QA at 375px, Playwright and the `tools/release-demo` judging deferred to hardening | No | After deploy |
| 2 Explain | [F3 AI insights](../features/ai-insights/status.md) | Done | Yes | Yes | Step 1 (2026-11-09: facts on the finding page, no AI: the `evidence` job, rules, `GET /findings/{id}/explanation` and read-again, *What lines up with this* with F28's release row folded in, `just explain-demo`; migration 0066, ClickHouse 000040, ADR-039). Founder's "does the first screen tell you where to look": not yet run, founder checks after Phase 2. Four open decisions in its status. Step 2 (2026-11-09: links at the finding's window in the address, the card's cause line, the `explained` follow-up and its per-channel switch, ratings and `/settings/explanations`, `/findings/sample`, an incident's Look next; migration 0067, no ADR); T2.13 unticked (AC104's deploy and traffic dates need a source, open decision); the Slack/email follow-up and Playwright checks: not yet run, founder checks after Phase 2. Step 3 built 2026-11-11, committed 2026-11-11 (the fence: idle `scheduler-insight` with its own Postgres account `nexovue_insight`, four queue functions, the `ai` network, `ai_*` tables; migration 0070 on F22's 0069, ADR-040). Spike passed (a column-level `UPDATE` does not permit `LOCK TABLE`); it found two gaps no grant closes (`NOTIFY`, overridable timeouts), open decision 7. T3.4, T3.10, T3.11 unticked: `just insight-fence` written, not yet run. Step 4 built 2026-11-11, committed 2026-11-11 (connect a provider, test it, limit, preview, consent, switch, opt-outs, usage and runs: `nexovue_platform.ai`'s three wire formats, the connect-time transport and the connection test on `scheduler-insight`, `/ai` routes, `/settings/ai`; migration 0078, ADR-047). Built against fake providers only: no format is offered until a dated real call, which needs the founder's free-tier key; T4.7's Playwright check and T4.8's customer doc deferred to the hardening pass; T4.9 (Bedrock signing) not run. Step 5 built 2026-11-11, committed (wording checked by code, with a limit that stops new runs: the validator, the wording decision in the evidence job, the `word` job and AI sweep on `scheduler-insight`, reservations and the run cap, read-time checks and "Write in plainer words", the canary, metrics and alerts; migration 0072, no ADR). Against fake providers only; T5.8's Playwright check deferred, T5.9 not built (needs `just insight-fence` and Playwright); open decision 7 still the founder's. **Steps 2c and 6 (code) built 2026-11-21, committed** (`tools/ai-eval`: stated-cause scoring, answer keys, stored facts and replay, the deploy scheduler, the healthy run's windows, forged spans, 100 findings at once measured live, then the bed's harness and report skeletons on fake providers; no migration, no ADR). Waiting: T2.17's runs and report (a machine that stays on), T2.18 (founder), T6.3's real-model runs (founder, after Phase 2), T6.5 (nothing has passed AC63); six open decisions in its status | No | After deploy |
| 3 Track | [F22 Case management](../features/case-management/status.md) | Done | Yes | Yes | Step 1 (2026-11-07); Step 2 (2026-11-07) except T2.9, which waits on F3. Step 3 (2026-11-08) against a simulated GitHub (T3.0 and the real-browser checks are the founder's after Phase 2); its four decisions answered 2026-11-09. Step 4 (2026-11-09; migration 0061, ADR-035), against the simulated GitHub, without T4.8 (real GitHub, after Phase 2) and T4.9 (memory); seven decisions open for the founder, none blocking. Step 5 (2026-11-09: the webhook doorbell, T5.1; no migration; T5.2's real delivery is the founder's after Phase 2); four decisions open, none blocking. Step 6 (2026-11-11: reference links, promotion, service mapping, public repositories; migrations 0068 and 0069, to be re-chained; no new ADR), against the simulated GitHub; eight decisions open, none blocking. Step 8 next; Step 7 waits on Atlassian | No | After deploy |
| 4 Ask | [F4 AI chatbot](../features/ai-chatbot/status.md) | Done | Yes | Yes | Step 1 (2026-11-02); Step 2 built and committed 2026-11-12, locally on `wt/f4s2`, not pushed, on F3 Step 5 (ticket, loop, checks, API, panel, Chat settings; the one real local-model call not yet run, needs the founder's key or local model); Step 3 built 2026-11-12, committed 2026-11-12, locally, not pushed (23 tools, aged-out ranges, quotes by reference, figure flags, real links, starters, error cases; no migration, no new ADR; real-model parts not yet run); Step 4 not built (needs real models); Step 5 built 2026-11-21, committed, on `wt/f4s5` (runbooks, knowledge chunks, keyword search, fact lines, runbook quotes; migration 0076, ADR-067; T5.7's bed re-run waits on Step 4 and needs the founder's key or local model); Step 6 built 2026-11-21, committed, on `wt/f4s6` (embeddings and hybrid search, fake providers only; migration 0078, ADR-079, ADR-080; real calls, T6.4's measurement and T6.6's bed not yet run: the founder's, after Phase 2); Step 7 built 2026-11-21, committed, on `wt/f4s7` (case notes and resolved cases in knowledge, a note ranked above fact lines, Save as runbook, `get_finding_timeline`, F22's note box and *What leaves this server* row; fake providers only; migration 0080 (`down_revision` 0078 until re-chained at commit), ADR-088; T7.3's bed run not yet run: the founder's, after Phase 2, and it waits on Step 4); not on the buyer round (foundation item 27) | No | After deploy |
| 5 Fix | [F5 AI solution](../features/ai-solution/status.md) | Done | Yes | Yes | Step 1 built 2026-11-11, committed 2026-11-11 (the deploy line's earlier version inside F28's row, and the brief under it, with AI off; `GET /findings/{id}/fixes`; no migration, no ADR). T1.6's Playwright spec written, not run: deferred to F5's hardening pass (founder, 2026-11-11). **Step 2 built 2026-11-11, committed 2026-11-11** (`wt/f5s2`, on Step 1's commit): the rules module K1 to K10 with its lists, diff, patch, display facts, secret patterns and prose checks in `nexovue_platform.fixes`; the fixture repository of 12 seeded bugs, the span seeder, the rules report (`docs/research/f5-rules-report-2026-11.md`) and the traceability check in `tools/ai-eval/fix/`; F22's simulated GitHub extended with git data and pull requests. No migration, no ADR, no model, no real GitHub. Open decisions in its status, none blocking Step 2b; the founder reads the rules report (Step 2's *Judgeable*). **Step 2b built 2026-11-21, committed** (`wt/f5s2b`): the general suggestion without code access, with fake providers only: `finding_fixes` (migration 0077, re-chained after F4 Step 5's 0076), `REQUEST_FIX`, G1 to G9, the `insight-fix` queue (2 of 8 slots), the job, the sweep, its own routes and section, the bed (real run not yet run: the founder's, after Phase 2); ADR-073 to ADR-075. **Step 3 built 2026-11-21, committed** (`wt/f5s3`): consent per repository and folder, code locations, the Nexovue suggestions App (read only), the GitHub account link, and reading and listing a suggestion's files from the simulated GitHub only; Send stops at Step 3's guard and nothing reaches a provider; migration 0079 (down_revision 0077 for now, re-chained at commit after F4 Step 6's 0078); ADR-082 to ADR-084; open decisions 28 to 41 in its status. **Step 4 built 2026-11-21, committed** (`wt/f5s4`): the console's two GitHub return pages, then the suggestion with fake providers only: Send to the AI role, K1 to K10 and display facts, the model asking for files, the diff read back checked, Check my access, the sweep's records and the requester's emails; migration 0081 (down_revision 0080); ADR-091; T4.11 (outbound limit) not built, open decision 43; real provider runs not yet run (the founder's, after Phase 2); open decisions 43 to 58 in its status. **Step 5 built 2026-11-21, committed** (`wt/f5s5`, on Step 4's commit): the patch rebuilt by a person's own check run against the simulated GitHub, its download and the tamper alert; "Was this suggestion useful?" for both kinds; taking stored code back at every end (an owner's withdrawal, the Fix App's disconnect, AI off, 30 days, GitHub access gone 72 hours, a deleted project, a removed member), the owners' email when consent ends by itself, and the backup and restore keeping the deletion promises; no write access, fake providers only; migration 0082 (down_revision 0081); ADR-094 to ADR-096; T5.5's Docker release check written, not run; open decisions 59 to 76 in its status | No | After deploy |
| 6 Around | [F19 SLO tracking](../features/slo-tracking/status.md) | Done | Yes | Yes | Step 1 and Step 2 (2026-11-08); T2.7, judging the suggestions, waits for the founder. Step 3, the monthly answer (2026-11-08). Step 4, burn alerts (2026-11-08, under foundation item 27; migrations 0058 to 0060, ADR-034); its decisions answered by the founder 2026-11-09. Step 5, what a burn may withhold (2026-11-09; migrations 0063 and 0064, ADR-038); the PagerDuty and inbox checks are the founder's after Phase 2. Step 5b, the budget reader F20's panel reads (`SloBudget`, a live target by name inside a project, `GET /slos/{id}/budget`), added by founder decision 34 (2026-11-21) and built 2026-11-21, committed (no migration, no ADR; open decision 4, which window, in its status) | No | After deploy |
| 6 Around | [F20 Dashboards](../features/dashboards/status.md) | Done | Yes | Yes | Step 1 (2026-11-02); Step 2 (2026-11-08; migration 0056, ADR-031 and ADR-032; its downgrade refuses, founder 2026-11-09). Step 3, the builder (2026-11-09; no migration; the phone check of the picker is the founder's after Phase 2; three open decisions in its status). Step 4, the chart does a dashboard's job (2026-11-09; no migration; T4.10's 30-chart trace and phone tap are the founder's after Phase 2; open decisions 5 to 7 in its status). Step 5, every panel kind and marks on charts, built 2026-11-11, committed 2026-11-11 (no migration; ADR-045, the API's own bounded ClickHouse account; the measurement T5.7, the browser specs and the phone check deferred to the hardening pass; open decisions 8 to 12 in its status). Step 6, the address, refresh and staying honest, built 2026-11-11, committed 2026-11-11 (no migration, no ADR; the three browser specs and the 375 px run deferred to the hardening pass; open decisions 13 to 16 in its status). Step 7, staying few and recoverable (list, last opened, picker, versions, Recently deleted, purge, dashboards showing a service), built 2026-11-12, committed (no migration, no ADR; the 375 px and phone Safari checks deferred to the hardening pass; open decisions 17 to 21 in its status). Step 8, metric series (a gauge or sum by one chosen series or all combined, `series_changed`, `too_many_series`, `GET /metrics/{name}/series`), built 2026-11-12, committed (no migration, no ADR; T8.1 found the metrics screen's histogram and heatmap subtracting across instances, recorded as its own bug and not fixed; open decisions 22 to 28 in its status). Step 9, starting points, export and import (*Start from Overview* whole, *Service health* and *Infrastructure*; "Start a dashboard from this service"; export with its warning; `POST /dashboards/validate` and import as a draft, new or over a dashboard), built 2026-11-12, committed (no migration, no ADR; browser and phone runs deferred to the hardening pass; open decisions 29 to 34 in its status). Step 10, drag to reorder beside the buttons (`@dnd-kit/core` 6.3.1; a handle, a pointer only, other panels held still, the page's one live region), built 2026-11-21, committed (no migration, no ADR; the Playwright spec written, not run). T10.2, the reliability-target panel on F19's Step 5b, re-planned by founder decision 34 and built 2026-11-21, committed (no migration, no ADR; decision 35 settled; open decisions 36 to 38 in its status) | No | After deploy |

**All seven features' specs, designs and plans, and the foundation, approved by the founder on 2026-11-02.** F6 merged on 2026-11-02 (PR #24). Each feature starts as *The order of work* below allows, on the one branch `feature/phase-2`. Founder questions open: **none** (all approved 2026-11-02). **2026-11-08:** the buyer round is a validation milestone, not a gate, and real-GitHub checks wait for the founder's test after Phase 2 (foundation items 27, 28). F19's SLO work runs on `scheduler-light`, not a new container (F19 D66, 2026-11-02). **2026-11-21:** every real-AI-model test is the founder's after Phase 2, and Jira (F22 Step 7) is out of Phase 2 (foundation items 31, 32). F5 Steps 7–8 are built now with write access off, and F19 gains the budget reader F20's reliability-target panel needs (items 33, 34). Waiting on outside parties: the lawyer's review (foundation items 3 and 16).

## The order of work

Nothing starts before **F6 merges**: findings and migrations 0041 to 0050 exist only on its branch.

What each feature needs before it can start, read from the documents (foundation section 9, each plan):

| Work | Needs first |
|---|---|
| F20 Step 1 (`/` moves to the Findings feed) | F6 |
| F4 Step 1 (safer MCP server, findings tool) | F6 |
| Browser-door change | F6 |
| F28 Steps 1a to 3 | F6 |
| F28 Step 4 onward | The browser-door change |
| F3 Steps 1 to 2c (facts, no AI) | F28's release functions (F28 Step 1b to 3) |
| F3 Steps 3 to 7 (AI container, provider, wording) | F3 Steps 1 and 2. Not the Step 2b go decision, which the founder reads after Phase 2 (foundation item 30, 2026-11-09) |
| F22 Steps 1 and 2 (cases on findings) | F6 |
| F22 Steps 3 to 6 (GitHub connection) | F22 Step 2. Built against a simulated GitHub; the founder tests real GitHub after Phase 2 (foundation item 28) |
| F22 Step 7 (Jira) | Out of Phase 2 (founder, 2026-11-21, foundation item 32) |
| F4 Steps 2 onward (chat panel) | F3's provider (F3 Step 4). Not the buyer round (foundation item 27, 2026-11-08) |
| F5 Steps 1 and 2 (deploy line, brief; the checks) | F28's release facts and F3's fix brief. Not the buyer round or the GitHub trial (foundation items 27, 28, 2026-11-08) |
| F5 Step 2b (general suggestion without code access) | F5 Step 2; F3's provider (F3 Step 4). **Not F22,** so it can ship before rung 3 (F5 D72) |
| F5 Steps 3 onward (rung 3 and up) | F22's GitHub connection (F22 Steps 3 to 6) |
| F19 Steps 1 to 3 | F6; for T1.5, F3's floor (F3 T1.7), or F19 builds it first (F19 plan P3) |
| F19 Step 4 onward (burn alerts) | The browser-door change |
| F20 Steps 2 to 4 | F20 Step 1 |
| F20 Step 5 (marks on charts) | F28's `markers_in_window` |
| F20 Step 10 (SLO panel) | F19's `slo_budget` panel |

## One branch for all of Phase 2

**Founder's decision, 2026-11-02:** all Phase 2 work goes on one branch, `feature/phase-2`, cut from `main`
after F6 merged. It reaches `main` through **one pull request at the end**. That pull request is opened as a
draft from the start, so CI runs on every push and a break is seen the same day.

The founder accepted what this costs: the final pull request is very large, the features ship together or not
at all, and nothing reaches `main` until the end.

### Rules on the branch

1. **One commit per plan step,** named `<feature> <step>: <what it does>`, for example `F28 Step 1a: the
   per-version rollup`. The history then reads like this tracker.
2. **Before every push:** `git pull --rebase origin feature/phase-2`, fix any conflict, run the tests again,
   then push. Never push to `main`, never force-push this branch.
3. **At most two sessions at once,** on different lanes below, so they rarely touch the same files. They also
   share the founder's usage limit.
4. **Migrations take the next free number on the branch** at the time of the commit: Alembic
   (`apps/api/migrations/versions`, one head only) and ClickHouse (`deploy/clickhouse/migrations`, next free
   `000038`). A rebase that brings in a migration with the same number means renumbering yours before pushing.
5. **Shared files have one owner** (foundation section 9, *Foundation pieces*). For example, `lifespan.py`'s
   import change is done by whichever of F5, F19 or F20 comes first, and the others build on it. Likewise the
   `scheduler-light` floor (`uncapped_floor`): whichever of F3's T1.7 or F19's T1.5 comes first.
6. **A step is done** when `just check` passes, the step's own checks pass, `/review-diff` has run, the plan's
   tasks are ticked, and `status.md` and this table are updated in the same commit.
7. **Nobody waits for CI.** A session tests on its own machine, pushes and finishes; CI runs on the draft pull
   request in the background. **Before starting a step, a session reads the latest CI result on the draft pull
   request. If it is red, it fixes that first,** in its own commit, then builds its step.
8. **If the code disagrees with the plan,** the session stops and writes the question under *Open decisions* in
   that feature's `status.md`. It does not redesign.

### Lanes

| Lane | Order |
|---|---|
| **A: the chain** | Browser-door change → F28 → F3 → F5 |
| **B: cases** | F22 Steps 1 to 6 → F4 Step 2 onward, once F3's provider (F3 Step 4) is on the branch |
| **C: around the chain** | F20 Step 1 and F4 Step 1 first (both small), then F19 Steps 1 to 3, then F20 Steps 2 to 4 |

Lane A is the critical path. F5's Steps 1, 2 and 2b need only F3, so they can come before F22 is finished.

### Limits

- **Memory.** A cloud session's machine has 16 GB, like the founder's Mac's Docker allowance. If the full dev
  stack does not start, the session runs the unit and web tests, and CI on the draft pull request runs the
  full `just check`.
- **Usage.** Every session counts against the founder's plan limits.

## How to update this page

When a step starts, lands on the branch or is measured, change its row and the date at the top of the section. A step that turns out to need something new is added to *The order of work* with what it needs.
