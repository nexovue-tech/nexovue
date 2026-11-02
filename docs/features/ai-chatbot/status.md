# Status: AI chatbot (F4)

## Lifecycle status

**Approved; plan approved, ready to implement.** The founder approved the specification, design and plan on 2026-11-02. No code exists yet.


## Current task

None. Waiting for F6 (pull request #24) to merge.


## Completed

- **Research**: [`research.md`](research.md), 2026-11-02. It was input, and approves nothing.
- **Specification**: [`spec.md`](spec.md): 107 acceptance criteria, with a table from requirement to criterion; D1 to D24 carried from the research, D25 to D79 new, D80 to D86 answered on 2026-11-02.
- **Design**: [`design.md`](design.md): the ticket, tool catalog, loop, checks, knowledge, screens with wireframes, failures, security, cost, 19 numbered differences from the foundation and F3, 2 proposed ADRs.
- **Plan**: [`plan.md`](plan.md): 8 steps. Step 1 (a safer MCP server) needs only F6, and the founder can judge it with Claude Code. Step 2 starts only after the founder's conversations.

**Corrections to the research, found by reading the code:**

- Deleting a person does not delete their conversations. `scrub` keeps the user row (`members/service.py:188-204`), so it must do so on purpose.
- Most screens cannot take an exact time range from the address. A citation link carries F3's `?from=&to=&project=` where the screen reads it, and says the range is not applied where it does not. A click never moves the person's window (D33).

**Aligned with F3's specification and design** in review: the reservation, chat's 80% share, the fingerprint, `TASK_SETS`, `INSIGHT_GRANTS`, the `ai` network, the window in the address, and F3's bases and word lists. An earlier claim that this covered the queue was false, because F3 had dropped row-level security on `jobs`. Corrected in the second review: F4 adds `assistant` to F3's three job functions. **Nobody has re-checked the other points since.**

## Next action

Start per [`phase-2-tracker.md`](../../product/phase-2-tracker.md) once F6 (pull request #24) merges. Start at plan T1.1 (Step 1 needs only F6). Step 2 onward waits for F3's provider and the buyer round's rule. File the design's 2 proposed ADRs in `docs/decisions/` at implementation start, numbered then.


## Open decisions

**None for the founder.** All three were approved on 2026-11-02, as recommended (*Founder answers, 2026-11-02*, below). The continue rule is applied to the buyer round's answers, and the accuracy bar to Step 4's report.

## Blocked

- **Steps 2 to 8 are blocked on F3 being built.** Step 7 is also blocked on F22, and one tool in Step 3 on F28.
- **All steps are blocked on F6 merging** and on founder approval of these documents.

## Risks

- **Nobody has said they want this.** The research has no interviews. The founder's five to eight conversations now come before Step 2, and a developer happy with Claude Code may need only Step 1.
- **Chat will sometimes be wrong in ways that look right.** Citations, labels and figure flags help a person check. They do not prevent a believable false cause. Rivals' published accuracy figures cannot be compared with anything here, and nothing is measured.
- **Injection can't be proved absent.** The structure limits what success gains: no write, no way out, one project. A steered model can still give a wrong answer.
- **Personal data reaches the provider.** Names, email addresses and customer ids in logs stay after masking (`mask/rules.go:25-38`). The consent step says so.
- **Answers may paraphrase a log,** so log erasure cannot reach them. Conversations expire at derived retention. Stated, not solved.
- **All figures are guesses:** 60,000 tokens a question, 8 steps, 8 KiB, 50 questions a day, 45 million tokens a month, 576 MB, 5 minutes, 40 characters, 200 ms. The bed's cost run and T6.4 replace them.
- **Local models may be weak** at the required answer shape and at resisting injection (LogJack, one paper). Which one to document comes from results.
- **Exact-scan search time at 5,000 chunks, on a 1 GB Postgres, is unmeasured** (T6.4).
- **Rivals move quickly.** Elastic is the closest on own model, own server and a knowledge base; its current terms need a further check before any comparison is published.
- **Row-level security on messages and steps is F4's own first use.** A probe showed the mechanism, not the real statements. T2.0a proves them first, with a stated fallback.
- **Tested only on free models (foundation item 4, changed 2026-11-02).** Free tiers have low limits, so Step 4's runs take longer; a question of about 31,000 tokens (a guess) is larger than some free limits. Free tiers may train on inputs and let people read them, so only seeded, synthetic data goes.
- **"Supported" covers only what was tested.** OpenAI, Anthropic and Bedrock have no free API tier. They stay "not yet tested" until someone tests them, though customers use them most, so the setup pages may name few providers at launch.
- **The local model shares the 24 GB Mac with the stack chat reads** (D80; decision 0047). If it spills or swaps, the smaller model replaces it, and the report says so. No GPU money is approved.
- **No server measurement before deployment** (foundation item 9). The 576 MB ceiling and search times are read on the Mac's Docker; the 16 GB server is measured only on Oracle after deployment, and its free allowance (2 Arm CPUs, 12 GB) is below that size.

## Validation

**Two small probes ran; nothing else:** no test of the product, no model, no build. In a throwaway `pgvector/pgvector:0.8.6-pg18-trixie` container with no network, under a role like `nexovue_insight`: a final write with reservation 0 aborted under a USING-only policy, under `WITH CHECK (true)`, and under separate `SELECT` and `UPDATE` policies; a `SECURITY DEFINER` function did it, and a second call changed nothing; finished rows and their steps were hidden; an upsert with no `SELECT` grant, and a view filter on an ungranted column, were each refused, and passed once granted. On FastAPI 0.162.0, in a scratch app with nested routers, a dependency saw `/search` for `/api/v1/logs/search`, and `abc%2Fdetection` reached `/services/{service}/detection`. The code was read on `feature/anomaly-detection`, with uncommitted work present, and line numbers may drift. Vendor pages and pgvector's README were read through a summarizing tool on 2026-11-02. Other outside facts come from the research.

## Specification impact

This is the first specification for F4. It needs changes to others once approved: `personal-data/spec.md`, `team-and-api-access/spec.md`, and differences in `phase-2-foundation.md` (design, *Specification impact*). None was edited in this work.

## Documentation impact

`docs/product/feature-catalog.md` still says F4 is to be specified, and links nowhere. `docs/product/setup/api.md` and the architecture documents change only when code does (plan, T1.8, T8.3). None was edited in this work.

## Reviews

Eight personas reviewed these documents on 2026-11-02, with 162 findings. **Each was checked against the code or a source before acting on it.** All 162 described a real problem, so none was rejected whole. 15 proposed fixes, or parts of them, were rejected or replaced, and are listed below with why. Nothing below is approved; all of it awaits the founder.

### Verdict per persona

| Persona | Verdict as received | Findings | IDs below |
|---|---|---|---|
| Architect | ready with fixes | 20 | A1-A20 |
| Developer | not ready | 21 | D1-D21 |
| UX | not ready | 28 | U1-U28 |
| Sales | not ready | 19 | S1-S19 |
| Marketing | ready with fixes | 14 | M1-M14 |
| Customer | not ready | 21 | C1-C21 |
| Security | not ready | 21 | X1-X21 |
| QA | not ready | 18 | Q1-Q18 |

These are the verdicts on the documents as they were. Nobody has re-reviewed them since these changes.

### What changed, in short

- **The ticket now works.** It allows by handler, lives on the message, and ends with the question.
- **Money.** The question holds the reservation, a check runs before each call, history is capped, and a dedicated sweep settles a dead question. The cost table now counts the tool list, so a typical question is about 31,000 tokens, not 15,000, and the suggested limit is about 45 million.
- **One clock,** `deadline_at`, from `call_seconds`, and compare-and-set on every state write.
- **Safety.** A citation alone earns no label; every model-controlled string that reaches the screen is checked; log quotes are folded; "no way out" is gone from product text; the AI role sees only rows in flight.
- **F3 and F28 reconciled:** network, task sets, grants, fingerprint, tool calling, window in the address, release routes.
- **`vector` no longer threatens alerts.** A `setup` step creates it, and failure means keyword search.
- **Added:** data levels, a read record, availability route, wireframes, wording table, 52 acceptance criteria, a requirement-to-criterion table, *What these tests cannot prove*, packaging, panel versus MCP, launch brief.

### Conflicts between personas, and the decision

| Conflict | Decision |
|---|---|
| A1 (handler) against D2 and Q13 (prefix-free path) | **A1.** The probe saw `/search`, which names no one route |
| A2 (message holds it; F3's sweep ignores chat) against D9 (opening `ai_runs` row) | **Message holds it, call rows hold 0.** F3's sweep then needs no change, and no fake call row exists |
| A11 (hard requirement, or out of the chain) against AC43 | **Out of the chain, by a `setup` step.** BR17 is firm, and F3 AC71 is the precedent |
| U1 (a failing argument ends the step) against X3 (hide, keep running) | **X3.** "Which URL failed?" must stay answerable |
| C11 (Copy answer) against S14 (Copy steps) and U5 | **Copy steps.** U5 is the founder's |
| D12 (grant retention) against Q8 (API marker) | **D12,** with a grant on `project_retention`. Q8's claim that the worker cannot read it holds only without a grant |
| U8 (browser zone) against C14 (`users.timezone`, else browser) | **C14,** which includes U8 |
| A12 (evict facts) against C8 (facts outside the cap) against D19 (runbook refused at cap) | **Limits that fit, plus eviction.** Runbooks always fit, facts have their own ceiling, notes always save |
| X12 and D19 (delete in flight) against A15 (treat as gone) | **409 to a person;** removal settles in its transaction, and the worker treats a gone row as a normal stop |
| X1 (taint) against S1 and Q1 (match rule) | **The match rule,** at least as strict |
| S4 (about 25 million) against the corrected table | **About 45 million.** S4's arithmetic left out the tool list |

### Rejected: fixes or parts of fixes

The problem each one named was still fixed by other means, unless it says otherwise.

| Finding | Rejected | Why |
|---|---|---|
| D2 | Compare `removeprefix(API_PREFIX)` paths | `/search` names no one route. Checked by probe |
| A2 | Have F3's sweep ignore `purpose='chat'` | Not needed: chat's rows hold 0, so F3 stays unchanged |
| D9 | One opening `ai_runs` row carrying the reservation | A row for a call that never happened, in a table of calls |
| A11 | pgvector as a hard requirement, excepted from BR17 | An exception to the rule that protects alerts, when an alternative exists |
| U1 | End the step on failure | See conflicts. X3's form is used |
| U3 | `<main>` as a *size* container | Size containment on a scrolling element would break its height. Inline size does the job |
| D19 | A runbook save refused at the chunk cap | Limits now fit, so the case cannot arise; only the 200 and 32 KiB limits refuse |
| C3, S7 | A send-time attribute drop list; excluding one service | The project is the unit, and masking settings are the place. Data level and email masking cover the larger objection. **Remains a risk:** a sales objection |
| C11 | **Copy answer** | U5: model words do not leave the console by nexovue's own hand. A person can still select text |
| C15 | **Send this answer to my admins** | D19, BR14: widens who reads poisoned text, and undoes privacy |
| C13, S19 | One runbook on several projects; a CLI import | A shared row breaks the project cascade and search's binding, so upload makes separate copies. Tokens are read-only. **Remains a risk:** stale copies |
| C17 | Emails at 50% and 70% | F3 A31 chose the screen; this role holds no email key. An on-screen warning is added. **Remains a risk:** nobody looks at the screen |
| S6 | Keep notes past the telemetry's clock | F22 will not keep cases past what they name (`retention.py:6-9`), and its founder decision 2 owns that. Save as runbook is added instead |
| X17 | Worker or `valid_for` confirming the route matches the tool | The slot rule and handler list close it, and a second check adds no protection |
| M5 | Recording Agent Builder as needing an Enterprise licence | The page read says only "appropriate subscription". Recorded as not confirmed |

**Also not done as asked:** U7's panel line "Screens now show…" is not needed, because a click no longer moves the person's window. C2's new tools are not added to MCP in this version, though tokens gain the routes; D45 keeps MCP to seven new tools. A17's "a browser key can set the name" stays true for names passing the pattern, which are fenced and inert.

### What remains as a risk

- **A believable false cause.** A matched sentence can still mislead. The label says what matched.
- **The command and URL checks are heuristics,** and a false refusal or a miss are both possible. The bed reports rates.
- **Paraphrase of a log** stays in stored answers. **Log erasure cannot reach it.**
- **A broken AI process still reads all chunks,** questions in flight and every connection key. Acceptable on a single-tenant install; not on a hosted one.
- **The API now shares a network with model-driven code.**
- **FR40's masks and intake masking have holes.** Names and customer ids still go at *facts and text*.
- **Row-level security, tool calling on real providers, embedding on Bedrock and Ollama, and exact-scan time are all unproved.**
- **Phone keyboards and real screen readers** are manual checks, and `interactive-widget` was not run.
- **A stopped question's last call may still be running** while a new question starts, bounded by its reservation and slot.
- **Expensive:** the bed's cost rose, and T2.10b touches 18 finished files. Self-hosted only for now, so **this feature earns no money yet.**
- **Slack stays out, by U5,** and rivals answer there.
- **Rival facts** are unusable in public until T8.5.
- **Memory is short on a self-hosted install: 30 days by default.** Foundation decision 6 makes it an operator setting; the default rises only after the bed measures disk use.
- **The repository is public,** confirmed on 2026-11-02 by GitHub's API without a login (visibility public, AGPL-3.0). Results stay outside it until the founder has read them (foundation decision 5).

### Second review, 2026-11-02

Fifteen proven problems. Each was checked against the code or F3, F22 and F28's documents before changing anything, and all fifteen held. All are fixed in these documents; none is implemented, and nobody has re-reviewed the result.

| Severity | Problem | Result | Where fixed |
|---|---|---|---|
| Blocker | F4 built on F3's dropped row-level security on `jobs` | Fixed. Also found: the claim function needs a queue list, or the two queues' caps fail | Design 3, grants, *A plain limit*, difference 11; T2.0a, T2.3, C4 |
| Blocker | The row-level policy stopped every final write | Fixed, **but not as the review proposed.** A separate `WITH CHECK` still aborts, as a probe showed, because an `UPDATE` with a `WHERE` checks the `SELECT` policy too. One function makes every final write (D73) | Design *Settling*, grants, AC67 |
| Major | Aged-out check used raw retention for rollup tools | Fixed. Also found: `service_stats_1m` and `detection_readings` keep the lesser of derived and 35 days, so there are three clocks (D74) | Design *The aged-out check*, spec error table, AC24, T3.1 |
| Major | Grant table broke its own rule | Fixed. `ticket_hash` needs no grant, as the function nulls it | Grants |
| Major | Removing `is_token` broke F3 and F22 | Fixed. `is_token` kept; F3 and F22 read `is_person` (D75) | Trap 2, T2.2, T7.2, AC105 |
| Major | Quotes had no working rate tier | Fixed (D76) | Routes, T3.2, AC107 |
| Major | Rejected link and limit decisions survived | Fixed. C2, reuse table, difference 4, D44, duplicate ADR bullet, this file | As named |
| Major | Link table against F3's helper and router; AC22 | Fixed (D79) | Link table, D33, AC22, T3.3a |
| Major | F22 said to need no change | Fixed. F22's note service stays as it is | Specification impact, D58, T7.2 |
| Major | Runbooks did not always fit the cap | Fixed, by a ceiling of 18 chunks (D77). Merging at a fixed minimum would not fit: at 1,000 characters it allows 6,800 chunks | Design 6, FR32, AC106 |
| Major | Gate criteria that could not be scored | Fixed. AC51 at 90%, a guess (D78); AC80 at 60 seconds, a guess; AC54 | Spec, design, plan |
| Minor | AC57 depended on an unstated limit | Fixed | AC57 |
| Minor | Undeclared foundation differences | Fixed | Differences 18, 19 |
| Minor | Stale code claims | Fixed. Also found: `@lg:` is not the same width as `lg:`, from memory of Tailwind's defaults, which T2.10b checks | Design, T2.10b, citations, memory |
| Minor | Unsourced competitor statements | "Few" sentence dropped. The Grafana claim is marked unsourced, as no URL exists | Spec *Problem*, research |

F3's and F22's documents were reconciled with these changes on 2026-11-02 (*Cross-feature reconciliation*).

### Every finding

**Fixed** means the documents now hold the fix named; the location is the main one. All 162 are fixed in this sense, none implemented, and *part* marks those with a rejected part above.

| ID | Problem | Where fixed |
|---|---|---|
| A1 | Allow-list compares wrong value | D55, design 1, AC53, T2.2 |
| A2 | Dead question leaks reservation; timeout returned | D53, design *Settling*, AC27, AC58 (part) |
| A3 | 60,000 does not limit spending | FR19, FR6, cost table, AC25, AC56 |
| A4 | Clocks disagree | FR22, D67, design *One clock*, AC54, AC55 |
| A5 | Waiting outcomes wrong | States, design *The job*, AC29, AC54 |
| A6 | Sweep sends excluded text; deleted projects | Design *Embedding*, inventories, grants, AC35, AC70 |
| A7 | No embedding model column | FR27, FR31, data table, AC85, T6.1 |
| A8 | Grants break worker | Grants, AC67, T2.3 |
| A9 | Consent weaker than F3 | FR25, D41, AC3 |
| A10 | Not reconciled with F3 | Design status, network, T2.3, T2.9, differences |
| A11 | `vector` can stop the product | D62, design *Extension*, AC43, T6.1 (part) |
| A12 | Knowledge cap unenforceable | FR32, FR28, design *The caps*, AC83 |
| A13 | F4 inside F22's transaction | D58, T7.2, specification impact |
| A14 | Delete resets daily limit | D56, AC37 |
| A15 | Ticket outlives conversation | D54, design 1, AC11 |
| A16 | Deploy tools on dead routes | FR7, C9, T3.1 |
| A17 | Stale fact; BR10 names | BR10, FR28, AC39, AC84 |
| A18 | Short memory horizon unstated | Goal, FR41, FR42, AC95 |
| A19 | One basis per tool | Design 2, check 7, AC21 |
| A20 | Poll returns whole conversation | Routes, T8.1, AC98 |
| D1 | Nobody builds tool calling | T2.7a, T6.0, AC94 |
| D2 | Every ticket read refused | A1's fix (part) |
| D3 | No network path | Design *The network*, T2.3, AC66 |
| D4 | Grants fail | As A8 |
| D5 | Log quotes read as gone | Design *Quote display*, AC19, T3.2 |
| D6 | Encoding claim false | FR8, design client, AC10, T2.8 |
| D7 | Task registration wrong | Design 3, T2.3, T2.9, T5.5, T6.1 |
| D8 | Sweep, AC27, counters cannot work | `assistant_sweep`, AC27, T8.2, columns |
| D9 | Dead reservation | As A2 (part) |
| D10 | Attacker text labelled measured | Design 2, FR7, AC12, T1.7 |
| D11 | False-alarm line; project finding | FR28, AC83, AC84 |
| D12 | AC24 unbuildable | Grants, loop, AC24, T3.1 |
| D13 | Five-minute lockout; late write | As A4 and A5 |
| D14 | Gate before what it tests | T5.7, T6.6, T8.0 |
| D15 | AC15 has no file | AC15, T2.0, T2.10 |
| D16 | Vector work too early | D62, T2.1, `query_embedding_model` |
| D17 | Ticket purpose; `ticket.id` | Design 1 |
| D18 | Viewers and runbooks | D48, AC92 |
| D19 | Cap and in-flight delete | FR32, routes, AC59, AC83 (part) |
| D20 | Step 1's demo has no command | T1.0, T1.1 |
| D21 | Stale plan; tests unnamed | Plan, all tasks |
| U1 | Step arguments unchecked | FR14, check text, AC62 (part) |
| U2 | Panel failure takes the console | Design *Placement*, AC73 |
| U3 | Pushing breaks layouts | D68, T2.10b, AC48 (part) |
| U4 | No availability route | Routes, AC74 |
| U5 | Five-minute wait; silences | States, phase lines, AC54, AC103 |
| U6 | RefreshControl falsified | Design *Polling*, AC75 |
| U7 | Citation moves window | D33, FR3, AC22 |
| U8 | Wrong clock | FR3, FR9, AC76 |
| U9 | Check step fails on phones | Flow A, AC48 |
| U10 | Stale context | FR2, design *Context*, AC49 |
| U11 | Answer below steps | D70, wireframes |
| U12 | Refusals unmapped | Design, *Error cases*, AC79 |
| U13 | Answer shape lacks flags | Design 4, AC16, AC77 |
| U14 | Unchecked reason text | FR12, `refused`, AC77 |
| U15 | Chip wording false | FR17, *Words* |
| U16 | No link table | FR18, *Where each step leads*, AC22 |
| U17 | Accessibility vague | Design, AC50 |
| U18 | Closed panel, reload | Design, UR19, AC78 |
| U19 | Too little data | States, *Error cases*, AC24 |
| U20 | No wireframes | Design *Wireframes* |
| U21 | Phone asserted | Design composer, AC48 |
| U22 | Quotes unsafe | FR15, *Words*, AC63 |
| U23 | Settings conflicts F3 | FR24, FR26, routes, AC2 |
| U24 | Correlation id; failures | States, AC103 |
| U25 | Panel width fixed | D69 |
| U26 | Runbooks under-decided | D20, D48, design, AC92 |
| U27 | Starters undefined | *Starters*, AC49 |
| U28 | Wrong and delete results | D47, *Words*, AC91, AC102 |
| S1 | Label laundering | BR6, check 7, AC61 |
| S2 | "No way out" untrue | BR18, FR14, FR15, AC62, AC63 |
| S3 | Selling line false | Founder decision 2, FR24 |
| S4 | 20 million fails | D26, cost table, `over_limit`, AC101 (corrected) |
| S5 | Limit oversold | FR19, AC56, AC58 |
| S6 | 30-day memory | Goal, dependencies, FR41, FR42 (part) |
| S7 | Log text all or nothing | D63, FR39, AC68 (part) |
| S8 | "Exactly" untrue | UR13, FR24, AC2 |
| S9 | Meaning search for few | Provider table, D61 |
| S10 | No sellable model answer | FR26, AC96, Step 4 report |
| S11 | MCP objection | Spec, *Chat versus MCP* |
| S12 | No hosted statement | Spec, *Who gets what*, AC92 |
| S13 | Metrics omitted | FR7, D59, T3.1 |
| S14 | Evidence unshareable | D33, FR38 |
| S15 | Owner cannot answer | `assistant_read_log`, AC72 |
| S16 | BR15 false | BR15, D60, AC35 |
| S17 | Gate meanings differ | AC15, AC51, research |
| S18 | Counts mislabelled | FR7, design 2 |
| S19 | Runbook setup | FR, upload, AC92 (part) |
| M1 | BR18 over-claims | BR18, AC99 |
| M2 | Sentry missing | Research table, spec line 104 |
| M3 | Claude answer undone | Research 8.4, spec |
| M4 | No name or positioning | D52, BR18, T8.3 |
| M5 | Elastic stale | Research table (part) |
| M6 | "Few choices" unsupported | Spec, research rows |
| M7 | Price message misleading | Research 4.4, 8.6 |
| M8 | No launch work | T8.5, gate |
| M9 | Reports published by merge | Founder decision 4, Step 4 |
| M10 | Market risk untested | Preconditions, founder decision 5, T8.6 |
| M11 | Persona over-promises | Spec target users |
| M12 | Soft competitor facts | Research |
| M13 | Not a difference | Research 8.3, 4.5 |
| M14 | Meaning search promise | BR18, T6.5 |
| C1 | Measured on invented cause | As S1 |
| C2 | Tools cannot answer | D59, T3.1, T3.1a, T4.1 |
| C3 | Consent all or nothing | FR39, FR40, FR24 (part) |
| C4 | What went where | As S15 |
| C5 | Commands through quotes | As S2, FR15 |
| C6 | Third-party outage withheld | D66, FR14, AC18 |
| C7 | 30-day memory | As S6, FR41 |
| C8 | Flapping fills index | FR28, design *The caps*, AC83 |
| C9 | Spending unenforced | As A3 |
| C10 | Caveats removed silently | BR5, FR12, FR13, AC16 |
| C11 | Cannot hand over | D33, FR38 (part) |
| C12 | Meaning search for few | D61, T6.0 |
| C13 | Runbooks too much work | FR, upload, AC92 (part) |
| C14 | Wrong clock | As U8 |
| C15 | No real signal | D47, `assistant_monthly_counts`, AC101 (part) |
| C16 | Secrets in runbooks | FR40, FR32, AC69 |
| C17 | 80% surprise | FR19, `over_limit`, AC101 (part) |
| C18 | No handover read | FR7, T7.2 |
| C19 | Silent waits | States, phases, `call_seconds` |
| C20 | Private route | AC96, T8.3 |
| C21 | Consent edges | FR25, FR24, D48 |
| X1 | Label chosen by model | As S1 |
| X2 | Log quotes | Check 5, AC63 |
| X3 | Step records | Check text, AC62 |
| X4 | Consent weak, late | FR25, loop, AC3, AC60 |
| X5 | Opted-out embedding | As A6 |
| X6 | Names in index | BR10, FR28, AC39 |
| X7 | Measured tools return text | Design 2, FR7, AC12 |
| X8 | Masking overstated | Design preview, FR24, AC2 |
| X9 | Runbook secrets | FR32, FR40, D48, AC69, AC92 |
| X10 | AC32 false | Copy check, AC32, *cannot prove* |
| X11 | BR15 expiry | D60, AC35 |
| X12 | Limits in deletable rows | D56, routes, AC37, AC59 |
| X13 | Role reaches too far | D57, grants, *A plain limit*, AC67 |
| X14 | Audit incomplete | AC71, AC72 |
| X15 | Query strings in logs | Design *Secrets*, AC65, T2.3 |
| X16 | Network missing | As D3 |
| X17 | Encoding claim | As D6 (part) |
| X18 | `valid_for` gaps | Design 1, AC7, AC11 |
| X19 | Question's trust class | FR6, AC64 |
| X20 | AC15 undefined | AC15, T2.10 |
| X21 | Personal-data inventory | AC34, inventories |
| Q1 | Laundering untested | AC61 |
| Q2 | AC15 unprovable | AC15 |
| Q3 | Other model text | BR7, AC15c, AC62, AC77 |
| Q4 | Regression promise thin | AC4, AC80, T2.13, T8.1 |
| Q5 | Requirements without criteria | AC86-AC104, table |
| Q6 | Gate weaker than foundation | AC51, founder decision 1, T4.1, T4.2 |
| Q7 | Gate runs too early | T5.7, T6.6, T8.0 |
| Q8 | AC30, AC24 hollow | AC24, AC30, T3.1, T3.5 |
| Q9 | Names under measured | Design 2, T1.7 |
| Q10 | AC32 false | As X10 |
| Q11 | Budget mechanism | AC25, AC56, AC57 |
| Q12 | Stop, death, sweep | States, FR21, AC27, AC28, T2.9 |
| Q13 | Ticket refusal only | AC53, T2.2 (part) |
| Q14 | Ranges for deploys | FR7, FR9, AC22, T3.1 |
| Q15 | No reachable provider, seed | T2.0, T4.1 |
| Q16 | Isolation proof hollow | AC82, T5.5, design inventories |
| Q17 | NFRs without targets | AC97, AC104, T2.3, AC50 |
| Q18 | No limits statement | Spec, *What these tests cannot prove* |

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- `is_person` comes from F3, not T2.2, and F22's helper no longer needs a change at T7.2. A ticket could otherwise have failed on F22's form between T2.2 and T7.2 (design, plan, D75).
- F5 takes `scheduler-insight` to 8 slots later (D39, differences row 9).
- The findings link follows F20's move of the feed (design).
- Founder item 2 points to the foundation's one selling line (spec).
- F4's differences from the foundation were written into the foundation: tables, routes, `embed(texts, model)`, the 45 million suggestion, no `assistant_tickets`, and the MCP catalog sentence.

## Last updated

2026-11-02

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Own questions answered, 2026-11-02

Every open question in the five documents was read against the code and, where it rested on outside facts, checked on the web. Recorded in `spec.md`, *Own questions answered*, with sources.

**Decided by the authors (technical, so not the founder's):**

- **D80, the local model for the first report:** `gpt-oss:20b` on Ollama, on the founder's Apple M4 Pro with 24 GB, with `nomic-embed-text`. Native tool calling, Apache 2.0, fits 16 GB. No new cost. The report names the hardware and claims no speed elsewhere. (Plan question 1.)
- **D81, embeddings:** Bedrock through `InvokeModel` on Titan Text V2, not Converse; OpenAI-style accounts and Ollama through `/v1/embeddings`. T6.0's real calls still come first. (Plan question 3.)
- **D82, Elastic's licence:** a paid self-managed subscription, most likely Enterprise; T8.5 rechecks before any public text. (Plan question 2.)
- **D83, the reader-step trigger:** fixed before any run, at more than 10% of log-text trials followed, or any hard-gate failure traced to log text. (T4.4 and the design no longer send it to the founder.)
- **D84, slow knowledge search:** over 200 ms, a partial HNSW index per embedding model, with iterative scans, on the bundled pgvector 0.8.6. (AC97 and T6.4 no longer send it to the founder.)
- **D85, if the row-security fallback fails:** the authors redesign; Step 2 stays stopped until the new design passes the spike.
- **D86, accepting differences from the foundation:** no separate owner; each is written into the foundation, and approving the spec accepts them.

**Settled by the foundation's approval, removed from the open lists:** evaluation money, the selling line, AI on the hosted service, publishing results, and self-hosted retention (foundation items 4, 2, 1, 5 and 6). Pointers updated in spec, design, plan and research.

**Kept for the founder, rewritten with a recommendation:** the rule for building past Step 1 (scope and what is sold), the accuracy bar for "supported" (a public promise), and competitor research in the public repository (public exposure about named companies).

**Not checked:** the exact Elastic tier, as the subscriptions page's tier marks did not survive text extraction; whether a Bedrock API key works on `InvokeModel` (AWS's page shows only Converse); independent tool-calling scores for small local models, as the rankings found were vendor lists.

### Review, 2026-11-02

A second panel tried to break each answer above against the code, the foundation and outside sources.

**Held as written:** D81 (AWS's API-key page still shows only a Converse example, so T6.0's real call stays), D82, D83, D85, D86, and the five items settled by the foundation. The repository is public: an unauthenticated GitHub API call again returned `"visibility": "public"`, AGPL-3.0.

**Changed:**

- **D80, the local model, now fits the machine.** F3 runs its local model with the stack stopped (F3 A81); chat cannot, because its tools read the live API. A 14 GB model beside Docker's 11.7 GiB (`docker info`) on 24 GB would swap. The run now starts only what chat reads, lowers Docker's memory, checks `ollama ps`, and falls back to `qwen3:14b` (9.3 GB, 40K context).
- **D87, new: context length on a local model.** Ollama defaults to 4K context below 24 GiB of GPU memory, and its OpenAI-style endpoint cannot change it ([context length](https://docs.ollama.com/context-length), [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)). Chat's prompts pass 4K, so the bed and every customer's local model would lose text. Set to 32K for the bed; a connection probe refuses "ready for chat" if a known-size prompt comes back counted short; the setup page names `OLLAMA_CONTEXT_LENGTH`.
- **D84, the slow-search index, now gets used.** Postgres matches a partial index at planning, so a bound parameter never uses it ([Postgres docs](https://www.postgresql.org/docs/current/indexes-partial.html)), and the API's driver is asyncpg (`apps/api/pyproject.toml:13`). The model name goes in as a literal from the stored setting. A model chosen after `setup` is searched exactly until the next `setup`.
- **D88, new: Step 1 is built straight after F6.** Founder item 8 holds one round before F28's Step 1b, and founder item 1 asks people after seeing Step 1, which in the foundation's order would not exist yet. Design differences row 20; it must be written into the foundation before merge (D86).
- **Founder item 2, the accuracy bar:** now set after a tune run and before the report run, as F3 does (F3 AC62, A83), not from the report. The old wording contradicted D83's own reasoning that a bar set after the numbers can be bent. AC51 and plan Step 4 follow.
- **Founder item 3, competitor research:** widened. The foundation itself names rivals in the public repository (`phase-2-foundation.md:626-640`), as do other specs, so it needs one answer for the whole repository. Still the founder's.
- **Design, differences table:** rows 1, 2 and 19 marked as history, since the foundation now reads as this design does.

**Classification:** all three founder items stay the founder's (scope that changes what is sold, a public promise, public exposure about named companies). No agent decision was found to be a founder matter.

**Not checked:** whether Ollama truncates an over-long prompt silently or with an error (T2.0's first real call); whether `gpt-oss:20b` with a reduced stack actually fits (Step 4's first run).

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** the continue rule past Step 1, at least half yes to both questions (3 of 5, 3 of 6, 4 of 7, 4 of 8) (item 21); the "supported" bar, 80% right numbers, 90% right time ranges, every hard gate passed (item 12); dated competitor research allowed in the public repository with the "internal research, not a comparison or a claim" header, never in sales or marketing (item 15). The header must be added before merge.
- **Changed, B: no paid model calls.** Step 4 runs on a free-tier cloud model and `gpt-oss:20b` on the Mac. "Supported" names only what passed; OpenAI, Anthropic and Bedrock stay "not yet tested".
- **Changed, A: no rented test server.** Memory and search time are read on the Mac's Docker; the 16 GB server waits for Oracle after deployment.
- **Open decisions:** none for the founder.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 2 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.
