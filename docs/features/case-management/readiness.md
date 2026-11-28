# Readiness: Case management with GitHub issues (F22)

> **Assessed 2026-11-28, as one feature, on `feature/phase-2` at `c22a3698`** (the working copy; 18 commits ahead of
> `origin/feature/phase-2`, whose head `714cfab` is the last one CI ran).
> **Verdict: NOT READY for a production rollout that offers GitHub issue sync.** Cases on their own (take, note,
> hand over, resolve, reopen, the timeline and the feed, with no tracker connected) have no blocker of F22's own beyond
> the platform's. `docs/operations/production-readiness.md` is kept outside the repository (founder, 2026-10-29); this
> page does not update it. Jira (plan Step 7) is out of this release (foundation item 32): it waits on Atlassian ticket
> ECOHELP-191004, whose four weeks end 2026-12-25, and is not assessed. Nothing here approves a rollout: that is the
> founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass. The founder's Mac is evidence about the code
> against real GitHub, not about a deployed server.

## What the live test proved, and what it did not

The Phase 2 live test (`docs/changes/phase-2-live-test/checklist.md`, rows C1 to C4, 2026-11-27) is the first time
F22 met real GitHub. On the founder's Mac, self-hosted, no public address:

- **C1, the manifest flow:** passed only after two fixes. GitHub refused nexovue's manifest twice (a localhost hook URL
  even with `active: false`; then `issues` events with no hook URL). Fixed in `0b16f516`: an install GitHub cannot
  reach asks for no hook and no events (`connections/service.py` `github_manifest`, lines 290 to 313;
  `test_github_manifests.py`, 3 passed here). The App `nexovue-acme` was created.
- **C2:** installed on one public fork; the public repository was refused until an admin allowed it, after a warning.
- **C3:** an issue created with the exact text previewed, by `nexovue-acme[bot]`.
- **C4:** closing the issue on GitHub reached the case **by polling**, 5 minutes later, as the spec says; the case
  stayed *investigating* because its signal was still open.
- **Return pages:** after GitHub sent the person back, Connections showed the new state only on a manual refresh.
  Fixed in `a7005e40` (the return pages mark Connections out of date; the page re-reads every 5 s during a setup);
  `GitHubReturns.test.ts` and `ConnectionsScreen.test.ts`, 44 passed here. **Not re-observed against real GitHub.**

**What it did not touch:** the path a deployed server takes. A reachable install's manifest still asks GitHub for an
active hook and `issues` events (`service.py:306-312`); GitHub has never been sent that shape. No webhook delivery has
ever arrived from GitHub. The console was Vite's dev server (`nexovue-web-1` runs `pnpm … dev`), not Caddy, so the
manifest form's `form-action` widening (AC73) was not exercised either.

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | Cases without a tracker (Steps 1, 2, 8) | **pass** | This run: `uv run pytest apps/api/tests apps/scheduler/tests -q -n 4 --dist loadfile -k "connections or case"`, **577 passed, 1 skipped, 1 failed** (gate 4). The `ai-off` CI job (AC56, a real `scheduler-light` ends a case with no AI account) is green on PR #25's head `714cfab` (run 46011218394) |
| 2 | Setup by the manifest flow, unreachable install (T3.0, AC73 half) | **pass on the Mac; the fix not yet in CI** | C1, C2 above. `0b16f516` is one of the 18 commits CI has not run |
| 3 | Setup, reachable install (hook and `issues` events) | **open: a server GitHub can reach** | Never submitted to GitHub. C1 is the reason this is not assumed: two shapes read as valid from GitHub's documents were refused |
| 4 | Create, sync and close by polling (T4.8 half, C3, C4) | **pass for create and close-as-completed; one intermittent failure here** | C3, C4. In this run `test_a_worker_killed_while_creating_leaves_exactly_one_issue[before_first_attempt_commit]` failed once (`assert 0 == -9`: the child was not killed at its kill point) under `-n 4` with the dev stack's own `scheduler-connections` running; the test passed alone and `test_connections_sync_live.py` passed whole three times in a row (61 passed each). Cause not found |
| 5 | The rest of AC130's real-GitHub script | **open: the founder** | Not run: close as *not planned*, a `Fixes #n` merge, reopen, the label's outcome under `issues: write`, transfer (301), delete (410), a repository removed from the installation, hostile names rendered in a title (AC41). Each is proved only against the simulated GitHub, built from the authors' reading of GitHub's pages (status, Steps 3 to 6, *Unverified*) |
| 6 | Webhooks (T5.1, T5.2) | **open: a public address** | Never tested against real GitHub. The signature header and that a delivery carries `issue.id` are unconfirmed (`connections/webhooks.py`, the docstring of `_github_issue_id`). If either is wrong, a delivery rings nothing and polling still follows the issue within 5 minutes: slower, not lost |
| 7 | The manifest form behind Caddy (AC73) | **open: an install behind Caddy** | `deploy/caddy/Caddyfile:150` widens `form-action` to `https://github.com` on the start route; `test_caddyfile_headers.py` holds the text. No real Caddy and browser have served it |
| 8 | Public repositories and the allowance (T6.3, BR12) | **pass** | C2 (refused until allowed, with a warning); `connections/places.py`; in this run's batch |
| 9 | The tracker role's own Postgres account (T8.7, migration 0085) | **pass in tests** | `test_connections_grants_live.py`, `test_connections_account_live.py` in this run; the live test ran `scheduler-connections` as `nexovue_connections`. F5 found one missing grant after merge (fixed, `docs/changes/fix-sweep-records-as-its-account/`) |
| 10 | Migrations (0055 to 0069, 0085) | **pass, roll-forward on data** | In CI on `714cfab`. Rollback of the release is a restore (PR #25's release notes) |
| 11 | Configuration and secrets | **pass in code; rotation not rehearsed** | `CONNECTION_SECRET_KEY` is required by name in Compose (`:?`, `deploy/compose/nexovue.yaml:312` for the API, `:472` for `scheduler-connections`); the end-to-end test (T8.5) searches logs, jobs and answers for every secret and found none. `docs/operations/key-rotation.md` covers the key; never executed on an install |
| 12 | Capacity and memory (T4.9) | **open: deploy** | Compose's 384 MiB is a guess. Read here: `docker stats` shows 217.8 MiB of 384 MiB for `scheduler-connections` on the Mac, idle after the live test. The T8.4 timeline read is a sequential scan of every organization's events (open decision 20) |
| 13 | Health, monitoring, alerts | **pass in rules; not seen firing** | `NexovueConnectionsMissing` (`deploy/monitoring/rules/nexovue.yml:29`) and the per-queue oldest-ready-job alert. The oldest-due-link alert (T3.13's remainder) does not exist: nothing pages when links stop being read while the worker is up. No alert has fired on a real install |
| 14 | Rollback | **pass for the tracker layer; open for data** | `scheduler-connections` is behind the `connections` profile; without it cases still work (AC56). Issues already written stay in GitHub |
| 15 | Design QA (AC66, AC67, AC108) | **open: the founder** | The hand list in `status.md` is unrun at every width |
| 16 | Customer documents | **pass, now stale** | `docs/product/setup/trackers/github.md` says nothing has run against real GitHub (*What is not checked yet*, line 110); C1 to C4 now have. It does not mention the reachable choice's untried state |
| 17 | Founder's open decisions | **open** | Decisions 4 to 24 in `status.md` are built as recommended and not recorded as answered |
| 18 | CI on the code that would ship | **open** | CI is green on `714cfab` (check, ai-off, insight-fence, CodeQL). The 18 newer commits, including the manifest and return-page fixes, have not run in CI |
| 19 | Deploy | **open: the founder** | Nothing is deployed. TLS, the public edge, backups and operational ownership are the platform's |

## Blockers, for a rollout with GitHub sync

1. **A deployed server's GitHub setup is untried and may fail at its first step.** An admin who answers "GitHub can
   reach this server" sends a manifest with an active hook and `issues` events (`service.py:306-312`), which GitHub has
   never been sent. C1 showed GitHub refusing two other shapes nexovue's reading said were valid; if this one is
   refused too, a customer on a public server cannot connect GitHub at all. Gate 3.
2. **The manifest form behind Caddy is unobserved** (AC73). If the start route's widened `form-action` does not reach
   the browser, the browser refuses to post the form to github.com and no install behind Caddy can create its App.
   Gate 7.
3. **AC130's script is not run beyond create and close.** Reopen, transfer, delete, a `Fixes #n` merge and a removed
   repository are proved only against a fake built from GitHub's documents. If GitHub answers any of them differently,
   a case keeps showing an issue that has moved or gone, or the connection drops to *failing* and stops syncing every
   case on it. Hostile names in a title (AC41) are unchecked as GitHub renders them. Gate 5.
4. **The fixes the live test needed have not run in CI.** `0b16f516` (manifest) and `a7005e40` (return pages) are in
   the 18 commits ahead of the last CI run; shipping them unrun in CI ships code only one Mac has tested. Gate 18.

## Recommended

- Run a real webhook delivery once a public address exists (T5.2): until then the doorbell is inert and sync is 5-minute
  polling. Gate 6.
- Find why `test_a_worker_killed_while_creating_leaves_exactly_one_issue[before_first_attempt_commit]` failed once
  here (change-work if it repeats). Gate 4.
- Add the oldest-due-link alert (T3.13), so a sync that silently stops reading links pages someone. Gate 13.
- Measure `scheduler-connections` on the deployed server (T4.9) and re-measure the timeline read at a realistic event
  count (open decision 20). Gate 12.
- Update `setup/trackers/github.md` to what C1 to C4 proved and what they did not (the reachable path, webhooks).
  Gate 16.
- Re-observe the return-page fix against real GitHub; run the Design QA hand list; answer decisions 4 to 24.

## Not checked here

A deployed server; a real Caddy; any GitHub call (this assessment made none, and read GitHub only through PR #25's
check runs); the browser; key rotation; a restore. `test_setup_live.py` and the full `just check` were not run.
