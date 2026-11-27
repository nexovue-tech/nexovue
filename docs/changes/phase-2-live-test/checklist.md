# Phase 2 live test: real AI model and real GitHub

> **Date:** 2026-11-27 · **Where:** the founder's Mac (M4 Pro, 24 GB) · **Branch:** `feature/phase-2`
> **Why:** every Phase 2 feature was built against a fake AI provider and a simulated GitHub. This is the
> first run against real ones, before pull request #25 merges. A pass here moves a feature towards
> *Verified* (`docs/engineering/workflow.md`); it does not make any model *supported* (that needs the
> evaluation bed's runs) and proves nothing about a deployed server.

## Set-up (done 2026-11-27)

- Dev stack restarted as a **self-hosted** install (`NEXOVUE_DEPLOYMENT=self-hosted`) through a local
  override file outside the repository, with a fresh dev `AI_SECRET_KEY` and
  `AI_PRIVATE_ENDPOINTS=192.168.65.254:11434` (Docker Desktop's address for the Mac, checked from inside
  `scheduler-insight`).
- Ollama 0.40.11, native on the Mac (Metal), `OLLAMA_CONTEXT_LENGTH=32768`. Models: `qwen2.5-coder:7b`
  (tools, 32k context) for wording, chat and fixes; `nomic-embed-text` for embeddings.
- The OpenTelemetry Demo shop running for real traffic (`just otel-demo up`).
- GitHub: the fork `Raj-232/opentelemetry-demo`, public, Issues on.
- F5's write access stays **off in code** unless the founder decides otherwise for this test only.

## Checks

Each row: what is done, what must be seen, and the result. "Not run" is a result too.

### A. AI connection (F3)

| # | Do | Must see | Result |
|---|---|---|---|
| A1 | `/settings/ai`: save the connection (OpenAI-compatible, `http://192.168.65.254:11434/v1`, `qwen2.5-coder:7b`, a monthly token limit) | Saved; no error | Pass, 2026-11-27. Needed first: the console offers a format only after a recorded real call (AC77). `tools/ai-eval/real_call.py` from inside `scheduler-insight`: `openai_chat` to Ollama answered (42 in, 9 out, about 5 s); recorded in `real_calls.py`. The page then showed exactly what would be sent: 5 facts, stand-in names, no log text |
| A2 | Test the connection | A real call succeeds; the context check does not refuse "ready" (prompt not cut short) | Pass: "Passed: the provider answered" |
| A3 | An owner agrees to what is sent | Consent recorded for that address | Pass: "Agreed by Dev Owner on 27 Nov 2026"; the test call counted (48 of 2 million tokens) |
| A4 | Switch AI on | AI on; one project can be kept out | Pass: "AI wording is on"; Production included, with "Keep it out" |
| A5 | Change the model name | Consent ends and AI turns itself off until an owner agrees again | Pass, as the setup page documents it: changing the model turned AI wording off and required a new connection test; consent stands, because only a change of address, format or key ends it. **Gap:** the consent sentence still names the old model |

### B. Detect and explain (F6, F3, F28)

| # | Do | Must see | Result |
|---|---|---|---|
| B1 | Turn on a demo fault (`adFailure` or `paymentFailure` in flagd) | A finding opens within minutes (F6) | Pass: `adFailure` on at 15:09 UTC; "failing more, 19.0% at worst against 0.1%" joined the open `ad` finding as another signal (one finding per problem). 7 earlier findings closed themselves as recovered at 15:10 when traffic resumed |
| B2 | Open the finding | *What lines up with this*: facts with links, no AI words needed (F3 Step 1) | Pass: 21 facts on `checkout`, each with its basis (signed deploy record, nexovue's own checks, counted from telemetry); *Release before this* and *Elsewhere in this project*; the brief; the case section |
| B3 | Wait for the wording | A plain sentence from Qwen, labelled with the model, every claim tied to a fact | Pass with a finding. Wording runs only past the cause bar (FR50) or with the development override `SHOW_HELD_CAUSES=true` (A60), set for this test only. Qwen wrote two sentences, labelled "Written by AI (qwen2.5-coder:7b) ... its meaning was not checked", each citing facts. The first was right; **the second was wrong in meaning** ("almost no requests and some failures" for a fact saying 33 a minute as before, citing a fact about uninstrumented calls). Evidence for keeping wording held until the bar is measured |
| B4 | Send a signed deploy event, then a fault | The deploy marker on charts and *Release before this* on the finding (F28) | |
| B5 | Switch AI off | The finding keeps its facts; nothing breaks | |

### C. Cases and GitHub (F22)

| # | Do | Must see | Result |
|---|---|---|---|
| C1 | Create the GitHub App by the manifest flow (founder's clicks on GitHub) | The App exists on the founder's account | Pass, after two fixes. GitHub refused nexovue's manifest twice: "Hook url is not supported because it isn't reachable over the public Internet (localhost)", although the hook was inactive, then "Hook url cannot be blank" once the hook was left out but `issues` events were still asked for. Fixed: an install GitHub cannot reach asks for no hook and no events (`connections/service.py` `github_manifest`; the suggestions App's manifest too), with `test_github_manifests.py` seen failing first. Then the App `nexovue-acme` was created on the founder's account. **Small defect:** after GitHub returns to nexovue, the page shows the new state only after a manual refresh |
| C2 | Install it on `Raj-232/opentelemetry-demo` only | The connection shows the repository | Pass: installed on `Raj-232/opentelemetry-demo` only; "Working. Last synced 31 sec ago"; its test said "The connection works". The public fork was refused until an admin allowed it, after a plain warning |
| C3 | Start a case on the finding and create an issue | A real issue appears on the fork | Pass: the exact issue text was shown before writing; `Raj-232/opentelemetry-demo#1` created by `nexovue-acme[bot]` with that text (checked through GitHub's API); the case started, and the timeline recorded each step |
| C4 | Close the issue on GitHub | The case follows within one polling interval (no webhook: the Mac has no public address) | Pass, as the spec says (case-management spec line 334): closed on GitHub at 16:07; seen by polling at 16:12; the case stays *investigating* because the signal is still open, the page says "Issue closed in GitHub at 16:12 UTC, and the signal is still open" with Resolve, and the timeline adds "The issue was closed in the tracker, as completed" |

### D. Chat (F4)

| # | Do | Must see | Result |
|---|---|---|---|
| D1 | Turn chat on, with its own consent | The Ask button appears | Pass: the Ask button shows on the self-hosted stack. Chat consent at "Facts and text" recorded; the embedding model `nomic-embed-text` checked and passed in 3 s. **Small defects:** before consent the panel says "This model has not passed a dated real call" (the API reports `tested` only when chat is ready, `assistant/service.py:186`); the consent line reads "agreed on 21:03" with no date |
| D2 | Ask "what broke in the last hour?" | Steps live, then an answer citing its query and time range | **Not passed; nexovue failed safe every time.** `qwen2.5-coder:7b` writes its tool call as text, not in `tool_calls`, so its answer had no citations and was withheld ("The answer could not be checked, so it is not shown"). `qwen2.5:7b` makes real tool calls (the `openai_chat` tool format is proven by replaying nexovue's exact request through its own builder, parser and argument check), but about one call in three adds `from`/`to` to a tool that takes no window (`list_findings`, `list_releases`); the strict check refuses it, and the 7B model abstained instead of correcting itself. Chat needs a stronger model before it can be called working. **Gap:** a refused step stores no reason (arguments `{}`, failure empty), so an operator cannot see why |
| D3 | Ask about a service with no data | Says it found nothing, does not invent | |
| D4 | Plant a log line telling the model to do something | The answer does not follow it, and is labelled | |
| D5 | Run the two chat browser walks (`chat-broken`, `layout-widths`) on this self-hosted stack | They pass | |

### E. Suggested fix (F5)

| # | Do | Must see | Result |
|---|---|---|---|
| E1 | AI on, no code connected: *Suggest a general fix* (rung 2b) | An example labelled "not your code", or a refusal with its reason | Not run: rung 2b is offered only with a likely cause or a recognised error type, and the `ad` finding had neither (by design) |
| E2 | Create the read-only Fix App and connect the fork, with consent per repository | Consent recorded | Pass, after two fixes. The App `nexovue-suggestions-acme` (Contents and Metadata: read, no events) was created on the founder's account and installed on the fork only; a refresh read the installation from GitHub; code location `ad` → `src/ad`; source consent recorded for `qwen2.5:7b` at the local address. **Fixed on the way** (`FixAppPanel.test.ts`, each seen failing first): an expired setup left the panel with no button, and a setup left on GitHub could not be stopped (now *Stop setting up*, as F22 has). **Defects:** the first setup stayed *on GitHub* because GitHub returned to a tab signed out of the console, and no one signed in there; in the same tab, sign-in returns to the page and completes it (checked by Playwright), but the place to return to is kept per tab (`sessionStorage`), so signing in elsewhere loses it, by design; after installing, GitHub does not return to nexovue, so the new state shows only after a refresh; the code-location form shows no error when a save fails (`set.error` is never shown), and two extension clicks on *Save* sent nothing while a script click saved |
| E3 | Gather files for a finding with a stack | The exact file list, with reasons, before anything is sent | Pass with a wording defect. The `ad` finding has no stack, so a person names files (by design); `AdService.java` was named and its 2 imports added, each with its reason and bytes, read from the default branch and labelled "the running version is not known"; nothing sent before *Send*. **Defect:** the list says "These files came from the error's stack, which an outsider can forge" for files a person named. **Gap:** the line "To 192.168.65.254:" ends without the most tokens the request may use (spec FR13a) |
| E4 | Send | A checked suggestion, or a named refusal | Pass for nexovue; the model's fix was wrong. **Defect, HIGH, fixed:** the 3-file pack (21.5 KB) was refused *after* confirmation as "too long for this AI provider" without calling the provider: F5's request is checked against F3's `MAX_INPUT_TOKENS` 8,000 (`propose_fix.py:238`), while the pack allows 64 KiB (`records.py:365`, about 21,800 tokens; `research.md:219` expects 16,000 to 22,000). With `AdService.java` alone: a real call (5,071 tokens) and a named refusal, "not in these files". With a hint: a suggestion, 1 file, 2 lines, 5,265 tokens, labelled "Not compiled and not run" and "The model's explanation, not checked by Nexovue". The edit was wrong: it uses `adFailureLabel`, which does not exist in the file, and leaves the `throw` at line 239 alone. **Fix:** F3's limit now bounds only the request without its files; the files are bounded by the pack cap the person confirmed (`test_a_pack_inside_its_byte_cap_is_sent_not_refused_as_too_long`, seen failing first). Live after the fix: the same 3 files reached Qwen (7,314 tokens, none truncated, 70 s); its answer was `invalid_output` |
| E5 | Draft pull request | Refused with *write access off*, as shipped | Pass: no *Open draft* is offered; the suggestion and `/settings/ai` both say "Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done"; *Prepare patch* is offered instead |

### F. Safety

| # | Do | Must see | Result |
|---|---|---|---|
| F1 | A span or log with injection text reaches a finding | Not followed; the sentence is withheld or fenced | Partly. AI wording cannot carry injected text by design: its request holds no log or error text, which the preview showed before consent (A1). Chat's half is not proven: chat reads log text at this data level, but the 7B model produced no checked answer at all (D2), so an injection result would mean nothing yet |
| F2 | Stop `scheduler-insight` | Detection and alerts carry on; AI says it is unavailable | Pass: `scheduler-insight` stopped at 16:21. Detection carried on (33 project judgements in the next 70 s, no errors); the finding page kept its facts and its earlier AI words, no error screen; a chat question showed "Waiting for a worker", then "Taking longer than usual" at about 20 s, then "Not available right now." with Ask again at 120 s. Restarted healthy |
| F3 | Memory during the run | Docker under its 11.7 GiB; no container killed for memory | Pass: after about an hour of the full run (stack, OTel demo, Ollama outside Docker), Docker used 7.5 of 11.7 GiB (ClickHouse 2.7 GiB the largest); no container killed for memory or restarted |

## Not covered here

Webhooks from GitHub (no public address), a paid AI provider, the long fault runs, the deployed server, and
any claim that a model is *supported*.
