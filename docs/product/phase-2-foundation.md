# Phase 2 foundation: the shared decisions every feature follows

> **Status:** Approved by the founder, 2026-11-02, with the seven features' specs, designs and plans. Written and
> approved the same day. No code is written from it yet; each feature starts per [`phase-2-tracker.md`](phase-2-tracker.md).
>
> **What it is for.** Seven feature documents (F28, F3, F22, F4, F5, F19, F20) are being written at the same
> time. This document is what keeps them agreeing. Where a feature document disagrees with this one, the feature
> document must say so and say why; it must not quietly differ.
>
> **How it was checked.** The code was read, on branch `feature/anomaly-detection` with uncommitted work present.
> Nothing was run: no test, no query, no model. Line numbers are for that working tree and will drift. Every
> memory figure, token figure and limit in this document is a guess and says so. Founder decisions in
> [`phase-2-plan.md`](phase-2-plan.md) and the F6 documents stand and are not reopened.
>
> **Reconciled 2026-11-02.** After the seven features were written and reviewed, this document was changed to agree
> with what reading the code taught them. Each feature's design still lists where it differed, and its `status.md`
> records what the reconciliation changed. Where the two now read alike, that is why.

## The short version

1. **AI runs in one new container, `scheduler-insight`, from the existing `nexovue` image.** It has no telemetry
   database account, no repository key, no alert keys and no email key. No third image.
2. **Stored facts are gathered with no AI at all, and that step always runs.** The AI only words facts it is
   handed. Turn AI off, or stop its container, and a customer still gets the facts.
3. **One provider interface, three wire formats** (OpenAI-style, Claude's own, Amazon Bedrock's), covering both
   of the founder's options. Provider differences stop inside it.
4. **Attacker text is replaced where it can be, fenced where it cannot, and the fence is never the defence.**
   The defence is structure: the model has nothing to write with, and nexovue's own code checks all it says.
5. **A case is a finding, not a second record.** Explanation, timeline and suggested fix all hang off the finding.
6. **One connections table, opened only by a process that never runs a model.** Two GitHub Apps at most: one for issues, and a narrower one for fixes if a customer chooses F5. A model never holds a token.
7. **No feature is held back by plan in Phase 2.** Everything is free on a self-hosted install. AI on the hosted
   service stays switched off until the founder decides that question.

## Words used here

| Term | Meaning |
|---|---|
| **Provider** | Whoever answers a model call: the customer's AI account, or a model on the customer's own server |
| **Prompt injection** | Text an AI reads being treated as instructions. Here it arrives through telemetry |
| **Role** | One way of starting the `nexovue` image, chosen by its start command, and run as its own container (ADR-019) |
| **Rule of Two** | Meta's guideline: an AI step may have at most two of *reads untrusted input*, *reaches private data*, *changes something or sends data out* |
| **Evidence** | A stored fact with an id, such as "deploy v2.1 reached `checkout` at 14:02", that a claim can point to |
| **Abstain** | Answering "not enough evidence" instead of naming a cause |
| **GitHub App** | GitHub's own kind of integration: chosen repositories, chosen permissions, short-lived tokens |
| **Sealing** | Encrypting a secret so it opens only for its own purpose and owner (`sealing.py`) |
| **SSRF** | Server-side request forgery: tricking our server into calling an address it should not |

## 1. Where AI code runs

### The facts in the code

- `ADR-019` ships two images and leaves `insight` open: a third image only "if its dependencies are heavy".
- `insight` exists only in the development Compose file, and runs a package that does not exist
  (`deploy/compose/docker-compose.yaml:586-627`). The install layout has no such service, and `roles.py:19-30` has no such role.
- `scheduler-light` holds the telemetry reader, the eraser and the detection-writer accounts
  (`deploy/compose/nexovue.yaml:355-366`). It is the wrong home.
- **Every Python role shares one anchor of settings, `control-plane-env`** (`nexovue.yaml:84-115`). It carries
  `CHECK_SECRET_KEY`, the object store's keys and the email key. That key opens every check secret and every alert
  channel secret (`keys.py:93-118`). A worker built from that anchor would hold all of them.
- **All Python roles use one Postgres account** (`nexovue.yaml:91`). A search of `deploy/` and `apps/api/migrations/`
  finds no `CREATE ROLE`. What that account may do was not traced. The ClickHouse side already has narrow accounts
  (`deploy/clickhouse/users.d/`, ADR-023), which is the model to copy.
- Outbound calls to a customer's address go through a libcurl guard checked at connect time
  (`probes/addresses.py`, `alerts/senders/http.py:1-7`). The scheduler has no `httpx`; it replaced it with `pycurl`.

### The decision (FD1, FD2)

Three rooms, each with different keys.

| Room | Runs where | Does what | Holds | Never holds |
|---|---|---|---|---|
| **Facts** | Existing `scheduler-light`, new queue `evidence` | Gathers and freezes evidence. No model. Runs whether AI is on or off | The ClickHouse reader account, as it does today | Provider keys, repository keys |
| **AI** | **New role `scheduler-insight`**: `worker --queues insight` from the `nexovue` image | Calls the provider. Validates what returns. Stores it | Its own narrow Postgres account `nexovue_insight`; `AI_SECRET_KEY`; a network path to the provider and, for F4, to the API | ClickHouse account, `CHECK_SECRET_KEY`, `CONNECTION_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, email key, object store keys, Kafka |
| **Writing out** | New role `scheduler-connections`, for every GitHub and Jira call. The API only adds a job, because it has no connect-time address guard | Uses connector tokens. No model | `CONNECTION_SECRET_KEY` | `AI_SECRET_KEY`, and any model call |

The API is the exception to these rooms: it seals keys and must rotate them (`keys.py`), so it holds `AI_SECRET_KEY` and `CONNECTION_SECRET_KEY`. It calls neither a provider nor a tracker.

**What this means.** A successful injection can only change words, and those words are checked before anyone sees
them. If the AI process itself were ever broken into, there would be no telemetry database, repository or alert
channel within its reach.

**Rules the rooms create**

- `scheduler-insight` must not use the `control-plane-env` anchor. A test must read the install Compose file and fail
  if this service names any forbidden variable.
- `nexovue_insight` gets only what its tasks need: leasing `insight` jobs, reading its inputs, and writing named
  columns. A live test must prove it is refused a write to `findings`, `notifications`, `audit_log` and `connections`.
  It gets no rights on `jobs` at all: three `SECURITY DEFINER` functions claim, retry and finish, each listing its own queues
  (`insight`, then F4's `assistant`, F5's `insight-fix`), since `DELETE` permits `LOCK TABLE`, which row-level security does not limit (F3 A57). Row-level security is F4's and F5's, on their own tables. Grants live in `setup`, which runs after every restore. `AI_SECRET_KEY` is read only by the API (to seal) and this role (to open).
- The worker imports every task module (`jobs/registry.py:18-29`), so narrow roles name their own: `--tasks`, `TASK_SETS` and `SCHEDULER_ROLE` (F3), with values for `insight` and `connections`. `SCHEDULER_ROLE` must stay open to new values.
- Job arguments carry ids only, never text (`jobs.py` stores them readably in Postgres).
- The default job limits, 30 and 60 seconds (`jobs.py:27-28`), are too short for a model. Each AI task must name its
  own. **Starting guess:** 120 s soft and 180 s hard; a local model may need more. Concurrency is capped through
  `QUEUE_CAPS` (`worker.py:72`), and rises as features arrive: 4 slots with F3, 6 with F4 (`assistant` 4, `insight` 4), 8 with F5 (`assistant` 4, `insight` 3, `insight-fix` 2). All guesses.
- **On `scheduler-light`, capped queues share one floor** (F3 A56): together they hold at most 4 of its 8 slots, a guess, taking turns, so probes and alert sweeps keep the rest. The capped queues are `detection` (F6), `evidence` (F3), `releases` (F28) and F19's `slo`, `slo_counts` and `slo_history` (F19 D66). They ask for 10 seats against 4, so they wait for each other; probes and alert sweeps never wait for them.
- The container always runs, idle when AI is off, so that turning AI on in the console needs no server access.
  **Memory ceilings are guesses:** this role 384 MB, then 576 MB with F4 and 768 MB with F5; `scheduler-connections` 384 MB. F19 adds no container: its SLO queues run on `scheduler-light` under the floor above, which keeps its 768 MB (F19 D66, the founder's decision of 2026-11-02, superseding a 384 MB `scheduler-slo`). Together about 1.1 GB (768 + 384 MB; arithmetic on guesses) on top of the 13 GB of ceilings on
  a 16 GB server (`docs/self-hosting/sizing.md`). None is measured, and `sizing.md` must be updated when each is.
- **A local model never runs in nexovue's Compose file.** The founder's decision is that nexovue runs no AI service
  for self-hosters. The customer runs Ollama or similar themselves, and the 16 GB sizing does not include it.
- The dead `insight` service and `LLM_BASE_URL` in the development Compose file are replaced by this role (F3 owns
  this). The `ollama` development profile may stay.

**F4 fits this too.** The chat's tools are existing API service functions, and the scheduler cannot import
`nexovue-api` (`apps/scheduler/pyproject.toml:14`). So the model's loop runs in `scheduler-insight`, and each tool
request becomes an ordinary GET to the API's own `/api/v1` routes, using a short-lived, read-only ticket tied to the
person asking. That gives three things for free: the person's own access rules, the existing rate limit (a code-map
trap: in-process calls would skip it), and the existing tenant checks. F4's design owns the ticket's details; the
`view_as_tickets` table and the read-only gate in `auth/dependencies.py` are the nearest pattern. Tool request and answer
travel inside the worker; nothing here gives the model a write route, because the ticket is refused on anything but a read.

**The trap in `test_detection_has_no_ai.py`.** Its list of AI modules is fixed (`:37`), and it admits it cannot see a
provider reached by plain HTTP (`:15-16`). So `nexovue_platform.ai` and `nexovue_scheduler.insight` must be added to
that list in the same change that creates them, and all provider HTTP must live inside `nexovue_platform.ai`. The
evidence code must not import them either, and that gets the same test. **A walk from an API root needs one shared fix:** `lifespan.py:21` imports `mcp` only to type one argument, and every API route reaches it. Whichever of F5, F19 or F20 lands first types it as a small Protocol and moves the walker to `nexovue_platform/import_walk.py` (F19 D55, F20 D38).

## 2. One AI provider interface

### Why three wire formats

The customer research (R9) recommends "any compatible endpoint with regional choice". Reading the providers' own pages
shows one format cannot honestly carry that.

| Fact (read 2026-11-02, through a summarizing tool; check before relying on it) | Source | Consequence |
|---|---|---|
| Anthropic says its OpenAI-compatible layer is "not considered a long-term or production-ready solution for most use cases", and that `strict` on tools is ignored | [Anthropic](https://platform.claude.com/docs/en/api/openai-sdk.md) | A Claude account needs its own format |
| Bedrock's Converse API works on `bedrock-runtime` and accepts Bedrock API keys as a bearer token. AWS recommends short-term keys for production and long-term keys for exploration only | [Endpoints](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints.html), [API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) | The India route the research found needs this format. A stored long key is a real limit; the screen must say so |
| Ollama "supports a subset" of OpenAI's API. `tool_choice` is not ticked. `response_format` is, and its page does not confirm JSON-schema output | [Ollama](https://docs.ollama.com/api/openai-compatibility.md) | Never depend on a forced tool call or on the provider enforcing a schema |
| Several current Claude models refuse forced tool use, and refuse a `temperature` below 1 | Anthropic, same page | No such settings in the shared interface |

### The interface (FD3)

`nexovue_platform.ai` (approved; does not exist yet). Names may be refined by F3; the shape may not.

```python
class AIProvider(Protocol):
    def complete(self, request: AIRequest) -> AIResponse: ...  # one model call
    def embed(
        self, texts: list[str], model: str
    ) -> AIEmbeddings: ...  # optional; F4 only; a customer's embedding model is not its chat model
    def check(self) -> AICheckResult: ...  # "test this connection"
```

- `AIRequest`: `instructions`, `parts` (each either `Trusted` or `Untrusted`, section 3), `response_schema` or
  `tools`, `max_output_tokens`, `purpose` (`explain`, `chat`, `fix`), and ids for the run record.
  **It has no field for temperature, tool choice or top-p.**
- `AIResponse`: the raw text or tool request, token counts (with a flag when a count is estimated, because a server
  reported none), the model name reported, and an outcome: `ok`, `refused`, `too_long`, `limited`, `unavailable`, `bad_key`.
- **Adapters:** `openai_chat` (OpenAI, Azure-style gateways, Ollama, vLLM and others), `anthropic_messages`,
  `bedrock_converse`.
- **Plain HTTP through the existing guarded client** (`probes/http_check.perform`), with wire formats in `nexovue_platform.ai` and transport in `nexovue_scheduler.insight`, since `perform` needs `pycurl`, so every call passes the
  connect-time address check, and redirects are not followed. No provider SDK. A longer timeout is needed than the
  10 seconds a delivery uses.
- **nexovue always validates output itself**, whatever the provider promises. Invalid output gets one retry, then
  counts as a failed run.

**Both customer options are one row in `ai_connections`**, differing only in values: format, base URL, model, region label
and sealed key (empty for a local server with none). No code path may ask "is this local?", with one exception: the
screen's wording about where data goes.

**Private addresses.** A local model lives at a private address, which the guard refuses outside development
(`addresses.py`, `may_connect_to`). So an **install operator's** setting, `AI_PRIVATE_ENDPOINTS` (IP addresses or ranges with ports, since the guard sees only a resolved address, in the
environment, empty by default), allows named private endpoints. A console user cannot widen it, and loopback and
link-local addresses stay refused even then, so the cloud credentials address stays out of reach. On the hosted service
it stays empty.

**What "supported" means.** A provider or model may be connected if `check()` passes. It may be called **supported in
public** only after passing the injection suite and the fault scoring (section 11). The screen must say which.

**Region.** The connection stores what the customer typed, and shows it. nexovue must not claim a region it cannot verify.

## 3. Handling untrusted telemetry

Existing masking removes secrets, and leaves "ignore your instructions" exactly as written
(`apps/workers/internal/pipeline/mask/rules.go:26-38`). This is a separate layer. Input filters are not the defence: they
missed almost everything in log format (LogJack: 1 of 32, 0 of 32, 1 of 32), and these rules assume they will always miss.

### Three kinds of data

| Kind | Examples | Who can move it | Rule |
|---|---|---|---|
| **Facts** | Numbers, times, signal names, severities, ids, deploy order | Real traffic can move a number. Nobody can make a number say words | May be sent as they are |
| **Names** | Service, endpoint, version, exception type | **Anyone with a browser key** (`alerts/payloads.py:204`) | **Replaced by an alias** such as `service_1`, then restored by nexovue afterward |
| **Free text** | Log bodies, exception messages, stack frames, span attributes, deploy metadata, GitHub and Jira text, repository files, case notes, retrieved knowledge | Anyone | Not sent, unless the feature cannot work without it. If sent, fenced and labelled |

A person's own note counts as free text, since people paste logs into notes. A model's own stored words count as free
text when read back, so an explanation or an old answer gains no trust by having been stored.

### The rules (FD4)

- **U1. Facts first.** F3's wording reads facts and aliases only: no free text in its first version. This is the plan's
  "sanitized or replaced" at its strongest, and F6 already promised it for names (`anomaly-detection/design.md:940`).
  `findings.title` must never be sent, because it is composed from names.
- **U2. Fence what must be read.** F4 must read logs to search them, and F5 must read source. Such text is JSON-encoded, cut
  to a cap, stripped of control, bidirectional and invisible Unicode tag characters, and enclosed in tags carrying a random
  value per call. **This helps a little and is never counted as the defence.** The existing caps of 100 rows and 32 KiB stay
  (`assistant/bounds.py:15,18`).
- **U3. Nothing the model says is believed.** Output is parsed against a schema. Each claim must name evidence ids that were in
  its input, or the claim is removed. Aliases are restored by nexovue. Nothing from the model chooses a tool outside the fixed
  read list, or any argument outside its validated bounds.
- **U4. No way out.** F3 and F4 read untrusted text and private data, so by the Rule of Two they must not be able to send anything
  out. Model words are shown as plain React text. **No Markdown rendering, no HTML, and no link or image address written by the
  model.** Links are built by nexovue from evidence ids. Model text must never reach `dangerouslySetInnerHTML`
  (`features/docs/DocsScreen.tsx:89`, the one use today). Text containing a URL, email address or Markdown link is rejected by
  the validator. An image or link is a way out, because the browser fetches it and the address can carry data; this is an inference
  from the research, not a tested attack, and the suite must test it.
- **U5. Model words never leave the console.** Alert messages and webhooks are composed from stored facts by templates, through
  the existing escaping (`alerts/outbox.py:398`, `alerts/payloads.py:42-66`). A Slack message that unfurls a planted link is a way
  out. Pull request text follows the same rule: built from validated facts, with no raw attacker text, since a reviewer's own AI
  tools may read it.
- **U6. Label what rests on free text.** Fencing cannot stop a believable false cause (AIOpsDoom). So each claim states one of three bases, F3's words, used by every feature:
  `verified` (signed deploy records, checks; screen word by F3), `telemetry` (counted from what was sent, so a secret ingest key or a browser key may move it: "Counted from telemetry"), or `telemetry_text` (rests on words anyone can write, including a version only seen in traffic). A claim from `telemetry_text` alone can never be the stated
  likely cause, and approval by a person is required but is not treated as a safety boundary.
- **U7. Every AI link has a suite in the gate**, including the one that exists today. The MCP server returns raw log bodies with
  nothing marking them untrusted (`assistant/tools.py:201-225`), and has no injection test. F4 owns closing this first, by labelling
  untrusted fields in tool results and adding the suite. Nexovue cannot control a customer's own AI on the far end, and must say so.

### The Rule of Two, link by link

| Link | Untrusted input | Private data | Changes something or sends out | How it holds |
|---|---|---|---|---|
| F3 wording | Aliases only | Yes | No | Output is checked data, shown as plain text |
| F4 chat | Yes (names and text fenced, not aliased, because the person typed them too) | Yes | No | Read-only tools, plain text, nexovue-built links |
| F36 MCP | Yes | Yes | **Customer's own AI decides** | Labels and suite. Outside nexovue's fence; documented |
| F5 proposal | Source files and validated facts, no raw telemetry | Yes | No | Output is a proposal stored as data |
| F5 draft pull request | The stored diff, written by a model and re-checked by rules in a room with no model | Yes | Yes | **No model in this step.** A person's click, then a job in `scheduler-connections`, with prose from templates |

## 4. Cost limits and AI off

### On and off (FD5, FD6)

- AI is **off by default for every organization**, per the plan.
- Turning it on is one flow: choose a provider, test it, set a monthly limit, see a preview of exactly what would be sent for a
  real recent finding, and agree. **No limit, no switch.**
- There is one connection per organization, with an optional per-project opt-out, because a project is an environment
  (`models.py`, `Project`) and a customer may want to keep staging out.
- **Source code is separate consent** (F5), given later: sending a repository to a provider is a larger disclosure than telemetry.

### Limits (FD7)

| Limit | Scope | Starting guess | What happens when reached |
|---|---|---|---|
| Monthly tokens | Organization, required | Customer sets it; the screen suggests 2 million, and about 45 million once chat is on (F4 D26), **guesses until the test bed measures a real run**. Warning explanations and chat stop at 80%, fixes at 50%; a critical explanation may use all of it | Runs stop until the next month or a raised limit |
| Tokens per run | One call | Set by each feature | Run stops, marked `too_long` |
| Runs per finding | One finding | One at opening, one when evidence changes materially, and a person's click, at most 3 a day | The click says why it is refused |
| Questions per person per day | F4 | Starting guess 50 | The chat says when it resets |
| Tool steps per question | F4 | Starting guess 8. Each step multiplies error, so fewer is better | Answers with what it has and says so |

- **The unit is tokens, not money.** Nexovue does not know the customer's price. They may enter a price per million tokens; the
  money shown is then labelled an estimate.
- **Reserve before calling.** A run must take its maximum allowance from `ai_usage_monthly` in one atomic update before the call,
  and return what it did not use. Otherwise parallel runs can overspend.
- **Local models have limits too,** because tokens bound CPU time as well.
- **Saving money by not running:** no run for a muted finding until a person clicks for one (F3: a read must not start spending), and none while a finding is flapping
  (`notify.py`, `_flap_guard`), since a flapping service opens 5 findings an hour.
- The per-person limit is not a seat price, and must never be sold as one (`vision.md:152`).

### What each failure leaves behind

| Situation | Finding | Alerts | Facts | Wording | Chat, fix |
|---|---|---|---|---|---|
| AI off | Unchanged | Unchanged | Shown | None. A line appears to admins only when something other than a person turned it off (`ai_connections.off_reason`) | Off, says so |
| `scheduler-insight` stopped | Unchanged | Unchanged | Shown | Waits, then "not available right now" | Says so |
| Provider down, bad key | Unchanged | Unchanged | Shown | `failed`, with the reason to admins | Says so |
| Limit reached | Unchanged | Unchanged | Shown | `over_limit`, with the reset date | Says so |
| Model output invalid | Unchanged | Unchanged | Shown | `failed` | Answer withheld |
| Model abstains | Unchanged | Unchanged | Shown | "Nothing to add to the facts above", which counts as a good outcome | Fix refused |

**Acceptance for all of it:** a test must stop the AI role and run detection, alerts and every non-AI screen; findings must be
byte-for-byte the same as with AI on, apart from explanation columns. An alert must never wait for AI (plan, *Order*).

**One thing that is not switched off.** F36's MCP server is not nexovue's AI. A customer's own AI reaches it with a read token they
made themselves. The AI switch does not affect it, and the settings screen must say this rather than let someone assume otherwise.

## 5. Permissions and audit

Roles stay organization-wide; a person has no per-project limit (`auth/dependencies.py:163-185`). So "only what the asking user may
see" means this organization, or a token's one project. No new access model is needed.

### New actions (FD8)

Each write needs an action (`auth/permissions.py:16-42`), or the build fails (`test_authorization_coverage.py:331`). A viewer appears in
no row (`permissions.py:49`), and that rule stays.

| Action | Who | Gates | Owner |
|---|---|---|---|
| `MANAGE_AI` | Owner, admin | Connection, key, switch, limits, project opt-out | F3 |
| `REQUEST_EXPLANATION` | Owner, admin, member | Reading again, and asking for wording | F3 |
| `CONSENT_AI_DISCLOSURE` | **Owner only** | Agreeing to send telemetry, and separately source, to a provider. Same reasoning as `GRANT_SUPPORT`: it is the account holder's own act | F3, F5 |
| `MANAGE_CONNECTIONS` | Owner, admin | GitHub and Jira connect, disconnect, service-to-repository mapping, code locations | F22, F5 |
| `OPERATE_CASES` | Owner, admin, member | Assign, note, status, link an issue | F22 |
| `DELETE_ANY_CASE_NOTE` | As F22's design sets | Deleting another person's note. An author may always delete their own, even as a viewer: the one exception to the viewer rule | F22 |
| `ASK_ASSISTANT` | Owner, admin, member | Asking F4. It spends money and stores text, so it is not a plain read | F4 |
| `MANAGE_KNOWLEDGE` | Owner, admin, member | Runbooks | F4 |
| `REQUEST_FIX` | Owner, admin, member | Asking for a suggested fix, including a general one without code access (F5 rung 2b) | F5 |
| `OPEN_FIX_PULL_REQUEST` | Owner, admin, member. F5 may narrow this, not widen it | The click that opens a draft pull request | F5 |
| `CONFIGURE_SLOS` | Owner, admin, member | Targets | F19 |
| `EDIT_DASHBOARDS` | Owner, admin, member | Dashboards | F20 |
| `PURGE_DASHBOARDS` | Owner, admin | Hard-deleting a dashboard or a version at once | F20 |

- **Viewers read results and do not ask the AI.** A viewer reads explanations, cases, SLOs and dashboards, and a summary of a fix but not its code (F5 D7).
- **API tokens never trigger AI.** They read. A token that could spend the customer's AI budget would be a new risk. They read no model words, no fix and no dashboard. Who counts as a person is one helper: `Reader.is_person` (F3), and `is_real_person` (F22) for one not in a support view.
- **Support view:** the view is read-only by method (`auth/dependencies.py`), so writes are refused. F4 must also refuse reading a
  person's conversations during a support view, since they may contain pasted data, and that refusal is new work.
- Tokens and routes: F4's ticket is a third kind of `Reader`, read-only, and must not appear on any write route.

### Three records, kept apart (FD9)

| Record | Holds | Who reads it |
|---|---|---|
| `audit_log`, with the existing `<entity>.<past tense>` names (`audit/service.py:41`) | A person's or the system's **act**: `ai.turned_on`, `ai.limit_changed`, `ai.disclosure_agreed`, `connection.created`, `connection.deleted`, `repository.read`, `fix.pull_request_opened`, `case.assigned`. Never a secret, never a prompt | Admins (`permissions.py:61`) |
| `ai_runs` (new) | One row per model call: organization, purpose, subject, provider, model, region as typed, tokens, time taken, outcome, and **which fields were sent, not their contents** | Admins, on the AI settings screen |
| `finding_events` (new) | What happened to one finding, as named kinds with typed fields and no free text, for the timeline | A person reads all. A token reads kinds and times. A support view reads no note bodies |

`ai_runs` contains no telemetry text, so it can be kept and shown without becoming a second store of attacker text. Prompts are not stored by
default. **Debug capture is deliberately not offered in the first version**, since it would become exactly that store.

### Secrets (FD10)

- Reuse `SealedBox` (`sealing.py:95-137`), with **new purposes and new keys**: `AI_SECRET_KEY` for AI connections, `CONNECTION_SECRET_KEY` for
  GitHub and Jira. **A purpose alone would not separate them:** purpose is associated data under the same key (`sealing.py:136-137`), and notification
  channels already share the check key (`sealing.py:172-175`). Whoever holds a key can use any purpose. **The separation comes from which container
  holds which key.** `CREDENTIAL_ENCRYPTION_KEY`, held only by the API (`nexovue.yaml:288`), is the precedent.
- `Integration` is not the pattern. It holds no credential by design (`models.py:743`).
- Every new sealed column must be added to `SEALED_COLUMNS` (`keys.py:93`), or rotation silently leaves it behind. `keys.py` runs in the API's container,
  which must therefore hold every key.

## 6. The shared GitHub connection (F22 and F5)

**F22 owns and builds it, because Stage 3 comes first.** F5 extends it. F3 reads no repository in its first version. F28 does not need it, and stays
independent, because Stage 2 comes before Stage 3 (section 9).

### The decision (FD11)

**A GitHub App, not a personal token and not OAuth.**

- **Why:** the customer chooses repositories at installation. Tokens last 1 hour, and a request can narrow one to named repositories and fewer permissions
  ([GitHub](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)).
  A personal token would act as a person, with their whole reach, and stop working when they leave.
- **On a self-hosted install, each install creates its own App**, using GitHub's
  [manifest flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest): a few clicks, and GitHub hands back the App's
  private key and webhook secret. Nexovue cannot share one App's private key across installs. That page's GitHub Enterprise Server versions suggest this also works
  there, but this was not tried. **Only GitHub.com is promised at first.**
- **F5 adds a second App, `connections.kind = 'github_fix'`,** and F22 ignores it (F5 D36). Permissions belong to an App, so raising the first would reach every repository connected for issue sync. A customer can use F5 with no GitHub issue sync.
- **On the hosted service,** tracker connections do not exist until a shared App is built, which is F22's founder decision 3. No code path may assume either case.

### Three separate tokens

| Use | Permissions asked for at the moment of use | Feature | Who mints |
|---|---|---|---|
| Sync issues | `issues: write`, one repository | F22 | `scheduler-connections`, F22's App |
| Read source for a proposal | `contents: read`, one repository | F5 | `scheduler-connections`, F5's App, which writes a source pack |
| Open a draft pull request | `contents: write`, `pull_requests: write`, one repository | F5 | `scheduler-connections`, F5's App, from a person's click |

- **F22's App never asks for repository write.** Choosing F5 installs its own App, and GitHub asks the customer's organization owner to accept. Nothing asks for repository write
  access before someone chooses F5.
- **A plain warning:** `contents: write` lets a token push to any unprotected branch in that repository. Nexovue's code writes only to a new branch `nexovue/suggestion-<id>` and
  opens a draft (`draft` is a GitHub parameter). The screen must tell the customer to protect their main branch. "Narrow" means this and no more, and the docs must not claim more. The exact
  permission set must be tested against GitHub by F5, since GitHub's page read here names no fine-grained permission for this call.
- **Coming in:** one route for both trackers, `POST /api/v1/connections/webhook/{connection_id}`, reusing the signature check (`deploys/signatures.py`), with no delivery table, because a replay only asks for a re-read. **A self-hosted install may be unreachable from GitHub**, so F22 reconciles by polling, and webhooks only ever make that faster.
- **Text from GitHub and Jira is untrusted** (section 3).
- **Calling a customer's Jira or GitHub Enterprise** goes through the same guarded client. Cloud first: typed Jira addresses are limited to `*.atlassian.net`, and `CONNECTION_PRIVATE_ENDPOINTS` is not built yet.

**Why a new role, `scheduler-connections`, and not `scheduler-deliveries`:** the Compose file says deliveries does that "and nothing else", so a slow receiver holds only its own slots (`nexovue.yaml:388-389`). A slow
GitHub must not hold back an alert.

**Jira** shares the `connections` table and the sync machinery, with its own kind, token and rules. It sits under F22 and is no part of F5.

**Mapping:** `service_repositories` says which repository or Jira project a service's issues go to (F22). `service_code_locations` says which repository and folder its code lives in (F5), because the first may be a Jira project. A service with no code location gets no source-based fix, and the
screen says so.

## 7. How finding, explanation, case and fix relate

**Per ADR-022 and F6 D1, one detected problem is one record, and a finding grows.**

```
findings ──┬── finding_incidents  (exists)   threshold incidents, linked, not merged
 (exists,  ├── finding_explanations  (F3)    frozen evidence, claims, wording; versioned
 grows     ├── finding_events  (F22)         the timeline, append-only, no free text
 columns)  ├── finding_notes  (F22)          what people wrote
           ├── finding_links  (F22)          GitHub issue or Jira issue
           └── finding_fixes  (F5)           proposal, its approval, its pull request; children hold source packs and checks
```

### What grows on `findings` (FD12, F22 owns)

- `state` stays exactly as it is: whether the **signal** is open or closed. It closes itself on recovery (`0041:256,264`), so it cannot also mean "someone is still investigating".
- **New `case_state`:** null, then `investigating`, `resolved`. New `assignee_id`, `case_opened_at`, `case_resolved_at`, `root_finding_id`, and the working columns F22's design lists (`case_resolution`, `case_activity_at`, `case_worked_at`, `assignee_acted_at`, `case_resolved_by`, `case_warned_at`).
- **A case is a finding with a non-null `case_state`.** There is no `cases` table, and there must never be one.
- **It starts with no form** (R8). One button, **Take this**; assigning, noting or linking an issue also starts the case. No required severity, role or field.
- **No automatic case per finding.** A flapping service opens many findings an hour (code map). The vision's "case created automatically" is met by every finding already being ready to become one. The catalog's
  separate auto-created cases are replaced, as `ADR-022` already says.

### The hard cases, decided

| Situation | Decision | Why |
|---|---|---|
| **Signal recovers while people still work** (`state=closed`, `case_state=investigating`) | Both stand. The screen shows "recovered, still being investigated" | The columns mean different things |
| **New finding on the same service while its case is open** | New finding opens as F6 says (one open per service still holds, `0041:279-292`), and its `root_finding_id` points to the case's finding, so it joins that timeline, **if a person acted on that case within 7 days (F22 FR9)** | Avoids two cases for one problem, with no change to F6's indexes. A forgotten case must not swallow a new failure |
| **Retention** | A case's finding stays until its derived retention has passed **after `case_resolved_at`**; an unresolved case stays, until 90 days without a person's act, or 72 hours after recovery if nobody did more than take it (F22 D71). F22 amends `_PAST_DERIVED_RETENTION` (`detection/retention.py`) | Otherwise a resolved case vanishes on a plan clock mid-work. Closed findings keep their existing rule |
| **Threshold or check incident with no finding** | Stays in F21's lifecycle, and does not become a case in the first version. A linked one stays in the finding page's *Linked alerts* section | ADR-022 leaves this to Stage 3. Incidents belong to a rule, and giving them case columns repeats the merge ADR-022 rejected. Revisit if real users ask |
| **Timeline read** | `GET /findings/{id}/timeline` from `finding_events`, not `/audit` | `/audit` is admin-only (`permissions.py:61`), and a member must see their case's history |
| **SLO burn** | An F21 incident of new condition kind `slo_burn`, linked through `finding_incidents` when a finding is open. **It adds no second alarm** for a problem already reported (R7) | A burn does not fit a finding: three signals only (`detection.py:38`), self-closing, adopting after 7 days. The catalog's "into the findings feed" becomes "shown on the finding it joins, and on the SLO screen". A burn with no finding is an alert, not a finding. Also needs a migration for its frozen vocabulary (`0041:113`) |

### The explanation (FD22, F3 owns)

`finding_explanations` has one row per version, keyed on `finding_id` and `version`.

- **`evidence`**: frozen JSON list, each item with a stable id, kind (`deploy`, `error_group`, `dependency`, `check`, `incident`, `other_finding`, `traffic`, `infrastructure`, `absence`), the values, and the time it was read.
- **`absence` is a real kind.** "No deploy information for this service" must be stated, and it must never read as "no deploy happened" (customers research).
- **`likely_cause`**: from correlation code, not from the model. Null means not enough evidence, which is a valid outcome.
- **`wording`, `wording_state`** (`off`, `pending`, `done`, `failed`, `over_limit`, `abstained`, `held`): the model's plain-text sentences, with claims and the evidence ids each rests on.
- **Why frozen.** Evidence dies on different clocks: raw data at 7 days and rollups at 30 on the seeded Free plan (`0005_plans_table.py:34`), readings at 35 days, so links will break. The copy must hold names, numbers, exception type and group id, **not error templates or log bodies**, because a stored finding must not outlive the telemetry it names (`detection/retention.py`). It is deleted with its finding. A link to raw data that has aged out must say so.
- **A project finding** has no readings, thin signals and a null `service_name` (`findings/service.py:330`). F3 must cover it or say plainly that it does not.
- **The word is "likely", always** (R1). `PrecedingDeploy`'s "before, not because" (`errorgroups/schemas.py:15-26`) is the model for every claim about a deploy.

### The fix (FD14, F5 owns)

`finding_fixes` has one row per proposal: its `explanation_version`, state (working states, then `proposed`, `refused`, `approved`, `stale`, `opened`, `failed`, with a separate `pull_request_state`; F5's design holds the list), the change as validated data, what evidence led to it, what was not checked, the
files read, and the pull request's address. A draft requires a non-null `likely_cause`; a stack alone may reach a patch; whether it may reach a draft is F5's founder decision 4 (until then F5 D58 builds drafts for a signed deploy only). The default is a suggestion on the finding; a draft pull request is an extra, explicit click. **A general fix suggestion without code access (F5 rung 2b, founder's decision 2026-11-02) needs a likely cause or a linked error group whose exception type passed F3's pattern.** It is written from F3's facts only, is labelled as not the customer's code, and never becomes a patch, a pull request, an alert or an email. Its row is `finding_fixes` with kind `general`.

### Notifications

"The explanation joins it later" needs one new kind, `explained`. F3 owns it, including the frozen vocabulary (`alerts/model.py:47`, `0041:117`, `test_alert_vocabulary.py`) and `_compose_finding` (`messages.py:275`). **At most one per finding per channel**, capped per channel,
only to channels people read (`READ_BY_PEOPLE`, `channels.py:34`), never paging PagerDuty, only for a signed cause (strength `likely`, basis `verified`), and written from facts by template (U5). It needs no AI, so it works with AI off. It must not be added to
`FINDING_TOLD_KINDS` (`detection.py:234`), which decides closure and already has its meaning.

**How the facts job starts.** It is enqueued by task name inside detection's transaction (`project_job.py:925,1113`), because the later point has no finding ids and a crash there would lose the job, so detection imports nothing new. Whether to run the AI step is decided by the evidence job, never by detection.

## 8. Names

All of these are approved names and exist nowhere today. A feature may add to this list, and must not rename what is here without saying why.

### Tables

| Table | Owner | Note |
|---|---|---|
| `ai_connections` | F3 | One per organization. Sealed key |
| `ai_project_opt_outs` | F3 | |
| `ai_usage_monthly` | F3 | Per organization and month |
| `ai_runs` | F3 | No telemetry text |
| `finding_explanations`, `finding_explanation_ratings` | F3 | |
| `evidence_error_group_reads`, `explanation_stopped_rules` | F3 | |
| `releases`, `release_periods`, `release_sync_state`, `deploy_intake_state` | F28 | ClickHouse: `service_versions_1m` |
| `finding_events`, `finding_notes`, `finding_links`, `finding_emails_owed` | F22 | |
| `connections`, `connection_repositories`, `service_repositories` | F22 | Not `integrations`, which exists and means something else (`models.py:743`). `connections.kind` holds `github`, `jira`, `github_fix` |
| `assistant_conversations`, `assistant_messages`, `assistant_steps`, `assistant_settings`, `assistant_question_counts`, `assistant_monthly_counts`, `assistant_read_log`, `runbooks` | F4 | A ticket lives on its message: no `assistant_tickets` |
| `knowledge_chunks` | F4 | The first vector column, added by a `setup` step, not a migration, so a missing extension cannot stop `setup` |
| `finding_fixes`, `finding_fix_source_packs`, `finding_fix_checks`, `finding_fix_code_views`, `service_code_locations`, `fix_source_consents`, `fix_repository_settings`, `github_user_links`, `github_link_attempts` | F5 | |
| `slo_targets`, `slo_status`, `slo_daily_counts`, `slo_exclusions`, `slo_target_changes`, `slo_deleted_months` | F19 | |
| `dashboards`, `dashboard_versions` | F20 | Panels as checked JSON inside |

New columns: `findings.*` (F22, section 7); `deploy_events.commit_sha` and `door` on `spans` and `logs` (F28); `door` on `service_edges_1m` and `notification_channels.sends_explained` (F3); `incidents.about_*` and `notifications.withheld_*` (F19). **`deploy_events` needs no environment column:** a project already is an environment (`models.py`, `Project`), a correction to the code map's "missing".

### Words to keep apart

| Word | Means | Never means |
|---|---|---|
| `ai_*` | Nexovue's use of a provider | |
| `llm`, `llm_observability` | **The customer's** model calls, as telemetry (`llm_observability/router.py:18`) | Our AI. Do not use `llm` for it |
| `assistant` | Today, the MCP server (`assistant/server.py`). F4's chat joins it | |
| `connection` | GitHub or Jira | A provider. That is `ai_connections`, always with the prefix |
| `integration` | Existing CloudWatch and Postgres role connectors | Anything new |
| `insight` | The role and queue only | A product word. On screens, say "explanation" |
| `case` | A finding with `case_state` | A separate record |
| `incident` | F21's rule-bound record | A case |

### Code, queues, roles

| Thing | Name |
|---|---|
| Shared AI code | `nexovue_platform.ai` |
| AI tasks | `nexovue_scheduler.insight` (queue `insight`, role `scheduler-insight`) |
| Evidence tasks | `nexovue_scheduler.evidence` (queue `evidence`). It must not import `nexovue_platform.ai` |
| Other AI queues | `assistant` (F4), `insight-fix` (F5), both on role `scheduler-insight` |
| Connector tasks | `nexovue_scheduler.connections` (queues `connections`, capped at 2 of 3, and `connections-interactive`; role `scheduler-connections`) |
| Release sync | Queue `releases`, on `scheduler-light`, capped at 2 under the floor (F28) |
| SLO work | Queues `slo` (cap 2), `slo_counts` (1), `slo_history` (1), on `scheduler-light` under the floor; **no new role or container** (F19 D66, the founder's decision of 2026-11-02, superseding D39's `scheduler-slo`) |
| Event writer | `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, in `nexovue_platform/cases.py` (F22) |
| API's ClickHouse account | `nexovue_api_reader`, for all the API's reads (F20 D45) |
| Deploy reading both sides need | `DeployHistory` moves to `nexovue_platform`, because the scheduler cannot import the API (F28 does this) |
| Keys | `AI_SECRET_KEY`, `CONNECTION_SECRET_KEY`, each with `*_PREVIOUS_KEYS` like the existing pairs |

### Routes, under `/api/v1`

| Route | Owner |
|---|---|
| `/ai/connection`, `/ai/connection/check`, `/ai/preview`, `/ai/consent`, `/ai/switch`, `/ai/project-opt-outs/{project_id}`, `/ai/usage`, `/ai/runs`, `/explanations/...` | F3 |
| `GET /findings/{id}/explanation`, `POST .../explanation/read-again`, `.../refresh`, `.../feedback` | F3 |
| `/releases`, `/releases/{id}/comparison`, `.../errors`, `.../rollout`, `.../sender-details`, `/releases/markers`, `/releases/coverage`, `/deploys/webhook-secrets` | F28 |
| `PATCH /findings/{id}/case`, `GET .../timeline`, `/notes`, `/links` | F22 |
| `/connections`, `/connections/webhook/{connection_id}`, `/connection-tests`, `/service-repositories` | F22 |
| `/assistant/conversations`, `/assistant/conversations/{id}/messages`, `/assistant/availability`, `/assistant/settings`, `/assistant/reads`, `/assistant-quotes/{message_id}`, `/runbooks` | F4 (`/api/mcp` stays as it is) |
| `/findings/{id}/fixes` and its children, including `.../draft-pull-request`, `/findings/{id}/brief/files`, `/code-locations`, `/connections/github/...` for the Fix App, consent and a person's link | F5 |
| `/slos`, `/slos/{id}/budget`, `/slo-readings/...` | F19 |
| `/dashboards`, `/dashboard-panels/read`, `/dashboard-panels/preview` | F20 |

**Console:** `/releases`, `/slos`, `/dashboards`, `/settings/ai`, `/settings/connections`, `/settings/explanations`, `/settings/runbooks`, `/findings/sample`. A case list is the findings feed with a filter, not a new screen. `route-manifest.ts:18-34` is a closed list, and `route-manifest.test.ts` holds it
to the router. F20 owns what `/` becomes (section 9).

### The checklist every new table and route must pass

| Adding | Must also change | Or this fails |
|---|---|---|
| Any Postgres table with tenant data | `organizations/tables.py` | `test_deletion_live.py` |
| A ClickHouse table | `erasure/tables.py` | Erasure |
| A telemetry read route | `TELEMETRY_PATHS` (`ratelimit/policy.py:234`) | `test_ratelimit_coverage.py` |
| A write route | An action and `POLICY_OF` | `test_authorization_coverage.py:331` |
| A sealed column | `SEALED_COLUMNS` | Nothing fails, so review must catch it, and a new test should |
| A notification or condition kind | Frozen copy in a migration | `test_alert_vocabulary.py` |
| A ClickHouse statement | A module constant that binds the tenant | `test_tenant_chokepoint_structure.py` |
| A scheduler Postgres statement | An `org_id` bind, or a named cross-tenant exception | `apps/api/tests/test_scheduler_postgres_tenancy_structure.py`, from F6 |
| An AI module | `REPOSITORY_AI_MODULES` | Nothing, silently |
| A customer-facing change to setup | `docs/product/setup/` | Review |

## 9. Who owns what, and what each gives the others

### Order

**F6 must merge first.** Findings, the outbox's finding subject and migrations 0041 to 0050 exist only on this unmerged branch (`anomaly-detection/status.md`). No later feature should branch from `main` before that.

Then F28 → F3 → F22 → F4 → F5 → F19 → F20, as the plan's stages say. **Two small pieces go early, straight after F6 merges:** F20's Step 1, moving `/` to the Findings feed (F20 D58, FD15), and F4's Step 1, the MCP findings tool and untrusted labels, so it can be shown in the one conversation round (F4 D88, founder decision 8). Neither needs anything but F6.

**One browser-door change, before Stage 1 reaches a customer** (approved, founder decision 7 below; was F3's founder decision 3; F28 owns it, decision 22). Anyone holding a page's public site key can today send spans that count in rollups (read, not run; F3 row 8, F19 *Forged spans*). F28, F3 and F19 each need part of one fix, which gets one record under `docs/changes/`: browser-door spans stay out of `service_stats_1m`, `endpoint_stats_1m` and `service_versions_1m`; the service cap keeps the busiest; going over the cap is recorded; `synthetic.*` readings come only from the prober's door. F3 adds the door to `service_edges_1m` itself (A63). F28's Step 4 and F19's Step 4 wait for it.

### Foundation pieces

| Piece | Owner | Others |
|---|---|---|
| `scheduler-insight`, `nexovue_insight` account, keys | F3 | F4 and F5 add grants |
| Provider interface | F3 | F4 adds `tools` and `embed`; F5 adds source fenced input |
| Untrusted-text helpers, validator, fixtures | F3 | All AI links |
| `Reader.is_person` | F3 | F22's `is_real_person`, F4's ticket |
| Scheduler Postgres tenancy test | Exists, from F6 | F28 extends it first |
| `lifespan.py` import and `import_walk.py` | First of F5, F19, F20 | All API no-AI walks |
| Cost limits, AI switch, consent, `ai_runs` | F3 | F4's per-person limit; F5's separate consent |
| Evaluation bed | F3 starts it | F4 and F5 add suites (section 11) |
| GitHub and Jira connection | F22 | F5 extends it |
| `case_state`, timeline | F22 | `explained` is written by F22's T2.9 inside F3's job; F5 adds its own kinds |
| MCP untrusted labels and findings tool | F4 | |
| New actions | The owner in section 5 | |
| `sizing.md`, install kit's new secrets and accounts | Whichever feature adds the container | |
| What `/` shows | F20 | |

### Interfaces

| Feature | Gives | Takes |
|---|---|---|
| **F28** Releases | `DeployHistory` in `nexovue_platform/releases.py`; `deploys_in_window`, signed events only; `releases_seen_in_window`, labelled `telemetry_text`; `release_comparison(...)`, **by version first, through a new rollup `service_versions_1m`, with a time comparison as a labelled fallback** (F28 D1); `coverage_of`, saying whether this service sends deploys at all. To F20 and everyone: `markers_in_window`. It uses no AI and no connection | F6's findings, for "findings since this release" |
| **F3** Explanation | To all: `finding_explanations`, with evidence ids. To F21: the `explained` kind. To F22: events on explanation. To F4: explanations as a tool result. To F5: `fix_brief()`, validated facts only, for F5's model, on one explanation version (F5's copyable brief is its own template) | F28's functions; existing error groups, `service_edges_1m`, checks; `finding_incidents`; F6's frozen signals |
| **F22** Case | `case_state`, `root_finding_id`; `RECORD_FINDING_EVENT_SQL` with `finding_event_parameters()`, in `nexovue_platform/cases.py`, which the API and scheduler each execute; `connections`, `service_repositories`; to F4: notes and resolved cases as knowledge sources | F3's explanation, shown on the case; F6's lifecycle, which must record events |
| **F4** Ask | MCP untrusted labels; a findings tool; a catalog of read-only tools for the chat, shared in words and limits with MCP, whose bodies stay as they are; `knowledge_chunks`, deleted with their sources. **Each answer must show the query and time range it ran** (R6) | F3's provider, limits and fence; F22's notes, for knowledge. It offers answers that cite findings, and gives nothing back into the record |
| **F5** Fix | `finding_fixes`; events; to F22: fix on the timeline. Writes to GitHub only on a person's click | F3's fix brief, `likely_cause`, provider; F22's connections table and job role; `error_group` items' `group_id` and window, to find files |
| **F19** SLOs | `slo_targets`; `slo_burn` condition kind for F21, on `incidents.about_key`; `slo_budget` panel type for F20. Reads rollups' counts (`000008`, `000035`). A target must be limited to what the rollups can count exactly: endpoints are approximate (`endpoint_stats_1m`, code map), and a 30-day window sits at the edge of Free's retention | F21's routing, one-incident rule; `finding_incidents`; `readings.py` arithmetic |
| **F20** Dashboards | The batch read route, `/dashboard-panels/read`, needed because 30 panels at 10-second refresh would spend the whole quota (`policy.py:78`; arithmetic, not a measurement). A fixed list of panel types compiled to fixed statements, never free-form queries (`alerts/telemetry.py:1-13`) | `TrendChart`, F28's `markers_in_window` and `coverage_of`, F19's panel, stored by name and read from Postgres alone. No AI writes a dashboard |

**The front door (FD15).** The founder's recorded decision is that "the Findings feed is what a person lands on", and Overview holds `/` "until the insight engine exists" (`feature-catalog.md:317-325`). Findings now exist, and `/` is still Overview (`router.tsx:245`). F20 owns moving `/`
to the feed, with Overview at `/overview`, because the founder's decision stands and nothing has reversed it. F20 must also handle a project with no findings, so that the first thing a new user sees is not an empty page.

## 10. Plan packaging assumptions

**These are assumptions for the documents to build on. What is sold, to whom and at what price stays the founder's.**

- **Self-hosted: everything is free, including all of Phase 2.** Self-hosting is free and no licence machinery exists (`vision.md`, 2026-09-14), open-core is deferred (2026-09-15), and a self-hosted install has no plan allowance (`deployment.py`). The research says case management in the free product is the real
  gap (OpenObserve keeps it in Enterprise).
- **Hosted: no Phase 2 feature is gated by plan, apart from AI** (next point), and tracker connections, which are absent rather than gated until F22's founder decision 3 is answered. Plans keep differing by service count, bytes and retention. **No `plan_features` table** is added, since nothing would read it. Adding a paid feature later stays ordinary, and withdrawing a free one is what makes people leave (`vision.md`, 2026-09-15).
- **AI on the hosted service: the customer's own key only, when hosting opens** (founder decision 1, approved 2026-11-02). Off until then. One function, `ai_available(deployment)`, in `nexovue_platform/deployment.py` (the evidence job needs it and may not import `nexovue_platform.ai`), must answer this, and the design must let a customer's own key work there later with no schema change.
- **Never per seat** (`vision.md:152`). No limit may count people as a price.
- **No small count limit on SLOs or dashboards.** Limits here are for protecting the system, stated as such, and not as upsells. Honeycomb's limit of 2 on Pro is what to differ from.
- **Plan clocks affect features.** Free's 30 days of rollups limit SLO windows and case history. **The seeded values were not traced through later migrations**, and each feature must read the current values before relying on them.

## 11. The shared evaluation bed

**One bed, started by F3, extended by each AI link.** It builds on the plan's test bed and F6's tools (`tools/otel-demo`, `tools/detection-seed`), under new `tools/ai-eval`. The result of one run is one report, committed under `docs/research/`.

| Run | Input | Scores | Owner |
|---|---|---|---|
| **Faults** | The plan's 11 fault flags, and the 14 faults `tools/fault-bed/fault_model.py` holds (F3 row 21), each injected repeatedly | Caught; time to catch; stated cause right, wrong, or **honest miss**; every claim links to data | F3 (F6 already scores caught) |
| **Healthy** | No fault | Every stated cause is wrong (Causely's 67% result is why). Every finding counts as false | F3 |
| **Replay** | 5 to 20 known incidents with one whose right answer is "not enough evidence" (Rootly, NeuBird). **None exists yet; the first come from fault runs, then real customers** | Each miss classified: missing source, wrong context, wrong reasoning | F3 |
| **Injection** | LogJack's public 42 payloads, including 10 harmless controls, adapted from AWS logs to nexovue's fields; AIOpsDoom-style false causes; attempts to leave through output; disguised references | Instruction followed; way out reached; false cause stated; harmless controls not blocked | F3, then F4 and F5 |
| **Chat** | Questions with known answers | Right number, right time range shown; refusal to read another tenant | F4 |
| **Fix** | A fixture repository, since the demo's faults are coded flags (F5 D30). **Rung 2b:** synthetic findings, with hostile exception types and endpoint names, on free-tier or local models only | Proposal refused when thin; **zero writes without a click**. Rung 2b: no address, shell command, install step or secret reaches a person; hostile names never reach the request except as aliases | F5 |
| **Cost** | All the above | Tokens per explanation, question and fix. **This replaces every guess in section 4** | F3 |

**Gates (R2).** Passing means, on every option Nexovue names:

- **Hard, today:** every claim links to data; abstaining works; no write happens by any route other than a person's click; the injection suite passes; AI-off changes nothing.
- **Reported, then the founder sets the bar:** accuracy. Do not adopt the common "80% precision" before measuring. ORCA-bench's best agent got 25.3% of medium tasks right, on a demo system, and vendors claim 82% and 94.5%. These are different tests.
- **"The injection suite passes" must be defined carefully.** LogJack's own warning is that its rates are an upper bound, and no suite proves a model safe. Passing means no write, no way out, and no unlabelled false cause, across repeated trials, because a run of one proves little. It never means "injection-proof".

**Both options run through it.** A local model's result may be weaker, on both quality and safety: LogJack found 86.2% for one commonly self-hosted model and 0% for another. One model in one paper is thin, and it still cuts against assuming local means safer. **Which local model to document is decided from these results, not now.** Nothing has been measured on a 16 GB machine.

**Limits to state wherever results appear.** The demo is not real traffic (founder accepted, plan *Risks*). Results are from a demo and must say so.

**Where it runs, and on what models (founder decisions 4 and 9, 2026-11-02).** No rented server and no paid model calls. Short runs and replays run on the founder's Mac, against free tiers of AI providers or a local model, on synthetic data only. Weeks-long runs and every server measurement wait for the Oracle deployment after Phase 2 is built. Until then, no accuracy, limit or size from the bed is published. See *What A and B change*.

## 12. What Nexovue brings to the table

### True today (with source)

| Claim | Source |
|---|---|
| AGPL source, free self-hosting, no signup or phone-home for images | `vision.md`, 2026-09-14 and 09-15; ADR-004 |
| Detection that holds no AI, enforced by a test | `test_detection_has_no_ai.py`. It has known limits (`:15-16`) |
| Statistical findings, with a person's feedback, on the customer's own server, on this branch, awaiting review | `anomaly-detection/status.md` |
| Rollups written before sampling, so request and error counts are exact | `000024` header, `000035` |
| A read-only MCP server with nine tools | `assistant/tools.py`. **With the injection gap still open** |
| Signed deploy records, and "before, not because" wording in the code. F28 hardens the signature, which today covers the body only | `deploys/signatures.py`, `errorgroups/schemas.py:15-26` |
| Alerts to email, Slack, Teams, Discord, PagerDuty and webhooks | `channels.py:34-35` |
| Pricing that never rises with people | `vision.md:152` |

### True once built, only if these designs hold

These must stay in the future tense until shipped (competitors research).

1. **One open-source product that stores telemetry, detects, explains, tracks and drafts the fix, with AI in the customer's own account or on their own server.** Of the products checked, none was confirmed to do all of that in its open-source edition. OpenObserve keeps AI and incidents in Enterprise; SigNoz's Noz is Cloud;
   Grafana needs a Cloud stack. **OneUptime's README describes a free Community edition with an agent on the customer's server and own or local models; whether its fix path is free is unconfirmed (F5 T9.6). Strike this claim if it is.** That is a statement about what was checked, not proof that nothing else exists.
2. **Facts stay when AI goes.** The explanation's facts work with AI off, because correlation holds no model.
3. **A fence you can read.** The model cannot write, can reach no telemetry database or repository key (not "no route to GitHub": its network is open unless the optional outbound limit is on), and this is held by tests on an AGPL codebase anyone may read. Rivals describe human approval; the pages read did not describe this separation, though they may have it. **Say what Nexovue does, never what others lack.**
4. **Free case management on your own server**, with no per-person charge from nexovue. No saving comparison: rivals have free plans (F22).
5. **SLOs on the free product with no small count limit.** SigNoz's lack is the only rival gap supported, as of 2026-11-02.
6. **Published fault scores and injection results**, if the founder agrees. Coroot and HolmesGPT publish results on their own test sets (F3 research), so this meets a norm on faults; no vendor found publishes injection results. They would be demo results, and must say so.

### Must not be claimed

| Do not say | Why |
|---|---|
| "The only self-hosted AI root cause" | Aurora, HolmesGPT, OpenObserve Enterprise |
| "Your data never leaves your server", as a blanket | False on the customer's own AI account. True only on a local model. See *Founder decisions*, 2 |
| "Finds the root cause" | Evidence and a likely cause only. Independent accuracy is low |
| "More accurate than X" | Nothing is measured |
| "Injection-proof" or plain "safe" | Read-only limits damage and does not stop injection. False causes remain possible |
| "Works as well on a local model" | Unproven. The plan flags it |
| A general suggestion (F5 rung 2b) "fixes your code" or was written from it | Nexovue never read that code. It is an example pattern from the finding's facts, and is labelled so |
| "The only tool where a person approves the fix" | It is the norm: Datadog, Sentry, incident.io, Rootly |
| "Replaces PagerDuty, incident.io or Rootly" | On-call stays with PagerDuty, and no status pages or Slack-run response are planned |
| A deploy "caused" a finding, or a version seen in telemetry is a deploy | Order is not cause, and a seen version can be planted |
| "Better than Grafana" for dashboards | Parity is the aim |
| Anything on DPDP, RBI or SEBI compliance | No lawyer has read it. State only where data goes |
| Naming rivals in public | Their facts change monthly, and four are unresolved (competitors research) |

### Where rivals are plainly ahead

Maturity and customers: Nexovue is pre-deployment with none. Grafana on dashboards. Sentry and Datadog on tying problems to code. incident.io and Rootly on running an incident. HolmesGPT on data sources, and **free agents bolted onto an existing stack are the competitor most likely to be missed.** Security paperwork was not checked.

### Answering "why not just use Claude?"

A commenter asked this ([HN](https://news.ycombinator.com/item?id=50009404)). The honest answer: claims joined to stored facts, there the minute a finding opens, with permissions applied, needing no pasting of context. "Too much manual input of required context" is the top barrier in Grafana's own survey, which is seller-published and not specific to small companies.

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| FD1 | Where AI code runs | (a) In the API. (b) In `scheduler-light`. (c) New role, same image. (d) Third image | **(c).** ADR-019 allows a third image only for heavy dependencies, and plain HTTP has none. (a) and (b) put a model beside every secret. **ADR-019's open point closes with this; F3's `design.md` must carry it as a proposed ADR section.** A guess until a real provider is tried |
| FD2 | What the AI role may reach | (a) Reader account, as the code map suggested. (b) No telemetry account: it is handed frozen evidence, and F4 reads through the API | **(b).** Stronger than the plan's rule, and F3 needs nothing else. It costs a new Postgres account, and job functions in place of table rights. Correct over cheap |
| FD3 | Provider interface | (a) One OpenAI-style format. (b) Provider SDKs, or a framework. (c) Three plain-HTTP adapters | **(c).** (a) rests on a layer Anthropic says is not production-ready, and misses Bedrock's Converse. (b) loses the connect-time address guard |
| FD4 | Main injection defence | (a) Input filter. (b) Model choice. (c) Structure, with replacement and fencing added | **(c),** as R3. The evidence against filters is strong, and the customer chooses the model |
| FD5 | Who turns AI on | Per organization / per project / per organization with opt-out | **Per organization with project opt-out.** The key and bill are the organization's, and staging may differ |
| FD6 | Does the AI container always run | (a) Always. (b) Started by the operator | **(a).** Otherwise "turn it on" needs a shell. The cost is one idle container, memory unmeasured |
| FD7 | Spending control | None / optional / required hard limit, in tokens | **Required,** as R5. Tokens, since nexovue does not know the price |
| FD8 | Who may ask the AI | Viewers too / those who work | **Those who work.** It spends and stores, and "a viewer appears in no row" is worth keeping. Consent is owner-only, like support access. Friction accepted |
| FD9 | Storing prompts | Store / do not store / optional debug | **Do not store.** Otherwise `ai_runs` becomes a second store of attacker text. Debugging is harder; accepted for now |
| FD10 | Separating secrets | (a) New purpose under the existing key. (b) New keys, given only to the right containers | **(b).** A purpose does not stop a key holder, as `sealing.py` shows |
| FD11 | GitHub connection | (a) Personal token. (b) OAuth. (c) GitHub App, one per self-hosted install, and a second if F5 is chosen | **(c).** Chosen repositories, 1-hour narrowed tokens, and no person's reach. Setup is a little longer. GitHub.com first; Enterprise Server later, once tried |
| FD12 | Where case state lives | (a) `state`. (b) New columns on `findings`. (c) `cases` table | **(b).** (a) breaks because findings close themselves. (c) contradicts ADR-022 |
| FD13 | Whether a threshold incident becomes a case | (a) Yes. (b) Not in the first version; it links | **(b).** See section 7. F22 may overrule with reasons, as ADR-022 assigns it |
| FD14 | Whether a draft can exist without a likely cause | Yes / no | **No.** Thin evidence must not reach a one-reviewer team as a pull request. A labelled stack may reach a patch, and F5's founder decision 4 settles the rest. **A fix needs a cause:** F5's general suggestion without code access (rung 2b, 2026-11-02) needs a likely cause or a recognised error type, and never reaches a patch or a draft |
| FD15 | What `/` becomes | (a) Leave Overview. (b) Move to the feed | **(b),** as the founder's recorded decision says. F20 owns it |
| FD16 | SLO burn | (a) Grow the finding. (b) F21 incident, linked | **(b),** as R7, without a second alarm |
| FD17 | Model words in alerts | (a) Include. (b) Facts by template only | **(b).** Keeps a way out closed, and works with AI off. Cost: messages read less smoothly |
| FD18 | Free text for F3's first version | (a) Send error messages. (b) Facts and aliases only | **(b).** Safer, and wording may be poorer. ORCA-bench suggests more context helps, so the bed should measure it before this changes |
| FD19 | Hosted AI's default | On with own key / off | **Off until hosting opens; then the customer's own key only** (founder decision 1, approved) |
| FD20 | Plan gating | Gate some features / none | **None.** Matches the recorded decisions on self-hosting and open-core |
| FD21 | Local model in nexovue's install | Bundled / customer's own | **Customer's own.** The founder said nexovue runs no AI service, and the sizing excludes it |
| FD22 | Keeping evidence | (a) Links to live data. (b) A frozen copy on the explanation, without log bodies | **(b).** Evidence ages out on different clocks, and the plan requires every claim to link to its data. Costs some storage, unmeasured |
| FD23 | Where GitHub and Jira sync runs | (a) `scheduler-deliveries`. (b) New role `scheduler-connections` | **(b).** A slow GitHub must not hold back an alert. It costs one more container, memory unmeasured |
| FD24 | How F4 reads telemetry | (a) Move every read service into `nexovue_platform`. (b) Run the chat in the API process. (c) The AI role calls the API's read routes with a short-lived read-only ticket | **(c).** It keeps the fence, the person's access and the rate limit without moving code. It costs a little latency, and F4's design must prove the ticket cannot reach a write |

## Founder decisions

**All approved by the founder on 2026-11-02.** Items 1 to 8 were approved first, each as recommended. Items 25 and 26 are the founder's own later decisions on F5, the same day. Items 9 to 24 are the questions the seven feature specs then asked; the founder approved them all as recommended, with two changes, A and B, which also rewrite item 4. Each feature's spec marks its own items approved, and its `status.md` records the answers under *Founder answers, 2026-11-02*.

1. **Approved 2026-11-02.** **AI on the hosted service** (plan, open question 1). It decides who pays for models, and makes nexovue a processor of telemetry going to a provider. **Recommendation:** when hosting opens, offer the customer's own key only. Never pay for inference until measured costs are in hand. Nothing here needs this answer yet, and F3 builds for either.
2. **Approved 2026-11-02.** **The selling line** (customers research, 1). "All on your own server, so your data stays with you" is false for a customer using their own AI account. **Recommendation,** one wording built from F3's and F4's, true with chat on or off: "Your telemetry is stored on your server. AI is off until you turn it on, and you choose where it runs: your own AI account, or a model on your own server. Finding explanations send only numbers, times, fixed labels and stand-in names, and you can read the exact request first. Chat, if you add it, sends questions, log text, notes and runbooks under its own consent." Do not use the "own server" clause until a local model passes the bed (F3 item 2). Your approved wording, and what is sold, so your call.
3. **Approved 2026-11-02.** **A lawyer before any India compliance claim** (customers research, 3). Real money, unpriced. **Recommendation:** no public claim on DPDP, RBI or SEBI until then; state only where data goes.
4. **Approved 2026-11-02, changed the same day by B.** **Money for the evaluation bed.** *First approved:* one cap of about US$300 for F3 and F4 model calls. *Now:* **no paid model calls.** The bed's AI runs use free tiers of AI providers, or a local model (for example Ollama), and synthetic data only (change B, below). There is no rented test server either (change A, item 9).
5. **Approved 2026-11-02.** **Publishing fault scores and injection results,** and F5's verdict and refusal numbers. Coroot and HolmesGPT publish on their own test sets, so on faults this meets a norm; on injection it would be new among products checked. It would show weak numbers in public. **Recommendation:** yes, after you have seen the first run, labelled as demo results, and keep results outside the repository until then, because the repository may be public (F4 item 4).
6. **Approved 2026-11-02.** **How long a self-hosted install keeps data** (F22 item 2, F19 item 2). Nothing assigns a plan on a self-hosted install, so it silently gets Free's 7 days of raw data and 30 days of rollups, and a resolved case goes after 30 days. It decides what "free on your own server" means. **Recommendation:** an operator setting for both numbers now, keeping today's values until the bed measures disk use, then raising the default, with 395 days of derived data the likely target (a guess).
7. **Approved 2026-11-02.** **The browser-door change before Stage 1** (section 9; F3 item 3). **Recommendation:** take it before Stage 1 reaches a customer. **Owner: F28** (item 22).

8. **Approved 2026-11-02.** Five to eight conversations with on-call engineers and CTOs. **One round, not five.** F28 asks for them before its Step 1b, and F4, F5, F19 and F20 each add questions and a rule for what the answers change, in their own specs. Hold the round before F28's Step 1b, using all their questions. It costs your time, and it is the one thing no research here could answer.

### Items 9 to 26, approved 2026-11-02

Each was asked in a feature's spec, which holds the full recommendation. "As recommended" means that text, unchanged.

9. **Approved 2026-11-02, changed by A.** **The test server** (`phase-2-plan.md`, open question 2). *Asked:* the price of a rented server for the long runs. *Now:* **no rented test server.** Long runs and measurements happen on Oracle Cloud once the Phase 2 features are finished and deployed. See *What A and B change*, below.
10. **Approved 2026-11-02, changed by B.** **F5's model-call money** (F5 item 3, about US$150). *Now:* **no paid budget.** F5's runs use free tiers or a local model, like F3 and F4.
11. **Approved 2026-11-02, as recommended.** **F3's accuracy bar before "Likely: this deploy"** (F3 item 2). Per rule, wrong causes at most one tenth of the causes it states, rounded down; no cause at all on the healthy run; a rule stating fewer than 5 causes is not judged and stays silent. The bar is now fixed, before any result exists.
12. **Approved 2026-11-02, as recommended; run per B.** **F4's bar before a provider is "supported"** (F4 item 2). Right number on at least 80% of known-answer questions, right time range on at least 90%, every hard gate passed, each with its statistical bound. It is run on free-tier or local models.
13. **Approved 2026-11-02, as recommended; run per B.** **F3's go or stop on AI wording** (F3 item 1). Go only if at least 3 of 5 blind readers prefer the model's sentence and it adds something the template lacks. T2.11's calls use a free tier.
14. **Approved 2026-11-02, as recommended.** **Public wording.** F5's source sentence (F5 item 2) and F20's three sentences, including "we never price by person" (F20 item 4). Caps and limits are published only after they are measured, which is now on Oracle after deployment (A).
15. **Approved 2026-11-02, as recommended.** **Naming rivals in this public repository** (F4 item 3). Allowed only as dated internal research, headed "internal research, not a comparison or a claim", with unverified facts marked. Never in sales or marketing text. The header goes on the committed research files too, before this branch merges.
16. **Approved 2026-11-02, as recommended.** **Three more items for the lawyer** (item 3): liability for AI-written code (F5 item 1), nexovue's servers ever holding customers' source (F5 D59), and customers quoting SLO figures to their own customers (F19 item 2). Until then: plain "provided as is, you review and merge" terms before F5's rung 3, and SLO figures labelled for internal and board use only.
17. **Approved 2026-11-02, as recommended.** **Jira** (F22 item 1). Send Atlassian the one written question (F22 T0.1). Build Jira only on a written yes. With a no, or no answer in 4 weeks, F22 is done with GitHub only, and `phase-2-plan.md`'s *Done when* is amended to say so.
18. **Approved 2026-11-02, as recommended.** **F5's go or stop on write access, at Step 6** (F5 item 4). Go only on the stated conditions. Drafts from a stack alone stay "no" unless most buyers ask; GitHub Enterprise Server stays "later".
19. **Approved 2026-11-02, as recommended.** **Tracker sync on hosted** (F22 item 3). The shared GitHub App, and the Atlassian app if Jira is kept, are a launch condition for hosted paid plans (F22 step H).
20. **Approved 2026-11-02, as recommended.** **13 months of SLO figures on every plan, Free included** (F19 item 1). This overrides section 10's "Free's 30 days of rollups limit SLO windows" for F19's daily rows only.
21. **Approved 2026-11-02, as recommended.** **The rules for the buyer round** (item 8). F4: build Steps 2 to 7 only if at least half say yes to both questions (3 of 5, 3 of 6, 4 of 7, 4 of 8). F20: build the full builder only if at least half (3 of 5, 4 of 8) would not buy without it, counting only answers backed by a dashboard they built; otherwise templates only.
22. **Approved 2026-11-02, as recommended.** **F28 owns the shared browser-door change** (item 7).
23. **Approved 2026-11-02, as recommended.** **One free GitHub test organization, plus one spare free personal account,** shared by F22's Step 3 and F5's GitHub trial (F22 D93, F5 item 5). Nothing private goes in it.
24. **Approved in advance, 2026-11-02, as recommended.** **F28's later gate, only if Step 1a fails** (F28 spec). If only a cost check fails: keep version comparison and pay, if it fits the 16 GB server. If a correctness check fails: time comparison only.
25. **The founder's own decision, 2026-11-02.** **F5 stays approved.** Its design had asked not to be approved before the GitHub trial and the buyer round. Instead both block F5's Step 1, and a result that changes the design comes back to the founder (F5 D64).
26. **The founder's own decision, 2026-11-02.** **F5 rung 2b: a general fix suggestion without code access.** Where AI is on but Nexovue cannot read the service's code, a member may ask for a general pattern with example code, written from F3's facts only (FD18), checked by code, labelled "Example pattern, not your code. Nexovue has not seen your code. Check before using." It reads no repository and needs no GitHub connection or source consent. FD14 and section 5 carry it (F5 D65 to D72).

27. **The founder's own decision, 2026-11-08.** **The buyer round is a customer-validation milestone, not a build gate.** It changes items 8, 21 and 25 and F5 D64: no step waits on the conversations. Every feature is built to its full approved plan, which is the "yes" side of each rule in item 21 (F4 Steps 2 to 7; F20's full builder; F19 Step 4 onward past its demand gate; F28 Step 1b; F5 Step 1 onward). The round still happens, using `docs/product/phase-2-buyer-round.md`, and its answers are read afterwards as validation: an answer that argues against something built comes back to the founder, it does not stop work.
28. **The founder's own decision, 2026-11-08.** **No step waits on a real GitHub test organization (item 23).** F22 Steps 3 to 6 and F5's GitHub parts are built and tested against a simulated GitHub. Every check that needs real GitHub (F22 T3.0's manifest flow from a private address, T5.2's real webhook, F5's GitHub trial) is recorded as "not yet run, founder tests after Phase 2" and run by the founder then. Jira (F22 Step 7) still waits on Atlassian's written answer (item 17), which is a policy gate, not a test.

### What A and B change

**A. No rented test server now.** Building goes ahead. Anything that needs a machine that stays on for weeks, or a number about a real server, waits until the Phase 2 features are deployed on Oracle Cloud and measured there. Until then, every such number, limit, size or accuracy claim stays unpublished, and where a screen or page would show it, it reads "not yet measured".

**Oracle's free allowance is below nexovue's smallest supported server.** Oracle's Always Free Arm allowance is 1,500 OCPU hours and 9,000 GB hours a month, "equivalent to 2 OCPUs and 12 GB of memory", plus 200 GB of block storage and 10 TB of outbound data a month; an instance idle for 7 days may be reclaimed ([Oracle, Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), read 2026-11-02). nexovue's smallest supported server is 16 GB and 4 CPUs (`docs/self-hosting/sizing.md`), whose ceilings already add to about 13 GB. So:

- **The free allowance cannot prove the 16 GB sizing.** It is Arm, which nexovue's images support (ADR-019, amd64 and arm64), but it has 12 GB and 2 OCPUs. ADR-006 already says the full stack "barely fits" it, and the demo and bed add more on top.
- **A 16 GB measurement needs a paid Oracle shape,** for example the `VM.Standard.A1.Flex` with 4 OCPUs and 24 GB that `docs/operations/first-deploy.md` names, with the containers held to the 16 GB defaults. Above the free allowance, Oracle's Arm rate is reported as US$0.01 per OCPU hour and US$0.0015 per GB hour (an Oracle regional price page and a third-party review, through search, not confirmed on Oracle's main price list). By arithmetic, 4 OCPUs and 24 GB costs about US$28 a month above the free part. That is money, so the shape comes back to the founder at deployment.
- **The bed should not share a server with paying customers.** Its demo load and injected faults would compete with them. Run it before the first customer, or on a second instance.

**B. No paid model calls.** The bed's AI runs use free tiers or a local model, with **synthetic data only**: the demo, `tools/fault-bed` and fixture repositories, never a customer's data. What the free tiers offer, read 2026-11-02:

| Provider | Free use | Limits | May it train on inputs? |
|---|---|---|---|
| Google Gemini API | Free tier; also has an OpenAI-style endpoint with tool calling ([docs](https://ai.google.dev/gemini-api/docs/openai)) | Per model and project, shown in AI Studio, not on the public page ([rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)) | **Yes.** Unpaid use is used to improve Google's products, and human reviewers may read it ([terms](https://ai.google.dev/gemini-api/terms)) |
| Groq | Free plan | For `openai/gpt-oss-120b`: 30 a minute, 1,000 a day, 8,000 tokens a minute, 200,000 a day; the page does not clearly say which plan these are ([rate limits](https://console.groq.com/docs/rate-limits)) | Not stated on its data page. It keeps no inference data by default, may log for up to 30 days for abuse, and offers zero retention to all ([your data](https://console.groq.com/docs/your-data)). Secondary sources say its agreement bars training; not checked |
| OpenRouter, `:free` models | Free models | 20 a minute; 50 a day, or 1,000 after US$10 of credits ever bought ([limits](https://openrouter.ai/docs/api-reference/limits)) | Depends on the provider behind each model; a separate setting for free models ([logging](https://openrouter.ai/docs/guides/privacy/provider-logging)) |
| Mistral | "Experiment" plan, phone check | About 1 request a second (third-party sources only) | Third-party sources say free use may train by default, with an opt-out. Not checked on Mistral's own page |
| Cloudflare Workers AI | Free plan | 10,000 "neurons" a day ([pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)) | Not checked |
| Local model (Ollama) | Free | The founder's 24 GB Mac (F3 A81, F4 D80) | No; nothing leaves |

What B costs, stated plainly:

- **Rate limits are low.** A long run takes days, not hours. A single F4 question is about 31,000 tokens and an F5 call about 25,000 (guesses), larger than Groq's 8,000 tokens a minute for the model above. Gemini's free tier, or the local model, is the likely path for those.
- **Free tiers may train on what is sent, and a person may read it.** Hence synthetic data only. The injection payloads are public already.
- **"Supported" is claimed only for what was tested.** None of OpenAI, Anthropic or Bedrock, which customers use most, offers a free API tier. So the OpenAI-style adapter is tested through Gemini, Groq, OpenRouter or Ollama; **Claude's own format and Bedrock's stay "not yet tested"** against the real service, and setup pages say so, until someone tests them. One unconfirmed route: AWS gives new accounts up to US$200 of credits usable on Bedrock ([AWS, July 2025](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/)); whether Claude on Bedrock works on such an account was not checked.
- **A local model on the Mac competes with the dev stack for memory.** Decision 0047 flags it. F3 replays with the stack stopped (A81); F4 cannot, because chat reads the live API (D80).
- **A free-tier or local model is not the model a customer will use.** Results name the model and say so.

**What now waits until after deployment on Oracle** (each feature's `status.md` lists its own):

- F6's weeks-long false-alarm run (`phase-2-plan.md`, *Test bed*), and so the plan's false-alarm bar.
- F3 Step 2c: the rules' weeks-long scoring, T2.17's report and T2.18's check against the bar. **Until then no rule states a likely cause;** F3 ships facts only.
- F3 and F4's long model runs (F3 Step 6, F4 Step 4's report) where they need the demo running for days. Short runs and replays go ahead on the Mac with free tiers or a local model.
- F19 AC49, the weeks-long fault run, which reads "not run" until then. AC50's scale run may go ahead on the Mac's Docker, as F6's AC29 did, but its figures (the 200-target limit, `scheduler-light`'s 768M with SLO work on it) are not published as server figures before Oracle.
- F20 T5.7 and T11.6 may run on the Mac as build gates (their pass-mark file names the machine), but no cap or "how many at once" is published until they are rerun on Oracle.
- F22 T5.2 (a real GitHub webhook reaching a reachable server, so AC130 stays open) and its poll and write figures; F28's whole-server fit on 16 GB. Per-container memory (F22 T4.9, F28 T1.6) can still be read on the Mac's Docker.
- Every memory ceiling in section 1, and every `sizing.md` row they add; item 6's raised retention default, which waits for the bed's disk figures.

**Comes back to the founder at deployment:** which Oracle shape the measurements run on (the free allowance cannot show 16 GB), and whether the bed runs before the first customer or on its own instance.

## Not checked

- Anything running: no model was called, and every figure about memory, tokens, latency and quality is a guess.
- What the shared Postgres account may do today; how the install kit creates a new Postgres account (F3 answers the bundled image's case).
- Whether GitHub's manifest flow works from an install with no public address; the exact GitHub permissions a draft pull request needs; Claude on Bedrock's India regions through Converse with an API key.
- Whether `error_groups_seen` is filled with no alert rule (code map). Seeded plan values through later migrations.
- Provider pages were read through a summarizing tool, on 2026-11-02. Check before quoting.

## Sources

**In this repository:** [`phase-2-plan.md`](phase-2-plan.md), [code map](../research/phase-2-code-map-2026-11.md), [competitors](../research/phase-2-competitors-2026-11.md), [customers](../research/phase-2-customers-2026-11.md), [`ADR-019`](../decisions/ADR-019-two-images-each-role-its-own-container.md), [`ADR-022`](../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md), [`ADR-023`](../decisions/ADR-023-the-scheduler-writes-detection-state-to-clickhouse-through-its-own-account.md), [F6 design](../features/anomaly-detection/design.md). The research names its own outside sources, including LogJack, AIOpsDoom, ORCA-bench, Causely and Meta's Rule of Two; those figures are quoted from it and not re-read here.

**Read for this document, 2026-11-02:**

- [Anthropic: OpenAI SDK compatibility](https://platform.claude.com/docs/en/api/openai-sdk.md)
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility.md)
- [AWS: Bedrock inference endpoints](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints.html), [Bedrock API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html)
- [GitHub: installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [App manifest flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest), [Create a pull request](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)
