# Readiness: AI chatbot, Ask (F4)

> **Assessed 2026-11-22, as one feature (plan T8.4), in `wt/f4s8` (Steps 1 to 7 on `feature/phase-2`, plus Step
> 8's uncommitted changes).** **Verdict: NOT READY for a production rollout with chat on.** With chat off, which is
> the default, F4 adds nothing that runs: no blocker of its own beyond the platform's
> (`docs/operations/production-readiness.md` is kept outside the repository, founder 2026-10-29; this page does not
> update it). Nothing here approves a rollout: that is the founder's.
>
> Verdicts are **pass** (observed, with the evidence), **open** (cannot be observed yet, and what it waits on) and
> **fail** (observed not to hold).

## Blockers, for a rollout with chat on

1. **No model has been run.** Every option in the bed is a fake provider, so none is supported (T8.0; foundation
   items 31, 35a). An owner turning chat on today points it at a model nobody has measured for right numbers,
   abstaining or injection.
2. **AC80 is not measured.** A hostile question may read every log row of a week at each of its 8 steps. On a
   development VM, with 12 million log rows, each such read took 1.3 s at four questions at once and read about
   800 MiB (status, Step 8). Whether detection still judges every minute inside its 60-second cycle, and alerts
   stay within 60 s of a chat-idle run, is the fault bed's run, on the founder's machine; and the detection half
   has no measuring tool on the fault bed yet (open decision 64). Unmeasured, chat could delay the alerts the
   product exists for.
3. **No public claim is approved** (T8.5, AC100). The launch brief is a draft.

## Gates

| # | Gate | Verdict | Evidence, or what it waits on |
|---|---|---|---|
| 1 | Real models, the bed and its report (T8.0) | **open: the founder, after Phase 2** | The bed ran whole on `fake-answer-key` (1,533 trials, every gate held, the report labelled fake-provider output). Commands for the real runs: status, Step 4 |
| 2 | Injection with no model | **pass, with a limit** | The offline and live hostile suites passed in Step 8's batch (`test_assistant_hostile*.py`). A fake that obeys every instruction proves the code's walls, not what a model does |
| 3 | No effect on detection and alerts (AC80) | **open** | Blocker 2 |
| 4 | Memory and the poll routes (AC98) | **pass, indicatively** | `scheduler-insight` peaked at 318 MiB PSS under 4 looping questions against its 768 MB limit; poll routes 22 and 35 ms median on a 56-question conversation (`sizing.md`). A development VM's processes, not a server's container |
| 5 | The panel in a browser | **pass** | Playwright, 2026-11-22: `assistant.spec.ts` 4 passed twice; `chat-broken.spec.ts` 1 passed; `layout-widths.spec.ts` 3 passed (all three widths, every console screen). Design QA, a screen reader and a real phone are the founder's |
| 6 | Monitoring and alerts | **pass in rules; not seen firing** | Five gauges and `NexovueChatQuestionsUnclaimed`, `promtool` test SUCCESS (T8.2). No alert has fired on a real install |
| 7 | Security review | **pass for what the code does; two risks open** | Tickets, catalog GETs only, row-level security for the AI role (`security-and-tenancy.md`). Open: the context-length probe (D87, open decision 9) is not built, so a local model at its default context can lose the start of a prompt silently (the setup page says to set it); the answer check is a heuristic (BR18) |
| 8 | Consent | **pass** | The consent step carries BR18's sentence (AC99, `ChatSection.test.ts`, `test_banned_claims.py`); `CHAT_DISCLOSURE_VERSION` raised to 4, so every earlier agreement ends and an owner agrees again |
| 9 | Migrations | **pass; none in Step 8** | Steps 2 to 7's migrations were tested in their own steps |
| 10 | Rollback | **pass in design, not rehearsed** | Chat's switch, or `SCHEDULER_INSIGHT_REPLICAS=0`. `chat-broken.spec.ts` shows every screen working with chat failing |
| 11 | Documents | **pass, unreviewed** | `docs/product/setup/chat.md`, `docs/operations/ai.md` *Chat*, the architecture pages, `sizing.md`. "Models measured with chat: none yet" |
| 12 | Operational ownership | **open** | No operator but the founder; no install runs chat |
