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
| — | [Browser-door change](../changes/browser-door/status.md) (owned by F28) | Done | — | Yes, 2026-11-07 | No | No | After deploy |
| 2 Explain | [F28 Deploy and release tracking](../features/deployment-release-tracking/status.md) | Done | Yes | Yes | No | No | After deploy |
| 2 Explain | [F3 AI insights](../features/ai-insights/status.md) | Done | Yes | Yes | No | No | After deploy |
| 3 Track | [F22 Case management](../features/case-management/status.md) | Done | Yes | Yes | Step 1 (2026-11-07) | No | After deploy |
| 4 Ask | [F4 AI chatbot](../features/ai-chatbot/status.md) | Done | Yes | Yes | Step 1 (2026-11-02); Steps 2+ wait on F3 and the buyer round | No | After deploy |
| 5 Fix | [F5 AI solution](../features/ai-solution/status.md) | Done | Yes | Yes | No | No | After deploy |
| 6 Around | [F19 SLO tracking](../features/slo-tracking/status.md) | Done | Yes | Yes | No | No | After deploy |
| 6 Around | [F20 Dashboards](../features/dashboards/status.md) | Done | Yes | Yes | Step 1 (2026-11-02); Steps 2+ not started | No | After deploy |

**All seven features' specs, designs and plans, and the foundation, approved by the founder on 2026-11-02.** F6 merged on 2026-11-02 (PR #24). Each feature starts as *The order of work* below allows, on the one branch `feature/phase-2`. Founder questions open: **none** (all approved 2026-11-02). F19's SLO work runs on `scheduler-light`, not a new container (F19 D66, 2026-11-02). Waiting on outside parties: Atlassian's written
answer for Jira (F22 Step 7); the lawyer's review (foundation items 3 and 16).

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
| F3 Steps 3 to 7 (AI container, provider, wording) | F3 Step 2b go decision |
| F22 Steps 1 and 2 (cases on findings) | F6 |
| F22 Steps 3 to 6 (GitHub connection) | F22 Step 2 |
| F22 Step 7 (Jira) | Atlassian's written yes |
| F4 Steps 2 onward (chat panel) | F3's provider (F3 Step 4); the buyer-conversation rule (foundation, *Founder decisions*) |
| F5 Steps 1 and 2 (deploy line, brief; the checks) | F28's release facts and F3's fix brief; the buyer round and the GitHub trial (F5 D64) |
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
