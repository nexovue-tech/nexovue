# Finding explanation (F3): research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Input to F3's `spec.md`, `design.md` and `plan.md`. Nothing here is approved
> until the founder approves it, and no code is written from it.
>
> **Scope.** The explanation on a finding: correlation from stored facts, the provider-neutral AI interface, AI
> wording, and what remains of F2 (finding detail). Stage 2 (Explain). Builds after F6 and F28.
>
> **How it was checked.** The code was read on branch `feature/anomaly-detection` with uncommitted work present.
> Line numbers are for that working tree and will drift. **Nothing was run**: no model, no query, no test. Every
> figure about tokens, memory or cost for Nexovue is a guess, and says so. Web pages were read on 2026-11-02 through a
> tool that summarizes them: check any quote against the live page before publishing it. Nobody was interviewed.
>
> **Reads with:** [`phase-2-foundation.md`](../../product/phase-2-foundation.md) (shared decisions, "FD" numbers),
> [code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md),
> [customers](../../research/phase-2-customers-2026-11.md). This does not repeat them. It adds what reading F3's own
> code and rivals' own documents turned up.

## The short version

1. **The market's most credible design is the one the plan already chose.** Coroot's documents describe the same
   shape: non-LLM analysis finds the causes, and the model only explains what it was handed. Its own test cost cents
   per run. A vendor's single test, so it proves the shape is workable, not that it is accurate.
2. **So F3's value is mostly not the AI.** With facts first and names replaced by aliases, the model cannot discover a
   cause. It puts code-found facts into plain words. The differentiating work is the correlation code and the evidence
   record, which work with AI off. **The catalog's "the differentiator" should go, and not move to "evidence":** section 4 shows linked evidence is the entry fee. The narrower story is in `spec.md`, *Positioning*.
3. **Reading the code found five places the foundation cannot be built as written** (section 2). The worst: a
   `scheduler-insight` container with the foundation's forbidden list **cannot start in production**, and it can reach
   Kafka with no password whatever keys it holds.
4. **"Measured" is not "an outsider cannot touch it".** A browser key opens the door to spans, and no stored column
   records which door a row came through. Only a signed deploy record and nexovue's own checks are beyond an anonymous
   sender. The evidence record needs a third basis word (section 6).
5. **Facts-only input makes the strongest gate a plain test, needing no model.** Plant attacker text in every field,
   capture the outgoing request at a fake provider, and fail if one byte of it appears (section 7).

## Words used here

| Term | Meaning |
|---|---|
| **Evidence** | A stored fact with an id, which a claim can point to (foundation) |
| **Correlation** | Finding facts that line up in time or by dependency. It shows things happened together, not that one caused the other |
| **Abstain** | Answering "not enough evidence" instead of naming a cause |
| **Agent loop** | A model that chooses its own next query, reads the answer, and repeats |
| **Single-shot** | One model call with all input prepared beforehand, and no tools |
| **Base rate** | How often something happens anyway. If a service deploys every 30 minutes, "a deploy came before this" says almost nothing |
| **Transactional enqueue** | Adding a job in the same database transaction as the change that caused it, so both happen or neither does |
| **Structured output** | Asking a provider to return JSON that fits a given shape |

## 1. What exists

### Inputs F3 can use today

| Fact | Where | Note for F3 |
|---|---|---|
| A finding's signals: normal, band, current, worst, comparison, evidence endpoint, week-ago value | `packages/py-platform/src/nexovue_platform/detection.py:260-287`, written by `lifecycle.py` | A missing value is `None`, never 0 (`:266`). The correlation must keep that difference |
| Frozen readings for a service | `apps/api/src/nexovue_api/findings/queries.py:329-360` | At most 1,440, the latest day (`:318`). Empty for a project finding (`findings/service.py:330`) |
| Linked threshold incidents | `findings/queries.py:115-128` | Their `rule_name` is customer-typed text. Treat as a name |
| Other open findings | `findings` table, one open per service (`0041:279-292`) | The strongest cheap clue: a dependency already has a finding |
| Dependencies with exact counts | `deploy/clickhouse/migrations/000014_service_edges_1m.up.sql`, read by `apps/api/src/nexovue_api/servicemap/queries.py:92-112` | Kept for the derived retention (`000014:31`). Latency is the **caller's** view (`queries.py:9-14`). The callee name can come from the caller's own span (`000027` header), so it is attacker-reachable |
| Error groups in a window | `apps/scheduler/src/nexovue_scheduler/alerts/telemetry.py:58-96` | Project-wide with no per-service filter and no counts. F3 needs a new statement |
| Deploy order, with honest wording | `apps/api/src/nexovue_api/deploys/service.py:147-187` (`DeployHistory`), `errorgroups/schemas.py:15-26` (*before, not because*) | API only until F28 moves it (foundation, section 8) |
| Check state changes | `CheckStateChange`, `data/postgres/models.py` | Nexovue's own measurement, the most trustworthy telemetry-shaped fact |
| Sealing, guarded HTTP, job queue | `checks/sealing.py:95-137`, `probes/http_check.py:99`, `jobs/queue.py` | Each has a trap, section 2 |

### What the finding page does today

`apps/web/src/features/findings/FindingScreen.tsx:107-116` shows, in order: header, controls, what moved, readings,
services, linked alerts, feedback. Its charts pass no `markers` (`:535-544`), although `TrendChart` takes them
(`shared/components/TrendChart.tsx:97`). The card says probable cause "stays off the card until Stage 2"
(`FindingsScreen.tsx:332`). The API's `FindingDetail` (`findings/schemas.py:115-123`) has no place for an explanation.

### What does not exist

Correlation, a provider interface, provider settings, a switch, a stored explanation, cost limits, links from a finding
to its telemetry, and any injection fixture. The code map already lists these.

### What remains of F2

The catalog's F2 (`feature-catalog.md:249-250`) against the code, using F6's own boundary (`anomaly-detection/spec.md:140-141`):

| F2 promised | State | Decision |
|---|---|---|
| What was detected, readings, linked alerts | Built in F6 (D12) | Keep |
| Deploy events | Missing on this page | **F3**, using F28's data. Markers on the charts, and as evidence |
| Linked traces, correlated logs | Missing | **F3**, as links opening the right service and time. Not copies of the data |
| AI probable cause "with confidence" | Missing | **F3**, with no numeric confidence (decision A6) |
| Suggested fix | Missing | F5 |
| Create or view a case; ask the chatbot | Missing | F22 and F4. Leave room; build nothing |
| "Resolve" | Replaced | A finding closes itself (F6, AC1.8's amendment). Not reopened |

**Links are less simple than they look.** The time window is global, per-browser state in `localStorage`
(`shared/selection.ts:73,112`), set by `setAbsoluteWindow` (`:150`). Only traces keep their filters in the URL
(`app/router.tsx:294-302`), and logs carry only a service and a severity (`:330-334`). So a link can set the window when
clicked, as `ServiceLink` sets the project (`FindingScreen.tsx:170-172`). That changes the person's chosen window on every
other screen, and a copied link loses it. **The design must choose, and the honest fix is a window in the URL.** Raw data
also ages out first (7 days on the seeded Free plan, `0005_plans_table.py`, seed value; later values were not traced), so a
link to it must say when the data is gone.

## 2. Where the code disagrees with the foundation

The foundation asks to be told, not quietly differed from. Each row was read in the code.

| # | Foundation says | The code says | What F3's design should do |
|---|---|---|---|
| 1 | `scheduler-insight` holds no Kafka and no `CHECK_SECRET_KEY` | **It cannot start.** `kafka_brokers` is required, with no default and at least one entry (`scheduler/settings.py:53`). In production, a missing `CHECK_SECRET_KEY` is refused at boot (`settings.py:173`) | Make those requirements follow the role, with a test that boots the role on exactly the permitted variables |
| 2 | The role has "no telemetry database within reach" | **Kafka needs no password.** Its listeners are `PLAINTEXT` (`compose.infra.yaml:23`, `nexovue.kafka3.yaml:47`), and no Compose file names a network (a search of `deploy/compose/*.yaml` for `networks` found none), so every container shares one. A broken-into AI process could read `spans-raw` and `logs-raw` without any key | Give the role its own network, reaching Postgres and the outside world only. Postgres must then join two networks. Test it from inside the container |
| 3 | "All provider HTTP must live inside `nexovue_platform.ai`", through `probes/http_check.perform` | **Not possible together.** `perform` is in the scheduler and needs `pycurl`, which only the scheduler depends on (`apps/scheduler/pyproject.toml`). The platform cannot import the scheduler | Pure wire formats (build request, parse answer) in `nexovue_platform.ai`; the transport in `nexovue_scheduler.insight`. Add both to `REPOSITORY_AI_MODULES`, as the foundation already plans |
| 4 | `/ai/connection/check` is an API route, and the interface has `check()` | The API has `httpx` and no address guard. Letting it call a customer-typed address skips the connect-time check | The route enqueues a job on `insight` with `wake=True`, and reads the result from `job_results`, as a send test does (`jobs/results.py`, `jobs/queue.py:110`). So **the API never calls a provider** |
| 5 | The facts job is "enqueued after commit, next to the existing enqueue" (`project_job.py:1808`) | **That point has no finding ids,** because `Outcome` holds only counts (`project_job.py:435-447`). A crash between commit and enqueue would also lose the job for good | Enqueue inside the opening transaction: `jobs/queue.py:110` is built for this ("on a connection already in a transaction"), and `findings/queries.py:208-214` already does it. Detection still imports only a task name |
| 6 | Claim basis `measured` covers "rollups, signed deploy records" | Rollups can be moved by anyone with a browser key. See section 6. The customers research's "which an outsider cannot write" does not hold for rollups | Three bases, not two (section 6) |
| 7 | Memory ceiling guess of 384 MB | `scheduler-light`'s comment records "256M OOM-killed its child processes at startup" (`nexovue.yaml:384`), and the forkserver imports **every** task module (`jobs/preload.py:16`, `registry.py:19-30`), including `boto3` and Kafka clients the role never uses | Let a role name its own task modules. `registered()` already takes a list (`registry.py:60`), though `preload.py` passes none. Less memory, and less loaded code in the AI process. Still unmeasured |

**Answered, from the foundation's "Not checked" list.** `error_groups_seen` is **not** filled for a project with no
new-error rule. `new_errors.evaluate` loops only over projects found by `rules_of_kinds` (`alerts/new_errors.py:86-93`), and
`api/alerts/defaults.py` has no such rule by name. So "is this error new?" cannot read that table. It needs a look back over
`spans` and `logs`, which reaches only as far as raw retention.

*Superseded after review: the design now uses four `SECURITY DEFINER` functions and no rights on the table, because `DELETE` also permits `LOCK TABLE` (`spec.md`, A57). The reading below is kept as the record.* **Row-level security is workable, with care.** The job queue's statements are `_CLAIM` (`UPDATE` with a `FOR UPDATE SKIP LOCKED`
subquery), `_FINISH` (`DELETE`), `_RETRY` (`UPDATE`) and `ENQUEUE` (`jobs/queue.py:42-77`). Per PostgreSQL's documents, `FOR UPDATE` needs
both the `SELECT` and `UPDATE` policies' `USING` to pass, an `UPDATE` must also pass `WITH CHECK`, and a `DELETE` with a `WHERE` needs the
`SELECT` and `DELETE` policies. **An account that owns the table skips all of it** unless the table is set to `FORCE ROW LEVEL
SECURITY`, so `nexovue_insight` must not own `jobs`. Nobody has run this against the queue; it is the first thing F3's plan should try.

**Traps in existing code that F3 will hit**

- **A new notification kind reads as a new alarm by default.** `_compose_finding` ends in `else`, which writes `[warning] <title>` (`alerts/messages.py:310-312`).
  `explained` with no branch would look like a second opening. On PagerDuty, any kind outside `ENDING_KINDS` sends `trigger` (`alerts/payloads.py:163`). Webhook
  receivers would see a new `finding.explained` type (`payloads.py`, `webhook`). The foundation's limit to `READ_BY_PEOPLE` must therefore be enforced where the row is written.
- **`signals[].key` carries the name too.** It is `endpoint:<name>` (`detection.py:270-272`). A prompt built by copying a signal and removing `endpoint` would still leak it. **Build
  the prompt from an allow-list of fields, never by removing fields from a stored one.** `comparison` is safe: code composes it from a day and a clock (`lifecycle.py:543-550`).
- **The hard limit kills the child** (`jobs/registry.py:5-7`). A provider call killed midway may still be billed, and no usage comes back. Record the run as started before the call, and keep the
  reservation when a run dies. The foundation's "return what it did not use" must apply only to runs that finish.
- **A job can run twice** (a lapsed lease, `jobs/queue.py:3-7`). The write needs a unique key on finding and version, and `results.store` already models "the first result stands" (`jobs/results.py`).
- **Evidence reads can be heavy.** The error-group statement scans `spans` and `logs` in a window; the reader account stops at 30 seconds (`deploy/clickhouse/users.d/reader.xml`). The default limits are 30 and 60 seconds (`jobs.py:27-28`). The `evidence` queue also needs adding
  to `worker_queues` (`__main__.py:78-85`) and capping in `QUEUE_CAPS` (`jobs/worker.py:72`), so that a wide outage opening many findings cannot take all 8 of a worker's slots from the probes.
- **Scheduler ClickHouse statements have a fixed home.** Only four modules may hold the raw client (`test_tenant_chokepoint_structure.py:110-112`), and only `detection` is checked for binding a project (`:116`). Evidence reads need listing and the same check.
- **`http_check` was built for probes.** Its user agent says "nexovue-prober" (`http_check.py:37`), redirects default to on (`:65`), the timeout is whole seconds (`:66`), and `may_connect_to`'s private path is documented as development only (`addresses.py:69-104`). `AI_PRIVATE_ENDPOINTS` is a real change to that promise, and must be reviewed as one.

## 3. What users need

No one was asked. This is reasoning from the code and the [customers research](../../research/phase-2-customers-2026-11.md), which rests mostly on sellers' material. Treat it as what to design against.

### The buyer (a CTO or founder at a small company)

- Wants: fewer hours lost to incidents, and a bill that does not surprise. Cost is the top selection criterion in Grafana's seller-run survey (65%).
- Asks first: "Where does our data go?", then "What does it cost?", then "Is it right?"
- Needs from F3: a preview of exactly what is sent, a hard monthly limit, an off switch that changes nothing else, and a real answer to "why not just paste this into Claude?"
- **The honest risk:** a buyer who reads "AI insights" expects the AI to discover causes. This one does not, by design. If the page reads as a thin summary, it disappoints. Sell where-to-look facts with no AI needed, with the AI as optional wording on top.

### The on-call engineer, at 3 a.m.

- Wants one thing: where to look first, in under a minute, on a phone (AC1.10 applies).
- A wrong answer costs more than no answer, because people follow fluent text (customers research).
- Needs: the fact and its link above any prose; "not enough evidence" shown without apology; "no deploy information" never reading as "no deploy happened"; and how often this service deploys anyway (section 5, base rate).
- An alert must never wait for the explanation (plan). A second message must add something, or it is noise.

### A new developer on the customer's team

- Does not yet know the architecture. The dependency facts are most useful to them: "`checkout` calls `payments`, whose failures rose at the same minute".
- Needs plain terms. p95 appears in F6's words already; the wording should explain, not add jargon. **This is the clearest job the AI is good at**, and also the best reason to keep it.

### The admin

- Needs to connect a provider, see it work, set a limit, and read what each run did, without touching the server. `MANAGE_AI` and owner-only consent are decided (FD8).
- Hits a real setup problem on self-hosted: Ollama on the same machine bound to `localhost` is unreachable from a container, and loopback stays refused by design (`addresses.py:99-104`). The error must say what to change. A long-term Bedrock key has AWS's own warning against production use (foundation).
- Wants the settings page to say plainly that the MCP server is not governed by this switch (foundation, section 4).

### The attacker

Anyone reaching the customer's public site, or holding a public browser key, can act. Assume all of this is tried:

| Attack | What it does | What stops it |
|---|---|---|
| Instructions in a log, exception or service name | Tries to steer the model | None of that text is sent (U1), and a test proves it (section 7) |
| Forged spans naming a real service, timed with an outage | A believable false cause. Fence-free, because it is data | Cannot be stopped outright. The rule order puts signed and own facts first, and the basis is shown (section 6) |
| Real traffic making real errors | Moves numbers honestly | Nothing should: it is true. The basis still says "from telemetry" |
| Making the model emit a link or image | Would carry data out when the browser fetches it | Plain text, no Markdown, validator rejects addresses, links built by nexovue (U3, U4) |
| Service name holding a URL, restored into the sentence | The text is shown; no fetch happens unless something links it | Never auto-link text. Validate before restoring aliases, and test the restored form too |
| Flooding findings to burn the customer's AI budget | Bills, or empties the limit, so real findings get no wording | Flap guard, per-finding cap, monthly limit. Facts survive the limit. **F3 should reserve part of the monthly limit for critical findings** (decision A10) |
| Pointing the provider address at an inner service | Server-side request forgery | Connect-time guard, redirects off, operator-only private list |
| Admin account takeover | Sends telemetry facts to the attacker's "provider" | Owner-only consent, an audit row, and re-consent when the address changes (decision A9) |
| Breaking into the AI process through a library bug | Reaches anything on its network | Narrow keys, and the network change in section 2 |

## 4. How competitors do it, and at what price

Prices and editions come from the [competitors research](../../research/phase-2-competitors-2026-11.md) unless marked **new**, which were read for this document. A vendor's description of itself is a claim, and nothing was tried.

| Product | Who finds the cause | What the reader sees | Data sent to the model | AI off | Price, as listed |
|---|---|---|---|---|---|
| **Coroot** | **Non-LLM analysis** that walks the dependency graph; "doesn't involve LLMs" (**new**) | Report: anomaly summary, how it spread, key findings, fixes, charts (**new**) | Only its findings, "not all the raw telemetry" (**new**) | Not stated | AI root cause: Enterprise from $1 per CPU core a month, with your own key and any OpenAI-compatible endpoint (**new**, `ai/configuration`); Community through Coroot Cloud, 10 free investigations a month (**new**, `ai/overview`). Its pages tie the whole AI flow to those; which edition holds the ML step alone is not stated. Publishes its own scores on five scenarios of the OpenTelemetry Demo, from 2/3 to 3/3, undated, with the scale unexplained and no other vendor (**new**, `ai-benchmark`) |
| **Dynatrace** | "Causal AI" over its topology, ranking contributors (**new**) | Highlights the root-cause entity | SaaS | Not stated | Assist: no charge in the interface, consumes licence |
| **Datadog Watchdog** | Its AI engine, linking anomalies through dependencies; how it works is not said (**new**) | Root cause, critical failure, impact. Four cause types: version changes, traffic increases, AWS instance failures, running out of disk. **Latency and errors are never the root cause, only symptoms** (**new**, re-read 2026-11-02) | n/a | n/a | Part of APM; not checked |
| **Datadog Bits Investigation** | Agent forming hypotheses (**new**: the page read does not describe output) | Not described on that page | Telemetry | Separate product | Credits; about $6.50 to $8.45 each, by arithmetic from estimates |
| **Sentry Seer** | Agent over issues, traces, logs, code | "An initial guess" on the issue, then fixes (**new**) | Includes code, if linked | An organization toggle (**new**) | $40 per active contributor a month. Self-hosted: not stated by Sentry's page, unconfirmed |
| **OpenObserve** | Agent via MCP tools (**new**). Starts "the moment an alert fires"; "a complete evidence chain" (**new**, `ai-sre.md`) | Report; re-runs with a cooldown, 30 minutes in the sample (**new**) | A context window of logs, metrics and traces, sent to your configured model, including any OpenAI-compatible or self-hosted one (**new**, `ai-sre.md`) | Off by a setting | Enterprise, on a platform you can self-host. Earlier reading: free to 50 GB a day, unconfirmed there. That page lists $0.50 per AI credit and 20 free credits, and how this applies to self-hosted was not resolved (**new**) |
| **incident.io** | Agent, with an adversarial second agent (**new**) | Confidence score and sources per finding; timeline (**new**) | Hosted | Add-on | No price shown |
| **Rootly** | Competing hypotheses; "Inconclusive" | Evidence, or inconclusive | Hosted | Add-on | By quote |
| **HolmesGPT** | Agent loop over many sources (**new**) | Written to Slack or the source; format not described (**new**) | Tool results | Separate tool | Free, plus own model. Its repository's evaluations page links to latest results and a history (**new**, `docs/development/evaluations`). The numbers were seen only on preview hosts through a search, so they are not quoted here: own test set, model pass rates |
| **Grafana** | Assistant in Cloud; Sift runs configured checks (**new**: which checks was not read) | Each check's result on an investigation page (**new**) | Prompts and query context to Grafana Cloud | Separate | The Pro fee includes the first three active AI users (**new**, invoice page). Earlier reading: $20 per further active AI user with 40M tokens, not re-read |
| **SigNoz** | Noz, Cloud only | Chat | Cloud | n/a | Teams plan |
| **Aurora** | Agent in isolated pods | Reports | Own model | n/a | Free |

**What this shows**

- **Two families.** Code finds the cause and the model explains (Coroot, in part Dynatrace and Watchdog), or an agent investigates (most others). InfoQ's July 2026 piece describes a shift toward the first. It also notes that agents keep an edge on novel incidents, so this is not settled.
- **Linked evidence and "inconclusive" are the entry fee** (competitors research). Nexovue meets the market here, and does not beat it.
- **A useful rule from Watchdog:** a slower service is a symptom, not a cause. A cause sentence must name something other than the signal that moved.
- **Coroot and HolmesGPT publish results on their own test sets. No vendor read publishes accuracy on customers' real incidents.** incident.io says it backtests daily, with no figure. That narrower gap is open (foundation, section 12).
- **Self-hosted spending limits:** the OpenObserve page read has rate limiting through a gateway, no budget. Whether rivals offer a hard limit elsewhere was not checked, so do not claim uniqueness.

**There are two nearest rivals, and the plan should say so:** Coroot in design shape, and OpenObserve for the free self-hosted, own-model buyer. Coroot stores telemetry, finds causes without an LLM, sends only findings, and lets a customer use their own provider in Enterprise. Its AI in the free form goes through its cloud: its own pages read 2026-11-02 settle the conflict the competitors research recorded. OpenObserve's agent overlaps "there before anyone is awake" and "own model", though it sends logs to the model. What Nexovue may honestly add: AGPL with the whole chain as of the date stated, facts and wording on the customer's own account with no licence key, aliases instead of names, and test results if published. **Say what Nexovue does, never what a rival lacks.**

## 5. Methods compared

### Finding the cause

| Method | Evidence for it | Against | Verdict |
|---|---|---|---|
| **Time order only** ("a deploy came before") | Exists, honest (`DeployHistory`) | Weak alone: frequent deployers always have one | Keep as one fact, with its base rate |
| **Fixed rules over stored facts** | Deterministic, testable, works with AI off, costs no tokens. Coroot's and Dynatrace's stated direction | Knows only what it was written to look for. Novel incidents become "not enough evidence" | **Use.** Honest misses are acceptable |
| **Graph walk over `service_edges_1m`** | Coroot, Dynatrace, Watchdog all use dependencies. Rollups are exact and pre-sampling | Edges only exist where traces do. Latency is the caller's view, so the network and the callee look alike (`servicemap/queries.py:9-14`). Coroot's own test scenario hinges on this trap | **Use, depth 1 first.** Deeper only when measured |
| **Statistical causal inference** (for example Granger tests) | Academic | Needs far more minutes than a just-opened finding has. Nothing here says it would work on sparse small-team traffic | **Do not attempt** |
| **Single-shot model over frozen facts** | Coroot: no tools, no agent loop, a few cents. Fits the Rule of Two | Finds nothing the facts did not | **Use, for wording** |
| **Agent loop** | Most vendors. Possibly better on novel incidents | Needs tools (which FD2 removes), tokens vary widely, errors multiply (Honeycomb's arithmetic), and more data can mislead (Datadog's own account). ZenML and incident.io report them hard to debug, via InfoQ | **Not in F3.** F4 owns the only loop |
| **Retrieval of past incidents** (RCACopilot style) | 0.766 accuracy on Microsoft's own year of incidents (A) | Different task (predicting a category), a huge history, and a small customer has none | **Later**, after F4's knowledge store |
| **Fine-tuned model** (Meta; decision 0014's idea) | 42% at Meta, in backtesting on its own monorepo, tuned on about 5,000 examples | Needs data no customer has, and the founder's option is the customer's own model | **Do not attempt** |

**Numbers to keep apart.** 25.3% (ORCA-bench, medium tasks), 42% (Meta), 0.766 (RCACopilot), 82% and 94.5% (vendors) measure different things on different systems. None predicts Nexovue. **Meta's lesson is the useful part:** it withholds low-confidence answers, "sacrificing reach in favor of precision".

### A starting rule set (a suggestion for the design; every window here is a guess)

Each rule yields a fact. Only some may become `likely_cause`.

| Order | Fact | Source | Basis | May be `likely_cause`? |
|---|---|---|---|---|
| 1 | A dependency has its own open finding that began first | `findings`, `service_edges_1m` | Telemetry | Yes, if the edge's failures or latency moved too |
| 2 | This service deployed shortly before the start | F28's `deploys_in_window` | Signed | Yes, if deploys are rare enough for this service |
| 3 | A check on this service changed state | `check_state_changes` | Nexovue's own | Yes |
| 4 | A dependency edge moved at the same minute, with no finding | `service_edges_1m` | Telemetry | Yes, worded weakly |
| 5 | A new error group began after the start | New look-back over `spans` and `logs` | Telemetry, and forgeable | **Only with a second fact.** U6 forbids a cause from free text alone, and the group exists because of free text |
| 6 | Traffic rose before a slowdown | Own rollups (Watchdog lists traffic as a cause) | Telemetry | Yes, worded weakly |
| 7 | A linked threshold incident | `finding_incidents` | Telemetry | No: it is the same symptom |
| 8 | No deploy data for this service; no traces for its edges | F28's `coverage` | n/a (`absence`) | Never. It is stated, and lowers what can be said |

- **The base rate must be shown.** "Deployed 4 minutes before. This service deployed 31 times in the last 7 days." A person weighs the first sentence very differently with the second, and the query is cheap.
- **Healthy checks cut against a false cause.** The healthy run (foundation, section 11) must return no cause at all, or the rules are too eager.
- **Project findings.** Their title already names the suspects (`lifecycle.py:507-514`): collector, network or Nexovue's intake. Their signals are thin and their readings absent. **Recommendation:** cover them with their own small rule set (any shared deploy, shared dependency, and intake state), or plainly "not enough evidence". Never leave them blank.

### Timing

- **Deploy records can arrive late.** A pipeline may post after rollout. A first read at opening would miss them. **Starting guess:** read at opening, again about 5 minutes later, and at close. Each material change is a new version (foundation).
- **Findings are judged after the settling lag** (`detection.py:63`), so telemetry for the opening minutes should exist by the first read.
- **Read near opening, keep the result.** Readings and raw data age out (code map), and a job that runs late must say what it could no longer read.

### Which provider format, and how to get valid output

| Provider | Getting a shape back | What to handle | Source |
|---|---|---|---|
| Claude | `output_config.format`, type `json_schema`, on current models | **A refusal returns 200 and may not fit the schema; so may `max_tokens`. Check `stop_reason` before parsing.** Enum letter case may change. Limits: 24 optional parameters, 16 union types. First use of a new schema is slower | [Anthropic](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| Bedrock Converse | `outputConfig.textFormat` | `stopReason` includes `guardrail_intervened`, `content_filtered`, `malformed_model_output`, `model_context_window_exceeded`. `usage` holds token counts. Which models honour `outputConfig` was not read | [AWS](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) |
| Ollama | `format` natively, `response_format` through its OpenAI layer. **This updates the foundation,** whose page did not confirm schema output. Ollama's cloud does not support it | Quality varies by model. Token counts not read | [Ollama](https://docs.ollama.com/capabilities/structured-outputs) |

**Outcome mapping.** The foundation's outcomes (`ok`, `refused`, `too_long`, `limited`, `unavailable`, `bad_key`) fit these, with content filters counted as `refused`. **Use a provider's schema support where it exists, and never rely on it** (foundation, U3). A small schema stays inside every limit above.

**Why not a framework:** `litellm` and similar lose the connect-time guard (FD3), and are already on the deny-list of `test_detection_has_no_ai.py:38-81`.

### What it might cost

**The only measured prompt found is someone else's:** Coroot's test used about 9,800 input and 1,000 output tokens. Nexovue's facts-only prompt should be smaller; that is a guess. Using that size with list prices read today:

| Model | Input and output, per million tokens | One run, *arithmetic* | 185 runs, *arithmetic* |
|---|---|---|---|
| Claude Haiku 5.5 | $0.10, $0.50 | about $0.0015 | about $0.27 |
| OpenAI gpt-6-luna | $0.10, $0.50 | about $0.0015 | about $0.27 |
| Claude Sonnet 5.5 | $2, $10 | about $0.03 | about $5.50 |
| Claude Opus 5.5 | $4, $20 | about $0.06 | about $11 |
| OpenAI gpt-6-astra | $10, $50 | about $0.15 | about $27 |

- 185 runs is roughly what the foundation's suggested limit of 2 million tokens buys at this size. **These are not measurements of Nexovue.** The eval bed's Cost run replaces them.
- **What this suggests:** at this job's size the customer's bill is likely small, a small fraction of rival prices. **So do not ship F3 around saving tokens,** though the limit stays required, since a limit is what makes the bill promise true.
- **Do not advertise any of these figures.** Prices move, and they are not Nexovue's.

## 6. Three bases, not two

The foundation's `measured` includes rollups. Here is what the code shows.

- The browser door accepts OTLP (`apps/receiver/internal/otlp/handler.go:186-198`) with a public site key (`receiver/internal/auth/key.go:24-51`), gated by an origin list. The code itself calls that list "abuse mitigation, not an authentication boundary" (`auth/auth.go:65-70`). The site key sits in the customer's own web pages.
- The code's own docstring says such a sender can supply service and endpoint names (`alerts/payloads.py:200-204`).
- A search of the ClickHouse migrations found no column marking a row's door, apart from `web_vitals`.
- **Not traced:** whether every browser span reaches `service_stats_1m` and `service_edges_1m`. The design must trace it through the workers. If they do, an outsider can move a rollup, and also open a finding (an F6 risk to record). If not, a third basis may be needless for rollups, and still applies to error groups.

| Basis | Means | Examples | Can an anonymous outsider forge it? |
|---|---|---|---|
| `signed` | Carries a secret's proof, or is nexovue's own measurement | Deploy records (`deploys/signatures.py`), check results | No, short of a stolen secret |
| `telemetry` | Counted from what was sent to the intake | Rollups, edges, error group counts | **Possibly, in numbers and names**, and real traffic can always move it |
| `from telemetry text` | Rests on words in the telemetry | An exception type or message meaning something | Yes |

Rules: `likely_cause` may rest on `signed` or `telemetry`, never on the third alone (U6). The screen shows the basis in plain words. **A browser-only project is a real case** and should be tested: everything but deploys and checks is `telemetry`.

## 7. Safety: what facts-only makes possible

The foundation's U1 to U6 stand. Reading the code adds these.

1. **Types instead of filters.** The prompt's input should be a dataclass whose fields can only be numbers, times, members of a closed list, and `Alias` values. A free string then cannot enter by accident, and a new field cannot leak by default. This handles the `signals[].key` trap for good.
2. **A canary test, needing no model.** Plant unique markers in every attacker-reachable field: service, endpoint, version, exception type, message, span attributes, `deploy_events.metadata`, callee names, rule names, project names. Run the evidence and prompt steps against a fake provider. Fail if any marker is in the outgoing bytes. **This can run on every commit and costs nothing.**
3. **The plan's suite still runs on real models,** as the plan requires, on both options. With no free text sent, LogJack's log payloads are expected to reach nothing. That needs proving, not assuming. The live risks become forged facts and output escapes.
4. **Deterministic checks on what comes back.** Beyond the foundation's list:
   - Every number in a sentence must match a value in the claim's evidence (within rounding), or the claim goes. It is the cheapest check against an invented "4×".
   - Banned words: "caused", "because of", "root cause", "due to". Nexovue's rule is "before, not because" (`errorgroups/schemas.py:15-26`). A word list is crude, and the bed should report how many good sentences it drops.
   - Only known aliases may appear; validate, restore, then check the restored form again.
   - Claims with no evidence id go. With none left, the outcome is `failed`, and the facts still show.
5. **Let the model choose, not write, where it can.** "What to check next" should be picked by id from a list code offers ("open the deploys", "open `service_1`'s traces"). Links stay nexovue's, and this is useful even for a model that writes poorly.
6. **Prompt instructions are not a safety control.** OWASP says it is "unclear if there are fool-proof methods of prevention". Its list (validate output in code, least privilege, separate untrusted content, adversarial testing) is what this already does, apart from relying on a system prompt.

**What this does not fix.** A forged fact can still produce a wrong but valid sentence. The product answer is showing the basis, the base rate, and a thumbs-down.

**The trade-off.** With aliases, the model cannot tell that `service_3` is the database, and may write blander text. FD18 chose safety, and asks the bed to measure it. **A middle path to test:** closed-list hints derived by code, such as an edge's kind as `database`, `cache` or `queue` from `server_peer_kind`, or an error's class as `timeout` or `connection`. Being closed lists, they carry no instructions, though they can still be forged, and they count as `from telemetry text`.

## 8. What we should bring

**True once built, and only if the designs hold. Future tense until then.**

1. **An explanation that exists with AI off.** Facts, a cause by rule, and a template sentence, all with no provider. Most rivals' explanation is the AI. Coroot's analysis also runs without an LLM, so this is not unique.
2. **Every claim joined to a stored copy of its fact**, still readable after the raw data has aged out.
3. **Base rates beside deploy claims.** None of the pages read described this, which is weak evidence. Do not claim it as unique.
4. **Honest absence:** "no deploy information for this service", from F28's `coverage`.
5. **A preview of the exact bytes,** then the same bytes logged by field name. Nothing read describes this, though it may exist.
6. **The canary test and injection results, published,** if the founder agrees (foundation, decision 5).
7. **Model-free fallback wording,** so a local model that is weak or slow costs the customer little.

## 9. What we should not attempt

| Do not | Why |
|---|---|
| Promise a root cause, or show a percentage confidence | Uncalibrated numbers look exact. No data supports one (decision A6) |
| Let the model choose the cause | It sees aliases and no text. Foundation: `likely_cause` comes from code |
| Agent loop, tools or retrieval in F3 | Section 5, FD2 |
| Send log bodies or error messages in the first version | FD18. Revisit only on the bed's numbers |
| Model words in alerts | FD17 |
| Run a model in Nexovue's install | Founder's decision |
| Fine-tune or train | No data, section 5 |
| Streaming, a chat box, or "regenerate until you like it" | Not needed for 1 to 3 sentences. F4 owns chat. Foundation caps runs |
| Name a recommended local model now | Nothing was measured. Coroot's one test had `Gemma 4 31B` as the only self-hostable model finding the cause; a single vendor scenario, on an unknown machine |
| Say "AI" where a rule did the work | Trust, and honesty. The word stays off the fact section |

## Decisions taken

*Recommended by the authors, founder may overrule.* Foundation decisions are not repeated.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| A1 | How the facts job starts | (a) After commit, as the foundation says. (b) In the opening transaction | **(b).** The opening knows the id, and nothing is lost in a crash (section 2, row 5). The foundation should be amended |
| A2 | Where provider HTTP lives | (a) Platform, adding `pycurl`. (b) Platform via `httpx`, with no guard. (c) Wire formats in platform, transport in the scheduler | **(c).** (a) spreads a C dependency to the API. (b) loses the guard. The foundation's test list already names both modules |
| A3 | Who tests a connection | API / a job | **A job,** so the API never calls a provider address |
| A4 | How `scheduler-insight` is kept from Kafka | Trust it / own network | **Own network.** Holding no key means nothing when no key is needed. It costs a Compose change on a stack with no networks today, and needs a live test |
| A5 | Settings for the role | Dummy values / settings that follow the role | **Follow the role.** A fake `CHECK_SECRET_KEY` would break the very test FD1 asks for |
| A6 | Showing confidence (catalog's F2 says "with confidence") | (a) A percentage. (b) High, medium, low from the model. (c) Likely or not enough evidence, by rule, with the basis and base rate shown | **(c).** A model's own confidence is not calibrated, and incident.io's score could not be checked. The plan's word is "likely". Amend the catalog |
| A7 | A sentence with AI off | Facts only / template sentence too | **Template too.** The page reads the same in both modes, with AI as an improvement. The bed can then compare the two. If template wins, that is worth knowing |
| A8 | What the model writes | Free summary / 1 to 3 claims with evidence ids, plus next checks picked by id | **Claims and picks.** It is checkable, and short, per the noise concerns |
| A9 | Changing the provider's address or model | Admin freely / owner re-consents for the address | **Owner re-consents when the address changes,** since consent was for where data goes. Changing a model on the same address is an audited admin act |
| A10 | Flooding the limit | One pool / keep some for critical findings | **Keep 20% for critical findings** (a guess). Otherwise a flood of warnings silences wording when it matters most. It adds little code |
| A11 | Project findings | Skip / own small rule set | **Own small rule set,** or a plain "not enough evidence" |
| A12 | Window in links | Set global state on click / window in the URL | **In the URL,** for logs, errors and traces. It fixes shared links too. The console's work grows; the design should scope it. F3 may start with the click if the URL work is large, recording this as a known gap |
| A13 | Task modules per role | All, as today / each role names its own | **Own list** for `scheduler-insight`. Less memory and less code in the riskiest process. It must be proved to change nothing for existing roles |
| A14 | Numbers in model text | Trust / must match evidence | **Must match.** Cheap, and it catches the failure people fear most |
| A15 | Who sees the explanation | People only / also tokens | **Also read tokens,** with its own route in `TELEMETRY_PATHS` terms. `/findings` is already there (`ratelimit/policy.py:248`), so the route is covered by prefix; tests should confirm. Tokens never trigger a run (foundation) |
| A16 | Catalog wording for F3 | "The differentiator" / describe it as explanation with evidence | **Change it,** with this research as the reason. The catalog's "proactive finding-generation surface" is already wrong (code map) |

## Founder decisions needed

**None new.** Everything affecting F3 is already in the foundation's list: AI on the hosted service, the selling line, the lawyer, the evaluation budget, and publishing results. One note for the budget: on the list prices above, model calls for a first evaluation run look small beside the test server, since the calls are short. That is arithmetic on a guessed size, and F3's plan must bring a real estimate.

## Not checked

- Anything running. No model, query or test ran. Every token, cost and memory figure for Nexovue is a guess.
- Whether browser-door spans reach `service_stats_1m` and `service_edges_1m` (section 6).
- Row-level security against the real job queue, and how an install creates a second Postgres account.
- Whether Compose networks can split as proposed with no effect on other services, and whether `nexovue.yaml` adds anything from the infra files that changes this. A search for `networks` in `deploy/compose/*.yaml` found nothing, and those files were not all read through.
- Later plan migrations' retention values. `api/alerts/defaults.py` was searched by name, not read in full.
- Rivals' products: only their pages, through a summarizing tool. Datadog's Bits page did not describe its output. Grafana's Sift checks were not read. Whether Watchdog uses an LLM is not stated on its page.
- OpenAI's and Claude's prices were read today from their own pages and will change; the model names are theirs. Google's own pricing was not read.
- What real users want. This remains the largest gap.

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md), [customers](../../research/phase-2-customers-2026-11.md), [`feature-catalog.md`](../../product/feature-catalog.md), [F6 spec](../anomaly-detection/spec.md), [F6 design](../anomaly-detection/design.md). Decision 0014 was read through `git show e0f11a0^:docs/architecture/decisions/0014-provider-flexible-llm-interface.md`. Figures for LogJack, AIOpsDoom, ORCA-bench, Causely and Grafana's survey are quoted from the customers research and not re-read here.

**Read for this document, 2026-11-02**

- Coroot: [AI overview](https://docs.coroot.com/ai/overview), [AI configuration](https://docs.coroot.com/ai/configuration/), [AI benchmark](https://coroot.com/ai-benchmark), [The hard part is no longer the model](https://coroot.com/blog/hard-part-of-ai-root-cause-analysis-is-no-longer-the-model/) (vendor's own test, one scenario, one attempt per model)
- [OpenObserve: AI SRE](https://openobserve.ai/ai-sre.md); [HolmesGPT: evaluations](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/development/evaluations/index.md); [Grafana: Assistant on your invoice](https://grafana.com/docs/grafana-cloud/platform/cost-management-and-billing/manage-invoices/understand-your-invoice/assistant-invoice/)
- [InfoQ: AI root cause analysis and context engineering](https://infoq.com/news/2026/07/ai-rca-context-engineering)
- Datadog: [Watchdog root cause analysis](https://docs.datadoghq.com/watchdog/rca/), [Bits Investigation](https://docs.datadoghq.com/bits_ai/bits_ai_sre/)
- [Dynatrace: root cause analysis](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis)
- [Grafana: Sift configuration](https://grafana.com/docs/grafana-cloud/alerting-and-irm/machine-learning/configure/sift/)
- [OpenObserve: SRE agent setup](https://openobserve.ai/docs/enterprise-setup/sre-agent/)
- [Sentry: Seer](https://docs.sentry.io/product/ai-in-sentry/seer/)
- [incident.io: AI SRE](https://incident.io/ai-sre)
- [HolmesGPT](https://github.com/HolmesGPT/holmesgpt)
- [RCACopilot](https://arxiv.org/abs/2305.15778), abstract only
- [Meta: AI for incident response](https://engineering.fb.com/2024/06/24/data-infrastructure/leveraging-ai-for-efficient-incident-response/)
- [Design Patterns for Securing LLM Agents](https://arxiv.org/abs/2506.08837), abstract only here
- [OWASP: LLM01 prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- Providers: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing), [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Docker Hub: postgres](https://hub.docker.com/_/postgres); [Microsoft Learn: Azure OpenAI v1 API](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle); [Microsoft Learn: Azure OpenAI REST reference, 2024-10-21](https://learn.microsoft.com/en-us/azure/foundry/openai/reference), from a search summary only
- PostgreSQL: [Row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html)
