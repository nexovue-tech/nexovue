# Plan: AI chatbot (F4)

> **Status:** Awaiting founder approval, 2026-11-02. No file is edited until the specification, the design and this plan are
> approved.
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under each step name the
> file that changes and the test that proves it, read from the code. Where a file or test is new, it says *new*, and the path is a proposal.

## Preconditions

- [`spec.md`](spec.md) and [`design.md`](design.md) approved, including the proposed ADRs and the differences from the foundation.
- **F6 merged.** Findings and migrations 0041 to 0050 exist only on this unmerged branch (foundation, section 9). Step 1 needs this and nothing else, so it is built straight after F6 merges, ahead of the foundation's feature order, so the shared customer round can see it (D88).
- **For Step 2 onward, F3 built:** `scheduler-insight`, `nexovue_insight`, `AI_SECRET_KEY`, `nexovue_platform.ai` (provider contract, fence, validator), `ai_connections`, `ai_project_opt_outs`, `ai_usage_monthly`, `ai_runs`, `/settings/ai`, `shared/url-window.ts`, `TASK_SETS`, `INSIGHT_GRANTS`, the `ai` network, `tools/ai-eval`. F3's design exists and these documents were reconciled with it in review. F3 gives `nexovue_insight` no grant on `jobs`, and proves no row-level security, so T2.0a proves F4's own.
- **Before Step 2 starts: the founder's five to eight conversations** with on-call engineers and CTOs, the one shared round held before F28's Step 1b (foundation item 8), with Step 1 to show them, and the founder's continue rule, approved 2026-11-02 before the round (spec, *Founder decisions needed*, 1; foundation item 21). They need only the founder's time, and do not wait for F3. Ask: where would you ask this at 3 a.m.? Is Claude Code with MCP enough? Would you give a provider log text? Is a read-only answer without a dashboard useful?
- **For Step 7:** F22's `finding_notes` and `case_state`. **For two tools in Step 3:** F28's `GET /releases` and `GET /releases/{id}/comparison`.
- **Before the real-model parts of Step 4:** nothing to approve. Foundation decision 4, changed 2026-11-02: no paid model calls; a free tier of a cloud provider and the local model, on synthetic data only.

## Current system

Read from the code on 2026-11-02, on this branch with its uncommitted work. Nothing was run.

### What exists and is reused

| What | Where |
|---|---|
| MCP server, nine read tools, token auth | `assistant/server.py`, `tools.py:152-304`, `auth.py:46-78` |
| Result bounds, which say when they cut | `assistant/bounds.py:23-50` |
| The read door: person or token, with a project scope | `auth/dependencies.py:162-243` |
| The byte-identical 404 for an out-of-scope project | `projects/service.py:282-293` |
| Tenant-bound ClickHouse reader, one construction site | `auth/dependencies.py:246-257` |
| Read surface held by a list | `tests/test_read_surface_structure.py:41-68` |
| Actions and route gates; coverage test | `auth/permissions.py`, `auth/policy.py`, `tests/test_authorization_coverage.py` |
| Rate limiting by person, organization and token | `middleware/ratelimit.py:136-156,299-350`, `ratelimit/policy.py` |
| A remembered owner for a credential | `ratelimit/token_owners.py` |
| Ticket pattern: fingerprint, expiry | `support/tickets.py:48-63`, `ViewAsTicketRecord` in `models.py:2107` |
| Sealing and rotation | `nexovue_platform` `SealedBox`, `keys.py:93` |
| Job queue, caps, wake | `jobs.py:30-84`, `scheduler/jobs/worker.py:72`, `queue.py` `enqueue(wake=True)` |
| Expiry sweep | `jobs/expiry.py:32-42`, `detection/retention.py` |
| Deletion inventory | `organizations/tables.py`, `tests/test_deletion_live.py` |
| A person's removal | `members/service.py:188-204`, `scrub` |
| Personal data export | `profile/personal_data.py`, `tests/test_personal_data_live.py` |
| Stable log row identity | `LogLine.row_key`, `logs/search.py:84,186-195` |
| An exact range in the address | F3's `shared/url-window.ts` (`ai-insights/design.md:222`) |
| Panel parts: buttons, failure, skeletons, confirm, unsaved changes | `shared/components/` |
| Settings navigation, held by a test | `shared/settings/settings-nav.ts`, `route-manifest.test.ts` |

### Where the code disagrees with the documents

| # | A document says | The code says | What this plan does |
|---|---|---|---|
| C1 | Research: deleting a person deletes conversations, as children of `users` | `scrub` keeps the row (`members/service.py:188-204`), so nothing cascades. Only an organization's deletion does | T2.5 deletes them in `scrub` |
| C2 | Research: exact-range deep links "mostly ready", not checked | The window is a browser store (`selection.ts`). `/logs` takes only `service` and `severity` from the address (`router.tsx:330-335`), and no route reads a project | F3's `?from=&to=&project=` carries the range (D33), and a click never moves the person's window. T3.3 gives `/logs` `q`; T3.3a adds `project=` to the other destinations |
| C3 | Foundation: `nexovue_platform.ai` calls through `probes/http_check.perform` | `nexovue_platform` cannot import `nexovue_scheduler` | F3 settled it: formats in the platform, transport in `nexovue_scheduler.insight`. F4's own client is separate |
| C4 | F3 AC44: the AI account sees only `insight` jobs | Chat needs queue `assistant`. F3 gives no grant on `jobs`, and uses three functions that hard-code the queue | T2.3 adds `assistant` to `insight_claim_jobs`, `insight_retry_job` and `insight_finish_job`, plus F3's parity test |
| C5 | Foundation: 384 MB for the AI role | `scheduler-deliveries` has 384 MB for 4 slots (`nexovue.yaml`), and chat needs 6 | T2.3 sets 576 MB, a guess. T8.1 measures |
| C6 | Research: `/service-map` needs moving | Confirmed: `servicemap/router.py:17,29` takes `CurrentIdentity` | T1.5 |
| C7 | Design draft: `scope["route"].path` can be compared with `/api/v1/...` | It is relative to the innermost router: `/search` for `/api/v1/logs/search` (`metrics.py:92-94`; probe, FastAPI 0.162.0) | Allow by handler (T2.2) |
| C8 | Draft: percent-encoding closes path tricks | Uvicorn decodes before routing (`h11_impl.py:202`; probe: `abc%2Fdetection` reached `/services/{service}/detection`) | Path slot rule (T2.8) |
| C9 | Draft: `GET /deploys` for a time range; `get_deploy_impact` | `GET /deploys` takes no range, and F28 says it never will (`deployment-release-tracking/design.md:529`). F28 replaced the comparison route | `list_releases`, `get_release_comparison` (T3.1) |
| C10 | Draft: `tools/loadgen` plants a chosen log line | It sends fixed browser vitals and errors. `tools/gateseed` is the OTLP sender | T1.0 |
| C11 | Draft: the expiry sweep writes `unavailable` | `jobs/expiry.py` only deletes (`:32-42,49-55`) | `assistant_sweep` (T2.9) |
| C12 | Draft: counters in `metrics.py` from tasks | No scrape sees a child (`metrics.py:1-12`) | Read from Postgres (T8.2) |
| C13 | Draft: `vector` must exist before Step 2's migration | Nothing before Step 6 needs it, and a failure there stops `setup`, which runs before everything (`nexovue.yaml:54,119`) | `setup` step (T6.1) |
| C14 | Draft: AC24's aged-out range comes from routes | `resolve_window` only orders two times (`window.py:29-37`), so an old range returns empty | The loop decides it (T3.1) |

### Work the reading showed is already done

- `LogLine.row_key` already exists, so no new log identity is needed.
- `TELEMETRY_PATHS` already includes `/findings` and `/service-map` (`policy.py:240,248`).
- `readsurface/spec.py` builds from routes, so the published document follows T1.5 with no edit of its own (`test_read_surface_spec.py`, header).
- pgvector's image and extension are on the install (`compose.infra.yaml:138,149`, `01-extensions.sql`). No Python package is needed; typed SQL casts do.

## Change required

As [`spec.md`](spec.md) and [`design.md`](design.md).

## Impact

| Area | What changes |
|---|---|
| Files | API: `assistant/` (new `tickets.py`, `router.py`, `service.py`, `schemas.py`, `queries.py`), new `assistantquotes/`, new `runbooks/`, `auth/`, `middleware/ratelimit.py`, `ratelimit/`, `findings/router.py`, `servicemap/router.py`, `services/router.py`, `infrastructure/router.py`, `logs/search.py`, `members/service.py`, `profile/`, `organizations/tables.py`, `keys.py`, `main.py`, `roles.py`, `setup.py`. Scheduler: new `insight/assistant*.py`, `insight/api_client.py`, `insight/knowledge.py`, new `knowledge/`, `jobs/`, `metrics.py`, `beat.py`. Platform: new `assistant_tools.py`, `ai/` tools and embed, `ai/testing.py`. Web: new `features/assistant/`, new `features/runbooks/`, `app/layout/`, `shared/`, and 18 files' layout variants. Deploy: compose (both stacks), grants, monitoring. Tools: `tools/gateseed`, `tools/ai-eval` |
| Services | API; `scheduler-insight` gains a queue and slots. Non-AI queue gains one sweep |
| Schema / migrations | One migration for the assistant tables, one for runbooks and chunks. F22 step adds `note_id`. Vector columns come from a `setup` step |
| UI | Panel, TopBar button, Chat section in `/settings/ai`, `/settings/runbooks`, finding page link, `/logs` `q` |
| Tests | New: catalog, tickets, hostile provider, validator, knowledge, panel. Changed: read surface, MCP tools, authorization coverage, policy, ratelimit, deletion, keys, personal data, compose contract, detection-has-no-AI |

## Reuse

Everything in *What exists and is reused*. In particular: **no new read code for tools**, as each is a route; **no new rate limiter**, only a branch; **no new queue machinery**, only a cap; **no second Markdown or text renderer**, only React text.

## Gaps

- The ticket, its reader branch and rate-limit branch.
- The catalog, loop, validator and internal client.
- Conversations, steps, consent, and runbooks.
- Knowledge search and its writers.
- The panel.
- Chat's and MCP's injection suites.
- Tool calling and embedding in the provider contract.
- A provider and a seed the live stack can reach.

## Steps and tasks

Tests are named as files. `apps/api/tests/` and `apps/scheduler/tests/` are shortened to `api:` and `sch:`. Paths under `apps/api/src/nexovue_api/` and `apps/scheduler/src/nexovue_scheduler/` are shortened the same way.

### Step 1 — A safer MCP server, with no AI of ours

**Ships value with F3 unbuilt, and closes a gap open today (U7).**

*Judgeable:* the founder connects Claude Code to the local MCP server with a token (`Settings → API tokens`), and asks "what findings are open?" and "show me the logs for `checkout` yesterday from 14:00 to 14:30". The tool results name the untrusted fields. Then plant a log line saying "ignore your instructions", using `tools/gateseed --log-body` (T1.0), and read what Claude Code does. **The result is a demonstration and not a score.** The founder shows this in the conversations that decide Step 2.

- [ ] **T1.0** — `tools/gateseed/`: a `--log-body` option sending one chosen log record to the receiver with the dev key (`defaultEndpoint`, `config.go:13`) · proved by a Go test in `tools/gateseed`
- [ ] **T1.1** — `api:assistant/bounds.py`: an `untrusted` argument, in place. Moving it to the platform waits for T2.7, so Step 1 stands alone · proved by `api:test_mcp_tools.py`
- [ ] **T1.2** — `api:assistant/tools.py`: every result of a tool returning free text names its untrusted fields; `list_checks` stops dumping whole config (`:267`); `api:assistant/server.py`: `SERVER_INSTRUCTIONS` gains the paragraph · proved by `api:test_mcp_tools.py` (new `test_every_free_text_tool_labels_it`), `api:test_mcp_live.py`
- [ ] **T1.3** — `api:assistant/tools.py` `window()`: a fixed range or hours, both refused, at most 7 days; applied to every windowed tool · proved by `api:test_mcp_tools.py`, `api:test_mcp_live.py`
- [ ] **T1.4** — `api:assistant/tools.py:120`: a malformed `project_id` raises `InvalidRequestError` · proved by `api:test_mcp_live.py`
- [ ] **T1.5** — `api:servicemap/router.py:29`: `CurrentReader`, `require_readable_project`; `api:test_read_surface_structure.py` `THE_SURFACE`; `docs/product/setup/api.md` · proved by `api:test_read_surface_structure.py`, `api:test_read_surface_spec.py`, `api:test_read_surface_live.py` (a project-scoped token)
- [ ] **T1.6** — `api:assistant/tools.py`: seven new tools (design, section 7); `api:test_mcp_tools.py` `THE_TOOLS` and `READS` · proved by `api:test_mcp_tools.py` (every tool is a read), `api:test_mcp_live.py` (scope; `get_finding` has no person fields)
- [ ] **T1.7** — `api:test_mcp_injection.py` *new*: planted text in **every string or dict field of each tool's response model**, walked from the model, stays labelled and JSON-encoded, or is on a closed allow-list of enum, id and time types; including service name, endpoint and rule name, across findings, incidents, checks, services and the map · proved by itself, in `just check`
- [ ] **T1.8** — `docs/product/setup/api.md`: new tools, labels, fixed ranges, the plain statement of what nexovue cannot control; `docs/features/team-and-api-access/spec.md` FR32's list · proved by review

### Step 2 — One question, end to end, with three tools

**Starts once F3 is built. The thin path, in the real panel, before widening (ADR-0019).**

*Judgeable:* with AI on, on the dev stack, the founder agrees to chat's consent in `/settings/ai`, opens the panel on any screen, and asks "what is failing right now?", **against one real local-model call.** Steps appear live; the answer arrives whole, with citations and a link. Stops the AI container: the question says "not available right now" within about 2 minutes, and every other screen works. Signs in as a viewer: no button. **Then the founder decides whether to continue, on the rule set before the interviews.**

**Test stack**

- [ ] **T2.0** — `tools/ai-eval/fake_provider/` *new*: a fake-provider container with scripted and hostile modes, in the dev Compose profile, at a fixed private address on the `ai` network, listed in `AI_PRIVATE_ENDPOINTS` (F3's guard takes addresses, not names, and refuses loopback); and `HostileProvider` in `nexovue_platform/ai/testing.py`, importable by both test suites · proved by `sch:test_jobs_live.py`, reaching it from the role

- [ ] **T2.0a** — **Spike, first, because a failure changes T2.1.** `sch:test_assistant_account_live.py` *new*: create the role, policies and `assistant_finish_message` in a test, as F3's T3.1 does for its account, on both a superuser and a non-superuser owner: AC67's real statements, and finished rows hidden. **Fallback:** the role loses all table grants on messages and steps, and `SECURITY DEFINER` functions hand out one message by id and take each write. If that fails too, the authors redesign and Step 2 stays stopped until the new design passes this spike (D85) · proved by itself

**Credential**

- [ ] **T2.1** — `api:assistant/tickets.py` *new*: prefix, mint, seal, `valid_for`; migration `apps/api/migrations/versions/00NN_assistant.py` *new*: all assistant tables (no vector columns), their row-level policies, `assistant_finish_message` and `assistant_record_spend`; `api:data/postgres/models.py`; `api:organizations/tables.py`; `api:keys.py` `SEALED_COLUMNS` · proved by `api:test_assistant_tickets_live.py` *new* (every refusal of AC11), `api:test_deletion_live.py`, `api:test_keys_live.py`
- [ ] **T2.2** — `api:auth/dependencies.py`: the `get_current_reader` branch, allowing by handler (`TICKET_HANDLERS`); `Reader.message_id`, beside F3's `Reader.is_person`; `is_token` kept; `api:findings/router.py:90`; `_refuse_a_token_here` and `api:deps.py:175` gain the prefix · proved by `api:test_assistant_tickets_live.py`: a catalog route passes, parametrized (AC53); `GET /llm/calls` and `GET /findings/stats` refused, a non-HTTP scope refused (AC7); AC8, AC12; a table test over every kind of `Reader` (AC105)
- [ ] **T2.3** — `api:roles.py`: `scheduler-insight`'s `--queues assistant,insight --concurrency 6 --tasks insight`, and `api`'s `--no-access-log`; `sch:jobs/worker.py` `QUEUE_CAPS`, `assistant` first; `deploy/compose/nexovue.yaml`, `compose.infra*.yaml` and the dev `docker-compose.yaml`: API joins `ai`, 576 MB, `INTERNAL_API_URL`, the worker command; `api:setup.py` `INSIGHT_GRANTS`, with `EXECUTE` on F4's two functions; `assistant` added to `insight_claim_jobs`, `insight_retry_job` and `insight_finish_job`, the claim taking a queue list, and F3's parity test (`sch:test_insight_account_live.py`) · proved by `api:test_compose_contract.py`, a live test from inside the container (AC66), `api:test_assistant_grants_live.py` *new* running the worker's real statements under `nexovue_insight`, and each refused write (AC67), `api:test_api_access_log_live.py` *new* (AC65), `sch:test_jobs_live.py`, filling each queue in turn, with the other still claimed (AC29)
- [ ] **T2.4** — `api:middleware/ratelimit.py`: the ticket branch; `api:ratelimit/ticket_owners.py` *new*; `api:ratelimit/policy.py`: `tier_for` · proved by `api:test_ratelimit_callers.py` (AC13), `api:test_ratelimit_coverage.py`
- [ ] **T2.5** — `api:auth/permissions.py`, `api:auth/policy.py`: `ASK_ASSISTANT`, `MANAGE_KNOWLEDGE`; the five copies (`api:test_policy.py`, web `shared/identity.ts`, `shared/identity.test-util.ts`, `features/members/MembersScreen.test.ts`, `team-and-api-access/spec.md`); `api:members/service.py` `scrub`, settling first; `api:profile/personal_data.py`; `docs/features/personal-data/spec.md`; `sch:jobs/expiry.py`: per-message expiry, deleted projects, and per-table error handling · proved by `api:test_policy.py`, `api:test_authorization_coverage.py`, `api:test_personal_data_live.py` (AC34), `api:test_assistant_privacy_live.py` *new* (AC31, AC33, AC35, AC59, AC72), `sch:test_jobs_live.py`

**The API and the consent**

- [ ] **T2.6** — `api:assistant/router.py`, `service.py`, `schemas.py`, `queries.py` *new*; `api:main.py`: availability, conversation and poll routes, ask, stop, wrong, reads; one-in-flight, stale-row marking; `assistant_question_counts`; settings and consent, against F3's fingerprint; the context probe that refuses "ready for chat" when a known-size prompt comes back counted short (D87) · proved by `api:test_assistant_api_live.py` *new* (AC1, AC2, AC3, AC5, AC6, AC26, AC37, AC54, AC71, AC74, AC79, AC86, AC88, AC91), `api:test_authorization_coverage.py` (`POLICY_OF`, `EXEMPT`)

**The loop**

- [ ] **T2.7** — `nexovue_platform/assistant_tools.py`: catalog with field kinds, three tools (`list_findings`, `list_services`, `search_logs`); `bounded` moved from `api:assistant/bounds.py`, caps as arguments · proved by `api:test_assistant_catalog.py` *new* (AC12, AC14), `api:test_mcp_tools.py`, `sch:test_detection_has_no_ai.py`
- [ ] **T2.7a** — `nexovue_platform/ai/`: `tools` on `AIRequest`, and tool-call build and parse in `openai_chat`, `anthropic_messages` and `bedrock_converse`. F3 leaves this to F4 (`ai-insights/design.md:395`) · proved by `packages/py-platform/tests/test_ai_tool_calls.py` *new*, one fixture per format, including a call and text together, and a provider ignoring the tools (AC94)
- [ ] **T2.8** — `sch:insight/api_client.py` *new*: fixed base URL, no redirect, encoded; `ArgumentRule`'s path slot rule · proved by `sch:test_assistant_api_client.py` *new*, and `api:test_assistant_path_tricks_live.py` *new*, through real uvicorn (AC10)
- [ ] **T2.9** — `sch:insight/assistant.py`, `assistant_prompt.py`, `assistant_answer.py`, `assistant_sweep.py` *new*; `sch:jobs/registry.py` `TASK_SETS["insight"]`, **not** `TASK_MODULES`; `sch:beat.py`: the sweep's entry, on `insight`; the loop, one clock, budget check, compare-and-set writes, settling through `assistant_finish_message`, shortening old results, FR40's mask, the checks of design section 4 (citations, ways out, labels, arguments, copying; quotes and figures come in Step 3) · proved by `sch:test_assistant_answer.py` *new* (AC16, AC18, AC21, AC61, AC62, AC64), `sch:test_assistant_loop.py` *new* with a scripted provider (AC28, AC55, AC56, AC58, AC60, Stop during the last call), `sch:test_assistant_budget_live.py` *new* against real Postgres (AC25, AC57), `sch:test_jobs_live.py`, really killing a child between calls and at the hard limit, reading `ai_usage_monthly` (AC27), and with the worker absent (AC54), `sch:test_beat_schedule.py`
- [ ] **T2.10** — `sch:test_assistant_hostile.py` *new*: offline, loop plus validator plus a recording fake API client (AC15a), in `just check`. `api:test_assistant_hostile_live.py` *new*: real writes counted by row diff, no other tenant's rows (AC9, AC15b), in CI on every pull request, and a failure when absent. `features/assistant/hostile.test.ts` *new*: rendered output (AC15c) · proved by themselves
- [ ] **T2.10a** — `api:assistant/`: `sch:test_assistant_structure.py` *new*: only conversation routes read `assistant_messages.answer`; detection and alerts never name it (AC81) · proved by itself

**The panel**

- [ ] **T2.10b** — `main` becomes an `@container`; `lg:` and `xl:` layout variants change to container variants, each threshold chosen for its screen, since `@lg:` is not 1024 px, in `features/traces/TraceScreen.tsx:69`, `features/errors/ErrorsScreen.tsx:182`, `features/servicedetail/ServiceDetailScreen.tsx:454,526`, `features/overview/OverviewScreen.tsx:157` first, then `features/admin/OrganizationScreen.tsx`, `features/alerts/ChannelDetailScreen.tsx`, `features/infrastructure/InfrastructureScreen.tsx`, `features/infrastructure/databases/DatabasesView.tsx`, `features/profiles/CompareView.tsx`, `features/rum/RumScreen.tsx`, `features/services/ServicesScreen.tsx`, `features/synthetics/CheckDetailScreen.tsx`, `CheckForm.tsx`, `NewCheckScreen.tsx`, `PhaseBreakdownPanel.tsx`, `SyntheticsScreen.tsx`, and `shared/components/SettingRow.tsx`; `app/layout/TopBar.tsx`, outside `main`, gets a container of its own; resizing checked on non-`TrendChart` charts and the service map. **Its own task, before the panel, because it touches finished screens** · proved by `tools/e2e/tests/layout-widths.spec.ts` *new*, each route in the manifest at main widths the panel causes (AC48)

- [ ] **T2.11** — `apps/web/src/features/assistant/` *new*: `AssistantPanel.tsx`, `Conversation.tsx`, `StepRow.tsx`, `AnswerSentences.tsx`, `api.ts`, `states.ts`; `shared/assistant-context.ts` *new*; `app/layout/AppShell.tsx`, the panel's own `ErrorBoundary`; `TopBar.tsx`; `RefreshControl.tsx` leaving assistant queries out; `apps/web/index.html`'s viewport · proved by `features/assistant/*.test.ts` *new* (AC17, AC48, AC73, AC75, AC77, AC78, AC103), `app/layout/AppShell.test.ts` *new*, `app/layout/RefreshControl.test.ts`, `tools/e2e/tests/assistant.spec.ts` *new*, against T2.0's scripted provider, with a phone flow
- [ ] **T2.12** — F3's `/settings/ai` screen: Chat row and section, preview, data level, counts, read record, MCP sentence; `/ai/preview?purpose=chat`, on `MANAGE_AI` · proved by `features/aisettings/ChatSection.test.ts` *new*, `api:test_assistant_api_live.py` (AC2, AC37, AC68, AC72, AC89, AC96, AC101)
- [ ] **T2.13** — AI-off test: extend F3's stopped-role test to a question and to non-AI findings and notifications (AC4); a named test opening each manifest route with chat broken, `tools/e2e/tests/chat-broken.spec.ts` *new*; `sch:test_detection_has_no_ai.py` · proved by themselves

### Step 3 — Every tool, quotes, figures, and real links

*Judgeable:* the founder asks the known questions in research, section 2, with a deliberate time range, then clicks each step's link, and checks that the screen shows the same data. A log line with a URL shows as inert text. A figure the model invents shows the flag.

- [ ] **T3.1** — `nexovue_platform/assistant_tools.py`: remaining tools, including `get_finding_explanation` (F3), `list_metrics`, `get_metric_histogram`, and, when F28 is built, `list_releases` and `get_release_comparison`; aged-out range decided in the loop, on each tool's own clock · proved by `api:test_assistant_catalog.py` (each windowed tool's clock), `sch:test_assistant_loop.py` (AC24, on a short-retention project, and a rollup tool inside derived retention and past raw), `api:test_assistant_tickets_live.py` (AC53).  **AC23 is a bed result, in T4.1;** a scripted provider choosing a rollup tool would prove nothing. The figure flag on a count citing only a list is held in `sch:test_assistant_answer.py`
- [ ] **T3.1a** — `api:services/router.py` `service_trend`, `list_service_endpoints`; `api:infrastructure/router.py` `list_nodes`, `node_trends`: `CurrentReader`, `require_readable_project`; `THE_SURFACE`; `docs/product/setup/api.md`; catalog tools · proved by `api:test_read_surface_structure.py`, `api:test_read_surface_live.py` (a project-scoped token), T4.1's questions
- [ ] **T3.2** — `sch:insight/assistant_answer.py`: quotes by reference, value checks, folding, imperatives, and figure matching; `api:logs/search.py`: `LOG_LINE_AT`, with `received_at`, service and severity bound; `api:assistantquotes/router.py` *new*: `GET /assistant-quotes/{message_id}`; `api:main.py`; `api:ratelimit/policy.py`: `/assistant-quotes` on `TELEMETRY_PATHS` · proved by `sch:test_assistant_answer.py` (AC20, AC63), `api:test_assistant_api_live.py` (AC19, with a sub-millisecond timestamp), `api:test_tenant_chokepoint_structure.py`, `api:test_ratelimit_coverage.py` (AC107)
- [ ] **T3.3** — `features/assistant/StepRow.tsx`: links from the API, Copy steps; `app/router.tsx:330-335`: `/logs` `q`, through F3's `shared/url-window.ts`; `features/logs/`: reads it · proved by `features/assistant/*.test.ts` (AC22, AC93), `features/logs` tests, `tools/e2e/tests/assistant.spec.ts` (a link in a fresh session)
- [ ] **T3.3a** — `shared/url-window.ts`: a project-only parser, joined in `validateSearch` in `app/router.tsx` for `/findings`, `/findings/$findingId`, `/services`, `/service-map`, `/metrics`, `/alerts`, `/alerts/incidents/$incidentId`, `/synthetics`, `/traces/$traceId` and `/releases/$id`, setting the selected project on arrival as F3's does · proved by `app/` route tests, `tools/e2e/tests/assistant.spec.ts` (a link in a fresh session on another project, AC22)
- [ ] **T3.4** — `features/findings/FindingScreen.tsx`: **Ask about this finding**; `features/assistant/starters.ts` *new*, from the design's table · proved by `features/assistant/*.test.ts` (AC49)
- [ ] **T3.5** — Every *Error cases* row has its sentence, and a test that produces the state · proved by `api:test_assistant_error_cases.py` *new* and `features/assistant/states.test.ts` (AC30, AC48). Keyboard and focus by `tools/e2e/tests/assistant.spec.ts` (AC50); screen readers by a recorded manual VoiceOver and NVDA pass in Design QA, since Playwright cannot hear one

### Step 4 — Real models, in the gate

**Spends no money** (foundation founder decision 4, changed 2026-11-02). The cloud option is a free tier, chosen for limits that fit a question of about 31,000 tokens (a guess); Groq's listed 8,000 tokens a minute does not, so Gemini's free tier is the likely one. A free tier may keep and read what is sent, so only the seeded, synthetic data goes. The local model runs first: `gpt-oss:20b` on Ollama, on the founder's Apple M4 Pro, 24 GB (D80). Chat's tools read the live API, so only the services chat reads run, with Docker's memory lowered; `ollama ps` must show the model fully on the GPU, or `qwen3:14b` replaces it. Ollama's context is set to 32K and recorded (D87).

*Judgeable:* one report, kept **outside the repository until the founder has read it** (foundation founder decision 5, approved 2026-11-02), for one free-tier cloud option and one local model: for each, the model, hardware used, seconds per question, shape failure rate, chat scores, injection scores, tokens per question, and demo-result labels. **This report covers tools without knowledge; "supported" waits for T8.5.** The accuracy bar was approved on 2026-11-02, before any run (spec, *Founder decisions needed*, 2); it is never moved after a run is seen. "Supported" names only the providers and models that passed it here; the rest read "not yet tested".

- [ ] **T4.1** — `tools/ai-eval/chat/` *new*: a deterministic seed across logs, traces, rollups, errors, deploys and two organizations (`tools/detection-seed` writes only `service_stats_1m`), and an answer file generated from it, with at least 50 questions, including "how many" (AC23), "not enough evidence", differing zones (AC76), no telemetry, still learning, stopped reporting, "which endpoint got slow", "is the disk full", a custom metric, a third-party host · proved by `tools/ai-eval/tests/test_chat_seed.py` *new*, that answers match reads through the API, and `test_chat_score.py` *new*, with a control run on `HostileProvider` (AC51)
- [ ] **T4.2** — `tools/ai-eval/injection/`: chat's additions (design, *Injection suite*), harmless controls included · proved by `tools/ai-eval/tests/test_injection_score.py` *new*, and a first report (AC51)
- [ ] **T4.3** — `tools/ai-eval/cost/`: replaces the guesses in FR10, FR11, FR19, FR20, and the monthly suggestion, in the code and the documents · proved by the report, then changed constants and their tests (AC52, AC104). A worked monthly cost is published only after this, labelled with the rate used
- [ ] **T4.4** — Apply D83's rule, fixed before the run, to T4.2's report: propose D2's reader step if planted log text is followed in more than 10% of log-text trials on an option that otherwise passes, or a hard-gate failure traces to log text · proved by the recorded decision and its numbers in `status.md`

### Step 5 — Runbooks and keyword search

*Judgeable:* the founder pastes a runbook, asks "what do we do when `checkout` runs out of connections?", and gets the runbook's text in an inert quote, labelled with its age. Deletes the runbook, and asks again: "no record of this". Works with a provider with no embeddings.

- [ ] **T5.1** — Migration `00NN_knowledge.py` *new*: `runbooks`, `knowledge_chunks` (generated `content_tsv`, GIN index, `CHECK`s), no vector column; `api:data/postgres/models.py`; `api:organizations/tables.py` · proved by `api:test_deletion_live.py`, `api:test_assistant_migrations.py` *new*
- [ ] **T5.2** — `api:runbooks/` *new*: routes, multi-file upload, chunking in the same transaction, caps, the 18-chunk ceiling, credential warning, audit, `ai_available` gate · proved by `api:test_runbooks_live.py` *new* (AC38, AC69, AC71, AC90, AC92, AC106, a viewer row), `api:test_authorization_coverage.py`
- [ ] **T5.3** — `features/runbooks/` *new*, with a read view; `shared/settings/settings-nav.ts`; `app/router.tsx` · proved by `features/runbooks/*.test.ts`, `route-manifest.test.ts`
- [ ] **T5.4** — `api:assistant/knowledge.py` *new*: `AssistantTicketReader`, keyword search; `nexovue_platform/assistant_tools.py`: `search_knowledge`, as the named exception · proved by `api:test_assistant_knowledge_live.py` *new* (AC41, AC42), `api:test_read_surface_structure.py` (route not on the surface), `api:test_assistant_catalog.py`
- [ ] **T5.5** — `sch:knowledge/tasks.py` *new*, added to `sch:jobs/registry.py` `TASK_MODULES`; `sch:beat.py` `KNOWLEDGE_SWEEP_ENTRY`: fact lines for recovered findings, in chat-on organizations, one per service, signal and day; deletes lines of later false alarms; the cap and eviction · proved by `sch:test_knowledge_writers.py` *new* (AC39, AC83, AC84, a project finding), `sch:test_beat_schedule.py`, `sch:test_detection_has_no_ai.py` with `nexovue_scheduler.knowledge` **added to `REPOSITORY_AI_MODULES`** (AC82), and a live test that findings deleted through `sweep` remove their chunks (AC82)
- [ ] **T5.6** — `sch:insight/assistant_answer.py`: a runbook quote is the only one labelled a command; label · proved by `sch:test_assistant_answer.py`, `api:test_assistant_hostile_live.py` with a poisoned runbook (AC40)
- [ ] **T5.7** — Re-run the bed: injection payloads in runbooks and fact lines, across later questions, plus a cost run · proved by the report (AC51, AC52)

### Step 6 — Embeddings and hybrid search

*Judgeable:* the founder asks "why is it slow?" against a runbook that says "latency", and it is found with embeddings on, and not found with them off. The settings section shows which is on.

- [ ] **T6.0** — T6.5's provider checks, moved first, since they decide what gets built; `nexovue_platform/ai/`: `embed(texts, model)` adapter per format that has one: `/v1/embeddings` for OpenAI-style accounts and Ollama, `InvokeModel` on `amazon.titan-embed-text-v2:0` for Bedrock, not Converse (D81) · proved by `packages/py-platform/tests/test_ai_embed.py` *new* and one real call
- [ ] **T6.1** — `api:setup.py` `provision_vector()`, its grants, Alembic's drift check, the comment in `deploy/postgres/init/01-extensions.sql`, `docs/self-hosting/`; `assistant_settings.embedding_model` and the second connection (setting, `check()`, audit, consent line, `SEALED_COLUMNS`); `sch:insight/knowledge.py` *new*, in `TASK_SETS["insight"]`: embedding sweep, exclusions, batches, count against the month, recheck, re-embedding; `sch:beat.py` `EMBED_SWEEP_ENTRY` · proved by `sch:test_knowledge_embed.py` *new* with a fake provider (AC41, AC70, AC85), `api:test_setup_no_vector.py` *new* (AC43), `sch:test_beat_schedule.py`, `api:test_keys_live.py`
- [ ] **T6.2** — `api:assistant/knowledge.py`: exact-scan, RRF, read of `assistant_steps.query_embedding`, filtered by its own model; `sch:insight/assistant.py`: stores it, nulls it · proved by `api:test_assistant_knowledge_live.py` (two models' vectors never compared; another message's step refused; AC90)
- [ ] **T6.3** — `/settings/ai` Chat section: embedding state; answers saying "keyword only" · proved by `features/aisettings/ChatSection.test.ts`, `features/assistant/*.test.ts`
- [ ] **T6.4** — Measurement: 5,000 chunks at 1536 dimensions on a 1 GB Postgres; time per search, memory · proved by a report in `status.md`, and the design's guess replaced. **Over AC97's 200 ms guess, add a partial HNSW index per embedding model in `provision_vector()`, with iterative scans on, and report time and recall again (D84). The model name goes into the search as a literal from the stored setting, since a bound parameter cannot match a partial index**
- [ ] **T6.5** — Provider table in the spec and `docs/product/setup/`, filled from T6.0's calls · proved by review
- [ ] **T6.6** — Re-run the bed's knowledge places with embeddings, plus a cost run · proved by the report (AC51, AC52)

### Step 7 — Notes and resolved cases

**After F22.**

*Judgeable:* the founder resolves a case with a note, then in a new conversation asks "how did we fix this last time?". The note appears, labelled, with its author's date. Deleting the note removes it from the answer.

- [ ] **T7.1** — Migration *new*: `knowledge_chunks.note_id` · proved by `api:test_deletion_live.py`
- [ ] **T7.2** — `sch:knowledge/tasks.py`: note chunks and case resolved, with **no change to F22's note service;** Save as runbook on a resolved case; `get_finding_timeline`; FR42's sentence; in F22's code, a ticket row in its AC69 table test, a row for notes going to the AI provider in *What leaves this server*, and the note box's sentence, shown only with chat on: "With chat on, notes can be sent to {provider label}" · proved by F22's AC69 test (AC105), the note box's test, `api:test_assistant_knowledge_live.py` (AC38), `sch:test_knowledge_writers.py` (AC39, AC83), `api:test_runbooks_live.py` (AC95)
- [ ] **T7.3** — `tools/ai-eval/injection/`: a planted note, across questions · proved by the report (AC40)

### Step 8 — Ready to run

*Judgeable:* `just check` passes with its counts read, the readiness assessment is written, and the founder reads one page saying what is measured and what is not.

- [ ] **T8.0** — **Final bed run on the complete feature:** chat, injection and cost across the whole tool list, on each named option. Only this can call an option supported · proved by the report (AC51, AC52, AC96)
- [ ] **T8.1** — Measure `scheduler-insight` under load, **and detection and alerts under 4 hostile questions looping 7-day reads** on the fault bed's data (AC80); the two poll routes on a long conversation; `deploy/compose/nexovue.yaml`; `docs/self-hosting/sizing.md` using F3's T3.10 memory record · proved by `tools/fault-bed/chat_load.py` *new*, the measurement in `status.md`, `api:test_compose_worker_memory.py` (AC98)
- [ ] **T8.2** — `sch:metrics.py` `read_queues`, `deploy/monitoring/rules/nexovue.yml`, `deploy/monitoring/tests/nexovue_test.yml`: design's metrics, read from Postgres, and alert · proved by that monitoring test
- [ ] **T8.3** — Docs: `docs/product/setup/` (turning on chat, runbooks, each tested local model with its hardware and results, `OLLAMA_CONTEXT_LENGTH` (D87), `AI_PRIVATE_ENDPOINTS`, no sync), `docs/operations/`, `docs/architecture/security-and-tenancy.md`, `current-system-reference.md`, `docs/product/feature-catalog.md`, `docs/product/vision.md:134`; `docs/decisions/` once ADRs are accepted · proved by review, `api:test_banned_claims.py` *new* (AC99), and `tools/repo-audit` if it covers them
- [ ] **T8.4** — Run `/review-diff`, then `/readiness-check` · proved by their records in `status.md`
- [ ] **T8.5** — A one-page launch brief, for founder approval: the name and positioning; each public claim beside the test or report proving it; the approved selling line; a provider table of what was measured, including embeddings; who can get it (self-hosted only, as the hosted service shows no panel); it can read, cannot read, from the catalog; a repeatable demo using Step 3's questions plus a planted log line; foundation section 11's demo-data warning; competitor facts rechecked from vendor pages within 30 days before release, including Elastic's licence for the Observability chat. **No public text before this is approved.** · proved by the founder's approval (AC100)
- [ ] **T8.6** — After launch, with no phone-home: a voluntary design-partner check with a few named installs that share the admin section's counts · proved by the record in `status.md`

## New risks

Standing risks live in `status.md`. Found by this analysis:

- **Step 2's panel needs F3's screen.** If F3's `/settings/ai` ships in a different shape, T2.12 moves.
- **Handler matching rests on `scope["route"].endpoint`,** seen in a probe on FastAPI 0.162.0, not in the real app. T2.2 tests a route that passes as well as ones refused, because a test that only sees the value set would pass while everything is refused.
- **The panel's width work (T2.10b) touches screens that already work,** in 18 files. A missed variant shows only with the panel open.
- **Row-level security on messages and steps is F4's own first use.** T2.0a proves it first. If it fails, small `SECURITY DEFINER` functions replace it, and if those fail too, the authors redesign before Step 2 continues (D85).
- **The API on the `ai` network** puts a model-driven process beside the API's whole port.
- **Vector columns outside Alembic** can drift. The drift check names them.
- **The command heuristic has two ways to fail:** missing a disguised command, or refusing a harmless sentence. T4.2 measures both.
- **Answer rejection can be turned into denial.** A planted name that is not in a name field may make the model trip the validator, withholding an answer about that service. Steps stay visible, and the attacker gains no data. T4.2 tests it, and reports the false-refusal rate.
- **Weak local models may fail the answer's shape often.** Each retry costs tokens. The bed reports the rate.

## Questions

None open. The three earlier questions were answered on 2026-11-02 (spec, *Own questions answered*):

| # | Question | Answer |
|---|---|---|
| 1 | Which local model is tested, on what hardware? | `gpt-oss:20b` on Ollama, on the founder's Apple M4 Pro, 24 GB, with `nomic-embed-text` (D80), context set to 32K (D87), and a reduced stack running beside it; `qwen3:14b` if it does not fit. T4.3's report names the hardware |
| 2 | What does Elastic's Observability chat need in licence? | A paid self-managed subscription, most likely Enterprise; T8.5 rechecks the tier before any public text (D82) |
| 3 | Does `bedrock_converse` or a separate route carry Titan? | A separate route: `InvokeModel` (D81). T6.0's real call proves a Bedrock API key works on it |

## Gates

- [ ] Specification approval
- [ ] Design approval
- [ ] **Plan approval — before the first edited file**
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design and its wireframes, with the manual screen-reader and phone-keyboard records, for customer-facing work
- [ ] Claims approved — the launch brief (T8.5), before any public text
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

**Run once, at the end of each step, not after every edit.** Read counts, not exit codes.

- `just check`: formatting, lint, types, tests, contracts. It holds the catalog, hostile provider's offline half (`test_assistant_hostile.py`), validator, read surface, authorization and MCP tests.
- Live suites with the stack up, in CI on every pull request: tickets, assistant API, hostile provider's live half, grants, privacy, knowledge, deletion, keys, personal data, ratelimit. A live suite must fail loudly when its stack is absent.
- `just e2e` for `assistant.spec.ts`.
- `tools/ai-eval` for the bed, per its own rules, on the founder's Mac, with free tiers or the local model and no paid calls (foundation items 4 and 9, 2026-11-02).
- **"The injection suite passes" means what the spec says,** over 3 trials per payload and place, with the bound shown: no write, no way out, no unlabelled false cause, and abstaining on at least 90% of "not enough evidence" cases, a guess. It never means injection-proof.

## Rollout and rollback

**The real risk is not the rollout.** It is this: nobody has said they want this, and Steps 2 to 7 are large. The founder's conversations come before Step 2, with a rule set in advance. A developer happy with Claude Code may need only Step 1, and it stands alone. Step 2 is the point where the founder applies that rule. **After launch nexovue cannot see use,** as metrics carry no tenant label and images do not phone home (T8.6).

There is no deployment and no customer, so no migration of live data is at stake. AI stays off by default, so an install is unaffected until an owner agrees. Rolling back means turning chat off in settings, or stopping `scheduler-insight`. Dropping the tables loses conversations and runbooks, and nothing else depends on them.

**Hard to reverse after a first customer:** the ticket's prefix and the read surface additions. MCP's new tools are a public promise once documented. Runbook content is customer data to keep.

## Outcome

To be filled in when the work completes.
