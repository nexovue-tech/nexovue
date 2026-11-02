# Design: Finding explanation, with optional AI wording (F3)

## Status

**Approved** by the founder, 2026-11-02, against [`spec.md`](spec.md), after eight persona reviews (`status.md`, *Reviews*). No code is written from it yet.
The feature's lifecycle status lives in [`status.md`](status.md).

**How it was checked.** The code was read on branch `feature/anomaly-detection` with uncommitted work present. Line numbers are for that
working tree. **Nothing was run.** Every window, limit, memory, token and cost figure is a guess and says so. Two facts were read on the web on
2026-11-02 through a summarizing tool: PostgreSQL's `INSERT` privileges and the Docker image's `POSTGRES_USER`. Provider facts come from
[`research.md`](research.md) and [`phase-2-foundation.md`](../../product/phase-2-foundation.md) and were not re-read, apart from AWS's API keys page, read directly for the review on 2026-11-02, and PostgreSQL's `LOCK` page, read the same day.

## Words used here

| Term | Meaning |
|---|---|
| **Role** | One way of starting the `nexovue` image, run as its own container (ADR-019) |
| **Transactional enqueue** | Adding a job in the same database transaction as the change that caused it, so both happen or neither does |
| **Row-level security** | A Postgres rule that limits which rows an account can see or change in a table, enforced by the database |
| **`SECURITY DEFINER` function** | A database function that runs with its owner's rights, so an account can do one narrow thing without holding rights on the table |
| **Advisory lock** | A lock on a number rather than a row, which any account may take unless the function is revoked, and which nexovue uses for leadership |
| **Column grant** | Postgres permission for an account to read or change named columns, and no others |
| **Lease** | How a worker holds a job: if the worker dies, the lease lapses and the job can run again |
| **Reservation** | Taking the most a run could spend from the month's allowance before calling, so parallel runs cannot overspend |
| **SSRF** | Server-side request forgery: tricking our server into calling an address it should not |
| **Rule of Two** | Meta's guideline: an AI step may have at most two of *reads untrusted input*, *reaches private data*, *changes something or sends data out* |
| **Wire format** | The exact shape of the request and answer a provider expects |

## Context and goal

A finding says what changed and never why. This design gives each finding a frozen evidence record, a likely cause by fixed rules, and optional AI
wording, in three separated steps (foundation, FD1):

```
 detection (no AI)            facts (no AI)                    wording (AI, optional)
 scheduler-light              scheduler-light, queue evidence  scheduler-insight, queue insight
 ───────────────              ───────────────────────────────  ──────────────────────────────
 opens finding ──same tx──▶   reads Postgres + ClickHouse      reads model_input only
 alert goes out               runs the rules                   calls the provider
                              writes finding_explanations ──▶  checks the words
                              writes `explained` notification  writes wording, ai_runs
                              decides whether to run AI
```

The alert has already gone when the first box finishes. Each step can fail, or not exist, without touching the one before it.

**What this means.** A successful injection can only change words, those words are checked for form when written and again when read, and the model is given no text an attacker
wrote. If the AI process itself were broken into, the bundled telemetry stores, repository keys and alert channels are out of its reach, and it can read no real names. It can still read provider keys, spend tokens, and open raw sockets. It holds no right on the job queue or `findings`, and Postgres limits what it can do to others (see *The Postgres account*); on a Postgres where `setup` cannot revoke the advisory-lock functions, it could still take the beat leader's lock and stop scheduling, which `/settings/ai` and `setup` then say. **The Compose network removes the bundled services; it is not an outbound firewall** (the guard runs in the process, `probes/addresses.py`), so it could reach a server's private LAN, a metadata address, or a Kafka named by address outside Compose. `docs/operations/ai.md` says what an operator should block on the host.

## Where this differs from the foundation

The foundation asks to be told, not quietly differed from. Rows 1 to 7 are the research's, confirmed by reading the code again. Rows 8 to 33 are new.

| # | Foundation or research says | The code says | This design |
|---|---|---|---|
| 1 | `scheduler-insight` holds no Kafka and no `CHECK_SECRET_KEY` | It cannot start: `kafka_brokers` is required with at least one entry, and production refuses a missing `check_secret_key` (`scheduler/settings.py:53,173`) | A `SCHEDULER_ROLE` setting makes both follow the role, with a test booting on exactly the permitted variables |
| 2 | "No telemetry database within reach" | Kafka listens with `PLAINTEXT` (`compose.infra.yaml:23`), and no Compose file names a network | The role gets its own network, which removes the bundled stores. It reaches Postgres and the outside. Prometheus joins it to scrape (A39) |
| 3 | All provider HTTP inside `nexovue_platform.ai`, through `probes/http_check.perform` | Not possible together: `perform` is in the scheduler and needs `pycurl`, which only the scheduler depends on | Wire formats in `nexovue_platform.ai`, transport in `nexovue_scheduler.insight`. Both join `REPOSITORY_AI_MODULES` |
| 4 | `/ai/connection/check` is an API route calling `check()` | The API has `httpx` and no address guard | The route enqueues a job with `wake=True` and reads `job_results` |
| 5 | Facts job enqueued after commit, at `project_job.py:1808` | That point has no finding ids (`Outcome`, `:435-447`), and a crash there loses the job | Enqueued inside the transaction, at `:1113` and `:925` |
| 6 | Basis `measured` covers rollups | Rollups can be moved through a browser key (see *Basis*) | Three bases |
| 7 | Every role imports every task module | True (`jobs/preload.py`, `registry.py:19-30`) and costly | The role names its own modules |
| 8 | Research: "whether browser spans reach rollups" is not traced | **Traced by reading: they do.** Both doors publish through `opts.Publisher.PublishTraces` (`otlp/signals.go:176`, `handler.go:213-215`), and `countable` and `edgeSpan` copy no door (`traceassembly/stages.go:39,64`) | `telemetry`, as the research feared. F28 requires an F6 change for `service_stats_1m`, and this design adds the door to the edge rollup (A63). Until they land, a browser key can move a rollup, and so can open a finding |
| 9 | Research rule 3: "a check on this service" | `Check` has no service field (`data/postgres/models.py:492-531`) | Project-wide fact, never a cause (A17) |
| 10 | `AI_PRIVATE_ENDPOINTS` holds "hosts and ports" | `ConnectGuard.open_socket` sees only a resolved address (`addresses.py:139-146`). Resolving a name there again reopens rebinding | IP addresses or ranges, with ports (A23) |
| 11 | "`nexovue_insight` gets only what its tasks need", provisioning open | `restore-postgres.sh:43` runs with `--no-privileges`, so a restore drops all grants. No `CREATE ROLE` exists today | Grants applied by `setup`, which runs after every restore (A34). The job queue is reached through functions, so no policy depends on the role (A57) |
| 12 | The AI role reads "its inputs" | Foundation leaves the column open | It reads only `model_input`, by column grant (A19) |
| 13 | `wording_state` has six values | A muted or flapping finding fits none | Adds `held` (A22) |
| 14 | Section 5 has no action for asking for wording | `ASK_ASSISTANT` is F4's | Adds `REQUEST_EXPLANATION` (A20) |
| 15 | Routes list lacks consent and opt-out | They are needed | Adds `/ai/consent`, `/ai/project-opt-outs/{project_id}`, `DELETE /ai/connection`, `.../explanation/read-again`, `/settings/explanations`' reads and writes |
| 16 | `ai_available` in `nexovue_platform.ai` (`:545`), and evidence must not import that package (`:462`) | Gather needs both, so the two sentences cannot both hold | `ai_available` in `nexovue_platform/deployment.py`. `aliases` in `nexovue_platform/evidence/`. The word job's task and queue names in the evidence contract (A37) |
| 17 | The writing-out room never holds `AI_SECRET_KEY` (`:70`) | The API must seal the key, as it does `CREDENTIAL_ENCRYPTION_KEY` (`nexovue.yaml:288`) | `api` and `scheduler-insight` hold it, no other service |
| 18 | The role has a path "for F4, to the API" (`:69`) | F3 has no use for it | The `ai` network holds no API in F3. F4 adds it, and AC42 must still deny Kafka and ClickHouse then. F4's design must say so |
| 19 | Frozen evidence holds error templates (FD22) | A template keeps names (`readings.py:70-79`), for the derived retention (`0005_plans_table.py:74-75`) | Exception type, group id and counts only (A47) |
| 20 | `explained` when `likely_cause` exists | A forged span could then push a message, and a fan-out would send many | Signed causes only, from the settled read, capped per channel (A42) |
| 21 | Section 11: 11 fault flags; 42 payloads on real models | `tools/fault-bed/fault_model.py` has 14. All 42 reduce to one kind of input | 14 faults; the 42 go through with no model, and real trials vary what an attacker can move (A54) |
| 22 | Rule order and "cause must not be the signal" as the research put it | Datadog treats a slower or failing service as a symptom, and F3's dependency rules do not | F3 is deliberately looser, and says so (spec, FR8). No authority is borrowed from Datadog |
| 23 | `:82-83`: row-level security on `jobs` stops the role touching another queue | `DELETE` permits `LOCK TABLE` in any mode, which row-level security does not limit (PostgreSQL `LOCK`). An AI-only rule would sit on the alert queue with AI off | Four `SECURITY DEFINER` functions and no table rights. Same end, stronger (A57) |
| 24 | Each feature counts its own cap against 8 slots | Capped queues are claimed first (`worker.py:261-269`). `detection`, `evidence` and F28's `releases` at 2 each hold 6 of 8, and a later queue could take the rest | One floor for all capped queues (A56). Foundation section 1 should state it as a shared rule |
| 25 | Foundation section 8: F3 builds the scheduler Postgres test | It exists: `apps/api/tests/test_scheduler_postgres_tenancy_structure.py` | F28's T1.13 extends it first. F3 confirms it covers `evidence/` and `insight/` (A72) |
| 26 | Section 4: AI off shows admins a calm line on turning it on | The on-call page is not where to sell a setting | No line unless something other than a person turned it off (A53) |
| 27 | Section 8: the screen word is "explanation" | A heading "Where to look first" sat over "Not enough evidence" and competed with "Look next" | Heading is "What lines up with this" (A69) |
| 28 | Section 11: passing injection includes "no unlabelled false cause" | The model names no cause | Measured with no model by AC76; the difference is recorded in the spec |
| 29 | Section 12 and `phase-2-plan.md`: selling line "your data stays with you" | Alert channels carry names | The foundation's one wording, approved by the founder 2026-11-02 (item 2); its "own server" clause waits for a local model to pass AC63 (spec A81) |
| 30 | F28 FR44: an opening line naming the last release | It says order only, and is left out of a grouped message (F28 AC70) | F3 still sends its one follow-up, with the rule's judgement and base rate (A55). PagerDuty carries F28's line, and no more |
| 31 | `:247`: no run for a muted finding "until a person opens it" | A GET must not start spending, and tokens read that route | A click starts the run (spec FR35). See *Alternatives and tradeoffs* |
| 32 | `:414`: `explained` "at most one per finding" | `notifications` rows are per channel, and a channel told later would hear nothing | At most one per finding per channel, capped per channel (spec FR21) |
| 33 | `:260`: an abstaining model reads "Not enough evidence" | The same words already mean no cause was found, with two meanings on one page (UX 17) | "Nothing to add to the facts above" |

**Answered, from the foundation's "Not checked" list.** `POSTGRES_USER` on the Docker image is created "with superuser power" ([Docker Hub: postgres](https://hub.docker.com/_/postgres), read 2026-11-02). The comment in `deploy/postgres/init/01-extensions.sql` calls it an application role that must not have
superuser, which is out of step with what the bundled image does. It matters here in two ways: `setup` can create `nexovue_insight` and revoke from `PUBLIC`. An install pointed at its own managed Postgres needs an account with
`CREATEROLE`, or an operator creating the role by hand; `docs/self-hosting/` must say so.
**If the role cannot be created,** `setup` logs a clear warning and carries on, `/settings/ai` says AI is unavailable (the API reads `pg_roles`), and no other service is affected (AC71). With no policy on `jobs` (A57), `setup` need not refuse any account for AI's sake, so an install that will never use AI carries no AI rule on its alert queue.

## User experience

**The on-call engineer's job:** know where to look first, in under a minute, on a phone.

1. A message arrives, as it does today, and links to the finding (unchanged, on time).
2. The finding page opens. Under the title, above the actions, is one line, its space always held so nothing below it moves: "Likely, from telemetry only: calls to `payments`, which opened its own finding 3 minutes earlier. See traces" Or "Possibly: calls to `payments` started failing in the same minutes. See traces" Or "Not enough evidence to name a likely cause", with "Look next" directly under it. Before the first read finishes it says "Reading the facts", and every other state has its words in the spec, *States*.
3. Below the actions are numbered facts, each with its basis, and a link where one exists, then "Look next", which always renders. The person follows a link to the right service and time.
4. If AI is on, plainer words sit under the facts, labelled as AI, for someone who does not know the system.
5. About five minutes later (a guess), if a signed deploy is the likely cause, one follow-up message says so and gives its base rate (`explained`), whether the deploy was known at opening or arrived since. Otherwise nothing more is sent. PagerDuty carries the alert, F28's release line and the link; the cause is on the page.

The flow crosses no extra screens: the question is answered on the page the alert already links to.

**The owner's and admin's job:** turn it on knowingly, once. One page, `/settings/ai`: a status sentence and checklist at the top, then provider, test, limit, preview, consent, and the switch last, in the order the work is done. An admin who is not the owner gets as far as the preview and sees "Waiting for an owner to agree: {owner names}", with a link to copy (A31).

See `docs/design/principles.md`: rule 1, the sentence leads; rule 2, a missing reading is an em dash.

## UI structure

### Finding page (`features/findings/FindingScreen.tsx`)

Order changes from header, controls, signals, readings, services, incidents, feedback (`:107-116`) to:

```
 Header                 the title sentence (unchanged), then one code-written line:
                        Likely: the deploy of checkout v2.1, 4 minutes before this
                        (3 deploys in 7 days). See that minute
 Controls               (unchanged)

 What lines up with this                                                    serif h2, no box, like its siblings
 Read at 14:08 IST · 2 open findings name this likely cause
 1 14:02 IST  checkout v2.1 deployed. 3 deploys in 7 days   Signed deploy record   Service, at that minute
 2 14:03 IST  Calls to payments: failures 0.4% → 6%         Counted from telemetry Traces
 3 14:03 IST  New error: TimeoutError                       Read from telemetry    Errors
 · Show all 9 facts
 · When no deploy is in the window: Last deploy: v2.0, 2 h 10 min before this
 · No traces for checkout's calls to redis in this window
 Read the facts again

 Look next   Traces · Logs · Errors · Deploys · Infrastructure (whole project)     always here; below md, above the facts
 In plainer words                                                                  only with AI on
 Written by AI (gpt-x); its numbers and names were matched to the facts,
 its meaning was not checked
 checkout slowed 4 minutes after a deploy. Fact 1 · Fact 2
 Write in plainer words · Can ask again 2 more times before 18:30 IST

 Was this useful?  Yes · No (The likely cause is wrong · It did not help · The AI wording is poor)   not shown to viewers

 What moved             (unchanged)
 Readings               charts get `markers` from F28 (:535-544)
 Services, Linked alerts, Feedback   (unchanged)
```

**Layout.** A plain section, no card (`docs/design/design-system.md:19`). Only sentences are capped with `max-w-prose`: the cause line and AI wording. The header line's space is reserved during the skeleton, so the actions never move. From `md` up, facts are a full-width `ol` with columns for time, fact, basis and link; below `md` they stack, with the basis label wrapping under the sentence, and "Look next" comes above them, its links on separate wrapped lines at least 44 pixels high (`TopBar.tsx:42` holds that size). "Basis:" is visually hidden text before each label, and sub-blocks are `h3`. Names use the page's mono style, wrap with `overflow-wrap:anywhere`, and past about 80 characters (a guess) the rest sits behind a disclosure.

**Segments.** Summary, fact and card sentences reach the screen as segments, `{text}`, `{name}` and `{at: ISO}`, as wording does. A name can never read as Nexovue's own words, and a time is formatted on the client by `formatZonedTime` in the project's zone, with its name. Moving to a fact expands the list, moves focus and shows a highlight. After "Show all N facts", focus moves to the first newly shown fact. A citation is a link with `aria-label` "Fact 2: checkout calls payments" and a target of at least 24 pixels, or plain text reading "a fact from that earlier reading" when that fact is not in the latest list.

New files: `features/findings/ExplanationSection.tsx`, with `explanation.ts` (pure: state to words, tested like `finding-page.ts`). Reused: `SkeletonRows`, `InlineFailure`, `Button`, `ConfirmPanel`, `SelectField`, `formatZonedTime`, `useCan`, `TrendChart`'s existing `markers` prop (`TrendChart.tsx:97`), and `deployMarkers` (`shared/deploy-markers.ts:37`).

**States it owns.** Every state in the spec's *States* tables, plus: **loading**, a skeleton; **partial**, facts with `could_not_read` lines, never hidden; **empty**, "Not enough evidence to name a likely cause", which is a result and not an apology, with "Look next" and the setup step, and used only when every read completed: a failed read reads "Could not finish reading the facts, so no likely cause yet" and gets no setup step; **stale**, data held and a refetch failed: `isError` turns true on a failed refetch even with data held, so the section checks for data first, not as `FindingScreen.tsx:78-86` does, and shows "Read at {time}. Could not refresh; still trying" through the `role=status` element, in `text-ink-dim`. `InlineFailure` appears only when nothing is held; **dangerous**, none. **"Look next" sits outside the explanation query's branches,** built from `FindingDetail`, which has already loaded. 401, 403, 429, 5xx and offline stay central (`docs/design/principles.md`, rule 3). A run refused at the cap, and a read-again too soon, are therefore 409 problems with `next_run_at` and `next_read_at`, shown inline, because a 429 would become a toast (`app/query-client.ts:84-90`).

**Deliberately not shown:** a percentage; the word "AI" in the facts part; "root cause" or "caused" in a code-written sentence; a prompt to admin about turning AI on, since `/settings/ai` is the place (only a system-caused off shows one line, spec FR26); a streaming effect.

**Actions.**

| Label | Does | With AI off | Who | States |
|---|---|---|---|---|
| Read the facts again | A new gather. No tokens | Works | `REQUEST_EXPLANATION` | Pending; "Read at {time}: nothing new"; a new version; too soon: disabled, "Read at {time}. You can read again at {time}" |
| Write in plainer words | A run | Hidden | `REQUEST_EXPLANATION` | Pending, old words staying; "Can ask again 2 more times before {time}"; at the cap, disabled, with "Reached the limit of 3 in 24 hours, including automatic ones. Try after {time}" |
| Was this useful? | One rating. After it: "Thanks, recorded. Change" | Works; "The likely cause is wrong" offered only with a cause, and "The AI wording is poor" only when wording was shown | Working roles; hidden from viewers | |

**Poll.** The query refetches every 5 seconds for 15 minutes after the finding opens (guesses), whatever the wording state, then every 60 seconds while it stays open, using `refetchInterval` in `features/findings/api.ts`, and keeps going past each threshold. The API returns the display state and `runs_left`, so a stale read cannot strand the page. When the latest version changes, an always-present `role=status` element announces it, and a changed cause shows "Changed at {time}: was {old sentence}". This route reads Postgres only (A32).

**Words, recorded once.**

| Where | Text |
|---|---|
| Section heading | What lines up with this |
| Strength | "Likely: ...", "Possibly: ...", on page and card. A cause not `verified` adds "from telemetry only": "Likely, from telemetry only: ..." |
| No cause | Not enough evidence to name a likely cause. Only when every read completed |
| Reads failed, no cause | Could not finish reading the facts, so no likely cause yet |
| Cause held (spec FR50) | The facts that line up with this are below |
| Could not load | The explanation could not be loaded |
| First read | Reading the facts. After 5 minutes: Still reading the facts. Nexovue is busy with other findings |
| Job expired | The facts could not be read |
| Aged out | Raw traces aged out on {date} |
| Old words | Written from the reading at {time}, before the facts changed |
| Wording abstained | Nothing to add to the facts above |
| Deploy history short | 3 deploys in the 2 days nexovue has records for, too few to judge how usual this is |
| Last deploy | Last deploy: v2.1, 2 h 10 min before this |
| An unshowable type | An error type that could not be shown |
| Rating reasons | The likely cause is wrong; It did not help; The AI wording is poor |
| Bases | Signed deploy record; Version seen in telemetry; nexovue's own check; This install's intake record (self-hosted only); Counted from telemetry; Read from telemetry text. A signed line beside a cause adds: "The deploy is a signed record; the link to this finding is timing only" |
| Alert | "A likely place to look on {label}: {sentence, no strength prefix}, {base rate}. See the page for the current reading" |
| Wording label | Written by AI ({model}); its numbers and names were matched to the facts, its meaning was not checked |

**The sample.** `features/findings/sample-explanation.ts` *new*: fixed data for a likely cause, an abstention and absences, rendered by `ExplanationSection` at a new route, `/findings/sample` (in the router and `route-manifest.test.ts`), under a visible "Sample" label, with no API call. It is linked from a new element in `SummaryState` (`FindingsScreen.tsx:205-240`) and from `/settings/ai`. That same element holds the real dates of spec FR55.

### Finding card (`features/findings/FindingsScreen.tsx`)

One line under the sentence, code-written, for open findings with a cause, in its own short form: "Likely: deploy of checkout v2.1 (3 in 7 days)", "Likely, from telemetry only: calls to payments", or "Possibly: ...". It wraps on an open card rather than truncating, so a deploy shows no less doubt here than on the page. The comment at `:332` goes. Closed cards stay a single quiet line, and may truncate as `:338-345` does. Never model words.

### AI settings (`/settings/ai`, new `features/aisettings/`)

A rail entry under "This organization" in `shared/settings/settings-nav.ts:75-90`, with keywords (`ai`, `model`, `provider`, `ollama`, `claude`, `openai`, `bedrock`, `limit`, `tokens`). Sections, in order:

| Section | Holds | Who sees | States |
|---|---|---|---|
| Status | On or off in one sentence, and for owner and admin a checklist linking to each missing step. When the consenting owner has left, or the model changed, it says so. Member and viewer read one sentence: "AI wording is off here. An owner or admin can set it up.", with no checklist | Everyone | Loading: skeleton. Load failed: `InlineFailure`, showing no switch and no status sentence, so a failed read never reads as off (design principles, rules 2 and 3). Hosted: "Not offered on the hosted service yet". Account missing: "AI is not available: the database account could not be created". Container not running: "The AI container is not running on this server". *Beat lock exposed*, owner and admin only, shown when `has_function_privilege('nexovue_insight', 'pg_try_advisory_lock(bigint)', 'EXECUTE')` is true: "This database would not let setup remove a right from the AI account. If the AI container were broken into, it could stop scheduled work. See the operations guide" |
| Provider | Wire format as a native `<select>` listing only formats with a dated real call on record (AC77), base URL, model, region as typed, key (write-only), time per call. Where-data-goes sentence, changing for a private address, saying the key identifies the customer to the provider, and that its terms decide what it keeps. `SettingRow`, `TextField`, `SelectField`. **Dangerous state:** saving a changed address or format uses `ConfirmPanel`: "This erases the saved key and turns AI off for {org} now. An owner must agree again before it can return." A changed model says "AI turns off until this passes a test" before saving. "Remove this connection" uses the same panel | Owner, admin | Validation, unsaved changes, saving |
| Test | Button, then a result, with a plain reason. Says "Not yet tested by nexovue" until a model is on the supported list. After consent, "Try it on a recent finding" (a metered run) | Owner, admin | Waiting; passed; failed; no answer in 60 seconds (a guess): "The AI container did not answer. Ask whoever runs this server to check it" |
| Limit | Monthly tokens, required, suggesting 2 million (labelled a guess). Optional price, and a worst-case estimate when given. 20% note for critical | Owner, admin | Validation |
| Preview | A plain-language summary of what is sent, then instructions and exact body in a wrapping, copyable block, with the alias table beside it from `md` up and above it below that. Lists every field and fact kind the schema can send | Owner, admin | Empty: sample, labelled |
| Consent | What is agreed to and where it goes, including that a gateway's model name can change who receives data. Owner's button. **Dangerous state:** it names the address and what is sent in real numbers (fields in the preview). `ConfirmPanel` | Owner acts; admin reads, with owner names and `CopyButton` | Waiting for an owner |
| Switch | On or off, last, disabled until the checklist is complete | Owner, admin | |
| Projects | Per-project opt-out | Owner, admin | |
| Usage and runs | A bar with a sentence, "1.2 million of 2 million tokens used, resets 1 December", estimate labelled. Link to `/settings/explanations` for ratings per rule. `DataTable` with columns time, outcome, finding, tokens, then the rest, including the address; each run is a stacked row below `md`. "View what was sent", with its match statement | Owner, admin | Empty: "No runs yet" |
| What this does not cover | One line: the MCP server is reached with your own read tokens, and this switch does not affect it | Everyone | |

**Who sees what.** Owner: all. Admin: all, without acting on consent. Member and viewer: Status and the last line only. The rail entry shows for all, because the first sentence answers "is AI on here?". It holds no provider address for them (`GET /ai/connection` returns status only).

Audit and run list use `VirtualTable` if over its threshold. It holds no prompt or model text.

### Explanation settings (`/settings/explanations`, new)

Not an AI screen, and built in Step 2, so it stands if the stop-or-go says stop. For `CONFIGURE_DETECTION`: rating and rule counts, a per-rule "Stop naming causes from this rule" with `ConfirmPanel` stating that the rule's facts stay, and a copyable summary holding counts only, for a customer who chooses to send it. Others read nothing here. States: loading, load failed, empty ("No findings yet"), saving.

### Links, and the window in the URL

Today the window is global, per-browser state (`shared/selection.ts:73,112`). **The 19 files that read a window all do so through one hook**, `useLiveWindow` (`:342`; found by a search), so the reading side is small. The writing side is not: each screen below rewrites its whole search, and would drop the window on a filter change, so each must keep it.

- `?from=&to=&project=&finding=` on `/traces`, `/logs`, `/errors`, `/services/$name`, `/infrastructure` and F28's `/releases`, parsed by one helper, `shared/url-window.ts`, joined in each route's `validateSearch` (`app/router.tsx:275,294-334,340-348`). `/services/$name` has no `validateSearch` today, so it gains one. `TraceListScreen.tsx:75`, `ErrorsScreen.tsx:69`, `LogsScreen.tsx:94-99` and the infrastructure screen spread the parsed window into each `navigate`. `finding` is a validated UUID and grants nothing: the destination only uses it to link back.
- `useLiveWindow` prefers a valid URL window over the stored one (`useSearch({ strict: false })`; `shared` already imports the router, as `SettingsLayout.tsx` does). The stored selection is never written.
- The window control (`app/layout/WindowControl.tsx`) shows the URL window, with a line under it that wraps at 390 pixels and prints the zone, since the existing `title` holding exact times is no use on a phone (`WindowControl.tsx:41-42`): "Showing this finding's window, 13:47 to 14:20 IST. Back to the finding · Use my own window". With no `finding` value it reads "Showing 13:47 to 14:20 IST · Use my own window". Choosing another clears the URL's. Moving away through the navigation ends it, plainly, rather than carrying it along.
- `project` in the URL sets the selected project on arrival, as `ServiceLink` already does on click (`FindingScreen.tsx:170-172`), so a copied link shows the right data.
- A window is finding start minus 15 minutes to end plus 5 (guesses), or now.

Links are built only by `features/findings/evidence-links.ts`. "Look next" is built from `FindingDetail` (service, project, `started_at`, `closed_at`), and the API sends `raw_kept_until` there too, read from `project_retention` (`0005_plans_table.py:71-75`), so links exist while the explanation loads or fails. A link to raw data past retention renders the aged-out line instead. A fact's link goes to the data itself, by kind:

| Fact | Opens |
|---|---|
| Error | The Errors screen with that group selected at its time. The selection is local state today (`ErrorsScreen.tsx:71`), so it gains a `group` search value |
| Dependency | **A callee that reports for itself:** `/traces` with `service` the callee, since `service` means traces holding a span from it (`traces/router.py:44-46`). The list may include other callers, and the link's label says "Traces through {callee}". **A callee only the caller named:** `service` the caller, with `attr` the fact's `callee_attr` and `attr_value` the name without its parentheses (`000027`). **No `callee_attr`:** `service` the caller alone, labelled "Calls from {caller}, not narrowed to {callee}". Each adds `status=ERROR` or a `min_duration_ms` for a latency move (`traces/filters.ts:37-46`). A76 |
| `slower` signal | `/traces` for the service with `min_duration_ms` at the finding's worst reading |
| Deploy | `/releases` for that release |
| Infrastructure, in "Look next" and on a host fact | `/infrastructure` with the URL window, labelled "Infrastructure (whole project)", since host readings are not tied to a service |

The message text is read on the Errors screen, through its existing routes, when someone opens the link. The finding page never fetches or stores it (A47).

## Proposed design

### New pieces

| Thing | Where | Holds |
|---|---|---|
| Evidence contract | `packages/py-platform/src/nexovue_platform/evidence/` | Queue and task names, including the word job's, `EVIDENCE_CONCURRENCY`, `CAUSE_BAR_RECORDED` and the released-rule list beside it (spec A85: a rule not on it behaves as if the bar were unmet), `gather_evidence_request`, the item types, `ModelFacts`, `Alias`, `aliases.py`, `display_name`, `fix_brief`. No AI code |
| Evidence tasks | `apps/scheduler/src/nexovue_scheduler/evidence/` | `store.py` (telemetry reads), `reads.py` (Postgres reads), `rules.py` (pure), `sentences.py` (pure), `gather.py` (task), `explained.py` (notification) |
| Provider contract | `packages/py-platform/src/nexovue_platform/ai/` | `AIRequest` (`instructions`, `parts` as `Trusted` or `Untrusted`, `response_schema`, `max_output_tokens`, `purpose`, run ids), `AIResponse`, the three wire formats (pure build and parse), `explain_prompt.py` (constant instructions, `PROMPT_VERSION` and response schema), `validate.py`, `limits.py` (statements), `untrusted.py` (JSON encoding, caps, stripping control, bidirectional and invisible characters, per-call random tags). F3's own request has no `Untrusted` part, and F3 builds and tests these for F4 and F5 (foundation, `:513`) |
| AI tasks | `apps/scheduler/src/nexovue_scheduler/insight/` | `transport.py`, `word.py` (task), `check.py` (task), `store.py`, `sweep.py` |
| API | `apps/api/src/nexovue_api/ai/`, additions in `findings/` | Settings, consent, preview, usage; explanation read, refresh, rate |
| Eval bed | `tools/ai-eval/` | Built on `tools/fault-bed`, `tools/detection-seed`, `tools/otel-demo` |
`ai_available(deployment)` sits in `nexovue_platform/deployment.py`, which the scheduler's settings already import. **The fix brief:** `fix_brief(explanation)` returns one version's `ModelFacts`, its likely cause and evidence ids, with no restored name, for F5.

### Facts: when and how

`nexovue_platform.evidence.gather_evidence_request(finding_id, org_id, reason, countdown=0)` builds a `JobRequest`, like `compute_baselines_request`: queue `evidence`, ids only, `expires_seconds` 3600, `max_attempts` 8 (a held lock retries, and a dead worker's job runs again; attempts are counted at each claim, `jobs/queue.py:58`), soft 60 and hard 90 seconds (guesses; the default 30 and 60 do not fit several reads).

Detection adds them on its open connection through `queue.enqueue`:

| When | Where (`detection/project_job.py`) | Reason | Delay |
|---|---|---|---|
| Service finding opens | after `_open`, `:1113` | `opened`, then `settled` | 0, then 300 s (guess: deploy records can arrive late) |
| Project finding opens | after `_OPEN`, `:905-925` | same | same |
| Signal joins, or escalates | `_notify_change`, `:1379` | `changed` | 0 |
| Job closes a finding | beside `finding_closed`, `:945`, `:1139`, and in `_adopt`, after `notify.finding_adopted`, `:1372` | `closed` | 0 |
| A signed deploy record arrives | `deploys/service.py`, on the API's connection, per item of F28's batch, for an open finding on that service whose start is within 30 minutes after the deploy | `deploy` | 0 |
| A person asks | `POST .../explanation/read-again` | `person` | 0, `wake=True` |

A person closing a finding (false alarm, new normal) adds nothing: what was read stays. `__main__.py:78-85` adds `EVIDENCE_QUEUE` to the default queues, and `worker.py:72`'s `QUEUE_CAPS` caps it at `EVIDENCE_CONCURRENCY`, 2, imported from the platform's evidence contract, so `worker.py` imports no more of the evidence package than a name. It also gains `insight: 4`, which does nothing on a 4-slot worker, and is what F4's design builds on.

**The floor (A56).** A cap alone does not protect probes: `_claim_into_idle_slots` serves capped queues first and the uncapped lane last, with what is left (`worker.py:261-269`), and detection 2, evidence 2 and F28's `releases` 2 hold 6 of 8, leaving 2 for probes and alert sweeps, and the next queue added would take those. So `Worker` gains `uncapped_floor`: capped lanes together may hold at most `concurrency - uncapped_floor` slots, and each pass starts from the next capped lane, so none starves. `roles.py` passes `--uncapped-floor 4` to `scheduler-light`, a guess. Other roles default to 0: `scheduler-insight` has no uncapped queue. It holds if slots rise, and needs no feature to re-count. F19's SLO queues join this floor too: `slo` 2, `slo_counts` 1 and `slo_history` 1, on `scheduler-light`, with no role of their own (F19 D66, the founder's decision of 2026-11-02). Capped queues then ask for 10 seats against 4 and wait for each other; probes and alert sweeps keep 4 whatever they do. Whichever of F3's T1.7 and F19's T1.5 is built first builds the floor.

**`gather(finding_id, org_id, reason)`**, in `scheduler-light`, using the existing accounts:

1. Take a session advisory lock on the finding. If another gather holds it, `Retry` in 30 seconds, so 8 attempts wait out the longest holder (hard limit 90 seconds) and its successors. A job spent this way logs the finding and its reason. Reads of one finding never overlap, so a later version can never hold older facts (AC3).
2. Load the finding and its project by `org_id` and `id`. A deleted finding, or a deleted project, ends the job.
3. Run each read below on its own, under lowered bounds (`lowered_read_settings`, as `DETECTION_READ_SETTINGS` does, for both time and memory: 10 seconds and 256 MiB, guesses). A read that fails is remembered.
4. `rules.decide(...)` is pure and gives the `likely_cause`.
5. `sentences.py` writes each fact's sentence, the summary and the card's short form as segments. A name passes through `display_name` (strips control, bidirectional and invisible characters, caps length), the one helper both API and alerts use. A stopped rule (spec FR54) names no cause; its facts stay.
6. Build `model_input` and `aliases` (see *The model's input*). Pure data, with no AI code, from Step 1, so the preview has something real to show. The alias map and evidence ids are reused from the previous version, and only appended to, so `service_1` keeps its meaning across a finding's life.
7. Fingerprint: SHA-256 of identities and conclusions only (FR43), sorted. If it equals the latest version's, write no version, and if `reason=person`, report "nothing new". **Go to step 8's `explained` check either way.**
8. One transaction, or only the check when no version is written: insert version = latest + 1. Then **`explained.write`, for every read after the settled time, against the latest version**, then decide the wording (see *Wording states*), and enqueue `word` if due. "After the settled time" means `reason` is not `opened` and the finding is at least 300 seconds old, so it is judged from the job, since a stored row does not say which read made it. So a signed deploy already on the opening's version, with an unchanged settled read, still sends its one message.
9. **A failed read does not repeat the job.** The version is written with an `absence`, reason `could_not_read`, at once. A later read fills it, including one re-read in 60 seconds (reason `retry_read`). This keeps a failing read in a wide outage from tripling the work and pushing jobs past their expiry. An earlier version's item of that kind is carried, marked as read then, rather than being lost.

**Evidence is read once and kept.** Readings and raw data age out on other clocks, and the frozen copy holds names and numbers, never log bodies or error message text (A47).

**Bounds on one finding** (spec FR51; all guesses, named constants). `MAX_EDGES_READ` 50, the top by calls, inside the statement. `MAX_EVIDENCE_ITEMS` 100. `MAX_MODEL_FACTS` 30, in FR44's order. `MAX_ALIASES` 200. `MAX_INPUT_TOKENS` 8,000. Past any of them, code writes an `absence` with reason `truncated`. A run over `MAX_INPUT_TOKENS` ends `too_long` before it reserves anything, which is the foundation's per-run limit (section 4). Callee names can be forged, so without these one sender could grow a row, its aliases and a customer's bill.

### Reads

| Fact | Source | Statement | Note |
|---|---|---|---|
| Finding's signals | `findings.signals` | Postgres | Missing stays missing |
| Linked incidents | `finding_incidents`, `incidents` | Postgres, as `findings/queries.py:115-128` | `rule_name` is customer text: treat as a name |
| Other open findings | `findings`, `0041:279-292` | Postgres | Only those that began first, and only direct dependencies count for rule 1 |
| Checks | `check_state_changes` joined to `checks.name` | Postgres | Not `reason`, which holds a run's own words (`models.py:594`) |
| Deploys, base rate, coverage | F28's readers (F28 design, *Signatures, for F3*). `deploys_in_window` in `nexovue_scheduler/releases/facts.py`, signed events only, for this service, its direct dependencies and the base rate. `releases_seen_in_window`, `seen` versions, for rule 5. `coverage_of` in `nexovue_platform/releases.py` is pure, and its `reported_at` and `seen_at` come from F28's read of `releases`. **F28 builds the readers for the last release at any age (its FR32) and its 2-hour project list (its FR52): `release_before` and `releases_near(..., service, ...)` in `releases/facts.py`, in F28's T3.8 (F28 D56, design *Signatures, for F3*). T1.4 calls them and adds none of its own. `releases_near` reads Postgres only, so T1.4 marks the services this one calls from `service_edges_1m`** | Postgres | Reads through F28's functions. `metadata`, `url` and `source` are never handed over (F28 design, *The fact F3 receives*). F28's `basis` maps as in the spec, FR5. Evidence id from `deploy_id` or `release_id`. `release_comparison` also reads ClickHouse, and F3 does not call it |
| Edges | `service_edges_1m` | **New** ClickHouse statement in `evidence/store.py`, this service as caller, for the finding's window and an equal one before, `door = 'server'` where the project has server-door rows, top `MAX_EDGES_READ` by calls, with `server_peer_attr` for the link (A76) | Callee name can come from the caller's span (`000027`), so it counts as attacker-reachable. Latency is the caller's view (`servicemap/queries.py:9-14`), so the wording says "calls to", never "payments is slow" |
| Traffic | `service_stats_1m` | **New** statement, windows as above | Rollup counts are exact |
| Error groups not seen in the hour before | `spans`, `logs` | **The most expensive read here, narrowed (A62).** One statement per project and minute, written out in `evidence/store.py`: per group, using `GROUP_ID_SQL` and `MESSAGE_TEMPLATE_SQL` for grouping only, `minIf` of `received_at` in the window from start minus 15 minutes to now, and `countIf` in the 60 minutes before that (a guess), keeping groups with none earlier, `ORDER BY count DESC LIMIT 2000`. **It is not `alerts/telemetry.py`'s `ERROR_GROUPS_SQL`**, which orders by `first_received` ascending under `LIMIT 50000`, for a continuing sweep, and on this question a limit would cut the newest groups. The result (group id, type, services, counts, first seen) is kept for an hour in `evidence_error_group_reads`, so a wide outage reads it once per project and minute | Bounded by raw retention. `received_at` prunes only by day partition, and the sort key starts tenant, project, service (`000001_spans.up.sql:38-39`, `000002_logs.up.sql:31-32`), so T1.3 measures it at a stated volume on the 16 GB install, and **a timeout at that volume fails the task**: `could_not_read` is for outages, not a standing result. Held to 20 rows per service. A timeout is `could_not_read`, never "no errors". The message template is never stored. A type failing the identifier pattern is not stored (A47) |
| Intake (project finding) | `intake_refusals`, 15 minutes before the start | Postgres. Self-hosted only, checked through `deployment`. A named exemption in the scheduler tenancy test, since the table has no `org_id` (`models.py:1779-1786`) | A fact worded install-wide, never a cause. It rarely fires on a judged minute, since a refusal holds that minute (`settle.py:15-24`). The hosted service keeps F6's rule that this reaches no tenant (`holds.py:1-10`) |
| Last deploy, and the 2-hour project list | F28's readers, above, plus `service_edges_1m` to mark those this service calls | Postgres, and one ClickHouse read | Fact only, unless a rule names it |
| Hosts | The infrastructure metrics, `system.memory.utilization` and `system.filesystem.usage` over its limit where both exist (`infrastructure/conventions.py:42-46`) | **New** statement in `evidence/store.py`, where `host.name` exists | Project-wide fact only (A65). `conventions.py` says `system.filesystem.limit` is not emitted, so disk use needs the used and free states; T1.3 reads how the screen computes it. No `host.name`: a stated absence |

`evidence/store.py` joins `SCHEDULER_MODULES_ALLOWED_THE_RAW_CLIENT`, and `evidence` joins the `PROJECT_BOUND_PACKAGE` tuple F28's T1.11 creates.

### Basis

| Basis | Means | Examples | Can an anonymous outsider forge it? | Screen words |
|---|---|---|---|---|
| `verified` | Carries a secret's proof, or is nexovue's own measurement | Signed deploy records as F28 delivers them (F28's `reported`), check state changes, intake refusals | No, short of a stolen secret or a captured body within F28's time bound. Today's webhook signs the body only, with the delivery id in an unsigned header and a missing time stamped on arrival (`deploys/signatures.py:62-78`, `deploys/service.py:95-101`), so a captured body can be replayed. F28's D6 signs the id and requires a signed time, and F3 rests on that (AC93) | "Signed deploy record"; "nexovue's own check"; "This install's intake record" |
| `telemetry` | Counted from what was sent | Edges, rollups, finding-from-rollups | **By a secret ingest key, yes. By a browser key, yes until F28's F6 change and A63's edge door land.** | "Counted from telemetry" |
| `telemetry_text` | Rests on words in telemetry. Alone it never names a cause | An exception type or template; F28's `seen` version (A75, F28's D54) | Yes | "Read from telemetry text"; "Version seen in telemetry" |

**A forged span naming a real service, timed with an outage, can still produce a believable false cause on the page, and can open a finding, which sends its normal alert.** It cannot be stopped outright. What limits it: the door on the rollups (A63), a signed deploy outranks a telemetry cause, a telemetry cause sends no follow-up, the header and card say "from telemetry only", the basis and base rate are shown, and the bed reports how often each works (AC76). The rating does not limit it: it is stored for the customer and tunes nothing, and with no phone-home it cannot reach Nexovue.

### The rules (`evidence/rules.py`)

Pure functions over a `Facts` dataclass, so they run on every commit with no store. The table is in the spec (FR8 to FR11). Named constants, each commented as a guess: `DEPLOY_LOOKBACK` (30 minutes), `CHANCE_LIKELY_BELOW` (10%), `CHANCE_POSSIBLE_BELOW` (50%), `BASE_RATE_MIN_DAYS` (7), `ERROR_LEAD_IN` (15 minutes), `EDGE_MIN_CALLS` (30), `EDGE_FAILURE_RISE` (2× and at least 1 point), `EDGE_LATENCY_RISE` (1.5×), `TRAFFIC_RISE` (1.5×, ending before the slowdown began), `HOST_HIGH_UTILIZATION` (90%), `NEW_ERROR_LOOK_BACK` (60 minutes).

`likely_cause` is stored as `{evidence_id, rule, strength: "likely" | "possible", basis, supported_by: [ids], same_minute: bool}`. A cause must not be the moved signal itself (spec, BR6). A deploy's fact holds its chance beside its count, so the sentence and the test can show why. `rule` is a stable name, never a position in the list, held in one constant, because F5's reach table keys on it. An `error_group` item stores its `group_id` and the window it was counted over, because F5 finds stack frames from them. The alias-restoring function lives in `nexovue_platform/evidence/aliases.py`, so F5's API code can call it.

### The model's input (`nexovue_platform/evidence/model_facts.py`)

**Types instead of filters.** `ModelFacts` is a frozen dataclass whose fields may only be `float`, `int`, `None`, UTC minute offsets from the start, members of `Literal` lists, and `Alias` (a type with a validated pattern, `service_\d+`, `version_\d+`, `error_\d+`, `check_\d+`). `__post_init__` rejects any other value, so a new field cannot leak by default, and `signals[].key` (which holds `endpoint:<name>`, `detection.py:270-272`) has nowhere to go.

```json
{"schema": 1,
 "finding": {"subject": "service_1", "signal": "slower", "severity": "warning", "unit": "ms",
             "normal": 110, "worst": 412, "started_minutes_ago": 6},
 "facts": [
   {"id": "e1", "kind": "deploy", "basis": "verified", "subject": "service_1",
    "version": "version_1", "minutes_before_start": 4, "deploys_last_7_days": 3},
   {"id": "e2", "kind": "dependency", "basis": "telemetry", "caller": "service_1",
    "callee": "service_2", "callee_kind": "database",
    "failure_share_before": 0.4, "failure_share_during": 6.0}],
 "likely_cause": {"evidence_id": "e1", "strength": "likely"},
 "next_checks": [{"id": "c1", "kind": "open_deploys"}, {"id": "c2", "kind": "open_traces", "subject": "service_2"}]}
```

`callee_kind` is a closed list code derives from `server_peer_kind` (`database`, `cache`, `queue`, `other`). The research suggests this as the middle path to test: it carries no instructions, can still be forged, and a fact using it counts as `telemetry_text` for the rule that it alone cannot be a cause. Whether to keep it is decided by the bed. Facts hold no invented order: an `error_group` fact's `exception_type`, when it passes the identifier pattern, becomes an alias, `error_1`.

`aliases` is `{"service_1": "checkout", ...}`, stored apart, read by the API, and append-only within a finding. A fact's id comes from its identity: a deploy from `deploy_id`, an error group from its group id, a check from its check id, a dependency from its callee. Two deploys in a window are two facts.

### The wording decision and run

In the gather transaction, for a new version, a `word` job is added if `CAUSE_BAR_RECORDED` or its non-production override holds (spec FR50), `ai_available`, the connection is `enabled`, the project is not opted out or deleted, the finding is neither muted nor flapping, some non-absence fact exists, and the change is **material**: the first version, or the cause or a fact that may be one changed. Job: queue `insight`, kwargs `{"explanation_id", "org_id", "generation", "requested_by"}`, where `requested_by` is the person's id on a click and null on an automatic run, `expires_seconds` 3600, `max_attempts` 2, limits following the connection: soft = time per call + 20, hard = time per call + 40. Each attempt makes one call, and curl's timeout is the time per call.

### Wording states

One table, covering each way a row can sit in `pending`. **Each move to `pending` adds one to `wording_generation`, and the job carries that number.** The sweep and `word` act only on a matching generation, so a killed run, or a second attempt after a worker's death, cannot overwrite a newer click, and an older job stops without spending.

| Event | Previous version's state | New or current state | Job? |
|---|---|---|---|
| New material version, all conditions hold | Any | New: `pending` | Yes |
| New version, AI off, opted out or unavailable | Any | New: `off` | No |
| New version, muted or flapping | Any | New: `held` | No |
| New version, only absences | Any | New: `abstained` | No |
| New non-material version | `done`, `abstained` | New: same state, `wording_from_version` set, words carried | No |
| New non-material version | `pending` | New: `pending`. The older job sees a newer version and stops | **Yes** |
| New non-material version | `failed`, `over_limit` | New: same state and reason | No. Click, next material change, or next month |
| Click on "Write in plainer words" | `done`, `failed`, `held`, `abstained`, `over_limit`; `off` when AI is now on and the project is in; `pending` older than 10 minutes | Latest: `pending`, a new generation, old words staying visible until replaced | Yes, in one transaction with the update, with no re-read. Refused first at the route if at the cap, or if a recent `pending` is under way |
| Opt-out, or project deleted, while queued | `pending` | That project's latest: `off`, in the same transaction as the opt-out or the deletion | The job finds nothing to do |
| Mute while queued | `pending` | That service's latest: `held`, in the mute route's transaction (`findings/` and the service settings write) | The job finds nothing to do. **A gather in flight can still write `pending` just after the mute,** which costs at most one run under the per-finding cap. Accepted in writing, since `word` cannot read `findings` and closing it needs a lock detection would share |
| Job killed, or its worker died | `pending` | The sweep writes `failed`, reason `died`, if the generation still matches the run's | No |
| Job expired or spent with no run row | `pending` older than the job's 3,600-second life, by `wording_changed_at`, which each move to `pending` sets, no live run | The sweep writes `failed`, reason `expired`. `_SWEEP_SPENT` deletes such a job and changes nothing else (`jobs/queue.py:81-92`) | No |
| AI turned on after versions were written `off` | `off` | Stays `off`, words absent, facts whole, until a click or a later material version | Only as above |
| Task error | `pending` | `failed`, written before raising | No |

A carried sentence is restored and checked against the row at `wording_from_version`, never the latest row. `word` sets `wording_from_version` to its own row in the same `UPDATE` as the new words, so a click on a carried row is then checked against the row those words came from. Whenever the words' version differs from the one shown, including under a `pending` state after a material change, the screen says "Written from the reading at {time}, before the facts changed", and a citation of a fact not in the latest list is plain text. Evidence ids and aliases stay stable by construction.

While the connection is disabled or deleted, stored words are kept with their version and not served, and the state reads `off` (AC106, T5.7).

**`word(explanation_id, org_id, generation, requested_by)`**, in `scheduler-insight`:

1. Read `model_input`, `wording_state`, `wording_generation`, `project_id`, whether a newer version exists. If newer, state not `pending`, or a generation other than the job's, stop.
2. Read the connection, `ai_project_opt_outs` and the project's `deleted_at`. Off, opted out, deleted, consent no longer matching the fingerprint and schema, or an address failing FR23: write `off`, stop, spend nothing. Open the key (`SealedBox`, purpose `ai-connection`, owner `org_id:connection_id:<SHA-256 of base_url>`), so a key cannot open for another address.
3. One transaction, holding a row lock (`SELECT ... FOR UPDATE`) on the finding's first explanation row, since this account has no advisory locks (A58): on attempt 1 only, count `ai_runs` rows for this finding with `attempt = 1` in 24 hours. At 3: state `failed`, reason `run_cap`, spend nothing; any earlier words stay. Estimate input (bytes ÷ 3, cautious). Over `MAX_INPUT_TOKENS`: `too_long`, spend nothing. Reserve that plus `MAX_OUTPUT_TOKENS` 800 (guess). See *Cost*. Refused: `over_limit`. **Insert the `ai_runs` row, outcome `started`, with its `generation`, `requested_by`, `provider_host`, `prompt_version` and `request_sha256` of the exact body, and commit.** If that person was deleted while the job waited, the foreign key refuses the id, and the row is written with null. The hard limit kills the child (`jobs/registry.py:5-7`), and a killed call may still be billed, so the record comes first.
4. Build the request (pure), call through the transport.
5. Check `stop_reason` before parsing. Claude returns refusals as 200, and they may not fit the schema (research, section 5).
6. Validate. **Invalid output, a 5xx or a 429 raises `Retry` while an attempt is left** (a 429's `Retry-After` becomes the countdown, up to the job's life), so a new attempt has its own time limit and its own reservation and run row. A soft limit could not rescue a second call inside one job: it does not interrupt a call blocked in libcurl (`jobs/child.py:10-12`). On the last attempt the task writes `failed` rather than asking for a retry, because `worker.py:323` would only log it. A 429 waiting past the job's expiry fails.
7. One transaction: set `wording`, `wording_state`, `wording_from_version` to this row, where `wording_state = 'pending'` and the generation matches (the first result stands, since a job can run twice); settle the reservation; finish the run row. The model name kept is the configured one, or the reported one only if it fits `[A-Za-z0-9._:/-]{1,80}`.
8. Any other exception, including `SoftTimeLimit`, writes `failed` with a closed reason, then re-raises. Every parse and validate failure becomes a closed reason, and an exception raised here is built with no body, because `jobs/child.py:77` logs what it is given.

The instructions are short and constant. They say what the facts are and what shape to return. **They are not a safety control** (research, section 7).

### Provider interface

As FD3, with no field for temperature, tool choice or top-p:

```python
class AIProvider(Protocol):
    def complete(self, request: AIRequest) -> AIResponse: ...
    def check(self) -> AICheckResult: ...

    # embed(): F4 adds it. tools on AIRequest: F4 adds them.
```

Because the HTTP cannot live in the platform (row 3), a wire format is two pure functions, and the transport joins them:

```python
class WireFormat(Protocol):
    def build(
        self, request: AIRequest, *, model: str, base_url: str, key: str | None
    ) -> HttpCall: ...
    def parse(self, status: int, body: bytes) -> AIResponse: ...
```

| Format | Schema | Outcome mapping |
|---|---|---|
| `openai_chat` | `response_format`, json schema | `finish_reason`; 401 and 403 `bad_key`; 429 `limited`; 5xx and refused address `unavailable`; `length` `too_long`; `content_filter` `refused` |
| `anthropic_messages` | `output_config.format` | `stop_reason`: `refusal` `refused`, `max_tokens` `too_long` |
| `bedrock_converse` | `outputConfig.textFormat` | `stopReason`: `guardrail_intervened`, `content_filtered` `refused`; `malformed_model_output` invalid; `model_context_window_exceeded` `too_long` |

Provider schema support is used where it exists and **never relied on**: `validate.py` always runs. The schema stays small: one object, a list of claims, a list of picks, one boolean. Which Bedrock models honour `outputConfig`, and Ollama's token counts, are unread; a missing count is estimated, with a flag.

**`nexovue_scheduler/insight/transport.py`** calls `http_check.perform`, with `follow_redirects=False`, and refuses an `http` URL unless its resolved address is on `AI_PRIVATE_ENDPOINTS`, checked at connect time in the guard, since a hostname can resolve anywhere (`http_check.py:146` allows both schemes). `PreparedRequest` gains `user_agent` (today `nexovue-prober`, `http_check.py:37`), and it stays whole-second timeouts (`:66`). `perform` and `ConnectGuard` gain `private_endpoints: PrivateEndpoints = EMPTY` beside `private_allowed`. `may_connect_to` keeps its meaning; `open_socket` first asks `private_endpoints.allows(address, port)`, which applies every exclusion `may_connect_to` has (`addresses.py:99-104`: loopback, link-local, reserved, multicast, unspecified) and requires `is_private`, before looking at the list. Settings refuse an entry with no port, a range that is not wholly private, and a very wide range. This widens `addresses.py`'s promise that its private path is "development only" (`:69-104`), and that must be reviewed as a change to that promise. `perform`'s 1 MiB body cap stands.

**No code path asks "is this local?"**, except the screen's sentence about where data goes.

### Validation (`nexovue_platform/ai/validate.py`)

Pure, in this order, per spec FR37, with no database. Output is `ValidatedWording` or a reason. The API runs it again when it reads, against the `model_input` of `wording_from_version`, and shows `failed` if it does not pass (A48), because the AI account can write wording for any organization. It also maps `wording_reason`, `ai_runs.outcome`, `purpose` and `fields_sent` through closed lists when it reads them, since those too are written by that account and reach the screen.

- **Numbers.** Every number in a sentence must match a value in that claim's evidence, within rounding (1 decimal place, or `4×` from a ratio of two values). The cheapest check against an invented figure. Minute counts and "3 deploys" come from the facts too.
- **Words.** A word list is crude, and the bed reports how many good sentences it drops. **It tests form, not truth.** A sentence that says something false in allowed words passes, and the label says so.
- **Origin.** With a null `likely_cause`, or citing another subject, a claim may not use origin or blame words. A claim type alone proves nothing, and is there so the bed can count what models write.
- **Stored with aliases.** Claims keep `service_1`. The API restores them when it reads, from the aliases at `wording_from_version`, and returns `{segments, evidence_ids}` per claim, with segments `[{"text": "..."}, {"name": "checkout"}]`, which the screen renders as text. Restoring cannot create a link, because no element is ever built from a value, and AC19 tests this with a service named as a URL.
- **Next checks** are picked by id from the offered list, so links stay nexovue's, and they work even for a model that writes poorly.

## Data flow and interfaces

### API

| Route | Who | Does |
|---|---|---|
| `GET /findings/{id}/explanation` | `CurrentReader`; a token only in its project, with the same 404 as `get_finding` (`findings/service.py:159-161`) | Latest version, or `?version=n`: summary, likely cause, evidence, wording as segments, display state with its reason, `read_at`, `runs_left`, `next_run_at`, `next_read_at`, the previous cause's sentence if it changed, how many open findings share the cause, deploy-record days covered. **When `not reader.is_person`, `wording` is null and only its state is given** (BR10, A68). F3 adds `Reader.is_person` (`identity is not None`), because it is the first to need it; F22 builds on it, and F4's ticket then gets the same null as a token. While FR50 holds, cause sentences are left out |
| `POST /findings/{id}/explanation/read-again` | `REQUEST_EXPLANATION` | Enqueues a gather (`reason=person`), `wake=True`. Works with AI off. At most once a minute per finding (a guess): too soon is 409 with `next_read_at`. Ships in Step 1 |
| `POST /findings/{id}/explanation/refresh` | `REQUEST_EXPLANATION` | In one transaction: sets the latest version `pending`, a new generation, and enqueues `word`. No re-read. 409 when AI is off; 409 with `next_run_at` at the cap, read from `ai_runs`, or with a recent `pending` under way; the job's own count stays as the race guard. Audit row `ai.explanation_requested` |
| `POST /findings/{id}/explanation/feedback` | `MayOperateAlerts` | Upserts one rating per person per version; refuses `bad_wording` with no wording shown and `wrong_cause` with no cause; audit row `finding.explanation_rated`. Ships in Step 2 |
| `GET /ai/connection` | Any person | On or off and what is missing. For `MANAGE_AI` also the address, model, region and `has_key`. Never the key |
| `PUT /ai/connection` | `MANAGE_AI` | Saves. Refuses a bad scheme, user name, query or fragment, a model outside FR23's pattern, and a key holding a control character. A changed address or format erases the key and ends consent, and a changed key ends consent; each turns AI off. A changed model turns AI off and clears the price. All in one transaction, with audit rows, and then an email to owners (A67), whose failure does not undo the change |
| `DELETE /ai/connection` | `MANAGE_AI` | Erases the key, ends consent, turns off; audit row |
| `POST /ai/connection/check` | `MANAGE_AI` | Job on `insight`, `wake=True`; returns `request_id`. Constant body, very small output limit, metered as a run with purpose `check`, with no reservation, so it runs while the limit is still null (A51). Audit row `ai.connection_tested`, with the host |
| `GET /ai/connection/check/{request_id}` | `MANAGE_AI` | Reads `job_results`, bound by `org_id`; another organization's id is 404. The task itself wrote the pass onto the connection |
| `GET /ai/preview` | `MANAGE_AI` | Exact bytes, from the newest not-opted-out finding's `model_input` and `WireFormat.build`, with no key; the alias table; every field and kind the schema can send |
| `POST /ai/consent` | `CONSENT_AI_DISCLOSURE` | Carries the fingerprint the owner read, 409 on a mismatch. Records who, when, the fingerprint and schema version |
| `PUT /ai/switch` | `MANAGE_AI` | On or off; refuses naming what is missing |
| `PUT`, `DELETE /ai/project-opt-outs/{project_id}` | `MANAGE_AI` | Project proved to be the organization's. An opt-out sets that project's pending rows `off` in the same transaction |
| `GET /ai/usage`, `GET /ai/runs`, `GET /ai/runs/{id}/sent` | `MANAGE_AI` | Tokens, limit, reset; run rows, marking a run whose host matches no consent recorded at that time; what a run sent, rebuilt from `model_input`. If the rebuild's SHA-256 equals `request_sha256`, it says "Matches the hash of what was sent". Otherwise "Cannot be reproduced: nexovue changed since this run", and the facts without the instructions. Or a statement that the version has expired |
| `GET /explanations/rule-counts`, `PUT`, `DELETE /explanations/stopped-rules/{rule}` | `CONFIGURE_DETECTION` | Rating and rule counts for `/settings/explanations`, and stopping a rule, with an audit row. Ships in Step 2 |

`GET /findings/{id}` adds `raw_kept_until`. `GET /findings` adds `likely_cause_sentence`, the card's short form as segments, to each row, by a join on the latest version (at most 200 rows, covered by the unique index). All new write routes go in `POLICY_OF` (`test_authorization_coverage.py:161`). `/findings` is already on the telemetry tier (`ratelimit/policy.py:248`); `/ai` stays on the default tier, as it reads Postgres. `middleware/ratelimit.py` needs no change. The API imports `nexovue_platform.ai`'s pure parts only.

### Notification: `explained`

Written by the gather job, in its transaction, from facts:

- `NOTIFICATION_KINDS` and `NotificationKind` gain it (`alerts/model.py:47`), with a frozen copy in the migration (`test_alert_vocabulary.py`).
- `explained.write`: for a read after the settled time (gather, steps 7 and 8), against the latest version, with strength `likely` and basis `verified`, on an open finding, neither muted, flapping nor quiet, with `CAUSE_BAR_RECORDED` holding. Waiting means a late deploy record is already in, and the one message is better informed. Channels: those told through `FINDING_TOLD_KINDS`, with `channels.kind IN READ_BY_PEOPLE` (`channels.py:34`), `sends_explained` true (new column on `notification_channels`, edited in `ChannelsScreen.tsx`), **and holding no `explained` row for this finding at any sequence**, because the unique key includes `sequence` (`notify.py:133`, `models.py:1444`), so it alone would allow a second message. **The limit is enforced where the row is written** (research, traps): a kind outside `ENDING_KINDS` would otherwise send a PagerDuty `trigger` (`payloads.py:163`), and a webhook a new type.
- **The cap.** The outbox already holds a channel to 10 messages a minute and groups the rest (`outbox.py:78-81`, `:521-523`), which still allows far too many follow-ups over minutes. `explained.write` also counts this channel's `explained` rows in the last 10 minutes, under a transaction-level advisory lock per channel, taken in sorted order, since `evidence` runs 2 at once and a per-finding lock does not cover the count. The fifth is written with sequence 1, whose words add "More findings have a likely place to look; see the feed", and past it no row is written. Constants, guesses.
- **Data path.** The composer sees only `Subject.detail` (`outbox.py:698-705`). `_TAKEN` (`:272-290`) gains `notifications.sequence`, and a lateral join on the highest version of that finding whose cause has strength `likely` and basis `verified`, under the same org binding. `_subject` flattens its segments into `detail["cause_sentence"]`, through `display_name` and `one_line`, then channel escaping. A name failing the pattern `[A-Za-z0-9._-]{1,80}` reads "a service", for messages only. No such version at delivery: the row is dropped with `outcome_reason`, logged.
- `_compose_finding` gains a branch, ahead of its `else`, which would write `[warning] <title>` (`messages.py:310-312`), with the alert's wording from the copy table, and `explained` joins the tuple at `:313`, so it repeats no signals. It works through `compose_batch` (`:204`), since people's channels send what is due as one message, and that needs a change: today any kind but `resolved` counts as still open (`messages.py:210-218`), so two follow-ups would read "[warning] 2 alerts", kind `opened`, and recoveries with one follow-up would stop reading as recovered. `explained` leaves `still_open`, `worst` and the alert count, and alone in a batch has its own summary, "{n} findings have a likely place to look". Its input has no wording field. **F3 therefore changes a path every alert uses,** so AC78 compares the outbox's bytes for each existing kind and channel, and a bad row or a raising composer must drop only that row.
- Order: `_IN_ORDER` (`outbox.py:96-108`) already keeps a row behind an earlier one on a retry, and a burst or quiet-hours hold applies to `explained` as well.
- `slack()` sets `unfurl_links` and `unfurl_media` false for this kind. Slack's behaviour was not tested. Whether every alert kind should do so is F21's to weigh, since existing alerts carry the same names.
- It joins no `FINDING_TOLD_KINDS`, so closure is unchanged.

## Data and migration impact

**One Postgres migration per plan step that needs tables** (numbered after F28's), **and one ClickHouse migration** (A63). Nexovue is pre-deployment, so nothing is backfilled.

```
finding_explanations
  id uuid pk, org_id fk, project_id fk, finding_id fk CASCADE, version int, UNIQUE (finding_id, version)
  read_at, fingerprint text, reads_failed int default 0
  evidence jsonb            -- names, numbers. Never log bodies or error text. API reads; AI role cannot
  aliases jsonb             -- append-only per finding. API reads; AI role cannot
  likely_cause jsonb null, summary jsonb  -- segments
  model_input jsonb null    -- all the AI role can read of the facts
  wording jsonb null, wording_state text CHECK (off, pending, done, failed, over_limit, abstained, held)
  wording_generation int default 0  -- one more at each move to pending; set by gather and the API, never by the AI account
  wording_changed_at, wording_reason text null CHECK closed list  -- shown to admins, mapped again on read
  wording_from_version int null  -- whose numbers and aliases the words were checked against

finding_explanation_ratings
  explanation_id fk CASCADE, user_id fk SET NULL, org_id, helpful bool,
  reason text null CHECK closed list, created_at, UNIQUE (explanation_id, user_id)

ai_connections
  id, org_id UNIQUE fk, wire_format CHECK, base_url, model, region_label, sealed_key bytea null,
  key_id uuid null,  -- new on each saved key; re-sealing keeps it
  call_seconds int CHECK 30..300 default 100, monthly_token_limit bigint null, price_input, price_output null,
  enabled bool, off_reason text null CHECK (person, consent_ended),  -- why enabled is false; the quiet line (A53) and F5's BR9 read it (A80)
  checked_at, checked_ok bool, checked_fingerprint text
  consented_by fk users SET NULL, consented_at, consented_base_url,
  consented_fingerprint text, consented_schema int
  CHECK (NOT enabled OR (monthly_token_limit IS NOT NULL AND consented_at IS NOT NULL
         AND consented_base_url = base_url AND consented_fingerprint IS NOT NULL AND consented_schema IS NOT NULL))

ai_project_opt_outs  org_id, project_id fk CASCADE, created_by SET NULL, created_at, PK (org_id, project_id)
ai_usage_monthly     org_id, month date, used_tokens bigint, reserved_tokens bigint, PK (org_id, month)
ai_runs              id, org_id, project_id SET NULL, requested_by SET NULL, purpose CHECK, subject_id, explanation_id (no fk), generation int, wire_format, model,
                     region_label, provider_host, prompt_version, request_sha256, input_tokens, output_tokens, tokens_estimated, reserved_tokens,
                     started_at, finished_at, outcome CHECK, fields_sent jsonb, attempt
                     INDEX (org_id, started_at DESC), INDEX (subject_id, started_at)
evidence_error_group_reads  org_id, project_id, read_minute, groups jsonb, PK (org_id, project_id, read_minute)
                     -- one hour; retention.EXPIRED; holds no message text
explanation_stopped_rules   org_id, rule, created_by SET NULL, created_at, PK (org_id, rule)
notification_channels       adds sends_explained bool default true
```

Plus: the `notifications` `kind_is_known` check widened with `explained`, by the safe pattern in 0050 (`postgresql_not_valid`); `JOB_RESULT_KINDS` (`models.py:2034`) and the `job_results` check (`:2190`) gain `ai_check`, without which `check` fails its insert; the four job functions below. **One ClickHouse migration,** after F28's `000039`: `service_edges_1m` gains `door`, carried through `edgeSpan` (`traceassembly/stages.go:64`) from the envelope (A63), and `server_peer_attr`, the attribute `calleeOf` took a told-only name from (`serviceedges.go:113`), empty for a callee that reported for itself or was named by technology (A76).

**The two fingerprints.** The test's: SHA-256 of wire format, base URL, model, region and `key_id`. Consent's: the same without the model. Neither reads the key.

**Checklist from the foundation** (section 8):

| Adding | Change | Proved by |
|---|---|---|
| Tenant tables | `organizations/tables.py`: `ai_connections`, `ai_usage_monthly`, `ai_runs`, `evidence_error_group_reads`, `explanation_stopped_rules` in `DELETED_DIRECTLY` (`:32`), `ai_project_opt_outs`, `finding_explanations`, `finding_explanation_ratings` in `REMOVED_BY_CASCADE` (`:58`) | `test_deletion_live.py` |
| Write routes | Actions, `POLICY_OF`, and all five copies of the action list (F6's C14) | `test_authorization_coverage.py`, `test_policy.py` |
| Sealed column | `SEALED_COLUMNS` (`keys.py:93`), `Keys.ai` (`:60`) | New test: every `sealed_*` column in the catalog is listed, since today nothing fails |
| Notification kind | Frozen copy | `test_alert_vocabulary.py` |
| ClickHouse statements | Module constants binding tenant and project | `test_tenant_chokepoint_structure.py` |
| Scheduler Postgres statements | `org_id` bind | The test exists (`apps/api/tests/test_scheduler_postgres_tenancy_structure.py`), and F28's T1.13 extends it first. F3 confirms it walks `evidence/` and `insight/`, with named exemptions for cross-tenant sweeps and the install-wide intake read (A72) |
| AI modules | `REPOSITORY_AI_MODULES` (`test_detection_has_no_ai.py:37`), widened to walk `evidence` too | That test |
| Setup docs | `docs/product/setup/ai.md` new; `setup/alerts/webhooks.md` says `explained` is never sent to a webhook | Review |

**Retention.** Explanations are deleted with their finding, following `_PAST_DERIVED_RETENTION` (`detection/retention.py`), through the cascade. F22 changes that rule for cases. `ai_runs` and `ai_usage_monthly` join `retention.EXPIRED` at 13 months (a guess), and `evidence_error_group_reads` at an hour.

## Performance and scale

| Quantity | Value | Basis |
|---|---|---|
| Gather reads per finding | About 7, about 3 of them on ClickHouse, per read reason | From the design |
| Reads per finding opened | 2 (opening, 5 minutes later), plus changes and close | Design |
| Versions per finding | Fewer than reads, since moved counts make none (FR43). Guess: 1 to 4 | Unmeasured |
| A wide outage, 100 services opening at once | About 200 gather jobs, 2 at a time, at perhaps 5 seconds each: about 8 minutes to clear, and longer while detection, `releases` and F19's SLO queues hold the shared capped slots | **Arithmetic on a guessed read time. Not measured,** until AC97's test reports it. Probes and alert sweeps keep at least 4 of 8 slots however many capped queues are full (A56) |
| Same, with the error-group read failing | With a whole-job retry it would have been about 200 × 3 × 30 s ÷ 2 slots, about 2.5 hours, past the 3,600-second expiry. **A failed read now writes at once and does not repeat, and the statement runs once per project and minute** | Arithmetic on guesses |
| Wording, same outage | Up to 100 runs, 4 at a time, and warnings stop at 80% of the limit | Starting concurrency is a guess |
| Row size | Guess: 5 to 30 KB per version | Unmeasured. `jsonb` compresses |
| `GET /findings` | One lateral read per row, on `(finding_id, version)`, at most 200 rows | Index covers it; unmeasured |
| ClickHouse on the 16 GB install | The reader profile allows 1 GiB per query (`reader.xml`). F3 lowers it, as `detection/store.py:53-57` does | Unmeasured |

A wide outage is when an explanation matters most, and the opening of 100 findings all pointing at one dependency is the correlated case. The queue's order is `run_after`, so first reads go before `settled` ones, and a job past `expires_seconds` is dropped. So that this is never silent, beat counts findings older than 15 minutes with no explanation, and alerts on it. The page says "Still reading the facts" meanwhile, and a person can read again.

### AI cost

**The only measured prompt found is someone else's:** Coroot's test, about 9,800 input and 1,000 output tokens. Nexovue's facts-only prompt should be smaller; that is a guess. List prices read 2026-11-02 (research, section 5):

| Model | One run, *arithmetic* | 185 runs, *arithmetic* |
|---|---|---|
| Claude Haiku 5.5 | about $0.0015 | about $0.27 |
| Claude Sonnet 5.5 | about $0.03 | about $5.50 |
| Claude Opus 5.5 | about $0.06 | about $11 |

185 runs is what the suggested limit of 2 million tokens buys at that size. **These are not measurements of Nexovue and must never be advertised.** The bed's Cost run replaces them.

### Limits, in order, at the point of spending

```sql
-- atomic; a warning sees 80% of the limit, a critical finding all of it
UPDATE ai_usage_monthly
SET reserved_tokens = reserved_tokens + :reserve
WHERE org_id = :org_id AND month = :month
  AND used_tokens + reserved_tokens + :reserve <= :limit * :share
```

- A finished run moves its actual tokens to `used`, and returns the rest. **If actual passes the reservation,** the actual is still counted, so usage can pass the limit by that one overshoot, bounded by `MAX_OUTPUT_TOKENS` and the input estimate's error, and the next run is refused. The figure is reported, not hidden (AC53 allows only this).
- **A run that dies keeps its reservation** (the foundation's "return what it did not use" applies only to runs that finish). `insight.sweep`, on `insight`, every 5 minutes through `beat.py:412`, turns a `started` run older than its hard limit plus grace into `died`, and moves its reservation to `used`, flagged estimated. Overstating is the safe side.
- The month is the first of the month, UTC. The row is created by `INSERT ... ON CONFLICT DO NOTHING`, by the insight role.
- A local model has limits too, since tokens bound its CPU time.

### Memory

`scheduler-insight` starts at a 384 MB ceiling, 4 children, and a per-child limit of 48 MiB passed to `Slot` (`child_memory_limit`), since the shared limit of 64 MiB (`worker.py`) on 6 children would equal 384 MB before the parent, forkserver and shared pages are counted (arithmetic, not a measurement). All guesses. The research notes `scheduler-light`'s 256 MB OOM-killed (`nexovue.yaml:384`), because the forkserver imports every task module (`jobs/preload.py`). A role with its own short list should need much less, but this is unmeasured. T3.10 records memory for 4 and 6 children, and the `sizing.md` table changes when it is read. F4 owns the rise to 576 MB and 6 slots (its T2.3), and must use that record. A memory kill here marks runs `died`.

## Failure handling

| Failure | What happens | Retries | What it leaves |
|---|---|---|---|
| Crash between opening and commit | Neither finding nor job | Next minute's judgement | Nothing |
| ClickHouse down | Read fails; the version is written at once with `could_not_read`, and read again in 60 seconds, then by the settled read | Later reads | A version with `could_not_read`, filled later. Header reads "Could not finish reading the facts, so no likely cause yet", never "Not enough evidence" |
| Scheduler-light down | Jobs wait; expired ones dropped, and an alert fires | A click | Finding page says "Reading the facts", then "Still reading", then "The facts could not be read". "Look next" renders throughout |
| Gather runs twice, or two at once | Advisory lock, retried for longer than the holder's hard limit, then unique key | n/a | One version per distinct read, newest last. A job spent waiting logs its finding |
| Provider down, 5xx | `unavailable`. `Retry` 60 s, a new attempt | Once, by job | `failed` |
| Provider 429 | `limited`. `Retry` honouring `Retry-After` up to the job's life | Once | `failed` |
| Bad key | `bad_key`, no retry | No | `failed`; admin sees reason |
| Refusal, content filter | `refused`, no retry | No | `failed` |
| Too long | `too_long`, no retry | No | `failed` |
| Invalid output | `Retry`, a new attempt, so it has its own time limit | Once | `failed` |
| Model abstains | Stored | No | `abstained`, a good outcome |
| Hard limit, child killed | The job ends with no retry: `_record` deletes it through `queue.finish` (`jobs/worker.py:326-332`). Run row stays `started`; the sweep marks it `died`, moves its reservation, and sets the explanation `failed`, reason `died`, through `ai_runs.explanation_id`, where still `pending` and the generation matches | No | `failed`; reservation kept |
| Soft limit, or any uncaught error | The child reports `FAILED`, and the job is deleted too (`jobs/child.py:73-78`). `word` wrote `failed` first | No | `failed` |
| Worker process died | Only here does a lease lapse, so the job may run again if an attempt is left | Once | `failed` or `done` |
| AI container stopped | Jobs wait, then expire, and `_SWEEP_SPENT` deletes them with no state change. A dead process cannot write. `absent(up{job="scheduler-insight"})` alerts while a connection is enabled | n/a | `pending`; read as "not available" after 10 minutes, derived by the API from `wording_changed_at`. A click works at once; on return, the sweep writes `failed`, reason `expired`, for what is left |
| Key will not open after rotation | `bad_key` | No | Admin: save again; `keys.py` re-seal handles a normal rotation |
| Connection deleted or turned off mid-queue, or project opted out | The job reads it and stops, state `off` | No | Facts |
| Finding deleted mid-run | The update touches nothing | No | Run row stays |
| Project deleted mid-queue | The delete sets pending rows `off`; `word` re-checks `deleted_at` | No | Facts stay readable through the API's usual project check |
| Restore from backup | `start` runs `setup`, which migrates, then re-applies the role and grants | n/a | AC46 |
| AI account cannot be created | `setup` warns and finishes; `scheduler-insight` fails its own start; nothing else is affected | Operator | AC71 |
| `AI_SECRET_KEY` missing | Compose passes it with `:-`, so nothing else stops. The API reports AI unavailable, and only `scheduler-insight` fails to start | Operator | AC71 |

**Retry never repeats a side effect on a person.** `explained` is unique per channel, and `word` writes only over `pending`.

## Security, tenancy, and privacy

### Three rooms

| Room | Runs | Holds | Never holds |
|---|---|---|---|
| Facts | `scheduler-light`, queue `evidence` | Existing Postgres account, ClickHouse reader | Provider keys, `AI_SECRET_KEY` |
| AI | `scheduler-insight`, queue `insight` | `nexovue_insight`, `AI_SECRET_KEY`, a path to Postgres and the outside | ClickHouse, Kafka, `CHECK_SECRET_KEY`, `CONNECTION_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, email key, object store keys |
| Writing out | API; alert delivery | As today. The API also holds `AI_SECRET_KEY` to seal; `scheduler-deliveries` does not | Any model call |

**Rule of Two.** Wording reads aliases only (not untrusted text, though numbers and aliases can be influenced), reaches private data, and changes nothing or sends nothing out except the facts to the chosen provider. It holds because the output is checked data, stored and shown as text.

### The role (`scheduler-insight`)

- `ROLES` gains `scheduler-insight`: `worker --queues insight --concurrency 4 --tasks insight` (`apps/api/src/nexovue_api/roles.py`). `__main__.py` adds `--tasks`, passed to `Worker(task_modules=...)`, which `Slot` and `child.serve` already take (`worker.py:102,116`).
- `registry.py` gains `TASK_SETS = {"all": TASK_MODULES, "insight": (...insight modules...)}`. `set_forkserver_preload` (`worker.py:83`) runs at import, with one preload module. It moves into a function called before the first `Slot`, choosing `jobs/preload.py` or new `jobs/preload_insight.py`. The parent still imports `metrics` and so `detection.settle` at the top of `__main__.py`, and that is the parent's only cost, since a child is forked from the forkserver. **Proved by a test** that the forkserver's `sys.modules` holds no `clickhouse_connect`, `confluent_kafka` or `boto3`.
- `TASK_SETS["all"]` omits the insight modules, so a task runs only where its secrets are.
- Compose: its own anchor, `x-insight-env`, not `control-plane-env`:

```yaml
scheduler-insight:
  <<: [*service-defaults, *nexovue-image]
  command: ["scheduler-insight"]
  healthcheck: *scheduler-healthcheck
  depends_on:
    <<: *after-setup
  environment:
    <<: *insight-env   # ENV, NEXOVUE_DEPLOYMENT, LOG_*, SCHEDULER_ROLE: insight
    POSTGRES_DSN: postgresql+asyncpg://nexovue_insight:${POSTGRES_INSIGHT_PASSWORD}@postgres:5432/${POSTGRES_DB}
    AI_SECRET_KEY: ${AI_SECRET_KEY:-}   # never :?, which would stop the whole stack; the role checks it itself
    AI_SECRET_PREVIOUS_KEYS: ${AI_SECRET_PREVIOUS_KEYS:-}
    AI_PRIVATE_ENDPOINTS: ${AI_PRIVATE_ENDPOINTS:-}
  networks: [ai]
  deploy: { replicas: ${SCHEDULER_INSIGHT_REPLICAS:-1}, resources: { limits: { memory: 384M } } }
```

- The container runs by default, idle when AI is off (FD6). `SCHEDULER_INSIGHT_REPLICAS=0` is a documented way to run with no AI container at all, for a security review that wants a clean answer: `/settings/ai` then says so, the switch stays disabled, and AC48 proves nothing else moves (AC103). Hosted overlays keep `AI_PRIVATE_ENDPOINTS` empty. `setup` gains `POSTGRES_INSIGHT_PASSWORD` in its environment, and `api` gains `AI_SECRET_KEY`, as `CREDENTIAL_ENCRYPTION_KEY` (`nexovue.yaml:288`), not through `control-plane-env`. A Compose test fails if any other service names it. `restore_snapshot`'s list of services to stop (`deploy/nexovue:601`) gains `scheduler-insight`.
- **Queue path.** `queue.claim`, `retry_later`, `finish` and `results.store` take their statements from a small table chosen by role: the shared `_CLAIM`, `_RETRY`, `_FINISH` and `_STORE_RESULT`, or calls to the four functions under *The Postgres account*. Nothing else in the worker changes.
- **Settings.** `SCHEDULER_ROLE: Literal["full", "insight"] = "full"`. `kafka_brokers` gains an empty default, and a validator requires it when `full`. `_check_key_is_set_in_production` (`settings.py:173`) applies only when `full`. `ai_secret_key` is required when `insight`, in production. A typo in the value stops the process.

### Network (A4)

Compose's default network stays for all others. A new `ai` network, not `internal` (it needs the outside), holds `scheduler-insight` and `postgres`; `postgres` lists both `default` and `ai`, **in `compose.infra.yaml` and `compose.infra.deploy.yaml`, where it is defined** (`nexovue.yaml:59-62` includes them, and Compose refuses a service redefined by its including file), and in `compose.infra.dev.yaml` for the dev stack. `prometheus` lists both too, in the `monitoring` profile, since the role sits on `ai` alone (`prometheus.yml` finds targets by name). That puts Prometheus's tenant-free metrics within a broken AI process's reach, which is accepted. `test_an_overlay_adds_no_list_to_a_service_it_shares` (`test_compose_contract.py:218`) must keep passing: `networks` is a list, so an overlay must not touch it. The kafka3 overlay and quickstart must be checked. **Not run.** AC42 proves it from inside the container, on the merged install layout. CI and `just stack` start only `docker-compose.yaml` (`justfile:39-44`), so a test there would prove the wrong file. A new recipe, `just insight-fence`, starts `nexovue.yaml` under its own project, on the pattern of `dev-app` (`justfile:48`), and runs AC42, AC46, AC71, AC103 and AC48's stop arm, in its own CI job. These tests carry a marker, are left out of `just check` and `pytest -n 6`, and the job fails if any of them skips.

### The Postgres account

`POSTGRES_INSIGHT_PASSWORD` joins `ACCOUNT_PASSWORDS` (`deploy/nexovue:115`), so install writes it and `cmd_upgrade` adds it when missing (`:140`). `AI_SECRET_KEY` joins `write_secrets` (`:117`), and `add_missing_secrets` gains the keys list, so an upgraded install gets one.

`nexovue_api/setup.py`, after `migrate_postgres()` (`:74`), calls `provision_insight_account()`: creates the role if absent with `LOGIN` and `CONNECTION LIMIT 20` (A58), sets the password, `statement_timeout` and `idle_in_transaction_session_timeout` on it, then runs `INSIGHT_GRANTS`, a constant. `nexovue setup grants` runs that step alone, which means `main`'s argument check (`setup.py:69-73`) changes too. The `setup` service in `nexovue.yaml:121-125` gains `POSTGRES_INSIGHT_PASSWORD`, and `just migrate-pg` (`justfile:334-335`) a dev value, or the role would not exist in the dev stack and CI. **Restore needs no step of its own:** `nexovue restore` ends in `start` (`deploy/nexovue:619`), and `start` runs `setup` first, which also migrates a snapshot older than the image before granting. `restore-postgres.sh`, run by hand, prints that `nexovue setup` is the next step. The migration holds only tables and functions, which the dump keeps. In a dev stack the same step runs.

| Table or function | Grant | Note |
|---|---|---|
| `jobs`, `job_results` | **None.** | No row-level policy either (A57). A broken process cannot read, change or `LOCK` the alert queue |
| `insight_claim_jobs(worker, limit, grace)`, `insight_retry_job(id, worker, countdown, error)`, `insight_finish_job(id, worker)` | `EXECUTE` | `SECURITY DEFINER`, `SET search_path`, each hard-coding `queue = 'insight'`, the same logic as `_CLAIM`, `_RETRY`, `_FINISH` (`jobs/queue.py:42-77`). A parity test holds them in step (AC44). F4 adds `assistant` to their list, and F5 adds `insight-fix` |
| `insight_store_check_result(request_id, org_id, payload, ttl)` | `EXECUTE` | As `_STORE_RESULT` (`jobs/results.py:12-21`), hard-coding `kind = 'ai_check'`. `ON CONFLICT DO NOTHING` stays |
| `finding_explanations` | `SELECT (id, org_id, project_id, finding_id, version, model_input, wording_state, wording_generation, wording_changed_at)`, the last for the sweep's age check; `UPDATE (wording, wording_state, wording_reason, wording_changed_at, wording_from_version)` | Bound by `org_id` in statements. **Reach across every organization, and the reason a hosted service needs per-organization policies first (spec, FR31)** |
| `ai_connections` | `SELECT` of all but `consented_by`, `consented_at`, prices; `UPDATE (checked_at, checked_ok, checked_fingerprint)` | The check task's statement applies only if the fingerprint still matches |
| `ai_project_opt_outs` | `SELECT` | |
| `projects` | `SELECT (id, deleted_at)` | So `word` can re-check before it spends |
| `ai_runs` | `SELECT`, `INSERT`; `UPDATE (input_tokens, output_tokens, tokens_estimated, outcome, finished_at)` | Column-level, so a broken process cannot rewrite which address a run went to. It could still `INSERT` a false row, which is why the API checks hosts against audit |
| `ai_usage_monthly` | `SELECT`, `INSERT`; `UPDATE (used_tokens, reserved_tokens)` | |

**No table-level `UPDATE` or `DELETE` anywhere, on purpose.** `LOCK TABLE` in any mode, including `ACCESS EXCLUSIVE`, is allowed to anyone holding `UPDATE`, `DELETE`, `TRUNCATE` or `MAINTAIN` on a table, and row-level security does not limit it ([PostgreSQL `LOCK`](https://www.postgresql.org/docs/current/sql-lock.html), read 2026-11-02). That page speaks of privileges "on the table", and does not say whether a column-level grant counts. From memory it does not, and T3.1 must check it, on `finding_explanations` above all, since `alerts/outbox.py`'s join reads it.

**Beyond tables.** Postgres gives every account some rights no table grant limits, and the code relies on them being uncontested:

| Reach | Why it matters | What `INSIGHT_GRANTS` does |
|---|---|---|
| Advisory locks | Beat leadership is one fixed-key lock (`leader.py:26,55`). Taking it leaves every beat a follower, so nothing is scheduled. Detection's locks use derivable keys (`detection/projects.py:86-89`, `holds.py:53`) | `REVOKE EXECUTE` on the `pg_advisory*` and `pg_try_advisory*` functions `FROM PUBLIC`, `GRANT` to the install account. `word` uses a row lock instead |
| `pg_notify` | It takes a lock every committing transaction in the cluster waits on (`nexovue_platform/jobs.py`, docstring) | The same |
| Connections | One shared Postgres | `CONNECTION LIMIT 20` (A58) |
| `TEMP` | Disk on the shared server | `REVOKE TEMP ON DATABASE ... FROM PUBLIC`, `GRANT` to the install account |

**What remains.** Revoking from `PUBLIC` needs rights a managed Postgres account may not have. There, `setup` warns, `/settings/ai` states it to owners and admins (*Beat lock exposed*), `docs/operations/ai.md` says what the operator can do, and a broken-into AI process could still take the beat lock. Heavy statements within its own tables are bounded only by the timeout. **Nothing here has been run;** T3.1 is the first task of plan Step 3, and a failure returns to the founder.

**What this drops.** Row-level security on `jobs` and `job_results` was the earlier design (A33). It would have put an AI-only rule on the alert queue with AI off, made `setup` refuse an account that neither owned `jobs` nor was a superuser, and still needed a function for `DELETE`. `metrics.read_queues` runs only on beat's leader (`metrics.py:4-6`), so this account never reads `notifications`.

### Keys

`AI_SECRET_KEY` and `AI_SECRET_PREVIOUS_KEYS`: read by the API, to seal and re-seal, and by this role, to open. `SealedBox` with purpose `ai-connection`, owner `f"{org_id}:{connection_id}:{sha256(base_url)}"`. The API's `keys.py` runs in its container, which must hold it, per the pattern. `docs/operations/key-rotation.md` gains the column. One `AI_SECRET_KEY` opens every organization's key, which single-tenant self-hosting accepts and a hosted service must not take unexamined.

### Attacks, and what stops each

| Attack | Stopped by | Test |
|---|---|---|
| Instructions in a log, exception or service name | None of it is sent (BR8, types, column grants) | Canary (AC49), type (AC50), grant (AC45) |
| Forged spans timed with an outage | Cannot be stopped. The door on rollups narrows who can (A63). Signed causes rank first, the basis and base rate show, "from telemetry only" marks it, and a telemetry cause pushes no follow-up. A finding it opens still sends the normal alert | AC6, AC25, AC67, AC76 |
| A link or image from the model | Plain text, validator refuses addresses, links built by nexovue | AC19, AC51 |
| A service name holding a URL, a sentence, or bidirectional characters | Segments, shown as a separate name; `display_name` | AC19 |
| A planted link unfurled by a channel | `explained` rests on a signed record, names failing the pattern read "a service", unfurling off. Slack's behaviour not tested | AC27 |
| Flooding findings to burn the budget | Flap guard, per-finding cap, monthly limit, critical share. Facts survive | AC53, AC55, AC57 |
| Provider address at an inner service | Connect-time guard, redirects off, operator-only private list, own network | AC37, AC38, AC42 |
| **Admin sends the saved key to an address they control** | Changing the address or format erases the key in that transaction; the seal is bound to the address; `https` required | AC33, AC34 |
| Key or address swapped between an owner's reading and click | Consent carries the fingerprint the owner read | AC33 |
| A provider's huge or hostile answer | 1 MiB cap, schema, validator, plain text | AC51 |
| Admin account takeover | Owner-only consent over fingerprint and schema, audit rows naming the old and new host, an email to owners, the key erased on an address change. **It does not stop an owner takeover.** A model change on a gateway can move data without the owner; the consent text says so | AC32, AC33, AC72, AC102 |
| Breaking into the AI process | Narrow keys, own network, column grants, no rights on the queue. It can still read **every organization's** connection keys, spend tokens, and write words for any finding, and open raw sockets. Those words are checked again on read, so they cannot show as model words unless they fit the form | AC41 to AC45, AC65 |
| That process stopping alerts through Postgres | No table `UPDATE` or `DELETE` so no `LOCK`; no rights on `jobs`; connection limit; advisory locks and `pg_notify` revoked. **Left over where `setup` cannot revoke:** the beat lock. Stated on screen | AC43 |
| A captured signed deploy body replayed | F28's D6: the id is signed inside each item, and a signed time is required within bounds. Today's webhook does not do this (*Basis*), and F3 needs F28 first | AC93 |
| Forged callees flooding one finding | Bounds on edges, items, facts, aliases and input tokens | AC94 |
| A hostile provider writing into the label | Configured model name, or a reported one fitting a pattern. Closed lists re-mapped on read, and `CHECK`s | AC51, AC65, AC96 |
| Cross-tenant leak | Each job and statement binds `org_id`; a run reads one explanation | New structural test; live test |
| Stored model words reused | A model's stored words gain no trust: they are text when read back (foundation), and are not fed to a later prompt | AC59; schema has no input for them |
| Provider or model echoing attacker text into errors | Closed reasons, bodyless exceptions | AC59 |
| A second store of personal data | No error message text in the record, and an exception type stored only if it fits a code-identifier pattern, since it is sender-controlled free text | AC95; spec A47 |
| Model words leaving through an API token | The route returns no words to a token | AC23 |
| An admin altering the request after consent through the model field | Model pattern, percent-encoded in a path. Bedrock's exact URL was not re-read, so that path is an inference | AC34 |

### Privacy
**A class of data to state plainly.** Before this review the record would have held error templates, which keep people's names ("User Priya Sharma not found"), since only UUIDs, emails, hex and numbers are replaced (`readings.py:70-79`), for the derived retention, which ignores a project's shorter raw retention (`0005_plans_table.py:74-75`). So it holds checked exception types, group ids and counts. An exception type is free text from the sender, copied as received (`traceassembly/flatten.go:199`, `logwriting/flatten.go:190`), and a careless SDK or a browser span can put "User Priya Sharma not found" in it. No migration stores this column in Postgres today, so F3 would be the first long-lived copy. So it is stored only if it fits `[A-Za-z_$][\w.$:-]{0,120}`, and otherwise as the group id and "An error type that could not be shown". A kept type is treated as a name.

- **What goes to the provider** is the preview: numbers, times, closed lists, aliases. It does not include names, messages or attributes. This must be stated plainly, and must not be stated more broadly: "your data never leaves your server" is false on a customer's own AI account (foundation).
- `ai_runs` holds no content (FD9). Prompts are not stored. A provider may keep what it receives; that is between the customer and them, and the consent says so.
- Ratings write an audit row, which the personal-data export already covers. Removing a member keeps the user row and scrubs it (`members/service.py:188-194`), so a foreign key's `SET NULL` does not fire on that path: a rating and `consented_by` keep pointing at the scrubbed person. Consent stands when an owner leaves, the audit row names the id, and the screen says the person has left and offers a fresh consent.
- Service names in `evidence` are as sensitive as those in `findings.title`, and follow the same retention.
- Whether any of this meets DPDP, RBI or SEBI rules is not addressed. No lawyer has read it.

### Audit

Names, each with changed fields and never values, with one exception: `ai.connection_changed`, `ai.connection_deleted`, `ai.disclosure_agreed` and `ai.connection_tested` record the old and new scheme and host, with the path dropped since it may hold a secret, so that after a takeover a customer can learn which address received their data. Also `ai.key_replaced`, `ai.turned_on`, `ai.turned_off` (actor or system), `ai.limit_changed`, `ai.project_opted_out` and its reverse, `ai.consent_ended`, `ai.explanation_requested`, `finding.explanation_rated`, `explanation.rule_stopped` and its reverse. `ai_runs.requested_by` names the person on a click, and is null on an automatic run. `ai_runs.provider_host` says where each run went.
### Injection suite in the gate


| Layer | Runs | Needs a model? | What it proves |
|---|---|---|---|
| Canary, type, grant | `just check`, every commit | No | Attacker text cannot reach the outgoing bytes |
| Adversarial output at a fake provider | `just check` | No | The validator holds |
| Forged facts, `tools/ai-eval --run forged` | Before release | No | How often forged spans through the real intake open a finding, and how often each rule names a false cause. The real leftover risk, and it happens in code |
| All 42 public payloads, through the real pipeline | Before release | No | None reaches the outgoing bytes |
| Real-model run, `tools/ai-eval --run injection` | Before a provider is called supported, and on any change to prompt or validator | Yes | No write and no way out, across repeated trials on inputs an attacker can move. A clean result reflects absent input and says nothing about the model's resistance. Never "injection-proof" |

## Observability and operations

- **Metrics** (no tenant label, `metrics.py:10`), **read from Postgres on beat's leader in `metrics.read_queues`, not counted in tasks,** because a task runs in a child whose metrics no scrape sees (`metrics.py:1-12`): run outcomes and tokens from `ai_runs`, connections enabled from `ai_connections`, failed reads from `finding_explanations.reads_failed`, findings waiting for an explanation, ties and `truncated` absences. Queue depths already appear per queue through `_JOBS_READY`. Each worker process keeps its own loop and `JOBS_ENDED` metrics, so a `scheduler-insight` scrape job joins `prometheus.yml`.
- **Alert rules** in `deploy/monitoring/rules`, with cases in `deploy/monitoring/tests/nexovue_test.yml`: `evidence` queue oldest over 10 minutes; findings over 15 minutes old with no explanation; `insight` queue oldest over 30 minutes and `absent(up{job="scheduler-insight"})`, both while any connection is enabled, read from the new gauge.
- **Logs:** ids, outcomes, tokens. Never a body. Exceptions in `word` and `check` carry no body, and `http_check` reasons are passed through `secret` removal, as `alerts/senders/http.py` does for a receiver echoing a secret.
- **Sizing:** a row for `scheduler-insight` in `docs/self-hosting/sizing.md`. A local model is not included in the 16 GB, as the page must say.
- **Dead code removed** (F3 owns, foundation): the `insight` service at `docker-compose.yaml:589-627` and `LLM_BASE_URL`; the `ollama` profile may stay. A dev `scheduler-insight` replaces it.
- **Runbook:** `docs/operations/ai.md`: the container is stopped; rotating the key; a run stuck; allowing a private address; what is left where `setup` could not revoke the advisory functions.
- **Customer doc:** `docs/product/setup/ai.md`: Ollama binds `127.0.0.1:11434` by default, which a container cannot reach ([Ollama FAQ](https://docs.ollama.com/faq), read 2026-11-02). What to bind instead, a tested example of the address to list, the server setting and the restart it needs. **A firewall step allowing only the Docker network to reach that port:** the FAQ read says nothing about a password, so assume anything on that network can call it and use its CPU. No model is recommended yet, and the page says when one has passed. Bedrock's status. What is sent; that a key identifies you to a provider; that its terms decide what it keeps. What the switch does not cover. **A customer step: host firewall rules for this container,** including one blocking the metadata address and the private LAN, and that external stores named by address are outside the Compose fence. **How to run with no AI container** (`SCHEDULER_INSIGHT_REPLICAS=0`). On managed Postgres: `CREATEROLE`, or creating the role by hand, and what is left without revoke rights.
- **Setup documents:** threshold and check pages get "Look next" and no explanation (A66); frequent deployers get a possible lead with its base rate and no follow-up (spec FR9).

## Alternatives and tradeoffs

| Rejected | Why |
|---|---|
| Model finds the cause, agent style | Needs tools FD2 removes, errors multiply per step, and more data can mislead. Agents may do better on novel incidents, and we accept honest misses instead |
| Statistical causal inference | Needs far more minutes than a just-opened finding has |
| Facts job after commit | Loses the job on a crash, has no ids at that point |
| AI code in the API, or in `scheduler-light` | A model beside every secret (FD1) |
| A third image | Plain HTTP has no heavy dependency; ADR-019 reserves that case |
| A provider SDK or `litellm` | Loses the connect-time guard, and is on the deny-list already |
| One OpenAI-style format | Rests on a layer Anthropic says is not production-ready, and misses Bedrock's Converse (FD3) |
| Building the prompt from the evidence column, with an allow-list | Works, but leaves names within a broken-into process's reach (A19) |
| Live links to telemetry instead of a frozen copy | Evidence ages out on different clocks (FD22) |
| Stored, restored sentences | A stored restored sentence must be re-validated and would hold names in the model's output column. Restoring at read needs one lookup |
| Row-level security on `jobs` | Permits `LOCK TABLE` through the rights it needs, and puts an AI-only rule on the alert queue (A57) |
| A `wording_held` flag copied onto the row | The mute route setting `held` does the same with one less column; the small race is accepted in writing |
| Filling `error_groups_seen` for every project | A per-minute read on every project for a fact that can only support a cause (A62) |
| Sending `explained` only when F28's line did not name the deploy | That line is absent from grouped messages and states order only (A55) |
| The finding page fetching an error's template | The link reaches it; A47 stands |
| Setting the window on click | Moves a person's window on every other screen, and a copied link loses it (A12) |
| A job per finding-page view for a muted finding, as foundation `:247` allows | A GET must not start spending, and tokens read that route (row 31) |
| `checks.service_name` now | Widens F18; a check's failure is mostly the same symptom (A17) |
| Free-text rating reason | A new store of pasted text for little (A21) |
| A second model call inside one job | A soft limit cannot interrupt a blocked call, so limits would double for every call (A35) |
| Grants applied by a bash script | `setup` already runs after a restore, and `INSIGHT_GRANTS` is Python (A34) |
| A separate container for evidence | Costs memory for a case not yet measured (A38) |
| A rule override per project or install | No evidence on what values are needed (A50) |
| Error templates kept until raw retention passes | More code to keep text with no use in the rules (A47) |

**Tradeoffs accepted.** Aliases and no free text may make wording blander; FD18 chose safety, and the bed measures it. Fixed rules miss novel causes that Coroot's machine-learning analysis may catch, and those read as "not enough evidence". Infrastructure causes read as honest misses, with a host fact beside them. The AI's value may turn out small: the plan's stop-or-go after Step 2 asks this before the costly work, by a rule written first (A61). Four functions repeat four statements, held in step by a test. A floor leaves slots idle while capped work waits. A mute can lose by one run to a gather in flight.

## Specification impact

The rows on Phase 2 features and the foundation were applied on 2026-11-02 (`status.md`, *Cross-feature reconciliation*). The rest apply only when this is approved:

| Document | Change |
|---|---|
| `phase-2-foundation.md` | The table under *Where this differs from the foundation*, including a shared rule in section 1: one floor for capped queues on `scheduler-light` (A56). Also `:599` and `:665`: Coroot and HolmesGPT publish results on their own test sets, and vendors claim accuracy without a published method, so "no vendor publishes" and "new ground" must go. `:594`: Grafana Assistant reaches self-managed installs through a plugin and a Cloud account. `:664`: the bed's size (spec, *Founder decisions*). `:662`: the recommended selling line, if approved. `:495`: the scheduler test's owner is F28. `:613` of its *Must not be claimed*: add "no paid edition" and "a forged span cannot alert you" |
| `feature-catalog.md` | F3 and F2 entries, **on approval of the specification,** not in the last step (plan T0.1): "F3 Finding explanation", with AI wording optional (A6, A16) |
| `vision.md:135,140`, `phase-2-plan.md:14` | On approval, with the catalog: "AI insight" correlating a deploy with "DB connection errors" promises what F3 has decided not to do (no error text, no host causes, AI finding no causes). Use an example F3 can really produce, with a base rate and no error text. The selling reason follows the approved line |
| `deployment-release-tracking/design.md`, `plan.md` | Its *Release before this* section is folded into F3's when F3's Step 1 lands (A45), keeping FR32, FR33 and FR52's facts. It must keep `basis`, stable ids and `hostile_release_names.py`. Its AC40 is then proved through F3's section. **Its design must correct "cap of 2 of 8 slots" (*Filling the record*, *From telemetry*, step 2) for the floor (A56), add edges to its required F6 change (A63).** Its term stays "release" on its own screens; the finding page says "deploy", and that is settled here |
| `ai-chatbot/design.md`, `plan.md` | The API joins `ai` for F4, and AC42 must still deny Kafka and ClickHouse. F3's `insight: 4` exists, as assumed. **Its *A plain limit, decided*, its differences row 11 and T2.3 assume a row-level policy: on `jobs` it adds `assistant` to the job functions instead,** and its memory figure follows T3.10's record |
| `ai-solution/design.md`, `plan.md` | Its row-level policy on its own tables is its own first use, since F3 no longer proves row-level security on `jobs` |
| `ai-solution/design.md` | `fix_brief()` is defined here; it holds aliases and no names |
| `docs/research/phase-2-competitors-2026-11.md` | Coroot's conflict is settled by its own pages, read 2026-11-02 (research, section 4) |
| `anomaly-detection/spec.md`, `design.md`, `status.md` | FR16's F2 pointer; a risk that a browser key can open a finding, and the door on rollups (A63). **Approved by the founder 2026-11-02 to be taken before Stage 1 reaches a customer (foundation item 7);** this run may not edit that folder, and F28 owns it (item 22) |
| `alerts/spec.md` | `explained` joins the kinds; `compose_batch` leaves it out of open alerts |
| `team-and-api-access/spec.md` | FR1 table gains three actions |
| `docs/decisions/` | The ADRs below, once accepted |
| `docs/product/setup/alerts/webhooks.md` | Says `explained` is not sent |

## Proposed ADRs

These are written here and not as files. Numbers are assigned when accepted, since other Phase 2 documents may propose some too.

### Proposed ADR: AI runs in its own role, with its own account and network

**Status:** Proposed. Closes the open point in ADR-019.

**Context.** ADR-019 ships two images and leaves `insight` open, a third "if its dependencies are heavy". `scheduler-light` holds every sensitive telemetry account (`nexovue.yaml:355-366`), the shared `control-plane-env` carries the keys to every check and channel secret, all Python roles share one Postgres account, and Kafka needs no password.

**Decision.** AI code runs in a new role, `scheduler-insight`, from the `nexovue` image: its own settings anchor, its own Postgres account limited by column grants, with no rights on the job queue and four narrow functions in their place, its own network, its own task modules. Facts are gathered elsewhere, in a process that never imports AI code. The account's grants live in `setup`, which runs after a restore.

**Alternatives.** In the API, in `scheduler-light`, or a third image (FD1). Holding no key but sharing a network (fails: `PLAINTEXT`). Row-level security on `jobs` (permits `LOCK TABLE`, and puts an AI rule on the alert queue).

**Consequences.** First named network and first second Postgres account in this repository. Four statements exist twice, held in step by a test. One more container, memory unmeasured. An operator of a managed Postgres has one more step, and may be left with the beat-lock risk.

**Evidence needed before acceptance.** The plan's Step 3: the four functions matching `_CLAIM`, `_RETRY`, `_FINISH` and `_STORE_RESULT`; `LOCK TABLE` refused; advisory locks and `pg_notify` refused; a restore onto a server with no such role; the container's reach from inside it; memory.

### Proposed ADR: An explanation is a frozen evidence record, with code choosing the cause

**Status:** Proposed. Extends ADR-022.

**Decision.** `finding_explanations` holds versioned, frozen evidence, with stable ids from real identity, a basis word and code-written sentences as segments. `likely_cause` comes from fixed rules with signed facts first, or is null. Model words are an optional layer on top: they cite evidence ids, are stored with aliases, and are checked again when read. Three bases: `verified`, `telemetry`, `telemetry_text`, because a browser key can move a rollup.

**Alternatives.** An agent investigating; links to live data; a numeric confidence; a second table per kind.

**Consequences.** It works with AI off, and is testable. It knows only what it was written to look for. It costs storage, unmeasured.

### Proposed ADR: Capped queues share one floor on a shared worker

**Status:** Proposed. Extends F6's `QUEUE_CAPS`.

**Decision.** A worker with uncapped queues may be given `uncapped_floor`: all its capped queues together hold at most the slots above that floor, taking turns to go first. `scheduler-light` keeps 4 of 8 for probes and alert sweeps.

**Alternatives.** A cap per queue, each counted alone (leaves 2 of 8 with three queues, and each later queue shrinks it). Claiming `light` first (no help once long jobs hold every slot).

**Consequences.** Idle slots while capped work waits, so a wide outage's facts take longer. Probes and alert sweeps do not depend on any later feature's arithmetic.

### Proposed ADR: One provider interface, three wire formats, no SDK

**Status:** Proposed. Revives what decision 0014 aimed at, on today's facts.

**Decision.** `nexovue_platform.ai` holds pure wire formats: `openai_chat`, `anthropic_messages`, `bedrock_converse`. HTTP goes only through the connect-time guarded client, in `nexovue_scheduler.insight`. Nexovue always validates output itself. Both of the founder's options are one row in `ai_connections`.

**Alternatives.** One format, provider SDKs, frameworks.

**Consequences.** Three formats to keep working, and a provider's change breaks only one. A format stays off the screen until it has a dated real call. New provider-specific features are not automatic. The address guard widens by an operator's named list, a real change to its promise.

## Not checked

- Anything running. No model, query, or test ran. Every token, cost, memory and time figure for Nexovue is a guess.
- The job functions against the real queue, whether a column-level grant permits `LOCK TABLE`, and Compose networks across all five layouts.
- Whether a managed Postgres account can create a role; only the bundled image's behaviour was read.
- Whether F28's documents stay in step after this review. Its signatures were read on 2026-11-02, and F3 cites its section names rather than lines.
- How common same-minute ties are, and the error-group read's real time at volume.
- Whether browser-door edges can be carried at the cost A63 assumes; the workers were read only as far as `edgeSpan`.
- Which Bedrock models honour `outputConfig`; Ollama's token counts, and whether Ollama offers any access control beyond what its FAQ says.
- Slack's unfurling behaviour on these payloads. Whether a managed Postgres lets `setup` revoke from `PUBLIC`.
- Whether libcurl's AWS signing option works through `perform`; its name is from memory (A59). Bedrock's exact URL.
- Whether host readings could be tied to a service after all; only `infrastructure/conventions.py` and `queries.py` were read.
- `scheduler-insight`'s real memory, and the time a real gather takes.
- What real users want.
