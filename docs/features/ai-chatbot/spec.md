# Feature: AI chatbot (F4)

## Status

**Proposed**, 2026-11-02. Stage 4 of [`phase-2-plan.md`](../../product/phase-2-plan.md). Nothing here is approved
until the founder approves it, and no code is written from it.

Written from [`research.md`](research.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md) and a
reading of the code on branch `feature/anomaly-detection`, with uncommitted work present. No test, query or model ran. One small FastAPI probe and one Postgres probe ran during review (see `status.md`, *Reviews*). Every token, time and memory figure is a guess or arithmetic on a guess, and says so.

**Two corrections to the research, found while writing this.**

- *Deleting a person does not delete their conversations by cascade.* A removed member's row stays (`members/service.py:188-204`,
  "The row stays"), so nothing would cascade. Removal must delete them on purpose (BR14).
- *Most screens could not take an exact time range from the address.* The window lives in a browser store
  (`shared/selection.ts:150`), and `/logs` reads only service and severity from the address (`app/router.tsx:330-335`).
  F3's design now adds the range to the address (F3 A12, F3 design *Links*). FR18 follows F3, and the earlier plan of setting the window on click is dropped.

## Words used here

| Term | Meaning |
|---|---|
| **Provider** | Whoever answers a model call: the customer's AI account, or a model on their own server |
| **Prompt injection** | Text the AI reads being treated as instructions. Here it arrives through logs, traces and names |
| **MCP** | Model Context Protocol, the standard way an AI tool calls a product's functions. nexovue already serves one, read-only, for customers' own AI tools |
| **Tool** | A named read the model may ask for, such as `search_logs`. nexovue runs it; the model only asks |
| **Model loop** | The model asks for a tool, nexovue runs it and returns the result, and the model asks again or answers |
| **Step** | One tool run inside a question: its tool, filters, exact time range and row count |
| **Ticket** | A short-lived, read-only credential the model loop uses to read through the API on behalf of the person asking |
| **Embedding** | A list of numbers standing for a text's meaning, so similar texts can be found. Made by a model |
| **pgvector** | The Postgres extension that stores and compares embeddings (decision 0004) |
| **Way out** | Any route by which data could leave: an image address, a link, a command a person is told to run. An internal term, never used in product text (BR18) |
| **Fingerprint** | F3's short hash of where data goes: wire format, base URL, region and key id. Consent is tied to it, so a changed key or address ends consent |
| **Compare-and-set** | A database write that applies only if the row still has the expected value, such as `WHERE state = 'running'`, so a late writer cannot overwrite a newer state |
| **Data level** | The owner's choice of what chat may send: *facts only*, or *facts and text* |
| **Abstain** | Answering "not enough evidence" instead of guessing |
| **Runbook** | A customer's own written instructions for handling a problem |

## Problem

A person in the middle of a problem has a question, and nexovue makes them turn it into screens.

- **"Why was `checkout` slow yesterday at 14:00?" takes four screens today.** Service, traces, errors and deploys each
  need their own filters and the right window. At 3 a.m. this is where mistakes happen.
- **What the team learned is not in the product.** When a problem was fixed last month, the fix lives in someone's head or a
  chat thread. A new developer cannot ask "how did we fix this last time?".
- **The AI route that exists today has a known hole.** The MCP server gives a customer's own AI raw log text with nothing saying
  it is untrusted (`assistant/tools.py:224-225`), and no injection test exists. Its tools can only read "the last N hours ending now"
  (`tools.py:78-83`), and cannot read findings at all.
- **Chat is now expected.** Of the products checked, none treats it as rare (research, section 4). Several free routes exist, mostly by connecting an outside agent to a product (research, section 4). This is a statement about the products checked, not the whole market.

## Goal

A person with a question asks it in plain words, on any screen, and gets an answer they can check.

- **It reads only what they may read**, through the same routes and limits a screen uses.
- **It shows its work.** Each answer names the tools it ran, the filters, the exact time range, and links to the same data on the real screen.
- **It cannot change anything.** The part reading telemetry has nothing to write with, and nexovue's own code checks every answer.
- **It says when it does not know.** "Not enough evidence" and "the data does not cover that" are proper answers.
- **It remembers what people wrote, for as long as it is kept.** Notes and resolved cases go when their finding goes: **30 days after the case ends on a self-hosted install by default** (foundation founder decision 6, approved 2026-11-02: an operator setting, keeping today's values until the bed measures disk use). Runbooks last until deleted. Old answers are never treated as fact.
- **It is off until the customer turns it on**, and detection and alerts work exactly the same with it off.

## Target users

| Person | Wants | Surfaces |
|---|---|---|
| **P1 Dev / on-call engineer** | The right number fast, and how to check it | Panel on every screen, "Ask about this finding" |
| **New developer** | What a service is, what normally happens, what fixed this before | Panel, starter questions, runbooks |
| **Admin** | Control and a record of use, without reading people's questions | `/settings/ai`, `/settings/runbooks` |
| **Owner / buyer** | A cost it can predict, and a straight answer on where data goes | The consent step and its preview |
| **P2 Indie** | Same as P1, after an owner's AI setup and chat consent. The time to a first answer is reported from a Step 2 trial, not promised | Same |

**Not served:**

- **P3 Owner as a status reader.** That is F7's status page.
- **Viewers do not ask** (FD8). Asking spends money and stores text. A viewer sees no panel.
- **API tokens never ask** (foundation section 5). A customer's own AI reaches the MCP server instead.
- **People who want chat to do things.** It does not mute, acknowledge or build anything. See *Out of scope*.

The research has no interviews. The founder's five to eight conversations with on-call engineers are now a condition for starting Step 2 (plan, *Preconditions*), and the panel versus MCP question below is what they test.

## Scope

1. **A safer MCP server, first.** Free text labelled as untrusted, fixed time ranges, findings and single-item tools, and an injection suite.
2. **A side panel on every screen,** plus "Ask about this finding" on the finding page.
3. **Live tool-based answers** from a fixed list of read tools, using the customer's chosen provider, at the data level the owner chose.
4. **Live steps,** then the checked answer. Words are not streamed.
5. **Checked output:** a citation on every claim, quotes filled in by nexovue, figures matched against results, and no model-written links or commands.
6. **Private stored conversations,** which hold no tool result written by nexovue's code, and expire message by message.
7. **A consent step for chat, of its own,** with a preview of real data, because chat sends log text and F3 does not.
8. **Knowledge search over resolved findings, notes and runbooks:** keyword always. Meaning-based search through pgvector where an embedding model is set, from the chat connection or an optional second one.
9. **Runbooks:** paste or upload Markdown files (several at once, to one or more projects), list, read, edit and delete, in Settings.
10. **Limits:** tokens per question, steps per question, questions per person per day, and one question at a time per person.
11. **An injection suite and a chat suite in the gate,** on every model named as supported.

## Out of scope

| Left out | Why |
|---|---|
| Any write from chat: dashboards, alerts, muting, acknowledging | Breaks the Rule of Two (foundation section 3). This is a real trade-off, as most rivals can write. Revisit only as a person's click on a nexovue-built action, in a later feature |
| Free-form SQL from the model | Crosses the tenant chokepoint, and a scan can harm a server (`traces/queries.py:8-13`) |
| Streaming words as the model writes them | Shows text before the check has seen it |
| Markdown, tables or charts written by the model | A way out (U4). Tables or charts drawn by nexovue from results are also left out for now: results are not stored, and each step already links to the real screen |
| Shared or team conversations | Private is the safer start, and sharing widens who reads poisoned text. **Copy steps** (FR38) hands over the evidence without the model's words |
| Slack or Teams chat | Model words never leave the console (U5). **A real cost:** rivals answer where on-call people already are (Datadog Bits Chat and Sentry Seer Agent both work in Slack), and nexovue makes them open the console. The founder's U5 stands |
| Crawling a wiki or Git, or syncing runbooks | Needs connection and consent work, and widens the poisoning surface. API tokens are read-only, so a CI job cannot push runbooks either. Stated plainly in the setup docs |
| Cross-project answers | A ticket reads one project (D3). Only if real users ask |
| Service descriptions in knowledge | No such field exists. Add them to the index when one does |
| A bundled embedding or chat model | The founder's decision: nexovue runs no AI service for self-hosters |
| `/llm/calls` as a tool | It holds the customer's own prompts, a second disclosure (D24) |
| Fix suggestions from chat | F5 owns that. Chat may say a fix exists, never read its diff (F5-D7) |
| A keyboard chord for the panel | ⌘K is reserved for search (`TopBar.tsx:82`), and browsers own most others (D38) |
| A claim about accuracy, or "injection-proof" | Nothing is measured, and no suite proves a model safe |
| Excluding one service or one attribute key from chat | The project is the unit of opt-out, and a project's masking settings are the place for per-key rules (`projects/service.py:227-250`). The data level (FR39) answers the larger objection. Revisit on a real request |
| **Copy answer**, or sending an answer to admins | Either moves model words out of the person's own conversation (U5, BR14). Copy steps moves nexovue's text instead |

## Who gets what

*Internal. Nothing here is sold until built. Future tense in public.*

| Where | What they get | Note |
|---|---|---|
| Self-hosted | Everything, free, with the customer's own provider | The model bill is theirs |
| Hosted, now | Step 1's MCP gains only. No panel, no runbooks screen, no chunk writing (`ai_available`) | Hosted is the only paid product, so **this feature earns nothing yet.** Buyers will ask at the first sales call |
| Hosted, later | The customer's own key only: foundation founder decision 1, approved 2026-11-02 | |

**Chat versus your own AI over MCP.** After Step 1 a developer with Claude Code gets findings, single traces and fixed ranges through MCP, with no pasting and no chat from us. Self-hosted SigNoz offers the same route today. So the panel must earn its place:

| | Panel | Your own AI over MCP |
|---|---|---|
| Sentence checks and labels set outside the model | Yes (BR5, BR6) | No. nexovue cannot control the far end |
| Notes and runbooks searchable | Yes | No (D46) |
| One consent, one budget, no per-developer setup | Yes | No: each developer sets up, each with their own account |
| Usable by someone with no AI tool | Yes | No |
| Richer or freer answers | Not claimed | Possibly: it can write code, and sees whatever a developer gives it |

**A developer happy with Claude Code may need only Step 1.** This is the main question of the Step 2 decision. Sales says it first.

**It can read, and it cannot read,** generated from the catalog in T8.5. Can: findings, services and their endpoints and trends, dependencies, releases, alerts, checks, errors, logs, traces, metrics, hosts, F3's facts, notes and runbooks. Cannot: `/llm/calls`, other projects, session data, profiles, anything by writing SQL.

**Providers (updated by T6.5, unverified until then).**

| Provider | Chat | Meaning search |
|---|---|---|
| OpenAI-style account | Tool calling to be proved in T2.7a | Yes, on the same connection, unverified until T6.5 |
| Anthropic | As above | No embedding model of its own. Needs the second connection (FR27) |
| Amazon Bedrock | As above | Titan Text Embeddings V2 through InvokeModel, not Converse (D81). One real call in T6.0 |
| Ollama or another local server | As above; the model decides | Ollama documents `/v1/embeddings` (D81). One real call in T6.0 |

Keyword search works on all of them. **Public text says "keyword search always; meaning search where your provider offers embeddings".**

## User requirements

**On-call engineer**

- **UR1.** I can ask a question in plain words on any screen, and the panel already knows my project, window, and the finding or service I am looking at.
- **UR2.** While it works, I see what it is reading, in sentences such as "Reading logs for `checkout`, 14:00 to 14:30". It is never a bare spinner.
- **UR3.** For every answer I can see each tool, its filters and its exact time range, and click through to the same data on the real screen.
- **UR4.** I can ask about an exact past time, such as "yesterday from 14:00 to 14:30".
- **UR5.** I can tell what was counted from what a log line merely says, and from a sentence nexovue could not check.
- **UR6.** If it cannot answer, it says so and says why: no data, data aged out, the provider failed, or a limit was reached.
- **UR7.** I can stop a question that is taking too long.
- **UR8.** I can say an answer was wrong, pick why from a short list, and undo it.

**New developer**

- **UR9.** I can ask how something was fixed before, and get the notes and runbooks it came from, or "no record of this in what is kept", with how long cases are kept.
- **UR10.** I see starter questions that fit the page I am on.
- **UR18.** I can copy the steps of an answer, with links a colleague can open and see the same range, without any AI wording in them.
- **UR19.** If I close the panel or reload, I can see my question is still running, and I am told when the answer is ready.

**Everyone who asks**

- **UR11.** My conversations are mine: no admin or support person reads them, and I can delete them whenever I choose.
- **UR12.** I am told when I reach my daily limit, and when it resets.

**Admin and owner**

- **UR13.** As an owner, before I agree, I see a real sample of what chat would send, and a complete list of everything that can be sent, including questions, conversation history and text sent for embedding.
- **UR14.** As an admin, I can set the daily question limit, see how many questions each person asked, and see what was read and sent where (person, time, project, tool, range, row count, provider), without seeing any question or search phrase.
- **UR15.** As an admin or a member, I can add, edit and delete runbooks, and see how old each is.
- **UR16.** I can see plainly that the AI switch does not affect the MCP server, which my own AI tools reach with a token we made.

**A customer's own AI, through MCP**

- **UR17.** My AI can read findings, one trace, one error group, and any fixed time range, and is told which fields hold untrusted text.

## Business rules

- **BR1. Off by default.** Chat answers nothing until AI is on (F3), chat's own consent is given, and the project is not opted out.
- **BR2. Read-only by structure.** No tool writes. A ticket is refused on every method but GET, and on every route outside the catalog.
- **BR3. One project per question.** A question reads one project, the one chosen when the conversation began. Another project answers exactly as one that does not exist does.
- **BR4. Nobody reads beyond their reach.** A person's removal, a role change, a password reset, or AI being switched off stops their question at its next read.
- **BR5. Every claim cites.** A sentence stating a fact must cite a step that ran in this question. Otherwise it is removed, and the answer says how many were removed. A statement of what was *not* checked goes in a separate *Not verified* list, never silently.
- **BR6. nexovue decides the label; the model only chooses what it cites, so a citation alone earns nothing.** A sentence gets a fact label (`Counted from telemetry`, or F3's signed words) only if every number and name in it matches the cited rows, it has no cause, blame or command words, and it cites no free text. Otherwise it reads as telemetry text, team notes, or *the model's wording*. **A label says which source was cited and what matched. It never says the sentence is true.**
- **BR7. No model-written links, addresses or commands.** A link is built by nexovue. Anything the model shows from a log or runbook is a quote by reference, filled in by nexovue as plain text. Only a runbook quote may be labelled as a command. A log or note quote that looks like a command or link is folded under a fixed warning. This covers every model-controlled string that can reach the screen: sentences, step arguments, and the reasons shown for abstaining.
- **BR8. Model words never leave the console** (U5). None appears in an alert, webhook, ticket or pull request. Answers go into no record but the person's own conversation.
- **BR9. Stored words earn no trust.** Old answers and old tool results are never replayed as fact. A model's own words are never indexed.
- **BR10. Only what people wrote and code-composed facts are indexed.** Raw logs, spans and model words are not. A fact line may hold a name only if it passes the strict pattern `[A-Za-z0-9._-]{1,80}`, F3's. Otherwise it says "a service".
- **BR11. A count comes from an exact source.** Counts come from rollups. Traces are sampled and lists are cut, so a list is never a count.
- **BR12. No row is not "did not happen".** An empty result is said as "no matching rows in what was stored", with what was searched.
- **BR13. A person's limit is not a price.** The daily question limit protects the customer's budget and must never be sold as a seat (`vision.md:152`).
- **BR14. Conversations are the person's.** Admins see counts and a read record (tool, range, row count), not contents. Support views are refused. Removal or account deletion deletes them. They are in the person's data export.
- **BR15. A message does not outlive its data.** Each message goes when it is older than its project's derived retention, even in a conversation still in use. A conversation goes when its last message goes.
- **BR16. Knowledge goes with its source.** Deleting a note, finding or runbook deletes its chunks in the same transaction, at the database's level. Rows of a deleted project go at the next sweep.
- **BR17. AI off changes nothing else.** Findings, alerts and every non-AI screen behave the same with chat on, off or broken, and on an install with no `vector` extension.
- **BR18. No promise beyond the facts.** Public text says what the system does: "Chat has no tool that can write. nexovue checks each answer and removes model-written links and commands; that check can miss things. Log text goes to the AI provider you choose." It must never say "no way out", "injection-proof", "proved safe", or "your logs stay on your server" to someone using a cloud provider. It must not promise meaning-based search without naming the setups where it was measured, or "cheaper than" anyone. The customer-facing name is **Ask** (D52).

## Functional requirements

### Asking

- **FR1.** The panel opens from a TopBar button on every signed-in screen, for those holding `ASK_ASSISTANT`. On a wide window it pushes the screen aside, and the screen's layout responds to the width it actually has. On a narrow one it fills the window. Before anything is typed, it must show whether this person can ask here, and if not, why (`GET /assistant/availability`).
- **FR2.** A new conversation is bound to the selected project. If the TopBar's project differs from the open conversation's, the composer is replaced by "This conversation is about {project}. New question in {selected}". **Ask about this finding** always starts a new conversation, unless a question is in flight, when it shows that question with Stop. Context clears when its page is left.
- **FR3.** The question carries its context, checked by the API: the window shown; the person's time zone (`users.timezone`, else the browser's); and, when present, the finding id or service name of the page. A finding id from another project is refused in a sentence. When the window is pinned, the composer says "Asking about 14:00-14:30", with **Use live window**. Each question shows the context it was sent with.
- **FR4.** At most one question per person may be in flight. A second is refused with "still answering the last one", and offers Stop. A question whose job has expired no longer counts as in flight.
- **FR5.** Questions are capped at 2,000 characters. The API refuses longer ones with a sentence saying so.
- **FR6.** A question is answered in the context of its conversation: earlier questions and each earlier answer's sentences, all fenced as free text, including the current question, since people paste logs. Earlier tool results are not replayed. History is capped at 4,000 tokens (guess), newest turns first; the answer says when older turns were dropped, and expired messages are never included.

### Reading

- **FR7.** The tools are a fixed list, held by a test to the read surface (`test_read_surface_structure.py`):

  | Tool | What it reads | Basis |
  |---|---|---|
| `list_findings`, `get_finding` | Findings, as a token sees them: no person names or written reasons, and **no `title`** (U1) | counted, with names fenced |
| `list_services`, `get_service`, `get_service_detection` | Rollup health and detection state | counted, with names fenced |
| `get_service_map` | Dependencies. A peer may be named from a dialled host | counted, with names fenced |
| `list_service_endpoints`, `get_service_trend` | Which endpoint moved, and when. Routes join the read surface (D59) | counted, with names fenced |
| `list_incidents`, `get_incident`, `list_checks` | Alert incidents, checks. **No `detail`, no notification reason or channel name; a check's type and URL host only,** since a check's config can hold literal secrets | counted, with names fenced |
| `list_metrics`, `get_metric_histogram`, `list_hosts`, `get_host_trend` | Metrics, hosts, disk and memory. Host routes join the read surface (D59) | counted, with names fenced |
| `list_error_groups`, `get_error_group` | Errors | per field: counts counted, messages telemetry text |
  | `search_logs` | Log records | telemetry text |
  | `list_traces`, `get_trace` | Sampled traces, never counted | telemetry text |
| `get_finding_explanation` | F3's facts and likely cause, each with its own basis. F3's model wording is not sent | per row, as F3 labels it |
| `list_releases`, `get_release_comparison` | F28's `GET /releases` (with `from` and `to`) and `GET /releases/{id}/comparison`. No sender text. Must keep "before, not because" | per row, by F28's `basis` |
| `search_knowledge` | Notes, resolved findings, runbooks | per row: team's notes, or counted for a fact line |
| `get_finding_timeline` (Step 7) | F22's event kinds and times, as a token reads them. Answers cannot say who holds a case: "This answer cannot show who holds the case. See the case page." | counted |

- **FR8.** Tool arguments are validated against each tool's schema by nexovue before any read. The project is never a model argument. A value in a path slot must hold no `/`, `\`, `%`, control character, and must not be `.` or `..`. Encoding alone is not enough, because the server decodes the path before routing.
- **FR9.** A time range is at most 7 days per read, as MCP's is today (`tools.py:45`). The model is told the current time, the project's time zone and the person's, and reads typed times in the person's zone. Each windowed step records the exact range it read; a step with no time filter says "latest N, no time filter".
- **FR10.** A question runs at most 8 steps (starting guess). Past that it answers with what it has, and says "Stopped after 8 reads. This answer uses only the reads listed".
- **FR11.** A tool result sent to the model is cut to a smaller cap than MCP's: 8 KiB (guess), and it says when it cut. Older results are shortened to a one-line summary for later model calls.

### Answering

- **FR12.** The worker's output is parsed against a fixed shape: sentences, each with its citations and quote references; a short *Not verified* list; or an abstention, with a reason from a closed list (no data, data aged out, reads failed, outside what it can read, evidence too thin). nexovue writes the words of every reason.
- **FR13.** A sentence with no valid citation is removed, and the answer shows "One sentence was removed because it cited nothing it had read". An answer left with none is withheld, and the steps stay visible.
- **FR14.** Any answer holding a URL, email address, Markdown or HTML link or image, or shell-like command is sent back once with the reason. If it fails again it is withheld. A bare path such as `/api/checkout` is allowed, and so is a host-like name or exception type that appears exactly in a cited row's name field and passes `[A-Za-z0-9._-]{1,80}` with an optional port. A long exact run copied from a tool result (40 characters, guess) is sent back with "quote by reference". The same checks run on free-text tool arguments before a step is stored or shown; one that fails still runs, and shows "search phrase hidden".
- **FR15.** A quote is a reference to a step, a row and a field. nexovue reads the value and shows it as plain, non-clickable text, with control and direction characters shown as visible escapes. Up to 5 quotes per answer, 500 characters each (guess). A cut quote says so and links to its source, and a cut runbook quote offers no copy. A log or note quote that looks like a command or address is folded under "Text from a log, written by anyone who can reach your service. Do not run it". A sentence telling the reader to run what is quoted is removed unless the quote is a runbook's. A runbook quote offers **Copy**, and its age warning sits beside it past 180 days (guess).
- **FR16.** A figure in an answer that cannot be matched against the results it cites is flagged beside it: "Not found in what it read. Check this number." It is not refused at first, because the rate of false flags is unknown (D10). The flag is stored, so it survives a reopen.
- **FR17.** Labels, in one wording table (design, *Words*): **Counted from telemetry**; F3's signed words; **Your telemetry says this; nexovue has not measured it**; **From your team's notes, {age}**; **The model's wording: it read these steps, and this sentence was not checked against them**. A sentence with a cause word (F3's FR37 list) on any citation also shows "nexovue has not measured a cause", and can never appear as F3's *likely cause*.
- **FR18.** Each step links to its real screen with the project, filters and exact range **in the address** (F3's `?from=&to=&project=`), so a copied link shows a colleague the same data, and the person's own window on other screens does not move. A design table names each tool's destination. Where a screen cannot apply a filter the step says so, and where no screen exists it shows no link and says why.
- **FR38.** **Copy steps** copies nexovue-built text and links for a question's steps, with no model words.

### Limits and control

- **FR19.** A question reserves 60,000 tokens (guess) from the organization's month before its first model call, and returns what it does not use. When less than that is left below 80% of the month's limit, the question is refused with the reset date. The last 20% stays for critical findings' explanations, matching F3's rule for warnings (F3 AC55, D50). **Before each model call the worker estimates the call (bytes ÷ 3, F3's estimate, plus the output cap). If that would pass what is left of the reservation, the question goes to its partial ending.** So usage may pass the limit only by the one overshoot F3 BR13 allows. A question that dies, or a call that times out, keeps what that spend may have cost (F3 BR13). Admins see a warning at 80% of chat's ceiling.
- **FR20.** A person may ask 50 questions a day by default (guess). An admin may set it from 1 to 1,000.
- **FR21.** Stop ends a question at once for the reader: its ticket is refused, no new step runs, what ran stays visible, and `stopped` can never be overwritten. A model call already in progress cannot be interrupted; it may run to its time limit, its result is thrown away, and its cost is counted.
- **FR22.** A question has one clock: a deadline set when it is asked, derived from the connection's time per call (`call_seconds`, 30 to 300): the larger of 5 minutes and 3 × `call_seconds` (guesses). No model call starts unless the time left covers `call_seconds`, with one more kept back for the final answer. There is no separate environment setting.
- **FR23.** A failed question is never re-run on its own, as it would spend money twice. The person may ask again.

### Consent and settings

- **FR24.** Chat's consent is the owner's, separate from F3's. An admin can read the preview and sees "Waiting for an owner to agree: {names}". The preview shows one real sample per kind of free text, as it would be sent, a complete list of every kind that can be sent (generated from the catalog's `keep`), the provider and address, the project's real masking state, and how many runbooks and notes would be sent at once for embedding. With no data yet it shows a labelled sample, and consent is allowed. It states the chat-safe selling line (foundation founder decision 2, approved 2026-11-02).
- **FR25.** Consent is tied to F3's consent fingerprint (wire format, base URL, region, key id), to a chat disclosure version raised when a release widens what is sent, and to the data level. The agreeing request carries the fingerprint the owner read. A change to any of these ends chat's consent. A new model does not, following F3 A9: F3 turns AI off until a fresh test passes, and tells owners. Before every provider call, chat or embedding, the worker rechecks that AI is on, the project is not opted out, and these still match.
- **FR26.** `/settings/ai` gains a Chat row in F3's checklist, after its consent step, and a Chat section: on or off, consent, data level, daily limit, per-person counts, monthly outcome counts, the read record, embedding model and progress, and whether the current model is `tested` or `not tested` by the bed. It must state that the AI switch does not affect MCP.
- **FR27.** `assistant_settings.embedding_model` names the model. Null means keyword search only, and the settings section and each answer using it say so. An owner may add a second connection only for embeddings, with its own address, key, consent line and preview (D15, changed).
- **FR39.** The owner chooses a data level. *Facts only*: no log, trace or error text, no notes or runbooks; numbers, closed lists and names passing the strict pattern only. *Facts and text*: everything in FR7. Raising the level needs consent again.
- **FR40.** Before any text goes to a provider, the worker masks credential forms (Bearer and Basic values, URL credentials, `password=`-style pairs) in all free text, and email addresses unless an admin turns that off. This is a net with holes, not a guarantee, and the preview shows its result.

### Knowledge

- **FR28.** The index holds: notes written by people on a case; a resolved finding, composed by nexovue from facts only (service if it passes the strict pattern, signal, times, closed reason), at most one per service and signal per day; and runbooks. A project finding names up to 5 of its services. A finding title is never used. A fact line whose finding later becomes a false alarm is dropped, and search excludes it at once.
- **FR29.** Search combines keyword and meaning-based results when embeddings exist. Otherwise it uses keywords alone. It reads only the question's project. A person's note must rank above fact lines for the same service in a ranking test.
- **FR30.** One note is one chunk. A runbook splits on headings, with a size cap. Retrieved text is free text and always fenced.
- **FR31.** Changing `embedding_model` re-embeds in the background. Keyword search answers until that ends. Vectors from different models are never compared, including a question's own vector, which records its model.
- **FR32.** A runbook has a title, optional service, text, author and date. Its age shows beside any answer citing it. A runbook is at most 32 KiB, and a project holds at most 200, and a runbook makes at most 18 chunks however many headings it has, so runbooks fit the 5,000-chunk cap (3,600 chunks at most; arithmetic on guesses). A note always saves: at the cap its chunk is skipped after the oldest fact lines are evicted, and the settings screen counts what is not searchable. Saving warns when text looks like a credential, and needs a second click. Viewers read runbooks; support views see the list and no bodies.
- **FR41.** A resolved case offers **Save as runbook**, a person's click that copies a note and its fact line into a runbook that outlives the finding, labelled as person-written (Step 7).
- **FR42.** Knowledge search's empty answer reads "No record of this in what is kept. Cases are kept for {N} days after they end", so absence does not read as "it never happened".

### MCP

- **FR33.** Every tool result names which fields hold untrusted text, and the server's instructions say to treat them as data.
- **FR34.** Windowed tools accept a fixed range or a number of hours, never both.
- **FR35.** New tools: `list_findings`, `get_finding`, `get_trace`, `get_error_group`, `get_incident`, `get_service_map`, `get_service_detection`. A malformed `project_id` gets a sentence, not a plain error. `list_checks` must stop returning a check's whole config.
- **FR36.** The service map joins the read surface, openly, as tokens gain it too (D21).
- **FR37.** Knowledge search is not offered through MCP or to tokens in the first version (D46).

### Conversation states

| State | Meaning | What the person sees |
|---|---|---|
| `queued` | Saved; waiting for a worker | "Waiting for a worker", with elapsed seconds. Past 20 seconds, "Taking longer than usual", and a **Check it yourself** link |
| `running` | The loop is running | Live steps, a nexovue-written phase line ("Asking your AI provider, 25 s", "Checking the answer"), and Stop |
| `answered` | Passed the checks | Answer, labels, steps |
| `abstained` | Model said not enough evidence | nexovue's sentence for the reason from the closed list, and the steps. A good outcome |
| `withheld` | Output failed the checks twice, or no sentence survived | "The answer could not be checked, so it is not shown", and the steps |
| `partial` | Reached the step limit, the token reservation or the deadline, and answered with what it had | Answer, plus "Stopped after 8 reads" or "after 5 minutes", and "This answer uses only the reads listed" |
| `stopped` | The person pressed Stop | Steps that ran |
| `refused` | Their access or AI settings changed during the question | "Your access or AI settings changed during this question", and the steps that ran |
| `failed` | Provider down or bad key | What failed, **Check it yourself**, and **Ask again**, which refills the box and does not send. The reason goes to admins |
| `over_limit` | Chat's share of the month is used | "Chat has paused for this month to keep room for critical finding explanations. Resets {date}. An admin can raise the limit." |
| `unavailable` | The worker died, its job expired unclaimed, or the deadline passed | "Not available right now", **Check it yourself**, and non-AI screens still work. A queued question reads this as soon as its job expires, about 2 minutes, not at the deadline |

## Non-functional requirements

- **NFR1. No effect on the rest.** A question must not hold back an alert, a probe, or a detection job. Chat's work runs in its own queue, in the AI role (trap 7). Its reads share ClickHouse with detection, so AC80 tests this under load.
- **NFR2. Reachable under pressure.** A burst of F3 explanations must not hold chat's slots, and the reverse.
- **NFR3. First news at once.** A nexovue-written phase line appears as soon as the state changes. A first step should appear within about 5 seconds on a working cloud provider; a local model may take a call's length (30 seconds, a guess). **Targets, not measurements;** the bed reports them, against AC104. The answer's total time depends on the provider, and is reported rather than promised.
- **NFR4. Memory.** The AI role's ceiling rises from the foundation's 384 MB guess to 576 MB (guess), which must be measured, and `docs/self-hosting/sizing.md` updated.
- **NFR5. Postgres stays within 1 GB.** No vector index in the first version. Knowledge at the caps fits the arithmetic in the design, which is to be measured.
- **NFR6. Accessible.** The panel works by keyboard alone, announces new steps and the answer to screen readers, and is usable on a phone.

## Error cases

| Case | What the person sees | Can they recover | Told apart from a normal empty result? |
|---|---|---|---|
| AI off | Admin: how to turn it on. Member: "Ask an admin to turn this on", shown before typing | Admin can | Yes |
| Chat consent not given | Owner: the consent step. Others: "Chat is waiting for {owner name} to agree" | Owner can | Yes |
| Project opted out | "AI is off for this project" | Choose another project, or an admin changes it | Yes |
| Hosted service, AI not offered | No panel (`ai_available`) | No | Not applicable |
| AI container stopped | `unavailable`, when the job expires | When it returns | Yes |
| Provider down, bad key | `failed`: "Your AI provider did not answer", or "The key was refused". Admins see which | Retry; admin fixes the key | Yes |
| Provider rate limit | `failed`, naming the provider's limit | Retry later | Yes |
| Chat's share of the month gone | `over_limit`, with the reset date and who can raise it | Admin raises the limit | Yes |
| Daily limit reached | "You have asked 50 questions today. Resets at {time in your zone}. An admin can raise this." Beside the box, with the draft kept. Not a 429 | Next day, or admin raises | Yes |
| No telemetry in this project yet | A panel state with a setup link, and no starters, before anyone spends tokens | Send data | Yes |
| Service still learning its normal, or stopped reporting | nexovue-written fact in the answer; never "no problem" | No | Yes |
| Install has no `vector` extension | Keyword search, and the settings section says why | Operator | Yes |
| Tool refused by rate limit | The step says "the read was refused for now"; the answer says what it lacks | Ask again | Yes |
| Data aged out | "The data before {date} is no longer kept", written by code from the range and the retention of what that tool reads (raw or rollup), before any read | No | Yes, never as "nothing happened" |
| No rows | "No matching rows in what was stored", with filters and range | Change the question | Yes |
| Telemetry store down | The step failed, and the answer says so | Ask again | Yes |
| Quote's log aged out | "This log line is no longer available." A failed read is apart: "Could not read this now", with Retry | No, or retry | Yes |
| Knowledge has no match | FR42's sentence | Add a runbook | Yes |
| Removed mid-question | Their session already ends, the ticket is refused at the next read, and the question ends `refused` | No | Not applicable |
| Support view | No panel, and conversation routes refuse, in one sentence | No | Not applicable |
| Deleted conversation | Gone from the list; an open link gives the ordinary 404. Refused while its question runs: "Stop this question first" | Yes | Not applicable |
| Panel poll refused or failing | A 403 renders inside the panel and stops polling. After 3 failures: "Lost contact; this question may still be running", with Retry | Retry | Yes |

## Acceptance criteria

**Off, and consent**

1. **AC1.** With AI off, the question route must refuse, and no provider call, ticket or job must be created.
2. **AC2.** With AI on and no chat consent, asking must refuse. The preview must show a real sample per free-text kind as it would be sent, the complete list of what can be sent, including questions, history and embedding text, the project's real masking state and the bulk embedding count, and the chat-safe statement. With no logs it must show a labelled sample and still allow consent. A non-owner admin must read it. Agreeing must need `CONSENT_AI_DISCLOSURE`, carry the fingerprint read, get 409 on a mismatch, and write an audit row.
3. **AC3.** A changed address, wire format, region or key, a change of address and back, and a raised disclosure version must each end chat's consent, and changing only the model must not.
4. **AC4.** With the AI container stopped, a test must run detection and alerts, and findings and notifications must equal those of a run with AI on, apart from explanation columns, ids and times, listed in the test. A named test must open each route in `route-manifest.test.ts` with chat broken. A question must end `unavailable`.
5. **AC5.** A project opted out must refuse a question, in a sentence.

**Who may ask, and what it reaches**

6. **AC6.** Owner, admin and member must be able to ask. A viewer, an API token and a support view must be refused, each with a test.
7. **AC7.** A ticket must be refused on any method but GET, on any non-HTTP connection, and on any handler outside the catalog, including `GET /llm/calls`, `GET /findings/stats` and every `CurrentIdentity` route, each with a clear sentence and no data.
8. **AC8.** A ticket for project A asking for project B must get a response byte-identical to a project that does not exist.
9. **AC9.** A live test across two organizations must show a question in one cannot read the other, by any argument the model can supply.
10. **AC10.** Path tricks in every tool argument, such as `../../audit` and `x/detection` (which the server would decode into another tool's route), must be refused before any read. The test must run through real uvicorn.
11. **AC11.** A ticket must be refused once its question is final or past its deadline, after Stop, after the conversation is gone, after the person is removed, after a role change away from `ASK_ASSISTANT`, after a password reset, after AI is turned off, after the project is opted out or deleted, after chat's consent ends, and after the organization is deleted. A test must cover each.
12. **AC12.** `get_finding` through a ticket must not contain `acknowledged_by_name`, `feedback_reason` or `new_normal`, and a catalog test built from each route's response model must hold that every string or dict field in `keep` is declared as a closed value, an id, a time, a name or free text. `findings.title`, deploy `metadata` and check `config` must not be in any `keep`.
13. **AC13.** Tool reads through tickets must count against the asking person and organization: two people's reads must fill two allowances, and an invented ticket must not spend anyone's.
14. **AC14.** A test must hold the catalog to `THE_SURFACE`, so a tool on a route outside the read surface fails the build. `search_knowledge` is the one named exception.

**Answers**

15. **AC15.** A hostile test provider that tries every tool, path trick and kind of link and command must fail three ways. **(a) Offline, in `just check`,** `apps/scheduler/tests/test_assistant_hostile.py`: only catalog GETs are issued, and the stored answer holds no link, image or model-written command. **(b) Live, on every pull request,** `apps/api/tests/test_assistant_hostile_live.py`: a table-by-table row diff allows only `assistant_steps`, named `assistant_messages` columns, the read record, the count tables, `ai_runs` and `ai_usage_monthly`; anything else fails, and ClickHouse row counts must not change. **(c) Web:** the hostile output, rendered, has no link, image or executed markup, in sentences, step rows, reasons and the generated `/logs` address.
16. **AC16.** A sentence with no citation, or one citing a step that did not run, must be removed, the answer must show how many, after a reopen too, and an answer with none left must end `withheld`, with its steps visible.
17. **AC17.** Answers must render as plain text. A test must show that Markdown and HTML from the model reach the screen as literal characters, and `dangerouslySetInnerHTML` must not appear in the assistant feature.
18. **AC18.** An answer quoting a real `/api/checkout` path must pass; so must `(api.stripe.com)`, `redis.prod.local:6379` and `java.net.ConnectException` when in a cited name field, and "Docker restarted at 14:02". One with `https://evil/?d=x`, `![](…)`, an email address or `curl … | sh` in the model's words must not. A host-like word in no cited row must not.
19. **AC19.** A quote must show the stored value as plain, non-clickable text, and "no longer available" once the record has aged out or been erased. A log with a timestamp finer than a millisecond must still be found, and a failed read must not read as gone.
20. **AC20.** A figure not in the cited results must show the flag. A figure rounded as the validator allows must not.
21. **AC21.** Labels must follow BR6. A model claiming a fact label on a log-only sentence must still show the telemetry-text label. Each mixed tool (`list_error_groups`, `search_knowledge`, `get_finding_explanation`, `list_releases`) must label by field or row.
22. **AC22.** Each windowed step must show its tool, filters, exact range, row count and whether it was cut, and its link must carry the project in the address. Where the design's destination table carries a range, the link must carry it too, and opening the link in a fresh session must show the same project and range. Where that table says the range is not applied, the step must show that sentence. Opening any link must not change the person's stored window. A windowless step must say "latest N, no time filter". A test must hold every catalog tool to the design's destination table.
23. **AC23.** A question for "how many" must take its count from a rollup-backed tool in the chat suite's known-answer cases, and must not count `list_traces` rows.
24. **AC24.** An empty result must read "no matching rows in what was stored". A range past retention, on a short-retention project, must end the step "aged out" in code, with no read and no model involved. On a Free-plan project, a range 10 days old on a rollup tool, inside derived retention and past raw, must be read and must not say "no longer kept", while the same range on `search_logs` must end aged out. No answer may present no telemetry, a service still learning, or a service that stopped reporting as "no problem".

**Limits and failures**

25. **AC25.** Parallel questions, run against real Postgres at a nearly empty month, must not pass the limit, except by the one overshoot F3's AC53 and BR13 allow, which the test reports.
26. **AC26.** A second question while one is in flight must get 409, and asking must work once the first ends or its deadline passes.
27. **AC27.** A worker child really killed at the hard limit, and one killed between two calls, must leave the question `unavailable`, its remaining reservation moved to `used` and flagged estimated, `ai_usage_monthly.reserved_tokens` back to its earlier value, and the person able to ask again. The test must read `ai_usage_monthly` before and after.
28. **AC28.** Stop must refuse the ticket at once, start no new step, and return the unused reservation once the worker lets go. Stop during a hanging provider call must leave `stopped`, never overwritten by `answered`. A second question then asked must still respect the spending and slot limits.
29. **AC29.** A burst filling the `insight` queue must not stop a question being claimed, and the reverse. With a slow provider and `insight` full, a queued question must still be claimed before it expires.
30. **AC30.** Every row of *Error cases* must have a backend or live test that produces the state, and a render test that shows the stated sentence, listed row by row in `apps/api/tests/test_assistant_error_cases.py`.

**Privacy**

31. **AC31.** Conversations must be readable only by the person who owns them, including not by an owner, an admin or a support view. Each must have a test.
32. **AC32.** nexovue's code must store no tool result in `assistant_messages` and `assistant_steps`. A live test with the hostile provider must plant a log string, and find no exact run of 40 characters or more of it in those tables, except where the question itself typed it. **A model's paraphrase is outside this, and the consent says so.**
33. **AC33.** Removing a member, and a person deleting their account, must delete their conversations and tickets.
34. **AC34.** The personal data export must include the person's conversations, free-text step arguments as stored, runbooks they wrote, and their "this was wrong" marks, with a truncation flag, and an organization's deletion must remove all of it (`test_deletion_live.py`).
35. **AC35.** A message older than its project's derived retention must be deleted by the expiry sweep, even in a conversation kept active past retention, must not appear in later history, and the conversation must go with its last message. Rows of a deleted project must go at the next sweep. A failing F4 row in `_EXPIRED` must not stop the rows after it.
36. **AC36.** `ai_runs` must hold field names, never contents, for chat calls.
37. **AC37.** Admins must see per-person question counts, with no question text. Deleting a conversation, or its expiry, must not lower the count, and must not reset the daily limit.

**Knowledge**

38. **AC38.** Deleting a runbook, a note or a finding must delete its chunks in the same transaction. A live test must check each.
39. **AC39.** Model words, raw titles, logs, spans, and any name failing the strict pattern must never appear in `knowledge_chunks`, held by a test of its writers.
40. **AC40.** A planted instruction in a note, indexed, then retrieved by a later question, must not cause a write or a way out, and the sentence must carry the notes label.
41. **AC41.** With no embeddings, search must work by keywords, and say so. With two models' vectors stored, search must not compare across them.
42. **AC42.** Knowledge search must never return another project's chunk, whatever the arguments.
43. **AC43.** On an account that cannot create `vector`, `setup` must finish with a clear warning, detection and alerts must start, and the settings section must say search is keyword only. Steps 2 to 5 must have no dependency on the extension.

**MCP**

44. **AC44.** Each tool result must name its untrusted fields. A test must cover every tool that returns free text.
45. **AC45.** A fixed range must work on every windowed tool, and giving both forms must be refused in a sentence.
46. **AC46.** Each new tool must obey a token's project scope, and `get_finding` must leave out person fields.
47. **AC47.** The MCP injection suite must run in the gate, and `docs/product/setup/api.md` must state that nexovue cannot control a customer's own AI on the far end.

**Screen**

48. **AC48.** Every state in *Conversation states* must render, each with a test. With the panel open at 1024, 1280 and 1440 px, every route in the manifest must have no horizontal scroll outside a table, diagram or code block. The panel must push rather than cover at wide width. At 375 px: a 44 px Ask button fits the TopBar, a citation click closes the panel and offers **Back to answer**, main and sidebar are `inert` behind it, offline shows inside it, Enter makes a new line on touch, and quotes and filters wrap. On real iOS and Android the composer stays visible with the keyboard open (manual, recorded).
49. **AC49.** Starter questions must match the design's table for the page. A click must fill the box and send nothing. A question from "Ask about this finding" must carry that finding's id and start a new conversation, and context must clear when the page is left.
50. **AC50.** Opening must move focus to the box, closing must return it to the opener, and Escape must close without losing the draft. When Stop ends, focus must go to the state line. One always-mounted `role=status` must announce only changes, and everything must work by keyboard. Playwright proves these. It cannot hear a screen reader, so Design QA records a manual VoiceOver and NVDA pass.

**The gate**

51. **AC51.** The evaluation bed's chat run and injection run must report, on every option named as supported, over 3 trials per payload and place with the statistical bound beside each result: right number, right range, refusal across tenants, instruction followed, way out reached, false cause shown under a fact label, abstains when it should, figure false-flag rate, validator false-refusal rate, and harmless controls not blocked. **Hard gates: no write, no way out, no unlabelled false cause, and abstains on at least 90% of "not enough evidence" trials (a guess, fixed before any run; the founder may move it only with a recorded decision).** Accuracy is reported, and the founder sets the bar after a first tune run and before the report run, as F3 does (F3 AC62, A83) (*Founder decisions needed*, 2); no option is called supported before it is recorded, and a bar is never set or moved after its report run is seen. A partial run cannot call an option supported. Scorers must have tests, including a control: the bed run against `HostileProvider` must score instruction-followed at 100% and still show zero ways out. The report must repeat that the demo is not real traffic.
52. **AC52.** The cost run must replace the guesses in FR10, FR11, FR19 and FR20, and the suggested monthly limit, with measured figures before chat is called supported.

**Added by review: ticket and time**

53. **AC53.** Every catalog tool, called with a live ticket and valid arguments, must return 200 in the expected shape, and each must give the byte-identical 404 for an out-of-scope project. Parametrized from the catalog.
54. **AC54.** With the worker absent, a question must read `unavailable`, and free the in-flight index, by `queue_expires_at`. Every worker state write must be compare-and-set, including queued to running. A job claimed late must not run past `deadline_at`.
55. **AC55.** The worker must start no model call without `call_seconds` left, plus one more kept back for the final answer.

**Added by review: spending**

56. **AC56.** A 50-turn conversation and a hostile provider returning its maximum output must end `partial`, and recorded use must stay within the 60,000 reservation, apart from one overshoot.
57. **AC57.** With less than 60,000 tokens left below 80% of the month's limit, a question must be refused, and a critical explanation must still reserve. At a limit of 100 million and 79.9% used, 100,000 tokens remain below 80%, and a question must reserve.
58. **AC58.** A provider timeout must keep that call's estimate as used, flagged estimated, and return the rest.
59. **AC59.** Deleting a conversation with a question in flight must get 409. A member removed during a question must have the remainder settled in the same transaction, and the worker must treat "message gone" as a normal stop.
60. **AC60.** Before each provider call, a changed fingerprint, an opt-out, or AI turned off must end the question `refused` with no call. A live test must change each during a running question.

**Added by review: safety**

61. **AC61.** The hostile provider reading a planted log, writing a false cause and citing only a counted step, must not get a fact label. A deploy sentence with a cause word must show "nexovue has not measured a cause". A sentence with no number or name must show the model's-wording label.
62. **AC62.** Every way out placed in every tool argument must not reach a step row or `/logs` address. Closed values show as sent. A free-text phrase failing the checks must show "search phrase hidden", and must not be stored.
63. **AC63.** A command or URL planted in a log and quoted must be folded under the warning. "Run the command below" must be removed unless the quote is a runbook's. Direction and invisible characters must not reach the screen as such. A note quote must be labelled "A teammate wrote this".
64. **AC64.** Questions, current and past, must reach the provider in `Untrusted` parts. A pasted payload must be in the bed.
65. **AC65.** The API must not log tool arguments: a planted search phrase must not appear in its log output, including uvicorn's access line.
66. **AC66.** From inside the running `scheduler-insight`, on the merged layout, the API must be reachable, and Kafka, ClickHouse and the object store must not (F3 AC42).
67. **AC67.** Grants must be proved by running the worker's real statements as `nexovue_insight`, as well as by refused writes. Row-level security must hide finished `assistant_messages` and their steps from that role, and a direct final write by it must be refused. These real statements must each succeed: the final state after an answer; a refused reservation (`over_limit`, no reservation ever taken); a question's last call counted after Stop, bringing its reservation to 0; the sweep on a stopped row, a dead row and an expired `queued` row; the aged-out read of `project_retention`; and the monthly count's upsert, twice. A second finish must change nothing, and a finished answer must not be rewritable through the function. The role must claim, retry and finish an `assistant` job only through F3's functions, and claiming one queue must not return the other's job.
68. **AC68.** At data level *facts only*, no planted log string, note or runbook text may reach the fake provider, and tools returning them must not be offered.
69. **AC69.** A planted password in a runbook, a note, a question or a log body must not reach the fake provider in a form the credential mask covers, and saving a runbook holding one must warn. An email must not reach it unless the setting is off.
70. **AC70.** A chunk in an opted-out project, or a deleted project or organization, must see no `embed` call, and opting out must null its stored vectors.
71. **AC71.** Each act must write its audit row, without text: `chat.turned_on`, `chat.turned_off`, `chat.limit_changed`, `chat.consent_agreed`, `chat.consent_ended`, `chat.data_level_changed`, `runbook.created`, `runbook.updated`, `runbook.deleted`.
72. **AC72.** The read record must show person, time, project, tool, range, row count and provider, must hold no question or filter text, must survive conversation deletion, and must lose the person on removal.

**Added by review: answers on screen**

73. **AC73.** A panel that throws, or a poll answered 403, must leave the routed screen mounted and usable. A 403 must render inside the panel with `Refused` and stop polling.
74. **AC74.** `GET /assistant/availability` must return `ready`, `ai_off`, `chat_off`, `no_consent`, `opted_out` or `no_data` to anyone holding `ASK_ASSISTANT`, with no provider address, with an authorization test.
75. **AC75.** A polling assistant query must not change `RefreshControl`'s displayed age or fetching state.
76. **AC76.** A person in one zone on a project in another, 5.5 hours apart, asking about "yesterday at 14:00", must get the right range, in a known-answer case, and each step must show the person's zone, with the project's beside it when they differ.
77. **AC77.** A reopened answer must show each sentence's stored label and flagged figure, and the *Not verified* list, with nothing recomputed from results. A reason must come from the closed list.
78. **AC78.** While a question runs with the panel closed, polling must continue, and the button must read "Answering…", then "Answer ready", with a status announcement. A reload must resume the in-flight question and show Stop.
79. **AC79.** Each POST refusal must have its own problem code outside 429, render beside the box with the draft kept, and show the exact sentence of *Error cases*.

**Added by review: not disturbing the rest**

80. **AC80.** With 4 hostile questions looping 7-day `search_logs` and `list_traces` reads on the fault bed's data, every minute's detection project jobs must finish within the 60-second cycle (`DETECTION_SWEEP_SECONDS`), and an alert must be no more than 60 seconds later than in a chat-idle run (a guess, fixed before any run). Findings and notifications must match a chat-idle run, on the columns the test lists.
81. **AC81.** A structure test must hold that only conversation routes read `assistant_messages.answer`, and that `detection`, `alerts` and `findings` never reference it (BR8).
82. **AC82.** With the knowledge sweep at the chunk cap, detection and expiry must not be delayed, and `findings` deleted by the sweep's bulk deletion must take their chunks. `nexovue_scheduler.knowledge` must be in `REPOSITORY_AI_MODULES`.
83. **AC83.** A live test must plant a finding at each state, and fact chunks must be written at most once per service, signal and day. At the cap, a note must save, a runbook within its limits must save, the oldest fact line must go first, and the screen must count what is not searchable. A note must outrank 100 fact lines.
84. **AC84.** A recovered finding changed to a false alarm must drop out of search at once, and its chunk at the next sweep.
85. **AC85.** The `embedding_model` setting must be validated, audited and proved by `check()`, and AC41 and FR31 compare against it. A question's vector must carry its own model.

**Added by review: requirements that had none**

86. **AC86.** A 2,001-character question must be refused in a sentence. A finding id from another project must be refused.
87. **AC87.** A read asked for over 7 days must be refused; the 9th step must not run; a result over 8 KiB must say it was cut; older results must be one line in later calls; a sixth quote must be dropped.
88. **AC88.** The daily limit must refuse the 51st question, must accept 1 and 1,000, and refuse 0 and 1,001 as a setting. A failed question must not be re-run.
89. **AC89.** The Chat section must carry the MCP sentence, and MCP must keep working with AI off.
90. **AC90.** A false-alarm finding must not be indexed; hybrid search must merge, and must stand when either side is empty; one note must be one chunk, and a runbook must split by heading under the cap; changing the model must leave keyword search answering; a runbook over 32 KiB and a 201st must be refused in a sentence; knowledge must not be offered through MCP or to tokens.
91. **AC91.** This was wrong must take a reason, offer Undo, store only the closed reason, and never reach a provider: a test must hold that no prompt builder reads it. Delete must state how many questions go.
92. **AC92.** `/settings/runbooks` and chunk writers must do nothing where `ai_available` is false. A viewer must read a runbook, and a support view must see no body. Uploading 30 files must import them in one action, with per-file errors.
93. **AC93.** Copy steps must hold no model word, and a test must plant a marker in a sentence and not find it.
94. **AC94.** A provider returning both a tool call and text, and one ignoring tools, must each take a defined path, in fixture tests for all three wire formats.
95. **AC95.** Resolved-case **Save as runbook** must survive the finding's expiry, labelled as person-written. FR42's sentence must name the real number of days.
96. **AC96.** The panel and `/settings/ai` must show `tested` or `not tested` for the current model, from the bed's result. `docs/product/setup/` must name each tested local model with hardware, seconds per question, withheld rate and injection result, labelled as demo results, or say none has passed.
97. **AC97.** Knowledge search on 5,000 chunks at 1,536 dimensions must take under 200 ms on the 1 GB limit (a guess). Over it, a partial HNSW index per embedding model is added, and the same search must then take under 200 ms (D84).
98. **AC98.** Measured memory for `scheduler-insight`, and the panel's two poll routes on a long conversation, must be recorded in `sizing.md` and `status.md`.
99. **AC99.** The consent step and setup docs must carry BR18's sentence, and never the phrase "no way out". A test must search for the banned phrases.
100. **AC100.** No claim may reach public text without its proof in the launch brief (plan, T8.5).
101. **AC101.** The admin section must show monthly counts of answered, abstained, withheld, marked wrong by reason, and rejections by reason, with no text, and a warning at 80% of chat's ceiling.
102. **AC102.** A conversation within 7 days of expiry (guess) must show "Expires {date}".
103. **AC103.** A failed step, or `failed`, `unavailable`, `withheld` or `over_limit`, must show the nexovue-built **Check it yourself** link and the message id as the reference, and **Ask again** must fill the box and not send.
104. **AC104.** The bed must report time to first step, against NFR3's targets.
105. **AC105.** A table test over every kind of `Reader` must show that a ticket gets no person fields and null F3 `wording`, and does not fail on its missing identity. Once F22 is built, its AC69 table test must hold the ticket row too.
106. **AC106.** A 32 KiB runbook of nothing but short headings must save and make at most 18 chunks, and 200 of them must stay at 3,600 or fewer.
107. **AC107.** `/assistant-quotes` must be on the telemetry tier, and the poll route on the default tier, held by `test_ratelimit_coverage.py`.

### Requirement to criterion

So a gap is visible. *Report* means the bed reports it.

| Requirement | Criteria | Requirement | Criteria |
|---|---|---|---|
| UR1, FR1-FR3 | AC49, AC73, AC74, AC76, AC86 | FR4 | AC26, AC54 |
| UR2, UR6, NFR3 | AC30, AC48, AC103, AC104 | FR5 | AC86 |
| UR3, FR18, FR38 | AC22, AC93 | FR6 | AC35, AC56, AC64 |
| UR4, FR9 | AC22, AC76, AC87 | FR7, FR8 | AC12, AC14, AC10, AC53 |
| UR5, BR6, FR17 | AC21, AC61 | FR10, FR11, FR15 | AC87, AC63, AC19 |
| UR7, FR21 | AC28 | FR12, FR13 | AC16, AC77 |
| UR8 | AC91, AC101 | FR14 | AC18, AC62, AC32 |
| UR9, FR41, FR42 | AC95 | FR16 | AC20, AC77 |
| UR10 | AC49 | FR19 | AC25, AC56-AC58 |
| UR11, BR14 | AC31, AC33, AC34 | FR20, UR12 | AC79, AC88 |
| UR13, FR24 | AC2 | FR22, FR23 | AC54, AC55, AC88 |
| UR14 | AC37, AC72, AC101 | FR25 | AC3, AC60 |
| UR15, FR32 | AC38, AC83, AC90, AC92, AC106 | FR26, UR16 | AC89, AC96, AC101 |
| UR17, FR33-FR36 | AC44-AC47 | FR27, FR31 | AC41, AC85 |
| UR18, UR19 | AC93, AC78 | FR28-FR30 | AC39, AC68, AC83, AC84, AC90 |
| BR1-BR4 | AC1-AC11, AC53, AC60, AC105 | FR37 | AC90 |
| BR5, BR7 | AC15-AC18, AC61-AC63 | FR39, FR40 | AC68, AC69 |
| BR8 | AC81 | NFR1, NFR2 | AC29, AC80, AC82, AC107 |
| BR9, BR10 | AC39, AC40, AC64 | NFR4, NFR5 | AC97, AC98 |
| BR11, BR12 | AC23, AC24 | NFR6 | AC48, AC50 |
| BR13 | AC88 | BR15, BR16 | AC35, AC38 |
| BR17 | AC4, AC43, AC80 | BR18 | AC99, AC100 |

## What these tests cannot prove

Read this beside the criteria. No test stands behind these promises.

| Limit | What is done instead |
|---|---|
| Whether a citation really supports its sentence | The label says what matched, never that the sentence is true (BR6). The person can click through |
| A believable false cause | Labelled, and scored by the bed. It can still fool someone |
| Words a model paraphrased from a log | Stated in consent; they expire with the message |
| A provider's own retention | Consent says to read their terms |
| Quality on real traffic | The demo is not real traffic. The founder's interviews, and design partners after launch |
| That injection is absent | No suite proves it. Structure limits what success gains |
| A local model's behaviour on the customer's hardware | Reported per model tested; the setup docs say what was used |
| Whether anyone wants this | The conversations before Step 2 |

## Dependencies and constraints

**Must exist first**

- **F6 merged.** Findings exist only on this branch (foundation section 9).
- **F3:** `scheduler-insight`, `nexovue_insight`, `AI_SECRET_KEY`, provider contract, fence helpers, validator, `ai_connections`, `ai_project_opt_outs`, `ai_usage_monthly`, `ai_runs`, the on/off flow, `/settings/ai`, `shared/url-window.ts`, the evaluation bed. F3's design now exists, and this spec was reconciled with it in review. **F4 adds to F3's provider contract:** `tools` on `AIRequest`, tool calls in all three wire formats, and `embed`.
- **F22:** notes and resolved cases, for that part of knowledge only. Runbooks and chat do not wait for it. **How long a self-hosted install keeps its data sets how long this feature remembers:** foundation founder decision 6 (approved 2026-11-02) makes it an operator setting, 30 days for a resolved case by default.
- **F28:** `list_releases` and `get_release_comparison`, for those tools only. Before F28 is built, chat has no deploy tool. `GET /deploys` has no time filter, and F28 says it never will.
- **MCP work has no dependency beyond F6,** so it comes first (D23).

**Recorded decisions followed:** the plan's AI safety rules, FD1, FD2, FD7, FD8, FD9, FD24, decision 0004, `vision.md:245` (knowledge is embedded, telemetry is queried live), ADR-017, ADR-019.

**Where this differs from the foundation**, stated openly, as it asks. Each needs its owner to accept it; details and reasons, and four smaller ones touching F3, are in the design's section of that name.

1. Tool bodies are not separated from MCP's `Context`: chat calls routes instead (foundation section 9).
2. Names are fenced rather than aliased, because logs hold them anyway (section 3).
3. The URL rule stays, and is kept workable through quotes by reference (U4).
4. The suggested monthly limit rises when chat is on (section 4).
5. Chat has its own consent (section 4).
6. Chat's results use a smaller cap than 32 KiB (U2).
7. New action `MANAGE_KNOWLEDGE` (section 5).
8. New tables and queue: `assistant_steps`, `assistant_settings`, `runbooks`, queue `assistant` (section 8, which allows additions).
9. The AI role's memory ceiling and slots rise (section 1).
10. One connection per organization (section 4): an owner may add a second, for embeddings only.
11. A difference from F3: chat's call rows hold no reservation. The question holds it, and F4's own sweep settles it. F3's sweep needs no change.
12. Section 11's hard gates are kept, and AC51 now names them all.

## Open decisions

None are open. Each is taken below, or is listed under *Founder decisions needed*.

## Decisions taken

*Recommended by the authors, founder may overrule.*

### Carried from the research

D1 to D24 are in [`research.md`](research.md), *Decisions taken*, with options and reasons. In one line each:

| # | Decision |
|---|---|
| D1 | Fixed tools over GET routes, not SQL or a query language |
| D2 | One model loop, with structure first, built so a reader can later replace a log tool |
| D3 | A ticket reads one project and an allow-list of routes |
| D4 | The API mints the ticket and seals it for the worker |
| D5 | A ticket reads findings as a token does |
| D6 | Live steps by polling, not a word stream |
| D7 | Chat's capacity is separate from F3's |
| D8 | Commands only by reference to a runbook |
| D9 | Quotes by reference, filled by nexovue |
| D10 | Figures are matched and flagged at first, not refused |
| D11 | Chat has its own consent |
| D12 | Names are fenced in chat, not aliased |
| D13 | Exact scan first, no HNSW |
| D14 | Vector column with no fixed size, with the model's name |
| D15 | Provider with no embeddings | **Changed in review:** keyword search, and say so; an owner may add a second connection for embeddings only (D61) |
| D16 | Only person-written text and facts are indexed |
| D17 | Stored conversations hold no tool results |
| D18 | Messages last for derived retention, each on its own clock (D60) |
| D19 | A conversation belongs to its person |
| D20 | Runbooks by paste or Markdown upload first. Git and wiki sync are not in this version |
| D21 | The service map joins the read surface |
| D22 | Side panel and a finding-page entry; ⌘K stays with search |
| D23 | MCP first |
| D24 | `/llm/calls` is not a chat tool |

### New here

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D25 | How much one question may spend | (a) Per call only. (b) Reserve for the whole question, and shorten old results | **(b): 60,000 tokens reserved, guess.** Worst-case arithmetic otherwise reaches 288,000 tokens (research, section 7). A single allowance makes the worst case known before spending. Cost: with under 60,000 left, a question is refused though a typical one would fit |
| D26 | The monthly limit suggested when chat is on | (a) Keep 2 million. (b) Suggest 20 million. (c) Suggest what the arithmetic needs | **(c), about 45 million, as a guess. Changed in review.** The 5-person example needs about 34 million a month once the tool list is counted, and chat stops at 80%, so 34 ÷ 0.8 plus F3 is about 45. The customer still sets it. The bed's cost run replaces it |
| D27 | Where steps live | (a) JSON on the message. (b) Own table `assistant_steps` | **(b).** The worker appends one row at a time, and the role's grant can be insert-only. Cost: one more table |
| D28 | How a quote is shown later | (a) Store its text, with an expiry. (b) Store a reference, and read it when shown | **(b).** Retention and erasure then apply to it by themselves. Cost: a read per quote when an answer is opened, capped at 5, and quotes vanish with their log |
| D29 | Who decides the basis label | Model / citations alone / nexovue, from what the cited rows hold | **nexovue, from what matched. Changed in review:** the model chooses citations, so a label from citations alone is a label an attacker could set |
| D30 | How structure is proved | (a) Only real models, in the bed. (b) Also a hostile fake provider, in the offline gate | **(b).** It assumes the injection already won, runs on every commit, and costs nothing. Real models still run in the bed, because a fake cannot show a false cause being believed |
| D31 | How knowledge search reads | (a) AI role runs SQL itself. (b) A ticket-only GET, with the query's embedding stored on its step | **(b).** One door for every read, with the person's checks at each use. Cost: one column and a slightly odd route |
| D32 | What a runbook belongs to | Organization / project | **Project,** as checks and rules do, so search stays in the ticket's reach. Cost: a runbook for both production and staging is added twice |
| D33 | How a citation carries its range | (a) `from` and `to` in the address. (b) Set the window on click | **(a). Changed in review.** F3 A12 chose the address and rejected clicks moving a person's window everywhere, and only (a) lets a colleague check a citation. Cost: F3's helper covers `/traces`, `/logs`, `/errors`, `/services/$name`, `/infrastructure` and `/releases`; other destinations take `project=` alone (D79), and say the range is not applied. `/logs` gains a `q` in its address |
| D34 | What happens to conversations when a person leaves | Keep / delete | **Delete, in `scrub`.** The user row stays, so no cascade fires. They are the person's own words, which nobody else can read |
| D35 | Questions in flight per person | Many / one | **One.** It bounds spending, needs no queue logic, and a person cannot read two answers at once. Cost: a stuck question blocks until Stop or its deadline |
| D36 | Automatic retry of a failed question | Yes / no | **No.** It may spend the whole allowance twice. The person asks again |
| D37 | A Stop button | Yes / no | **Yes.** A slow local model could otherwise hold a person for 5 minutes |
| D38 | A keyboard chord | Choose one / none | **None in the first version.** ⌘K is reserved, browsers own most others, and a chord is hard to take back. The button is keyboard-reachable |
| D39 | How capacity is split | (a) `assistant` queue with caps. (b) Reserved slots within `insight` | **(a), 6 slots, each queue capped at 4 (guess).** `QUEUE_CAPS` already works this way (`worker.py:72`), so each keeps at least 2 when the other is full. Cost: 2 more slots of memory. F5 later moves this to 8 slots with `insight` at 3 (F5 D56) |
| D40 | Where runbooks live on screen | Inside `/settings/ai` / own `/settings/runbooks` | **Own screen.** Members write runbooks, and `/settings/ai` is for those holding `MANAGE_AI`. It also keeps working when a customer later changes provider |
| D41 | Where chat's consent lives | On F3's table / F4's `assistant_settings` | **F4's own,** holding F3's fingerprint rather than an address, so it can never be weaker than F3's |
| D42 | Who sets the daily limit, and who sees use | Fixed, hidden / admin sets, sees counts | **Admin sets; counts by person, no text.** This is the admin's control research asked for, and Grafana's page shows the same levels |
| D43 | Keyword search and merging | Language setting; merge | **`english` text search, and RRF with k = 60.** Both are starting guesses for the chat suite to test. Non-Latin text is kept unstemmed. pgvector's README names RRF and gives no k; 60 is from common usage, not verified here |
| D44 | Caps on knowledge | None / caps | **32 KiB a runbook, 200 runbooks a project, 5,000 chunks a project (guesses).** They protect a 1 GB Postgres, and are stated as protection, not as an upsell |
| D45 | How much MCP gains | Labels only / labels and tools | **Labels, ranges and seven tools.** A customer who never uses chat still gets a safer, more useful server |
| D46 | Knowledge through MCP | Offer / not yet | **Not yet.** It would send notes to a customer's own AI, with no consent step, and no embeddings on that path |
| D47 | "This was wrong" | None / one click / one click and a closed reason | **One click and a closed reason** (wrong number, wrong time range, wrong cause, missed it, unsafe suggestion), with Undo, never fed back to any model. Admins see counts only. It is the only signal of real-world quality this feature will have |
| D48 | Who writes and reads runbooks | Admins / everyone who works | **Owner, admin and member write (`MANAGE_KNOWLEDGE`); viewers read,** since nothing goes to a model and these are operating instructions; a support view sees no bodies. Poisoning by a member is a risk already present in notes, and each write is audited |
| D50 | Whether chat may use the whole month | (a) Yes. (b) Stops at 80%, as F3's warning explanations do | **(b).** Otherwise a busy chat day takes away the explanation for the next critical finding. Cost: chat pauses with a fifth of the limit unused, and the screen says why |
| D51 | A dead question's reservation | Return it / keep it all / keep what may have been spent | **Move the unsettled remainder to `used`, flagged estimated,** as F3 BR13 says. The question is the unit (D53). Usage may read high and never low. Cost: repeated crashes eat up to 60,000 tokens each |
| D49 | When the panel's first answer is thin | Wait for all tools / ship in steps | **In steps,** starting with `list_findings`, `list_services` and `search_logs`, to prove the path before widening (ADR-0019) |

### Added by the persona review

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D52 | The customer-facing name | Chat / Chatbot / Ask | **Ask,** with "Ask about this finding". "Anything" conflicts with a fixed tool list, "RAG over telemetry" with BR10, and "chatbot" implies streaming. `assistant` stays in code. Draft line, future tense until built: "Ask a question about your system and get an answer you can check. Each sentence shows what was read and over what time range. It can read but has no tool that can change anything, and it runs on the AI account or model you choose." |
| D53 | The unit of a reservation | Each call / an opening run row / the question | **The question.** `assistant_messages.reserved_tokens`, written with the atomic reservation. Call rows hold 0, so F3's sweep moves nothing twice. An opening run row (a reviewer's option) would be a fake call in `ai_runs` |
| D54 | How a ticket is stored | Own table / columns on the message | **Columns on the message.** Exactly one per question, so stop, death, deletion and retention all end it with no separate revocation, and three inventory entries go |
| D55 | How the allow-list matches | Path text / relative path / handler | **Handler,** `scope["route"].endpoint`, the same `module:handler` key `THE_SURFACE` uses. A probe on FastAPI 0.162.0 saw `/search` for `/api/v1/logs/search`, which names no one route |
| D56 | Who owns a daily count | Count messages / own table | **`assistant_question_counts`,** updated atomically with the POST. Otherwise deleting a conversation resets the limit |
| D57 | The worker's view of history | Read finished rows / snapshot at POST | **A capped snapshot on the message,** nulled at the end. The role then reads only rows in flight, enforced by row-level security |
| D58 | When note chunks are written | In F22's transaction / by the sweep | **The sweep, for organizations with chat on.** F22's note service needs no change, a note depends on nothing here, and no second copy exists for those who never turn chat on |
| D59 | Tools beyond FR7's first list | Leave out / metrics only / add endpoint, trend, metrics, hosts | **Add them.** "Which endpoint got slow, and when" is the lead question, and disk alerts raise incidents chat must look into. Cost: tokens gain these routes too, stated loudly in `THE_SURFACE` |
| D60 | Lifetime of messages | From last message / per message | **Per message,** or BR15 is false for anyone who keeps one conversation open |
| D61 | A second connection for embeddings | Later / in Step 6 | **In Step 6.** Without it a Claude customer never gets the plan's "through pgvector". Cost: its own address, key, consent line and preview |
| D62 | Where `vector` is created | In the Alembic chain / a `setup` step | **`setup`, like F3's role.** On failure it warns and search stays keyword only. Otherwise a missing extension stops alerts, against BR17. Cost: two columns outside Alembic, which its drift check must be told about |
| D63 | Data level | All or nothing / facts or facts and text | **Both levels.** The catalog already knows each field's kind, so it is cheap, and it answers the commonest objection honestly |
| D64 | Measured, as a word | Keep / F3's words | **F3's words.** F3 found a browser key can move a rollup, so "Measured" overstates it |
| D65 | Step arguments that fail the checks | End the step / run, and hide | **Run, and hide.** "Which URL failed?" must stay answerable; the phrase goes only to our own API |
| D66 | Host names in sentences | Refuse all / allow if in a cited name field | **Allow, inert,** so answers about a third-party outage are not withheld. A planted name stays plain text, and imperatives are still removed |
| D67 | Time limits | Several clocks / `deadline_at` alone | **`deadline_at` alone,** derived from `call_seconds` |
| D68 | How the panel fits at 1024 px | Overlay / container queries | **Container queries on `main`,** following the founder's rule that screens use the window. Cost: 18 files change their layout variants, a plan task of its own |
| D69 | Panel width | Fixed 420 px / grows | **`clamp(360px, 30vw, 640px)`,** with sentences capped, not the container. To be checked at wireframing |
| D70 | Answer before or after its steps | Steps first / answer first | **Steps live while running; when final, answer first, steps under "Read 5 things",** failed steps always visible |
| D71 | Email warnings near the limit | Email / on screen | **On screen.** F3 A31 chose the screen for the same kind of notice, and this role holds no email key |
| D72 | Masking emails before sending | Never / on by default | **On, an admin may turn it off.** It narrows a real disclosure. A quote still shows the stored value to the person |
| D73 | How the worker makes final writes, with finished rows hidden | (a) A separate `WITH CHECK`. (b) One `SECURITY DEFINER` function. (c) A grace period in the policy | **(b), recommended by the authors, founder may overrule.** A probe showed (a) still aborts, because an `UPDATE` with a `WHERE` checks the new row against the `SELECT` policy. (c) leaves finished questions readable for a while |
| D74 | Which retention the aged-out check uses | (a) Raw for all. (b) Each tool's own clock. (c) No check | **(b), recommended by the authors, founder may overrule.** (a) says kept data is gone. (c) reads as "nothing happened" |
| D75 | `Reader.is_token` | (a) Remove. (b) Keep, and move F3 and F22 to `is_person` | **(b), recommended by the authors, founder may overrule.** F3 adds `is_person`, F22 builds on it, and F4 relies on both, in the plan's order |
| D76 | Rate tier for quotes | (a) `/assistant` on the telemetry tier. (b) Own prefix `/assistant-quotes`. (c) Not naming the repository type | **(b), recommended by the authors, founder may overrule.** (a) moves the poll to the tighter tier. (c) skips the concurrency cap on a ClickHouse read |
| D77 | Runbook chunks | (a) Per-runbook refusal. (b) A minimum size that gives a ceiling | **(b), 18 chunks a runbook, recommended by the authors, founder may overrule.** A runbook within its limits keeps saving (AC83) |
| D78 | The abstention gate | (a) Reported only. (b) A hard gate with a rate | **(b), at least 90%, a guess, recommended by the authors, founder may overrule.** Answering with no evidence is the failure this gate exists for |
| D79 | Project on other screens' links | (a) Links do not carry it. (b) `project=` alone on the rest | **(b), recommended by the authors, founder may overrule.** Otherwise a colleague's link can open on the wrong project |

### Own questions answered, 2026-11-02

*Decided by the authors after reading the code and checking outside sources. These are technical, so they are not the founder's; the founder may still overrule.*

| # | Question | Options | Decision and why |
|---|---|---|---|
| D80 | Which local model the first report tests, on what hardware (plan, question 1) | gpt-oss 20B / Qwen3 8B / Gemma 4 small / a 70B model | **`gpt-oss:20b` on Ollama, on the founder's own Apple M4 Pro with 24 GB (`sysctl hw.memsize`), with `nomic-embed-text` for embeddings.** It has native tool calling and structured output, an Apache 2.0 licence, and a 14 GB download that fits a 16 GB machine ([Ollama library](https://ollama.com/library/gpt-oss)). Tool calling is what chat lives on. The Mac costs nothing new. **Cost, stated:** a Mac with unified memory is faster than a typical CPU-only Linux server, so the report names the hardware and claims no speed for anything else. Rankings of small local models in the sources found were vendor lists and disagree, so this is a starting choice, and a second model is added only if this one fails the shape gate. **Changed in review:** unlike F3's replay on the same Mac with the stack stopped (F3 A81), chat's tools read the live API, so the stack must run beside a 14 GB model on 24 GB, with Docker given 11.7 GiB today (`docker info`, read 2026-11-02). The run therefore starts only the services chat reads (API, worker, Postgres, ClickHouse), lowers Docker's memory for it, and records `ollama ps` showing the model fully on the GPU. This is the case decision 0047 flagged (`decision-log.md:247`: "Ollama will not run usefully on the dev machine", proposed, never decided), so this run is also its test, and the report says whether 0047 held. If it spills or swaps, `qwen3:14b` (9.3 GB, 40K context, [Ollama](https://ollama.com/library/qwen3)) replaces it, and the report says so. Context length is set per D87 |
| D87 | How a local model gets enough context for chat | Ollama's default / set it, and check it | **Set it, and check it. Added in review.** Ollama's default context depends on GPU memory: 4K under 24 GiB, 32K from 24 to 48 GiB ([Ollama, context length](https://docs.ollama.com/context-length)). Its OpenAI-style endpoint cannot set it per request; it needs `OLLAMA_CONTEXT_LENGTH` on the server or a Modelfile `num_ctx` ([Ollama, OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)). Chat's tool list and results alone will pass 4K, so a default install would lose the start of each prompt. The bed sets 32K (a guess, replaced by T4.3's largest measured call) and records it. The setup page tells operators to set it. The connection check, which is F3's provider check (F3 A86, since F3 owns the provider interface), sends a prompt of known size and refuses "ready for chat" if the provider's reported input tokens come back smaller. Whether Ollama truncates silently or errors was not confirmed here; T2.0's first real local call finds out |
| D88 | When F4's Step 1 is built, given the shared customer round | After F22, in the foundation's order / straight after F6 merges | **Straight after F6 merges. Added in review.** Founder item 8 holds one round before F28's Step 1b, and this feature's rule (founder item 1) asks people after seeing Step 1. In the foundation's order (F28 → F3 → F22 → F4) Step 1 would not exist by then. Step 1 needs only F6 (plan, *Before*), changes only the MCP server, and closes a hole that exists today (`assistant/tools.py:201-225`). If it is not ready in time, the round shows today's MCP server and the planted-line demo, and says the labels are coming. This is a difference from the foundation's order, row 20 of the design's table, and is written into the foundation before merge (D86) |
| D81 | How Titan and Ollama embeddings are called (plan, question 3) | Through `bedrock_converse` / a separate route | **A separate small embedding format per provider:** Bedrock through `InvokeModel` on `amazon.titan-embed-text-v2:0` (1,024 dimensions); OpenAI-style accounts and Ollama through `/v1/embeddings`. Converse is a chat API; AWS's Titan V2 model card and third-party Bedrock clients all embed through `InvokeModel` ([AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html)). Ollama lists `/v1/embeddings` as supported, string or list input, with `dimensions` ([Ollama](https://docs.ollama.com/api/openai-compatibility)). Bedrock API keys are bearer tokens for the Bedrock runtime endpoint ([AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html)); the page shows only a Converse example, so T6.0's one real call still proves a key works on `InvokeModel`. A long-term key is region-bound, so the error for a wrong region is named in the setup page |
| D82 | What Elastic's Observability chat needs in licence (plan, question 2) | Find out now / at T8.5 | **Known enough now; rechecked at T8.5.** Elastic's docs say self-managed use needs a paid subscription, and a docs change names Enterprise, which the subscriptions page did not confirm in text ([Elastic docs](https://www.elastic.co/docs/solutions/observability/observability-ai-assistant)). Elastic also now calls its old assistant deprecated in favour of an AI Agent. Nothing public names Elastic until T8.5 (foundation, *Must not be said*), so no work waits on the exact tier |
| D83 | The size of the trigger that moves a log tool to D2's reader step (T4.4; design, *Injection suite*) | Founder sets it / a rule fixed before the run | **A rule fixed now, before any run:** propose the reader step if, on any option that otherwise passes, planted log text makes the model follow its instruction in more than 10% of log-text trials (a guess), or any hard-gate failure is traced to log text. A rule set after seeing the numbers can be bent to fit them. **Cost, stated:** the reader doubles model calls for each log read, which the customer pays. The decision and its numbers are recorded in `status.md`; it is a design change, not a founder decision |
| D84 | What happens if knowledge search is slower than 200 ms (AC97, T6.4) | Founder decides / exact scan always / a partial HNSW index | **A partial HNSW index per embedding model in use, created by `provision_vector()`,** with iterative scans on so the project filter still returns enough rows. pgvector indexes a column of mixed sizes only through a cast and a partial index, for example `((embedding::vector(1536)) vector_cosine_ops) WHERE embedding_model = '…'`, and queries must use the same cast; HNSW takes up to 2,000 dimensions for `vector`, 4,000 for `halfvec`; iterative scans exist from 0.8.0 ([pgvector README](https://github.com/pgvector/pgvector)). The bundled image is `pgvector/pgvector:0.8.6-pg18-trixie` (`deploy/compose/compose.infra.yaml:138`). Under 200 ms, exact scan stays (D13). **Cost, stated:** an index uses memory on a 1 GB Postgres and is approximate, so T6.4 reports recall beside time. **Added in review:** Postgres matches a partial index at planning, so "parameterized query clauses do not work with a partial index" ([Postgres docs](https://www.postgresql.org/docs/current/indexes-partial.html)). The API uses asyncpg (`apps/api/pyproject.toml:13`), whose prepared statements may switch to a generic plan, so the search writes the model name as a literal, taken only from the stored setting, never from the request. A model chosen after `setup` gets its index on the next `setup` run, and meanwhile is searched exactly, still correct |
| D85 | What happens if `SECURITY DEFINER` functions also fail the T2.0a spike | Founder decides / authors redesign | **The authors redesign, and Step 2 stays stopped until a new design passes the same spike.** It is a database technique, not a money, scope or trust choice. The redesign and its test are recorded in `status.md` before Step 2 resumes |
| D86 | Who accepts each difference from the foundation (design, *Differences from the foundation*) | A named owner per row / the foundation and this spec's approval | **No separate owner.** The 2026-11-02 reconciliation wrote each difference into the foundation (`status.md`, *Cross-feature reconciliation*), and F3, F22 and F28 were reconciled the same day. The founder's approval of this spec accepts them. A later difference must be written into the foundation before merge |

## Founder decisions needed

**Settled by the foundation, removed from this list.** The founder approved the foundation's eight shared decisions on 2026-11-02 ([`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions)). They answer four of this spec's earlier items, and the F22 question this feature's memory depends on:

- *Money for the evaluation bed*: foundation item 4, **changed 2026-11-02: no paid model calls.** Chat's runs use a free tier of a cloud provider and the local model (D80), on synthetic data only. The old arithmetic stays in `status.md` history as a guide to token counts.
- *The selling line*: foundation item 2. Chat's panel and preview use that wording exactly (FR24).
- *AI on the hosted service*: foundation item 1. The customer's own key only, when hosting opens.
- *Publishing injection and chat results*: foundation item 5. Yes, after the founder has read the first run, labelled as demo results, and kept outside the repository until then.
- *How long a self-hosted install keeps data*: foundation item 6. An operator setting; today's values by default.

**All three approved by the founder on 2026-11-02, as recommended** (foundation, *Founder decisions*, 21, 12 and 15). The text is kept so the plan's references still point to it.

1. **Approved 2026-11-02, as recommended: the rule below is fixed before the round.** **When to build the chat panel at all: the rule for continuing past Step 1.** *Why it is yours:* it decides whether Steps 2 to 7, the large part of this feature, are built and sold, or whether nexovue ships only the safer MCP server. *Recommendation, set before the conversations:* these go into the one shared round before F28's Step 1b (foundation item 8), with Step 1 built straight after F6 so it can be shown (D88). Ask each person two questions after showing Step 1 working in Claude Code. (a) "Would your team turn on a chat panel inside nexovue, or is your own AI tool over MCP enough?" (b) "Would your company let log text go to your own AI account or a model on your own server?" **Build Steps 2 to 7 if at least half say yes to both** (in counts, as F20 counts its rule: 3 of 5, 3 of 6, 4 of 7, 4 of 8). If at least half want the panel but say no to log text, build Steps 2 and 3 with *facts only* as the default data level (D63) and stop to report. If fewer than half want the panel, stop after Step 1. Half is a guess for a sample of five to eight, and the answers are direction, not proof.
2. **Approved 2026-11-02, as recommended: 80% right numbers, 90% right time ranges, every hard gate passed, each with its bound.** It is run on a free-tier cloud model and the local model (foundation item 4, changed), so "supported" can name only those. OpenAI, Anthropic and Bedrock, which customers use most, have no free API tier and stay "not yet tested" on the setup page until someone tests them. **The accuracy bar that lets a provider be called "supported"** (AC51, plan Step 4). *Why it is yours:* "supported" is a public promise in the setup pages, and a low bar puts wrong answers in front of customers under nexovue's name. *When:* after a first small tune run and before the report run, as F3's is (F3 AC62, A83), so the bar cannot be bent to fit the numbers it judges. *Recommendation:* until set, nothing is called supported. As a starting point to react to: right number on at least 80% of known-answer questions and right time range on at least 90%, with every hard gate passed, each shown with its statistical bound.
3. **Approved 2026-11-02, as recommended, for the whole repository** (foundation item 15): dated internal research with the header "internal research, not a comparison or a claim" is allowed; never in sales or marketing text. **Whether the dated competitor research in this public repository counts as "naming rivals in public".** The repository is public: GitHub's API answered `"visibility": "public"`, AGPL-3.0, without a login on 2026-11-02. `research.md` and the shared competitor research name rivals, some facts unverified, and the foundation bars naming rivals in public. Two older files that name rivals are already committed (`docs/research/market-analysis-2026.md`, 32 mentions). **Review: this is wider than F4.** The foundation itself names rivals in the public repository, in its *Must not be said* table and *Where rivals are plainly ahead* (`phase-2-foundation.md:626-640`), and other specs do too (for example `dashboards/spec.md:597`). So it needs one answer for the whole repository, not one per feature. *Why it is yours:* it is public exposure about named companies. *Recommendation:* read the foundation's ban as covering sales and marketing text (site, setup pages, posts, decks), not dated internal research. That holds if each research file is dated, says at the top "internal research, not a comparison or a claim", marks unverified facts as unverified, and never says one product is better. Add that header before merge, to the committed research files too. If you prefer no public mention at all, the research tables, and the foundation's named rows, move to a private repository before this branch merges.
