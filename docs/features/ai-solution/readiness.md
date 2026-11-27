# Readiness: AI suggested fixes (F5)

> **Assessed 2026-11-28, as one feature, on `feature/phase-2` at `bed6e33`, after the founder's go on write access (T6.5, 2026-11-28) and the Phase 2 live test (2026-11-27 and 28).**
> **Verdict: NOT READY for a production rollout.** Four blockers are F5's own (below), on top of the platform's.
> `docs/operations/production-readiness.md` is kept outside the repository (founder, 2026-10-29); this page does not
> update it. Nothing here approves a rollout: that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold). Nothing in *open* is claimed to pass. Everything observed here ran on the founder's
> Mac (the dev stack, a public fork on GitHub.com, Anthropic and a local Ollama). **None of it is evidence about a
> deployed server.**

## What changed since the build

Until 2026-11-28 every write path (the draft pull request, **Run CI**, **Delete this branch**) was off in code. It is
now **on**: `write_gate.WRITE_ACCESS_GO` holds the founder's go (`fixes/write_gate.py:51`), naming Claude Sonnet 5.5,
the founder's decisions of 2026-11-27 in place of the buyer round, the terms in `docs/legal/suggested-fixes.md`, no
drafts from an error stack alone, and GitHub Enterprise Server later. `status.md` records the same go (*Blocked*), and
`test_fix_write_gate.py` holds the two equal. So this assessment is of a feature that **writes to customers'
repositories**, not of one that only suggests.

## Blockers

| # | Blocker | The failure it prevents |
|---|---|---|
| B1 | **The AI container can reach the cloud metadata address** (T4.11, the outbound proxy, not built; `deploy/` declares no proxy or egress rule) | `scheduler-insight` holds every organization's sealed provider key and the AI account; once broken into, it can ask `169.254.169.254` for the server's own cloud credentials. The founder made the block a requirement before the first deploy (2026-11-27, item 37a; `docs/operations/first-deploy.md`, step 5). F3's readiness records the open address (its S1); nothing has changed it |
| B2 | **The go names one model; the code does not.** `write_access_on()` returns True for every install and every model: `safety_gates_passed_on` is read nowhere but `write_gate.py` (searched: no other use). An organization on a model that never ran AC21 gets **Open draft pull request** and **Run CI** | Founder decision 3 of 2026-11-27 ("write access opens only if AC21's safety gates pass on a named AI option") and the go's text ("names Claude Sonnet 5.5") read as if drafts were for that model; the code opens them equally for, say, the live test's `qwen2.5-coder:7b`, which never ran AC21 and whose one suggestion (E4) used a variable that does not exist. AC21 itself only asks that a failing option be left out of the docs, and the rules K1 to K10 run on every model, so this is not a hole in the rules; it is a decision the code does not yet state either way. **The founder decides:** enforce the named option per draft, or record that the go covers every model the rules pass. Either is a small change; shipping without the decision is not |
| B3 | **Run CI has never reached real GitHub** (T8.4 not run). E5 opened, closed and deleted a real draft; no row of the live test pushed Run CI's second commit | Run CI pushes a commit to a branch in the customer's repository and lifts the CI hold, so the model's code runs in their CI, possibly with secrets. Only the simulated GitHub has seen it. Either run T8.4 on the scratch repository, or ship with Run CI hidden until it is run |
| B4 | **Customer-facing words are not approved** (foundation decision 2): W1 to W14 and the customer's page (open decision 125, still open on 2026-11-27). The page still opens with "Draft for the founder's approval … Nothing here is published until he approves it" (`docs/product/setup/ai-suggested-fixes.md:3`), and it renders in the console's setup docs | A release would show customers a page that says it is unpublished, and refusal, state and pull request sentences nobody has approved. The founder's direction of 2026-11-27 (the coordinator corrects, he approves the changes) has not yet been carried out |

## Recommended (does not by itself stop a rollout)

| # | Item |
|---|---|
| R1 | **A14: Delete this branch stays offered after the branch is gone.** Seen in E5 and confirmed in code: `fix_writer._delete` records only an audit entry (`fix_writer.py:769` to `783`), and the read offers the action while `pull_request_branch` is set and the state is deletable (`fix_draft_read.py:311`). A second click is harmless (the head is gone, so nothing is deleted), but the page says something false. Record the deletion on the row |
| R2 | **Open wording from the live test:** B5's `ai_off` sentence, "AI is off. An admin sets it up, then an owner agrees." (`fixes/sentences.py:93`), read when AI was only switched off. The items the brief calls A15 and A17 could not be found by those names in the repository; they belong in B4's wording pass |
| R3 | **Quality is unmeasured on real code** (T6.4 not run). The evaluation's verdicts (the founder's, an independent engineer's, with and without imports and rounds) all read "not yet given" (`docs/research/f5-evaluation-2026-11.md`). On real code there is one correct Claude fix (the evaluation's first case) and one draft from `CPULoad.java`; Qwen's one fix was wrong. Say nothing about quality until T6.4 (spec, *What may be said once shipped*) |
| R4 | **K6/K8 replay over real history not run** (T6.3; `docs/research/f5-k6-k8-replay-2026-11.md` is a skeleton). Their notices stay off (`notices.REPLAY_REPORT_READ`), so the false-refusal rate of the two rules is unknown |
| R5 | **AC37d: five states below 44 px at 375 px** (decision 120: `CopyButton`, `SelectField`, `Button`). Approved by the founder as a separate change after Phase 2 |
| R6 | **Documents behind the code.** `plan.md` T6.5 still reads "not given; write access is off in code" with T6.1 unticked, and `status.md`'s lifecycle line still says "`WRITE_ACCESS_GO` is None"; the evaluation report names no run date or commit. The *Blocked* section and the code agree; the rest should be brought in line |
| R7 | **T9.4 (setup timing), T9.6 (rivals within 30 days of launch), T9.7 (the demo run for real)** are the founder's and not done. AC52 needs T9.6 before any rival claim is published |
| R8 | **Webhooks from GitHub never tested** (no public address); every state change was read by the poller. A deployed install with a public address may receive them |

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | AC21's safety gates (T6.1) | **pass, with its limits** | `docs/research/f5-evaluation-2026-11.md` (filled in a4f49e4, 2026-11-28): Claude Sonnet 5.5, 204 trials, no way out, every evidence id resolving; A1, A3 to A6 and A19 at 20 or 21 each, all *reached a person*, 16 flagged `removed_check`; Haiku 5.5 passed too, 27 of 204 unusable. **Limits:** the authors' seeded fixtures only; "refused by rules" is 0 on every attack, so what passed is "no way out", not "the attack was stopped": a person's review is the protection, as the terms say. The report records no run date or commit (R6). The run was not repeated here |
| 2 | The founder's go (T6.5) | **pass as recorded; fail as enforced (B2)** | `write_gate.py:51` and `status.md` *Blocked* agree; `test_fix_write_gate.py` holds them equal and checks every write entry point asks the gate first (1365 platform tests passed here, below). The named model is not enforced |
| 3 | The rules, with no model (NFR1, AC20) | **pass** | The hostile-answer suites and the planted-change report (`docs/research/f5-rules-report-2026-11.md`, checked current by `test_rules_report.py`); run here, below |
| 4 | Real AI provider | **pass for Anthropic on one machine; open for every other** | Live test E4 (2026-11-28): after three fixes (Anthropic's schema dialect, every field required, the pack bound), Claude returned a correct "needs a person's decision" on `payment` and a correct fix on the evaluation's first case. Qwen (local) returned `invalid_output` after the pack fix. OpenAI, Bedrock and others: no call. `real_calls.py` records `openai_chat` and `anthropic_messages` only |
| 5 | Real GitHub, read (rungs 3 and 4) | **pass, one public fork** | E2, E3: the read-only Fix App created by manifest, installed on `Raj-232/opentelemetry-demo` only, consent per repository, files listed with reasons and bytes before Send. Fixed on the way (each with a test seen failing): stale state after GitHub's return, an unstoppable setup, a save that did nothing, the forgery warning on named files, the missing token line (FR13a) |
| 6 | Real GitHub, write: the draft (T7.11) | **pass on one public fork; one defect open (R1)** | E5, 2026-11-28: draft `Raj-232/opentelemetry-demo#2` from `nexovue/suggestion-7f4c64bf…`, authored by the App, body with "Not compiled. Not run" and the as-is line, no figures or login; closed on GitHub, read by the poller, branch deleted (404 after). Defects found and fixed: no public-repository control for the App and a route refusing its repositories (a4f49e4), the link return under StrictMode and the draft check (98c8e6e), the sentence said twice (91932f0) |
| 7 | Real GitHub, write: Run CI (T8.4) | **open: not run (B3)** | Simulated only (`test_fix_writer_live.py`) |
| 8 | The customer's CI is held on a draft | **open** | ADR-104's hold (`push` and `pull_request` runs, Actions only) is tested against the simulated GitHub. Whether the fork's Actions held on #2 was not recorded in E5. Other triggers and other CI systems were never checked (status, *Risks*) |
| 9 | Taking code back (BR9, AC29, AC48) | **pass in tests; one real path seen** | `fix_takeback.py` read: withdrawal, disconnect, project deletion and member removal each end in-flight work and run `CLEAR_STORED_CODE`. Suites in this run. The live test exercised none of them against real GitHub (no withdrawal or Fix App disconnect is recorded in rows A to F) |
| 10 | AI off, or the AI container stopped | **pass on the Mac** | B5: AI off, the section says so and the finding keeps its facts. F2: `scheduler-insight` stopped, detection carried on, no error screen |
| 11 | Migrations 0077, 0079, 0081 to 0084, 0086 | **pass in tests; roll-forward on a real install not done** | Migration rules and schema up/down/up in the suites run here (below). Nothing has been migrated on a deployed database |
| 12 | Configuration and secrets | **pass in code; open on a server** | The go cannot come from a setting, environment variable or row (`test_fix_write_gate.py`). The Fix App's private key and `AI_SECRET_KEY` were set only on the Mac's dev override; rotation under live use was never executed |
| 13 | Capacity | **open: deploy** | F3 (memory under 11.7 GiB on the Mac, live test F3) and NFR2's click figures (median 24 to 256 ms, slowest 852 ms, status decision 124) are one machine's. F5's share of `scheduler-insight`'s 768 MB is a guess until the server |
| 14 | Health, monitoring, alerts | **pass in rules; not seen firing** | `promtool test rules nexovue_test.yml` run here: SUCCESS, including `NexovueSuggestionRefusedAgain` and `NexovueSuggestionSweepStalled`. Runbook entries in `docs/operations/ai.md`. No alert has fired on a real install, and no one is paged yet (platform) |
| 15 | Rollback | **pass for stopping writes per repository; a release for all** | Runtime: an owner stops drafts per repository; AI off stops rung 3 on; the customer lowers or removes the App on GitHub. **Turning write access off for every install needs a new build,** since the go is a constant (ADR-097, by design). An opened draft stays on GitHub as `untracked` (plan, *Rollout*) |
| 16 | Terms | **pass, as decided** | `docs/legal/suggested-fixes.md`; founder decision 1 of 2026-11-27 defers the lawyer until a hosted service or a paid contract. The as-is line is in the draft body (E5) |
| 17 | Customer and operator documents | **fail: unapproved (B4)** | Customer page carries the draft banner; W1 to W14 waiting. `docs/operations/ai.md` covers the two alerts |
| 18 | Browser layout (AC37d) | **fail for five states (R5)** | Status decision 120, approved as a separate change |
| 19 | Deploy | **open: the founder** | Nothing is deployed. TLS, the public edge, webhooks and operational ownership are the platform's |

## Security review

Read for this assessment: `fixes/write_gate.py`, `fixes/sentences.py` (the as-is and refusal lines),
`findings/fix_takeback.py`, `findings/fix_draft.py` and `fix_draft_read.py` (the delete action),
`connections/fix_writer.py` (the delete path), `insight/propose_fix.py` (the pack bound after E4), `ai/formats.py`
(`anthropic_schema`), the customer's page, the terms and the evaluation report. The earlier step reviews in
`status.md` (each step's `/review-diff`, the two persona reviews of 2026-11-02, the CodeQL fix of 2026-11-23,
committed in bd072dc and a4af561) were read, not repeated.

| # | Severity | Finding | State |
|---|---|---|---|
| S1 | MEDIUM | The AI container reaches the cloud metadata address (F3's S1); F5 adds the Fix App's role in the same container's reach | **Blocker B1** |
| S2 | MEDIUM | The write gate is global: the go's named model is not enforced | **Blocker B2**, the founder's decision |
| S3 | MEDIUM | `contents: write` reaches every unprotected branch of every repository the App is installed on; only nexovue's code limits it to `nexovue/suggestion-*` | Accepted by design and said in the terms (item 6) and the customer's page; the customer must protect main and release branches |
| S4 | MEDIUM | A draft may run the customer's CI with secrets before review; the hold covers Actions' `push` and `pull_request` only, and was not observed on real GitHub | **Open** (gate 8); Run CI lifts it deliberately (B3) |
| S5 | LOW | A one-character logic change, or a planted address copied to a new line (K6 flags, does not refuse), reaches a person with every rule passed | Accepted; labelled on the page and in the evaluation's *What passing will not prove* |
| S6 | LOW | **Delete this branch** offered after deletion (A14) | Recommended R1; harmless |
| S7 | accepted | F3's and F4's wording checks keep the quadratic patterns F5's lost | Founder, 2026-11-27 (decision 131): a separate change after Phase 2 |

No tenancy hole, secret in a log, or path for a model's text to reach GitHub without the rules was found in what was
read.

## Tests run for this assessment, 2026-11-28

On the founder's Mac, against the running dev stack. **Other test runs (other features' assessments) were using the
same stores at the same time,** so a failure below is checked again alone before it is counted.

| Run | Result |
|---|---|
| `uv run pytest apps/api/tests -q -n 4 --dist loadfile -k fix` | 543 passed, 1 skipped, 1 failed, 1 error. **The failure was the branch moving under the run:** `test_fix_customer_page.py` was changed with the page in 60e7cb3, committed while the run was in progress; run again alone on `bed6e33`, 15 of 15 passed. **The error is not F5's:** `test_llm_observability_live.py` (selected by `-k fix`) needs `just gate-seed`'s spans, which this stack does not hold |
| `uv run pytest packages/py-platform/tests -q -n 4 -k fix` (the rules, sentences, write gate and its structural checks) | 1365 passed |
| `uv run pytest apps/scheduler/tests -q -n 4 --dist loadfile -k fix` (the hostile answers, the writer, the poller, the sweep, takeback) | 262 passed |
| `promtool test rules nexovue_test.yml` (`prom/prometheus:v3.15.1`, read-only mount) | SUCCESS |

## Could not be checked here

- Anything on a deployed server: capacity, TLS, webhooks, alerts reaching a person, a migration on real data.
- Whether GitHub Actions held on draft #2 (not recorded in E5), and Run CI on real GitHub (never run).
- CI's latest run on `bed6e33` (no GitHub CI tool used for this page).
- A rerun of the evaluation, the founder's verdicts, and quality on a real repository (T6.4).
- The items the brief named A15 and A17: no item by those names was found in the repository.
