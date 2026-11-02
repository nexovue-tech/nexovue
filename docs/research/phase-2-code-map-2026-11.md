# Phase 2 code map (2026-11)

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Type:** Research snapshot, read from the code on 2026-11-02, on branch `feature/anomaly-detection` with
> uncommitted work present. Line numbers are correct for that working tree and will drift.
>
> **Status:** Proposed input to the F28, F3, F22, F4, F5, F19 and F20 documents. It approves nothing. Where it
> says "suggestion", that is the author's opinion for the design to accept or reject.

## What this document is

For each remaining Phase 2 feature: what exists (with file and line), what is missing, what can be reused, and
where the catalog or the plan disagrees with the code. When a document and the code disagree, the code wins.

**How it was read.** Nothing was run. No test, no query, no running system.

- **Read in full:** the assistant (`assistant/*`), deploys (`deploys/*`), findings service, router and schemas,
  migration 0041, the permission table, roles, `nexovue_platform/detection.py`, ADR-019, ADR-022, ADR-023.
- **Read in part, by outline and targeted sections:** `detection/project_job.py`, `detection/notify.py`,
  `alerts/outbox.py`, `alerts/messages.py`, `audit/service.py`, `ratelimit/policy.py`, `middleware/ratelimit.py`,
  `errorgroups/*`, `servicemap/*`, `metrics/router.py`, `FindingScreen.tsx`, `OverviewScreen.tsx`, `TrendChart.tsx`.
  A claim about these rests only on the lines cited.
- **Decisions 0004, 0013, 0014 and 0047 have no file today.** `docs/decisions/decision-log.md:190,259,260` holds one
  line each. Their text is in git only: `git show e0f11a0^:docs/architecture/decisions/0014-provider-flexible-llm-interface.md`.

## The seven things that matter most

1. **The whole of Phase 2 stands on a branch that is not merged.** F6 is "Built; awaiting review"
   (`docs/features/anomaly-detection/status.md`). Findings, detection, ADR-022's outbox and migrations 0041 to 0050
   all live here. Later features should not start from `main` until this merges.
2. **There is no AI code, no AI switch and no provider key anywhere.** `apps/scheduler/tests/test_detection_has_no_ai.py:7-8`
   records the same search. The `insight` service in Compose cannot start (see *Where AI code runs*).
3. **An AI link already exists, and it has no injection tests.** The MCP server (F36, MCP is the protocol AI assistants
   use to call tools) returns raw log bodies, service names and error messages to a customer's own AI
   (`assistant/tools.py:201-225`, `assistant/bounds.py:23`). Nothing marks that text as untrusted. A search for
   "inject" in `apps/api/tests` found only `test_csv_cells.py`. The plan says every AI link has an injection suite, and
   the plan's table does not mention this one.
4. **A finding's own words are attacker-controlled.** `findings.title` and `signals[].endpoint` contain service and
   endpoint names from telemetry, which "anyone with a browser key can send" (`alerts/payloads.py:190`, docstring).
   The design already says the AI must never get the composed title (`anomaly-detection/design.md:940`).
5. **Rollups do not know about versions.** `endpoint_stats_1m` and `service_stats_1m` have no version column
   (`000008`, `000035`). Only sampled `spans` have one (`000001_spans.up.sql:31`), and sampling keeps every failed and
   slow trace but one healthy trace in ten (`000024` header). A before and after comparison per version read from spans
   would be badly skewed. Only a comparison by time around the deploy is honest today, unless a new rollup is added.
6. **Evidence dies on different clocks.** On the seeded Free plan, raw spans and logs last 7 days and rollups 30
   (`0005_plans_table.py:34`); readings last at most 35 days (`000036`); a closed finding is deleted at the derived
   retention (`detection/retention.py`, `_PAST_DERIVED_RETENTION`). "Every claim links to its data" and a case's
   timeline both need a stored copy of the evidence, because the links will break. These are seed values; later plan
   migrations were not traced.
7. **`scheduler-light` is the wrong home for AI.** It holds the eraser and detection-writer accounts
   (`deploy/compose/nexovue.yaml:363,366`). The plan's rule is that the AI reading telemetry holds no permission to
   write. A worker with its own role and only the reader account is what makes that true (see below).

## Across every feature

### Where AI code runs

The plan calls this a design decision for Stage 2 (`phase-2-plan.md:36-37`). The facts:

| Fact | Evidence |
|---|---|
| `insight` is in the **development** Compose file only, under a profile | `deploy/compose/docker-compose.yaml:586-627` |
| It runs `uv run --package nexovue-insight`, a package that does not exist | `docker-compose.yaml:599`; workspace members are api, scheduler and py-platform (`pyproject.toml:10`) |
| The install layout has no `insight` or `ollama` service | `deploy/compose/nexovue.yaml` service list |
| The image's roles do not include it | `apps/api/src/nexovue_api/roles.py:19-30` |
| ADR-019 lets it be a third image only "if its dependencies are heavy" | `ADR-019`, *Consequences* |
| The API already depends on `httpx` | `apps/api/pyproject.toml:19` |
| The job queue can carry it: Postgres `jobs` table, per-job limits and expiry, named queues | `nexovue_platform/jobs.py`; default limits are 30 and 60 s (`jobs.py:27-28`), too short for a model call |
| A worker with no `--queues` claims `light`, its region and `detection` | `apps/scheduler/src/nexovue_scheduler/__main__.py:78-85` |
| Queues can be capped per worker | `jobs/worker.py:72` |

**Suggestion for the F3 design.** Talking to a provider over plain HTTP needs no heavy dependency, so no third image is
needed. A new role such as `scheduler-insight` runs `worker --queues insight` from the `nexovue` image. It gets only
the reader ClickHouse account and a narrow Postgres path. New tasks go in `jobs/registry.py:18-29`. This fits ADR-019.
It is a guess until a real local model is tried.

**A trap.** `test_detection_has_no_ai.py:37` names the repository's AI modules in a fixed set. A new AI module not added
there is invisible to the test that guards "detection works with AI off".

### Telemetry text is attacker-controlled

- **Masking exists, and it is not this defence.** `apps/workers/internal/pipeline/mask/` removes credentials, cookies,
  card numbers and URL secrets before storage (`rules.go`). It leaves "ignore your instructions" exactly as written.
- **One-line and channel escaping exist,** and are the right starting point for display:
  `alerts/outbox.py:398` (`one_line`), `alerts/payloads.py:42-66` (Slack, Teams, Discord).
- **The console renders telemetry as React text.** The one `dangerouslySetInnerHTML` in `apps/web/src` is
  `features/docs/DocsScreen.tsx:89`, showing setup pages built from the repository's own documents with `markdown-it`
  (`apps/web/build/setup-docs-plugin.ts`). AI wording must never take that path, or a model's output becomes a way to
  inject links and markup.
- **Result caps exist:** 100 rows and 32 KiB, and a cut answer says so (`assistant/bounds.py:15,18,23`).
- **Nothing exists** for an untrusted-text envelope, for a provider call, or for injection fixtures.

### Who may see and do what

- Permissions are one table: `auth/permissions.py:16-42` (actions) and `:50-70` (roles). Route gates are in
  `auth/policy.py:70-83`. `test_authorization_coverage.py:331` fails the build for an ungated write. Every new write
  needs an action.
- **Roles are organization-wide, with no per-project limit for a person.** `Reader.project_scope` is None for a person
  and set only for a token (`auth/dependencies.py:163-185`). So "reads only what the asking user may see" today means
  "this organization", or the token's one project. It does not need a new access model unless the founder wants one.
- Reads are two kinds. `CurrentReader` admits a token and a person (`findings/router.py:42`). `CurrentIdentity` admits a
  person only: the service map (`servicemap/router.py`) and several services routes (`services/router.py:84,111,130`).
  An API token cannot read those.
- A signed-in support view keeps only `READ_AUDIT` of the actions (`permissions.py:103`). A chatbot used during a support
  view needs an explicit decision.

### Storing a secret

`nexovue_platform/checks/sealing.py:113,120` seals and opens with a key bound to the owner, with rotation.
`:178` (`channel_secret_owner`) and `NotificationChannel.sealed_secret` (`models.py:1209`) are the pattern for AI
provider keys, GitHub and Jira tokens. `channels/service.py:89` builds the box. `Integration` is **not** the pattern: it is
documented as holding no credential (`models.py:743`), and its kinds are fixed to `cloudwatch` and `postgres`
(`integrations/schemas.py:12`).

### Calling out to a customer's address

`probes/addresses.py:34,69` decides which addresses may be reached, and `alerts/senders/http.py:15` uses it. Reuse this for
a self-hosted model, Jira on a private network, and GitHub Enterprise. Each is a user-chosen address, which is exactly
the risk of server-side request forgery (making our server call somewhere it should not).

### Boundaries to respect

- The scheduler does not depend on `nexovue-api` (`apps/scheduler/pyproject.toml:14`). Logic both need must move to
  `nexovue_platform`. That applies to `DeployHistory` if the scheduler runs correlation.
- Every ClickHouse statement binds the tenant and is a module constant (`alerts/telemetry.py:1-13`,
  `test_tenant_chokepoint_structure.py`). No free-form query builder fits this.
- New tables must be listed in `organizations/tables.py` and, in ClickHouse, in `erasure/tables.py` (`:44` for readings).
- New telemetry routes must join `TELEMETRY_PATHS` (`ratelimit/policy.py:234`); `test_ratelimit_coverage.py` compares them.
- `deploy_events` has no retention sweep: `jobs/expiry.py:32-42` and `detection/retention.py` do not name it. It leaves
  only with its project (`organizations/tables.py:72`).

---

## F28: Deployment and release tracking

**Exists**

- The signed public webhook: raw bytes, HMAC check, then parse (`deploys/router.py:43-62`, `deploys/service.py:73-137`,
  `deploys/signatures.py:26,38`). Replays are refused by delivery id (`service.py:99-107`; unique index in `models.py`, `DeployEvent`).
- `deploy_events`: version, service, time, source, free-form metadata (`models.py:783`). It has **no environment and no commit column**.
- `DeployHistory.preceding`: the last deploy before an instant, with the discipline that order is a fact and cause is not
  (`deploys/service.py:147-187`, `:193-236`). The console says it in words (`features/errors/error-groups.ts:65`).
- Error to deploy link on the error list and detail (`errorgroups/service.py:185,189-205`, `errorgroups/router.py:67,116`,
  `ErrorsScreen.tsx:278,364`).
- Chart rules on the Overview and service detail (`shared/deploy-markers.ts`, `TrendChart.tsx:251`, `OverviewScreen.tsx:70`,
  `ServiceDetailScreen.tsx:114`).
- A version on spans and logs: `service.version` becomes `spans.deployment_version` (`traceassembly/flatten.go:25,28,195`,
  `000001_spans.up.sql:31`), and an occurrence reports it as `release` (`errorgroups/queries.py:71`).
- Secret rotation, audit (`audit/service.py:70-71,113-114`), its own rate tier (`ratelimit/policy.py:127`), and an MCP tool
  (`assistant/tools.py:271`).

**Missing**

- Releases list and screen. No route (`route-manifest.ts:18-34`).
- Before and after comparison, in any form.
- A regression raised from a deploy. `apps/scheduler/.../detection` does not read `deploy_events`: a search for "deploy"
  there found only the word "deployment" in `store.py`.
- Reading deploys by time. `GET /deploys` takes only a limit (`router.py:65-76`).
- A native GitHub setup. The body is our own envelope, and GitHub's `deployment` payload is refused by design
  (`deploys/schemas.py:28-44`). The signature headers match GitHub's, so a real GitHub delivery passes the signature and then
  fails on the body (`service.py:322-327`).
- Filtering traces by version. `TraceFilters` has no such field (`traces/queries.py:88`).

**Reuse:** `DeployHistory` and its queries (`queries.py:119,148`), `deployMarkers`, `TrendChart` with `markers`, `window.py`,
and `readings.py` for shared arithmetic. **Suggestion:** a per-version rollup only if the time comparison proves too weak;
a rolling deploy mixes two versions in the same minutes.

**Disagreements**

- Catalog: "deploy markers on all charts" (`feature-catalog.md:372`). Markers are on two screens. The finding page's
  chart has none (`FindingScreen.tsx:535`), nor do profiles or metrics.
- **A defect behind the markers:** the console asks with no limit or window (`servicedetail/api.ts:281-292`,
  `overview/api.ts:219`), so it gets the newest 50 (`deploys/schemas.py:17`). A window older than the last 50 deploys of
  the project draws no rules, with nothing to say so, and the service screen filters those 50 afterward.
- Catalog: "powered by deploy events (GitHub/CI webhooks)". Only a CI job posting our envelope works.
- `first_seen` on an error group means first inside the window (`errorgroups/service.py:166-170`), so its link answers "before
  this window's first sight". `ErrorGroupSeen.first_seen_at` (`models.py:1847`, written at `alerts/new_errors.py:66-73`) may be
  the true first sighting; whether it is filled without an alert rule was **not checked**.
- `docs/features/integrations-deploy-events/spec.md` is a 21-line baseline, not a specification.

## F3: AI insights (explanation)

**Exists: only inputs.**

- The finding: leading signal, normal, band, worst, comparison, evidence endpoint, a week-ago value
  (`nexovue_platform/detection.py:260-287`, `findings/schemas.py:29-53`).
- Frozen readings, so an explanation can quote what was judged (`000036`, ADR-023), read through `findings/queries.py:348`.
- Dependencies measured before sampling: `service_edges_1m` (`000014`, `000027`), read by `servicemap/queries.py`.
- Error groups in a window (`errorgroups/`, and for the scheduler `alerts/telemetry.py`), new errors (`alerts/new_errors.py:77`).
- Deploys, linked incidents (`finding_incidents`, `0041:306`), check state, other open findings.
- A place in the job queue. The project job opens a finding at `project_job.py:278,925,1116` and enqueues after commit at `:1808`.
- A notification path that does not wait: `notify.py:200`, written in the same transaction (ADR-013).

**Missing:** correlation code; the provider interface decision 0014 names; provider settings and keys; the on and off switch; a
stored explanation (no column or table); cost limits; any field on the API (`findings/schemas.py:60-81`) or card. The card's
probable cause "stays empty until Stage 2" (`anomaly-detection/design.md:446`). The finding page has no slot yet
(`FindingScreen.tsx:97-118`). Scoring against injected faults also does not exist; `tools/detection-seed` and `tools/otel-demo` are
where it would start.

**Reuse:** `DeployHistory` (after moving it, see *Boundaries*); the `Subject` and `Notice` shapes (`outbox.py:406,432`); `compose` for
wording (`messages.py:275`); sealing for keys.

**Traps**

- A project finding has no readings, signals are thin, and `service_name` is null (`findings/service.py:330`, `lifecycle.py:507`).
  The explanation must cover it or say it does not.
- "The explanation joins it later" needs a new notification kind. `NOTIFICATION_KINDS` is a checked vocabulary with a frozen copy
  in migrations (`alerts/model.py:47`, `0041:117`, `test_alert_vocabulary.py`), `FINDING_TOLD_KINDS` decides who gets closure
  (`detection.py:234`), and `_compose_finding` needs a branch.
- A flapping finding (5 openings an hour) must not spend AI money each time (`notify.py:399`).
- Readings are not available for a finding older than 35 days. Explain at or near the time, then keep the result.

**Disagreements**

- Catalog: F3 is "the proactive finding-generation surface". In the code, F6 generates findings with no AI, and an AI import in
  detection fails a test. F3 is the explanation only.
- Plan's table: "Findings: on an empty feed by design (`findings/api.ts`)" and "Missing: findings table, API" (`phase-2-plan.md:29`) are
  stale. Those exist.
- Catalog row F3 "the differentiator": the plan itself records this is not unique (`phase-2-plan.md:111`).
- Compose comment "`apps/insight` does not exist until F3/F6" (`docker-compose.yaml:587`): F6 shipped without it, on purpose.
- Decision 0014 names OpenAI and Ollama. The founder's later decision is the customer's own account or a model on their server; the
  provider list should follow the founder. Decision 0047 (local model not viable on the dev machine) is flagged, not decided.

## F22: Case management

**Exists**

- A finding with a person's acknowledgement, false alarm, new normal and audit (`findings/service.py:168-291`, `audit/service.py:132-134`).
- `finding_incidents` links to threshold incidents (`0041:306`).
- A delivery log (`notifications`, `notification_attempts`; `models.py:1399,1478`).
- A row lock and "changed since you opened it" (409) discipline (`findings/service.py:60-64,294`).
- Role-gated actions on the console (`shared/components/Permitted.tsx`).
- GitHub's signature scheme, implemented (`deploys/signatures.py`), and replay protection tables (`BillingWebhookDelivery`, `models.py:1107`).
- An outbox with leases and ordering, for reliable sends (ADR-013, `alerts/outbox.py`).

**Missing:** lifecycle beyond open and closed; assignee; notes; timeline read; case list; GitHub Issues and Jira connections; inbound
status sync; a place to hold a repository link.

**The hard points, all from the schema**

- `findings.state` is only `open` or `closed`, and `(state='open') = (closed_reason IS NULL)` (`0041:256,264`). A finding **closes itself**
  on recovery, while a person may still be investigating. So investigation state cannot be `state`; it needs its own column.
- Only one open finding per service and per project (`0041:279-292`). After a self-close, a new finding can open on the same service
  while the first case is still being worked. The design must say whether the new one joins the old case.
- **Closed findings are deleted at derived retention** (`detection/retention.py`) and their notifications go too. A case needs a stated
  retention, or a resolved case vanishes on the customer's plan clock.
- A flapping service opens many findings an hour, so "auto-created cases" per finding would flood.
- The audit log cannot be the timeline's read. `GET /audit` is admin-only (`audit/router.py`, `permissions.py:61`), and a member must see
  their case's history. Audit stays the record of who acted; timeline needs its own read.

**Disagreements**

- Catalog: "auto-created cases from findings" with a list and a separate lifecycle. ADR-022 and plan say a finding grows into a case with no
  second record.
- Catalog `:125` names "PagerDuty/Opsgenie connectors in F21/F22". Opsgenie is dropped (D6, `feature-catalog.md`, F21), and on-call stays
  with PagerDuty (plan).
- Catalog AC1.8 (`:232`) still mentions resolve on the card "reflected in F22"; its own amendment says a finding closes itself.
- ADR-022 leaves open whether a threshold incident can also become a case. Stage 3 must decide.

## F4: AI chatbot

**Exists**

- The MCP server inside the API, stateless, read-only, nine tools: `list_projects`, `list_services`, `get_service`, `search_logs`,
  `list_error_groups`, `list_incidents`, `list_checks`, `list_deploys`, `list_traces` (`assistant/tools.py:128-304`, mounted at `main.py:106`).
- Each tool calls the same service function as the HTTP route, under the caller's organization and scope (`tools.py:1-11,86-125`).
- Tests that hold the tool list and that every tool only reads (`tests/test_mcp_tools.py:33,47,77,82`).
- A console page telling people how to connect an assistant (`features/apitokens/ConnectAnAssistant.tsx`).
- `pgvector` in the Postgres image and the extension on first boot (`compose.infra.yaml:138,149`, `deploy/postgres/init/01-extensions.sql`).
- A WebSocket with a person's authorization, for log tail (`logs/router.py:561`, `features/logs/use-log-tail.ts`). It is the only streaming path.
- ⌘K reserved for search (`TopBar.tsx:82`).

**Missing:** any model call; a chat screen or panel; conversations; a knowledge store (no migration has a vector column, and no runbook or
note table exists); an embedding model choice; per-user cost limits; injection suite.

**Gaps in the tools**

- **No tool reads findings**, though findings are the product's centre.
- No single trace, service map, metrics, or error group detail tool.
- Tools hang off MCP's `Context` and read a token from the request scope (`tools.py:63-75,94-99`). A chat in the console signs in with a
  session, so the tool bodies must be separated from their MCP wrapper. `Call` (`:54`) is the seam.
- A person's reads go through the telemetry tier by path (`policy.py:74`). A console chat calling services in process would skip the
  middleware entirely, so it needs its own limit.

**The knowledge store's hazards**

- Embedding resolved findings stores attacker-controlled names in vectors, retrieved later as if trusted. Retrieved text needs the same
  untrusted handling as a log line.
- Embeddings must be deleted with what they came from: erasure and retention, the same as findings.
- An embedding model on a self-hosted install is a new memory cost. Sizing was **not measured**.

**Disagreements**

- Plan: "Chatbot: pgvector; Missing: everything else" (`phase-2-plan.md:33`). Catalog row F4: "Compose reserves… the package does not exist."
  Both ignore the MCP server, which is the live tool half of Stage 4. Catalog does list F36 as built, but does not connect the two.
- Catalog: "panel available on every screen". `AppShell` has no such slot today. That is work, not a contradiction.
- Decision 0004 mentions "resolved cases and runbooks"; neither is stored anywhere yet.
- `pgvector` is created only "on an empty data directory" (`01-extensions.sql:1`). A migration that needs it must not assume otherwise; the install
  layout should be checked in the plan.

## F5: AI solution (suggested fix, draft pull request)

**Exists: almost nothing, as the plan says.** Inputs only:

- Stack traces with release (`errorgroups/schemas.py:80-92`) and source-map lookup to original files (`sourcemaps/router.py:88`,
  `sourcemaps/service.py:102`).
- A deploy's version; metadata may hold a commit, but no field promises it (`deploys/schemas.py:44`).
- Sealed credentials, outbound HTTP guard, audit, role table, outbox (all above).

**Missing:** a repository connection of any kind (OAuth, GitHub App, token, or mapping a service to a repository); reading source; a fix record; an
approval flow; a draft pull request call; repository access audit.

**What the plan's rule forces, from the code.** "The part reading telemetry cannot itself write" needs two separate pieces. The reading AI produces a
proposal as data. A later step with no AI in it, on a person's explicit click, uses the repository token. The token must not be sealed where the
`scheduler-insight` role can open it: `SealedBox` works by purpose (`sealing.py:98`), so a separate purpose and key availability is the seam. This is a
**suggestion** for the design.

**Also new:** sending customers' source code to their AI provider is a bigger disclosure than telemetry. It needs its own consent, separate from turning AI on.

**Disagreements:** none of substance. Catalog and plan agree. "Shared with F22" means the connection must be designed once, and `Integration` does not fit it (see *Storing a secret*).

## F19: SLO tracking

**Exists:** no code. A search of `apps` and `packages` for `slo`, `error budget` and `burn rate` found nothing. Inputs and parts:

- **Exact counts before sampling:** `request_count`, `error_count` per minute (`000008`, `000035`, `000024` header).
- **Exact latency counts for a service:** `latency_buckets`, 64 fixed boundaries, so "requests over X" is reproducible (`000035` header).
- **Approximate only for an endpoint:** `endpoint_stats_1m` keeps quantile state, not buckets (`000008`). A latency SLO on one endpoint cannot be
  counted exactly from it.
- A window evaluator with saved position, waits and low-traffic rule (`alerts/thresholds.py:68-130`), shared reading arithmetic (`nexovue_platform/readings.py`).
- Alert routing, one incident per breach, flap guard (`alerts/incidents.py:206,248`).
- Check state history, for a synthetic availability measure (`CheckStateChange`, `models.py:573`).

**Missing:** targets table, budget calculation, burn alert condition, screen, API.

**Traps**

- `service_stats_1m` lasts 35 days (`000035`). A 30-day window fits, with nothing to spare for a quarter. `endpoint_stats_1m` lasts the derived retention, 395 days on paid and 30 on Free
  (`0005_plans_table.py:34-37`, seed values). A 30-day SLO on Free sits exactly on the edge.
- Alert conditions cap `window_minutes` at 60 and waits at 120 (`alerts/model.py:162-163`). A burn alert over hours needs its own condition kind, with a migration (`alerts/model.py:24`, `0041:113`).
- The rollups count `error_count` by one fixed definition. Letting a customer define "good" (for example, excluding 4xx) is not possible from them.
- Sharing the evaluator's arithmetic does not share the store: the `detection/store.py` readings are the detector's, written by its own account.

**Disagreements**

- Catalog: "budget-burn alerts into findings feed" (`feature-catalog.md:307`). A finding allows one open per service (`0041:279`), signals are only `traffic`, `failing`, `slower`
  (`detection.py:38`), and it auto-closes and adopts a new normal after 7 days (`detection.py:47`). A burn does not fit that. Either it is an incident (F21, which the feed does not show),
  or the finding model must grow. ADR-022's "one record of a *detected* problem" makes this a real design choice.

## F20: Dashboard builder

**Exists**

- Composed Overview on `/` (`router.tsx:245`, `OverviewScreen.tsx`), and a dedicated merged read (`/services/overview`, `services/schemas.py:148`).
- Charts: `TrendChart` on uPlot, with bands, spans and rules (`TrendChart.tsx:88-101`, `apps/web/package.json:32`), `HistogramChart`, `DurationHeatmap`, `DataTable`, `Sparkline`, `Delta`.
- Time window and refresh state (`shared/selection.ts:338`), and query keys.
- Metric tables by type (`000003` to `000007`), a metric catalogue, one histogram, a duration heatmap (`metrics/router.py:46,71,92`).

**Missing:** any stored dashboard (no table; the word appears in the API only as prose), a builder screen, sharing and permissions, layout, and **a general read of one metric over time**. The metrics router has no series route for a sum or gauge.

**Traps**

- **The rate limit is sized for screens of 8 reads.** `LARGEST_SCREEN_FANOUT` is 8, with a test (`policy.py:72`), and 8 in flight per person (`:92`). A read over that waits up to 5 s, then is refused (`middleware/ratelimit.py:67`).
  At 10-second refresh, 30 panels spend 180 reads a minute, the whole personal quota (`policy.py:78`). This is arithmetic, not a measurement. A builder needs batching, a slower refresh, or a new tier.
- **No free-form queries.** Statements are module constants that bind the tenant (see *Boundaries*). A builder must compile a fixed set of panel types to fixed statements.
- No drag-and-drop or grid library in `apps/web/package.json`. Choosing one is a research item.
- `route-manifest.test.ts` requires the sidebar and router to agree; the nav type is a closed list (`route-manifest.ts:18-34`).

**Disagreements**

- Catalog: "**The front door is unchanged**: the Findings feed is what a person lands on" and Overview holds `/` "until the insight engine exists" (`feature-catalog.md:317-325`). The code: `/` is Overview (`router.tsx:245`), findings now exist, and nothing moves it. No document read here decides what `/` becomes now, so it needs an owner.
- Catalog says F20 is "Part specified". `console-redesign` covers only the composed half.
- `overview/api.ts:144-148` and `OverviewScreen.tsx:48-51` still say a fifth read is refused. The tier is 8 and waits (`policy.py:92`). The staggered reads may be needless.

---

## Disagreements in one table

| Where | It says | The code says |
|---|---|---|
| Catalog rows F1, F2, F6 (`:64,65,69`) | No engine; not built; not specified | Built on this branch, awaiting review; catalog last audited 2026-09-21 |
| Plan `:29` | No findings table or API | Migration 0041, `findings/router.py` |
| Plan `:33`, catalog F4 | Chatbot: nothing but pgvector | MCP tools exist (F36), with raw telemetry and no injection tests |
| Plan `:36`, catalog F4 | Compose reserves `insight` | Dev file only; cannot start; no role, no package |
| Catalog F3 | Generates findings | F6 generates them, with AI forbidden by a test |
| Catalog F28 | Markers on all charts | Two screens; newest 50 deploys only |
| Catalog F28 | GitHub webhooks | Our envelope only |
| Catalog F22 | Separate auto-created cases | ADR-022: the finding grows |
| Catalog `:125` | Opsgenie connector | Dropped, D6 |
| Catalog F19 | Burn into findings feed | Finding schema does not fit it |
| Catalog F20 | Front door is the feed | `/` is Overview |
| F1 AC1.4 (`:228`) | WebSocket or pub/sub | Polling through `liveOptions` (`features/findings/api.ts:175-176`). Meets 30 s at the 10 s default; Redis is gone (ADR-020) |

## Not checked

- Actual AI quality, cost or memory for any model. Nothing was measured.
- Competitors. This is a code map only.
- Later plan migrations that may change the seeded retention values.
- Whether `error_groups_seen` is filled for projects with no new-error rule.
- The uncommitted changes in this working tree were read as they stand, not reviewed for correctness.
