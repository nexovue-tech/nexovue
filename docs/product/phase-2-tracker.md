# Phase 2 tracker

> **Type:** Progress tracker · **Started:** 2026-11-02 · **Owner:** the founder
> **Plan:** [`phase-2-plan.md`](phase-2-plan.md) · **Shared decisions:** [`phase-2-foundation.md`](phase-2-foundation.md)

This page answers one question: **how far along is Phase 2?** Each feature's own `status.md` holds the detail.
When a step's state changes, update its row here in the same pull request.

States: **Docs** (spec, design and plan written, Proposed) · **Approved** (the founder approved the plan) ·
**Building** · **In review** (a pull request is open) · **Merged** · **Measured** (checked on the deployed
server; see foundation, *What A and B change*).

## Where it stands, 2026-11-02

| Stage | Feature | Docs | Spec approved | Plan approved | Built | Merged | Measured |
|---|---|---|---|---|---|---|---|
| 1 Detect | [F6 Anomaly detection](../features/anomaly-detection/status.md) | Done | Yes | Yes | Yes | PR #23 open | After deploy |
| — | Browser-door change (owned by F28) | In F28's docs | — | — | — | — | — |
| 2 Explain | [F28 Deploy and release tracking](../features/deployment-release-tracking/status.md) | Done | No | No | No | No | After deploy |
| 2 Explain | [F3 AI insights](../features/ai-insights/status.md) | Done | No | No | No | No | After deploy |
| 3 Track | [F22 Case management](../features/case-management/status.md) | Done | No | No | No | No | After deploy |
| 4 Ask | [F4 AI chatbot](../features/ai-chatbot/status.md) | Done | No | No | No | No | After deploy |
| 5 Fix | [F5 AI solution](../features/ai-solution/status.md) | Done | No | No | No | No | After deploy |
| 6 Around | [F19 SLO tracking](../features/slo-tracking/status.md) | Done | No | No | No | No | After deploy |
| 6 Around | [F20 Dashboards](../features/dashboards/status.md) | Done | No | No | No | No | After deploy |

Founder questions open: **none** (all approved 2026-11-02). Waiting on outside parties: Atlassian's written
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
| F5 | F3's fix brief and provider; F22's GitHub connection |
| F19 Steps 1 to 3 | F6 |
| F19 Step 4 onward (burn alerts) | The browser-door change |
| F20 Steps 2 to 4 | F20 Step 1 |
| F20 Step 5 (marks on charts) | F28's `markers_in_window` |
| F20 Step 10 (SLO panel) | F19's `slo_budget` panel |

## Working in parallel

**Yes, parallel work is possible**, using one git worktree per lane. A worktree is a second folder on the same
repository, with its own branch, so two features can be built at once without switching branches. Three lanes
is the sensible most, for the reasons under *Limits*.

| Lane | Branch, one per feature | Order inside the lane |
|---|---|---|
| **A: the chain** | `feature/browser-door`, then `feature/release-tracking`, then `feature/ai-insights`, then `feature/ai-solution` | Browser-door → F28 → F3 → F5 |
| **B: cases** | `feature/case-management`, then `feature/ai-chatbot` | F22 Steps 1 to 6 → F4 Step 2 onward, once F3's provider is merged |
| **C: around the chain** | `feature/dashboards`, `feature/slo-tracking` | F20 Step 1 and F4 Step 1 first (both small), then F19 Steps 1 to 3, then F20 Steps 2 to 4 |

Lane A is the critical path: F5 cannot finish until F28, F3 and F22 are merged.

### Rules for parallel lanes

1. **One feature, one branch, one pull request**, each from the latest `main`. Never one branch for all of
   Phase 2: it could not be reviewed, and every lane would conflict with it.
2. **Migrations are numbered when merging, not when writing.** Alembic (`apps/api/migrations/versions`) and
   ClickHouse (`deploy/clickhouse/migrations`, next free `000038`) both break if two branches take the same
   number. Before a branch merges, rebase it on `main` and renumber its migrations to the next free ones.
3. **Shared files have one owner** (foundation section 9, *Foundation pieces*). For example, `lifespan.py`'s
   import change is done by whichever of F5, F19 or F20 lands first, and the others rebase onto it.
4. **Rebase each lane on `main` after every merge**, so conflicts stay small.
5. **One dev stack at a time.** The Mac's Docker has about 11.7 GiB, and the full stack uses most of it. Lanes
   share the one running stack, and a lane runs its live tests only when the stack is on its branch. Unit
   tests and the web tests can run in every lane at once.

### Limits

- **Review.** Every pull request waits for the founder's review and merge. Three lanes give three pull requests
  to review at a time, and more lanes only lengthen that queue.
- **Memory.** One dev stack, plus a local AI model when F3 or F4 is tested (decision 0047).
- **Each worktree needs its own setup** (`pnpm install`, the Python environments), a few minutes and a few GB
  of disk each.

## How to update this page

When a step starts, merges or is measured, change its row and the date at the top of the section. Link the pull
request. A step that turns out to need something new is added to *The order of work* with what it needs.
