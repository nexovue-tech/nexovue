# Phase 1 — Core System: Implementation Plan

> **Owner:** Raj · **Status:** Planned, not started (no code yet)
> **Companion docs:** `../product/vision-and-scope.md` (scope) · `../architecture/system-architecture.md` (technical design) · `../product/requirements.md` (features) · `../research/market-analysis-2026.md` (research)
> **How to use:** every task has a stable ID (`P1-nn`). Status values: `todo` · `wip` · `done` · `blocked` · `dropped`. Update the status column in place; never renumber IDs.

---

## 0. Scope

**Phase 1 = the core system.** Every data door lands its data in storage in one normalized shape, and the UI can show all of it.

**In scope**
- All four ingestion doors (OTLP, our probes, browser snippet, webhooks/pollers)
- All three signal shapes (spans, logs, metrics) + deploy events
- Storage, query layer, multi-tenancy, retention
- A competent dashboard UI over that data (the table-stakes screens)
- Onboarding to first data

**Out of scope — Phase 2**
Findings feed (F1), finding detail (F2), AI insights/chatbot/solution (F3–F5), anomaly detection (F6), owner status page (F7), case management (F22), alerting connectors (F21), session replay (F16), continuous profiling (F17), SLOs (F19), dashboard builder (F20).

**Scope boundaries decided 2026-08-25:** service map **is** in Phase 1 (F9 — a screen over trace data we already hold). Kubernetes telemetry **is ingested** via door 1 and is queryable, but pod/node/namespace-aware screens are Phase 2. The UI is fixed screens per signal — no user-built dashboards. Network monitoring (eBPF) and SIEM remain unplaced in any phase — open question, see §8.

**Phase 2 hooks built now because they are nearly free later:** per-minute materialized views, exact pre-sampling counters, deploy-events table, live-update pub/sub channel, usage metering counters. Skipping these means retrofitting the data layer.

**Standing rule:** the UI route `/` is reserved for the Phase 2 findings feed. Phase 1 dashboards live under named routes and never become the front door.

---

## 1. Decisions locked for Phase 1

| # | Decision | Rationale |
|---|---|---|
| D1 | **Walking skeleton first, then widen** | One thin end-to-end path running in ~2 weeks beats four parallel ingestion projects that integrate at the end |
| D2 | **Go on the data plane from day one** | Matches the architecture doc; avoids writing the hot path twice |
| D3 | ~~Redpanda instead of Kafka~~ → **Apache Kafka 4.x (KRaft)** | **Reversed 2026-08-25 after research.** Redpanda Community Edition is BSL 1.1, whose Streaming/Queueing Service clause is a live ambiguity for a commercial SaaS. The efficiency argument was also stale: Kafka 4.0 dropped ZooKeeper, and an April 2026 benchmark put Kafka 4.2 KRaft ahead on throughput at 3× less memory. `franz-go` code is unchanged either way |
| D4 | **Monorepo + GitHub issues + this plan as source of truth** | Plan lives in the repo so it is readable and updatable in-session |
| D5 | **Vite + React + TanStack over Next.js** | App is entirely behind auth; no SEO surface to pay RSC complexity for |
| D6 | **uPlot for time series** | Renders 100k points; card-oriented chart libs will not |
| D7 | **Auth built in FastAPI (sessions + argon2)** | Three personas do not justify Keycloak/Ory |
| D8 | **testcontainers for integration tests** | Mocks lie about columnar databases and brokers |
| D9 | **Service map is in Phase 1** (P1-55, P1-56) | Table stakes, and it is a derived aggregation over spans we already store — no new collection |
| D10 | **K8s ingested, not visualized, in Phase 1** | The data arrives free through door 1; pod/node concepts on screen are their own design problem |
| D11 | **Fixed screens, no dashboard builder** | F20 is explicitly the optional mode; building a builder now contradicts the insight-first thesis |
| D12 | **Cold tier built in Phase 1** (P1-57) | Raw TTL is 30 days; without tiering the data is simply deleted. *Clarified 2026-08-26:* this concerns any deployment holding data worth keeping. **Dev keeps its 3-day TTL** — that data is disposable and the laptop's SSD is not. The migrations carry the production 30/90-day TTLs; dev retention is applied separately by `deploy/clickhouse/dev-retention.sql` |

---

## 2. Repository structure

```
nexovue/
├─ apps/
│  ├─ receiver/         Go — OTLP/HTTP + gRPC, auth, rate limit, → Kafka
│  ├─ workers/          Go — trace-assembler, log-writer, metrics-deriver
│  ├─ api/              Python FastAPI — query layer + control plane
│  ├─ scheduler/        Python Celery — probes (door 2) + pollers (door 4b)
│  └─ web/             TypeScript — React app
├─ packages/
│  ├─ contracts/        internal normalized envelope + golden fixtures
│  ├─ ui/               design tokens + primitives
│  └─ client-ts/        API client generated from OpenAPI
├─ deploy/              compose, ClickHouse DDL, Postgres migrations
├─ tools/               loadgen, chaos, otel-demo, e2e
└─ docs/                PRD, architecture, ADRs, phase plans
```

**Inside services: domain-oriented, not layer-oriented** — `ingest/ traces/ logs/ metrics/ tenancy/`, not `models/ controllers/ services/`. Features arrive as domains; layer-first folders force every feature to touch six directories.

---

## 3. Milestones & tasks

### M0 — Foundations (repo, contracts, guardrails)
*Exit: `just dev` brings the infrastructure up healthy, `just check` passes, the envelope contract is frozen with golden fixtures, both databases migrate reversibly, and the app shell is walkable in both themes.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-01 | Repo init | git repo, `.gitignore`, README, LICENSE | ✅ done |
| P1-02 | Monorepo scaffolding | `apps/ packages/ deploy/`; `go.work`, uv workspace, pnpm workspace | ✅ done |
| P1-03 | Task runner | `justfile` — same verbs for every service; `just check` is the pre-commit gate | ✅ done |
| P1-04 | Quality gates | `.golangci.yml` (v2 schema), ruff + mypy --strict, `biome.json` with import-boundary rules. Run by `just check` — the GitHub Actions workflow is deferred to the deployment phase | ✅ done |
| P1-05 | Agent context | root `CLAUDE.md` + one per app, scoped to what is true inside it | ✅ done |
| P1-06 | ADR process | `docs/architecture/decisions/` — template, README, and 47 backfilled ADRs with supersede links in both directions | ✅ done |
| P1-07 | **Normalized envelope** | `packages/contracts` — protobuf envelope wrapping untouched OTLP bytes; `buf` codegen to committed Go + Python bindings; `buf breaking` gate; 8 golden fixtures with round-trip proofs in both languages | ✅ done |
| P1-59 | **Control-plane schema** | 11 tables with UUIDv7 defaults, `org_id` denormalized throughout, SHA-256 key hashes; idempotent seed producing a known dev API key | ✅ done |
| P1-08 | Design tokens & theming | `packages/ui` OKLCH scales, both themes, IBM Plex self-hosted; palette re-verified with the official `apca-w3` and a quantised WCAG check that runs as a gate | ✅ done |
| P1-09 | Compose — three targets | `compose.infra.yaml` shared via `include:`, plus dev (hot reload), app (production replica, multi-stage images, all healthy) and onprem (skeleton) | ✅ done |
| P1-10 | Migrations | golang-migrate for ClickHouse + Alembic for Postgres; both proved reversible up → down → up | ✅ done |
| P1-63 | App shell & routes | Vite + React 19 + TanStack Router/Query; global layout; 20 routes + 404 rendering `ComingSoon` with their task id; fetch client on `/api/v1/` | ✅ done |
| P1-64 | Config, validation & health | Typed config validated at boot, exiting with the variable named; `/healthz` dependency-free, `/readyz` per-dependency with its own deadline | ✅ done |
| P1-65 | Backend cross-cutting | RFC 9457 problem+json with correlation ids; sliding-window rate limits per IP and per email; explicit pool sizes, session-per-request, Valkey sessions; ClickHouse tenancy chokepoint | ✅ done |
| P1-66 | Logging & UI handling | One JSON log contract across three languages with redaction proved by test; React 19 root handlers, boundaries, one query-error mapper | ✅ done |
| P1-67 | Documentation reconciliation | Fifteen corrections applied; docs describe no tool, file or flow that does not exist as described | ✅ done |

### M1 — Walking skeleton
*Exit: demo app emits a span; it is visible in the browser as a trace waterfall; the whole path runs from `docker compose up`, and one e2e test proves it end to end in CI.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-11 | Demo app | `tools/demo-app` — three roles calling each other over HTTP; OTel configured only by standard `OTEL_*` vars; four named faults toggled at runtime | ✅ done, then **deleted 2026-09-01**. It served its purpose as a walking skeleton and then became a liability: being three tidy services we controlled, it never produced the messy polyglot telemetry a real customer sends, and it hid defects the OpenTelemetry Demo found within minutes of first running. `just otel-demo up` replaces it |
| P1-12 | Receiver: OTLP/HTTP | `/v1/traces`, protobuf parse via official OTel libs, **split batches one envelope per trace_id**, produce to Kafka keyed by trace_id | ✅ done |
| P1-13 | API keys + tenancy | SHA-256 key hash (never argon2 — hot path), lookup by prefix, single cached lookup resolving org_id + project_id; every envelope tagged at the edge | ✅ done |
| P1-14 | Rate limiting | per-key Redis counters; reject over-limit before any cost | ✅ done |
| P1-15 | Trace writer | `franz-go` consumer group on `spans-raw`; **write-through, no buffer in M1** (ADR 0054 supersedes 0009's window in part); batched insert via `ch-go`; ClickHouse migration `000004` adds the `trace_id` → time-bounds lookup the detail query needs | ✅ done |
| P1-16 | Query API | FastAPI `GET /traces`, `/traces/{id}` through a repository that cannot omit the tenant filter | ✅ done |
| P1-17 | Traces in the shell | session auth, trace list + waterfall screen, built into the shell P1-63 already provides. Routing and layout are **not** here — they landed in M0 | ✅ done |
| P1-49 | Onboarding | signup collects org details + one email → transaction creates org, owner user, `production` project and first key → two env vars → live first-span celebration. User never sees the words org or project | ✅ done |
| P1-60 | E2E harness | Playwright against the running compose stack via `just e2e` — deliberately **not** `just test`, which `just check` runs on every commit and must not require docker; first critical path: signup → org/project/key created → first span → trace visible | ✅ done |
| P1-18 | Load generator | `tools/loadgen` wrapping `telemetrygen` (OTLP/HTTP + gRPC, no app needed); first performance numbers recorded in this doc | ✅ done |

### M2 — Spine hardening (the part that makes it a product, not a demo)
*Exit: the five core invariants in §5 each have a test that fails loudly when broken.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-19 | OTLP/gRPC | port 4317 alongside 4318 | ✅ done |
| P1-20 | No-loss semantics | ack only after broker accept; idempotent consumers; dead-letter queue | ✅ done |
| P1-21 | Tail sampling | 100% error + slow traces, 10% healthy; per-tenant tunable | ✅ done |
| P1-22 | Exact counters | metrics derived from **all** traces before sampling → `metrics` table | ✅ done |
| P1-23 | Materialized view | `endpoint_stats_1m` — explicit target table with `SimpleAggregateFunction` counters and `AggregateFunction` quantiles, partitioned by month; proved to merge correctly rather than accumulate (Phase 2 anomaly-detection hook) | ✅ done |
| P1-24 | PII masking | masking step in workers; rules configurable in Postgres | ✅ done |
| P1-25 | Tenant isolation suite | tests that actively try to leak across tenants and must fail to | ✅ done |
| P1-27 | Chaos test | `just chaos` — kills Kafka then ClickHouse under sustained load and asserts on the broker's own consumer lag. **Zero loss proven**: 282,490 acknowledged, all counted, lag drained to 0. Found four real defects on the way, and measured the counter overcount ADR 0060 predicted (512 spans, 0.18%, ADR 0061) | ✅ done |

### M3 — Widen door 1 (logs + metrics + errors)
*Exit: all three signal shapes flow through the same pipeline and are queryable together.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-28 | Logs pipeline | `/v1/logs` → log-writer → `logs` table. Writer owes severity text → `severity_number`, the resource/record attribute split, a per-record decision on whether the body is a map (ADR 0062), and `received_at` so retention stays on our clock (ADR 0067). **On landing, measure stored log bytes per row and per request and re-check ADR 0071** — its retention cost model estimates logs at ~87% of stored bytes and that term is unmeasured | **done** — and the measurement it asked for is taken: **logs 10.1 bytes/row on disk, spans 18.1** (`system.parts`, 297k log rows / 12.4M span rows). ADR 0071 assumed ~35 bytes per log line, so it overstates log storage **3.5×**. Its *conclusion* survives: substituting the measured per-row bytes into its own volume assumptions (4 log lines per request, tail sampling keeping ~10% of traces) still puts logs at **82% of stored bytes** against the 87% it estimated, because the share is driven by sampling, not by row size. The volume assumptions themselves remain unmeasured and dev traffic cannot settle them — our load is synthetic and almost all spans |
| P1-29 | Metrics pipeline | `/v1/metrics` → the five OTLP metric tables (ADR 0066); `derive` moves off the old `metrics` table onto `metrics_sum` with temporality, monotonicity and start time, and `metrics` then retires | **done** — five OTLP tables live, `derive` moved onto `metrics_sum`, and the old `metrics` table is gone (verified absent from `system.tables`) |
| P1-30 | Error extraction | `exception_type` / `exception_message` promoted to columns; grouping view | ✅ done 2026-09-01, **spans included**. Logs had always written the columns; spans never did, so the span half of the errorgroups query had never returned a row — measured at 0 of 12,499,337. The first `exception` event on a span now fills them (ADR 0088). The earlier note blamed the demo app for logging exceptions rather than recording them as span events; that was true of the demo app and irrelevant to the defect, which was that nothing read the events we did have |
| P1-31 | Log↔trace correlation | "logs for this trace" query path both directions | **done** — `GET /logs?trace_id=` is the trace→logs path, and log rows carry `trace_id`/`span_id` so the reverse navigation needs no second endpoint |
| P1-32 | Log pattern grouping | Drain-style templating → `pattern_id` on logs *(optional — drop if it delays M4)* | **dropped** — explicitly optional in this row, and M4 is the priority. Nothing depends on it |
| P1-55 | Service dependency edges | Go worker derives caller→callee edges (call rate, error rate, latency) from cross-service parent/child spans → small ClickHouse table. No new collection | **done** — worker derives caller→callee edges into `service_edges_1m` (616 rows present in dev) |

### M4 — Doors 2 and 4 (our probes, webhooks, pollers)
*Exit: data arrives from sources the customer installed nothing for.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-33 | Celery + our own Beat scheduler | `Scheduler` subclass reading check definitions from Postgres (UI edits config, no redeploy); Redis broker. No community DB-scheduler dependency | **done** — Celery with our own `PostgresScheduler`, reading `checks` and `integrations` on a 30s timer. **Two beat replicas**, not one: a single scheduler is a single point of failure and the thing it decides is when every customer's checks run. Leadership is a Redis lock with compare-and-set refresh; negative-controlled against a stalled leader stealing back a successor's lock |
| P1-34 | Uptime + SSL checks | HTTP/latency-breakdown + certificate expiry → normalized metrics → Kafka | **done** — pycurl, because libcurl reports DNS, TCP, TLS, TTFB and transfer separately and httpx reports only a total. Verified against real targets and after a full round trip through Kafka and ClickHouse: the phases sum to the reported duration exactly. Certificate expiry rides on the same connection rather than a second probe |
| P1-35 | Browser flow checks | Playwright on an isolated queue (low concurrency, bigger containers) | **done** — Playwright on its own queue and its own image. Two defects found by running it: the image had never started (uv put its Python under `/root`, mode 0700, which the image's `pwuser` cannot traverse), and browser flows were routed to the *light* queue because both check types shared one task name — so every flow failed on a missing Playwright import while the queue separation did nothing. Both fixed and proven live: `2 of 2 steps passed` |
| P1-36 | Deploy webhooks | GitHub/CI → `deploy_events` in Postgres (Phase 2 correlation hook) | **done** — `POST /deploys/webhook/{project_id}`, HMAC-SHA256 over the raw body, `hmac.compare_digest`, raw stream read so a forgery cannot be answered 422 before its signature is checked. Verified live: signed 201, forged 401, unsigned 401. **Deferred, needs a migration:** independent rotation of the deploy secret (today it rides on the ingest key) and replay rejection (needs a delivery-id column) |
| P1-37 | Cloud + DB pollers | CloudWatch metrics; `pg_stat_statements` read-only; credentials encrypted at rest | **done, untested against real AWS** — ADR 0076: hold no credential we can avoid holding. The customer grants a role and we store its ARN plus an ExternalId we generate; each poll mints 900-second STS credentials. RDS uses IAM auth, so no password either. Self-hosted Postgres is refused rather than given an invented key scheme. Stubbed with `botocore.stub.Stubber` — **no real AWS call has ever been made**, so the end-to-end claim rests on documentation |

#### M4 follow-ups — reviewed and closed 2026-08-29

Eight findings from reviewing M4 against real customers and an adversary rather
than against the suite, which was green throughout. Seven are fixed; the eighth
cannot be fixed by writing code.

| # | Finding | Outcome |
|---|---|---|
| 1 | A broken connector was silent — `last_sync_at` and `status` never written, no error handling around `assume_customer_role` | **Fixed.** A poll records its outcome, and `last_error` carries AWS's own words. Verified live: a connector with an unusable role now reads `failing / Unable to locate credentials` |
| 2 | Nothing monitored the monitor | **Fixed.** Beat emits a heartbeat with ticks, scheduled checks and polls, and refresh failures — the counters that show movement. Live: `scheduled_checks=3 scheduled_polls=1 ticks=678` |
| 3 | No tenant fairness or quota | **Partly fixed.** 100 checks per project, 20 steps per flow. *Still open:* every tenant's browser flows share one worker at concurrency 1, so fairness needs per-tenant queues or more workers |
| 4 | Browser timeouts exceeded the task limit | **Fixed.** The browser queue has its own limits (180s/240s) and the per-step timeout is divided out of that budget, so a flow always finishes inside it |
| 5 | Postgres tables had no structural tenancy guard | **Fixed.** `test_postgres_tenancy_structure.py` derives tenant-scoped models from the models module and asserts every select/delete/update against one names `org_id` *inside the statement*. Four exemptions, each with its reason |
| 6 | The public webhook shared the default rate-limit tier | **Fixed.** Its own tier at 60/min |
| 7 | Deploy secrets could not rotate; replays were not rejected | **Fixed.** Migration 0007: secrets get their own table, provisioned at signup, many per project so a rotation overlaps. Deliveries carry an id under a partial unique index. Verified live: replay 409, both secrets accepted mid-rotation, revoked secret 401 |
| 8 | Door 4b has never touched real AWS | **Cannot be fixed here.** It needs an AWS account and a role created with our account in its trust policy. No code makes that evidence exist. Stays open |

The SSRF bypass found during the same review — browser step URLs stored
unvalidated — is closed and tested.

Known and accepted: DNS rebinding defeats target validation, because a check is
validated in the API and fetched minutes later elsewhere. Closing it needs the
prober to check the address it actually connected to.

#### M6 decisions, taken 2026-08-30

**Infrastructure and Usage screens are deferred, not dropped.** Both endpoints
are built and tested; neither has data. A distinct across all five metric tables
returns zero rows matching `system.*`, `container.*`, `host.*`, `process.*` or
`k8s.*`, and no resource carries a `host` key — nobody is running a collector's
hostmetrics receiver against us. `usage_daily` is empty because **no code writes
it**: not the worker, not the scheduler, not the receiver. The screens are a
day's work once something feeds them.

**That leaves a job nobody had noticed:** counting ingestion into `usage_daily`.
It belongs in the worker or the scheduler and is a prerequisite for both the
usage screen and any billing.

**Endpoint ranking: failures first, then latency.** Already what
`services/queries.WORST_FIRST` does — `error_count DESC, p95 DESC,
request_count DESC`. No magic slow-threshold, because a report export is
legitimately slow and would sit at the top of every list forever. The screen
offers a "sort by slowest" toggle for the other question. A per-endpoint latency
objective is the proper answer and is deferred: it needs a column, an editing
surface, and a decision about endpoints nobody has set one for.

**Screen order: Services, Synthetics, Service map**, then Errors and Logs.

**Still open:** where a price lives — a column on `plans`, or a table with
effective dates so a repricing does not rewrite what was already charged.
`plans` holds no money at all today, and a test fails the day that changes.

### M5 — Door 3 (browser / RUM)
*Exit: real user data from a script tag, correlated to backend traces.*

**Both halves met, and the second one only on 2026-09-06.** "Real user data from a
script tag" was proven in `tools/e2e/tests/rum.spec.ts` when P1-39 landed.
"Correlated to backend traces" was the open question the plan carried — the
stable SDK propagates `traceparent` on its fetch calls, but nothing here had ever
shown the two ends in one trace. P1-63 showed it: the OTel Demo's storefront,
trace `af4c252e…`, a browser span and 23 backend spans, `frontend-web →
frontend-proxy → frontend → product-catalog`. The criterion needed a real
application to demonstrate against, which is why it stood open until the demo
had a browser reporting through our door.

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-38 | Public site keys + CORS | non-secret per-site key path on the receiver | ✅ done. A public key is a **separate table**, not a flag on `api_keys`: the server door ends in `subtle.ConstantTimeCompare(HashKey(presented), stored.KeyHash)`, and a kind column makes that comparison conditional — a wrong branch there means a value printed in every page's source authenticates full server ingest (ADR 0086). It is not hashed, because a hash protects a published value against nothing. The browser door is its own path prefix for the same reason (ADR 0087): the preflight carries no credential — a browser announces the key header's *name*, never its value — so it is answered permissively and unauthenticated, and the POST behind it is what gets judged. `text/plain` is accepted on that door alone, because `sendBeacon` fixes credentials mode to `include` and could not deliver otherwise, which lost every page-unload flush (ADR 0089). Origin is checked **before** the rate limiter so a wrong-origin request cannot spend the owner's budget. Proven in a real browser through to a stored ClickHouse row |
| P1-39 | Browser SDK ingestion | `packages/browser-sdk`, the first thing this repo ships to a customer's page. **Stable `@opentelemetry/sdk-trace-web` for the page-view span and Google's `web-vitals` for the five vitals, with our own transport** — every OTLP exporter is still on the 0.x experimental line, and the stock one cannot send a beacon as `text/plain` with the key in the query string on unload, which ADR 0089 requires. `internal/pipeline/vitals` promotes each `browser.web_vital` record into `web_vitals` and its p75 rollup from **inside** `log-writing`: the records are already decoded and already masked there, and a consumer group of its own would decode every log record twice and need a second watermark. Both inserts carry an `insert_deduplication_token`, which migration 000030 said the writer must. **Two defects found by running it rather than reading it.** `device_class` was read off the log record and the SDK puts `browser.mobile` on the **resource** — the column would have filled with nothing, which looks exactly like a column nothing has filled yet; found by putting a real browser through the door. And a refused browser record parked the whole chunk, taking every other tenant's log records with it — the shape of the incident that once stopped ingest for 41 minutes — so a refusal is now counted and stepped over, because `web_vitals` is a promoted copy and `logs` is the record itself. The SDK also stopped being able to throw into a customer's page: the `load` listener was unguarded, and one unobservable metric cost all five. **Verified in a real browser**, which is the only place `sendBeacon`, the preflight and `text/plain` exist: 108 SDK tests, gzip 19,871 against a 25,600 budget, ClickHouse holding four vitals with `device_class = desktop`, and p75 of 100/200/300/400 reading back as 325 through `quantilesMerge` | ✅ done |
| P1-58 | Source maps | Upload, storage and symbolication, and the Errors screen calls it. **The minified bundle is not stored, and measurement is why**: `symbolic`'s `SourceMapCache.from_bytes` takes it beside the map, and run both ways on a real map the difference is *one field* — with the bundle a frame resolves to `Checkout.tsx:1` named `greet`, without it the same frame resolves to `Checkout.tsx:1` named `<unknown>`, and the surrounding source is identical either way because it comes from the map's own `sourcesContent`. So the bundle buys a function name and would cost storing the customer's whole application for every build. Bytes go to object storage over the generic S3 API (ADR 0015) with boto3 off the event loop through anyio; `storage_key` names a key and never a URL. **Parsing happens in the browser**, because the maintained parsers for browser stack formats are all JavaScript and a server-side one would be hand-rolled regex over three engines — which is also why `/symbolicate` takes frames rather than a string. **A path fallback, without which the feature does nothing in practice**: a stack frame names the URL the browser fetched and CI uploads what the bundler wrote, so the lookup tries the exact string, then the URL path, then the basename. Two defects found by running it: an **eval frame parses to an empty file, not a null one**, and left in it failed validation and cost the whole trace its symbolication — every Chrome stack from an injected script has one; and the release was added to the grouping projection but not to the statement selecting out of it, which **passed all 22 offline tests** because the fake store returns fixed columns whatever SQL it is handed. `test_errorgroups_live.py` exists for that gap and was watched failing with the same `KeyError` before the fix. **Verified against a genuinely minified module**: vite built and minified it, the map went through the upload API, a real Chromium threw from the bundle, and the screen resolved `checkout.min.js:1:111` to `Checkout.js:2:23` — `const lines = order.lines`, the exact line that threw. 3 of 3 frames | ✅ done |

### M6 — Dashboard UI surface
*Exit: every signal we collect has a competent screen, each covered by an e2e critical path, and the whole set survives the OTel Demo realism gate. This is the credibility layer, built once, not polished forever.*

**Every screen task below replaces an existing `ComingSoon` placeholder** — the route, the layout and the navigation entry already exist from P1-63. None of them creates a route, and none rebuilds the shell.

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-40 | Service overview | request rate, error rate, p95 trend; endpoints ranked by slowness/errors | ✅ done |
| P1-41 | Trace explorer | search + filters over the M1 waterfall | ✅ done |
| P1-56 | Service map | dependency graph from P1-55 edges; edge call rate/error/latency; click-through to a service | ✅ done |
| P1-42 | Errors screen | grouped exceptions, counts, first/last seen, stack traces | ✅ done |
| P1-43 | Logs screen | search, filters, **live tail** via WebSocket. Fed by a cursored ClickHouse poll per project, not a broker: nothing publishes log records to Redis today, and pub/sub loses whatever arrived while a viewer was reconnecting. A publish step in the Go log writer is the root fix and is not done | ✅ done |
| P1-44 | Infrastructure screen | hosts/containers: CPU, memory, disk, network. K8s metrics are ingested and queryable, but pod/node/namespace views are Phase 2 | ✅ done |
| P1-45 | Synthetics screen | check list, status, latency trends, failure detail with screenshot | ✅ done |
| P1-46 | Database screen | slow queries, mean time, calls, connection health | ✅ done |
| P1-47 | RUM screen | Core Web Vitals by page/geo/device; JS errors | todo |
| P1-48 | Usage & cost view | per-tenant ingestion counters → usage table → **the calendar month's service cost in-product**. The union of distinct services over the month at the rate in force, held to the plan's ceiling. Narrower than "projected cost", which is what this row used to say: it is **what has accrued so far, not a forecast of the month's end**, no cost is shown at all when the tier has no price row, and **volume overage is not in it** — the allowance is metered in bytes and its amount, and what happens above it, are still undecided. A month still being metered is labelled rather than shown as a small number, and a project whose service count jumps is warned about, never capped | ✅ done |
| P1-61 | E2E screen coverage | Critical path per screen against the running stack, run by `just e2e`. **Not** `just check` — P1-60 settled that, and the justfile follows it: the commit gate must not require docker | ✅ done |
| P1-62 | **OTel Demo realism gate** | `just otel-demo up` runs the OpenTelemetry Demo (18 services, ~10 languages) against our receiver. Trimmed to twenty containers it needs **1641 MiB**, not the 4–8 GB estimated, because the demo's declared limits are ceilings rather than reservations — no Docker allocation change was needed. Not a daily dependency. **What it found is the deliverable**: one malformed log record stopped ingest for every tenant for 41 minutes; five of twelve services had three blank promoted columns; the `queue` edge kind has never produced a row; span re-consumption after a crash duplicates rows | ✅ done |
| P1-63 | **Every door on the OTel Demo** | The demo fed door 1 only, so a developer running the daily traffic source still opened Synthetics, RUM and Infrastructure to empty screens. Five doors now, from two commands. **Door 1's browser half was one variable**: the demo frontend has always shipped an OTel browser SDK and we had blanked its endpoint, so un-blanking it at the *browser* door — through Envoy's existing `/otlp-http/` route, key in the query string — **answers M5's exit criterion.** Measured, not argued: trace `af4c252e…`, 24 spans, `frontend-web → frontend-proxy → frontend → product-catalog`, one waterfall from a click to the catalogue. `loadgen` cannot produce that and the deleted demo shop could not either. **Host metrics needed a collector of our own** — `hostmetrics` with `root_path`, the only container here that is ours and a *producer*, not upstream's dropped backend. Three things it taught: `system.cpu.utilization` is **off by default** (the default `system.cpu.time` is a name the screen does not read, so a collector left at defaults sends nothing usable); the filesystem scraper needs the filesystems bind-mounted and not only `/proc`, which closes conventions.py's *"No filesystem reading has ever arrived"*; and `host.name` must be set explicitly, which closes *"No resource carried a host.name; re-checked 2026-09-01 and still none does"*. 7 of the 13 names arrive — `filesystem.limit` is not one the scraper emits and the five `container.*` names still have no producer, both stated rather than hidden. Found on the way, and not a defect: **`recommendation` has been sending `system.*` all along** from inside its container with no host name, so the screen now shows a real host row and a reporter row together — ADR 0082's fallback working in the open, diagnosed by the screen itself. **Door 3 is injected by Envoy**, because the frontend image is pulled prebuilt and the clone is never edited in place; the bundle is served by `image-provider`, which Envoy already routes `/images/` to. `location.origin + "/otlp-http"` is one string that serves both a person on localhost:8080 and a container on frontend-proxy:8080, and both are same-origin so there is no preflight. Two traps: **Content-Length must be dropped before the body is buffered** — Envoy does not recompute it and the headers have already gone downstream by then, measured at 11549 declared against 11882 sent, which truncates the page — and **no `$` may appear in the injected JavaScript**, because the image's entrypoint runs envsubst with no variable list. **k6's browser mode is back on and both reasons we had disabled it were wrong**: not a Chromium per virtual user (`script.js` pins `vus: 1`) and not expensive (150 MiB of a 512 MiB limit) — but it produces **no browser telemetry at all**, ours or upstream's own `FrontendTracer`, over 55 completed iterations, so the RUM screen is filled by a person or by `just load`. **Door 2 needed no change to `targets.py`** (`host.docker.internal` validates today; its own docstring says it is not the boundary) and a narrow connect-time escape instead — ADR 0098, two settings, loopback and link-local refused at every setting, verified live with `127.0.0.1.nip.io` and `169.254.169.254.nip.io` both refused while the storefront answered 200. **Door 4a gets its first exercise ever** through `tools/otel-demo/doors.py`, stdlib only, credentials off the product rather than out of a constant. **Door 4b stays blocked** on ADR 0076: `astronomy-db` ships `pg_stat_statements` preloaded and a `monitoring_user` already granted, and none of it is reachable because the poller refuses password auth for self-hosted Postgres. Demo now **2007 MiB across 21 containers**, up from 1641 across 20 | ✅ done |

### M7 — Phase 1 exit
| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-50 | Load + soak | 24h soak against the budgets in §4; results recorded here | todo |
| P1-57 | Object storage cold tier | **SeaweedFS** in compose for local dev; **AWS S3 in deployment**; ClickHouse-native S3 tiering past the hot window + tested restore. Generic S3 API only | todo |
| P1-51 | Backup/restore | runbook + tested restore for ClickHouse and Postgres | ✅ done. **Logical** per store, not the volume copy this row first asked for — that is what makes it portable across versions and selectively restorable. Native ClickHouse `BACKUP`/`RESTORE`, `pg_dump --format=custom`. Two full cycles run with every Docker volume deleted in between; both sides matched on all 12 ClickHouse and 17 Postgres tables, and a known trace returned at 78 spans across 12 services with identical nanosecond timestamps. ~40s to data-back-and-checked. **The restore's real hazard was a watermark, not the data**: the dump carried `spans-raw:0 → 250` into a broker recreated at offset 0, which would have made the meter skip that partition's first 251 records for ever, silently — ADR 0095 extends ADR 0068's KeeperMap exclusion to Postgres's `usage_ingest_offsets`. Runbook `docs/delivery/runbooks/restore-from-backup.md`. **Left open, deliberately: schedule, retention, off-site location, encryption** — recovery point today is whenever somebody last ran `just backup` |
| P1-68 | Apply plan retention | **Narrowed 2026-08-31 after re-reading the code.** The `plans` table from [ADR 0071](../architecture/decisions/0071-plan-tiers-and-retention.md) exists and is seeded (migration `0005`, verified live), and the per-row stamp is live — so retention is already applied forward. The TTL-regeneration job this row used to name is gone: it belonged to ADR 0038, which [ADR 0072](../architecture/decisions/0072-retention-class-partitioning.md) superseded precisely by removing it. What is left is the downgrade path — changing a plan's retention affects only rows written afterwards, and making it retroactive would drop stored parts with no undo (§9). Deferred deliberately; nothing about it is a one-way door, and until it exists every tenant gets the fixed `pro`-shaped retention, which over-serves rather than under-serves. `scale` additionally needs P1-57 | todo |
| P1-52 | Ingest kill switch | per-tenant stop valve for runaway ingestion | todo |
| P1-53 | Security pass | key hashing, TLS at the edge, secret handling, dependency audit | audit done, **fixes outstanding**. Tenant isolation verified on both stores with evidence (6 structural tests on ClickHouse, 517 on Postgres) — no query escapes the chokepoint. Credential handling, secrets-in-git and the dependency audit came back clean across Go, Python and npm. Carrier-grade NAT was accepted as a public address by both address boundaries and is now refused (ADR-free, one commit). **Three findings remain and each needs a decision, not a patch:** browser synthetic checks have no SSRF boundary at all (`page.goto` bypasses the socket guard the HTTP check uses); a scraped site key can now spend a free org's whole month, because ADR 0093 made the consequence 31 days long and ADR 0087 reasoned about it when it was one second; and `TRUSTED_PROXY_COUNT` is unset behind Caddy, so ten login attempts a minute lock out every user. TLS and security headers at the edge are still unbuilt |
| P1-54 | Customer docs | setup guide for both onboarding patterns (SDK direct, own collector) | todo |

### M8 — The commercial layer (account, billing, the way in and the way out)

*Exit: a stranger can sign up, prove they own the address, recover a lost password, see what they
are being charged, pay for it, change plan, and leave — without any of it being a SQL statement we
run for them.*

**Why this is a milestone and not a settings screen.** M0–M7 build a system that works. None of it
makes the system *sellable*, and two things make that gap concrete rather than cosmetic:

- **The byte cap ships a dead end.** A free organization that spends its allowance is refused until
  the 1st, and the screen names the limit and the reset date because there is nowhere to send them.
  There is no upgrade path in the product. We built a wall with no door.
- **Signup trusts an address nobody proved.** `users.email_verified_at` has existed since migration
  0001 and **nothing writes it** — the same shape as `last_used_at` and `site_keys` before them: a
  column added in anticipation, and no code behind it. A customer who mistypes their email at signup
  owns an account they can never recover, because there is also no password reset.

**Two hard dependencies gate almost everything below, and neither exists anywhere in the repository.**

- **A transactional email sender.** Nothing sends mail — no SMTP, no provider, no queue. Verification,
  password reset, and invoices all need it, and it is the reason `/settings/members` was deferred in
  §9. It is a vendor decision before it is a code decision.
- **A payment processor.** Nothing takes money. `plan_prices` states what a plan costs and no code
  charges anyone. This is a vendor decision, a compliance question (tax, VAT, invoicing per
  jurisdiction) and a data question (what we store versus what the processor holds) — the same
  posture ADR 0076 took for cloud credentials: hold nothing we can avoid holding.

**What this milestone does not reopen.** §9's single-operator decision stands: `/settings/members`
still does not ship, because a role picker in front of enforcement that arrives in Phase 2 (F26) is a
promise the product would not keep. Project management is revisited here because one project per
organization stops being a scoping choice and starts being a limitation the moment someone pays.

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-69 | **Transactional email** | The sender nothing has today, and the blocker for three rows below. Needs a provider chosen, a deliverability posture (SPF/DKIM/DMARC on a sending domain), a bounce and complaint path, and a decision about what happens to a flow when delivery fails — a verification link that silently never arrives is worse than a visible error. Templates are the small part | todo |
| P1-70 | Email verification | Writes the `email_verified_at` that has existed unwritten since 0001. Forces: what an unverified account may do — ingest freely, ingest nothing, or read but not send — and how long a link lives. Verifying *after* signup rather than blocking it keeps the first-span celebration intact, which is the one moment onboarding earns | todo |
| P1-71 | Password reset | A single-use, expiring, constant-time-compared token, and the same answer for a known and an unknown address so the form is not an account-existence oracle. Ends every live session on success — `sessions` exists and is Valkey-backed, so the revocation path is real work, not a flag | todo |
| P1-72 | Profile & security screen | Name, email change (which re-verifies), password change (which requires the current one), and **timezone**. The timezone is not cosmetic: every screen renders timestamps in the browser's locale today, so an incident timeline read in IST against a UTC ClickHouse row is a reader doing arithmetic during an outage. Also lists live sessions with a way to end them | todo |
| P1-73 | Payment processor | The second hard dependency. Store the processor's customer id and nothing else that can be avoided — no card data, no PAN, ideally no address we are not required to hold. Webhooks for payment success and failure, verified the way `POST /deploys/webhook` already verifies its own: HMAC over the raw body, compared in constant time | todo |
| P1-74 | Plan change | The door in the wall. Upgrade must take effect on the ingest path within one allowance-cache TTL, because a customer who has just paid to be unblocked will retry immediately. Downgrade forces the harder half: what happens to data retained under a longer plan when the shorter one takes effect — and §9 already establishes that shortening retention retroactively drops parts with no undo, so the honest answer is probably forward-only again | todo |
| P1-75 | Billing screen | What is owed and why, payment method, invoice history, and a billing email separate from the login one — the person who pays is often not the person who signs in. The usage screen already shows what accrued; this is where it becomes a charge | todo |
| P1-76 | Project management | Create, rename, soft-delete. §9 records what this forces and it has not changed: who may press each, and what deleting a project does to telemetry already in ClickHouse — `projects.deleted_at` exists and nothing drops partitions, so a deleted project's data is invisible and still stored, still counted, still retained | todo |
| P1-77 | Account & organization deletion | The way out, and a GDPR obligation rather than a courtesy. Needs the ClickHouse purge path §7c already flags as missing — `ALTER TABLE spans DELETE` does not cascade to `span_trace_lookup`, and the same will be true of `trace_summaries` and both rollups. Cancelling a subscription is the easy half | todo |

**Ordering that is not negotiable:** P1-69 before 70 and 71. P1-73 before 74 and 75. Everything else
can move.

---

---

## 4. Performance budgets (measured by `just load`, not estimated)

**Target machine: Apple M4 Pro, 24 GB, full stack co-resident in Docker** (see `dev-environment.md`). On one laptop these are a **regression gate, not a capacity model** — production numbers are re-measured when a production host exists.

| Budget | Target | Measured |
|---|---|---|
| Receiver throughput | ≥ 10,000 spans/sec sustained, single instance | **62,464 spans/sec** — pass. Generator-bound, not a receiver ceiling: the receiver sat at 24% of one core. Three senders at once reached only 65,536 spans/sec and saturated the laptop |
| Receiver accept latency | p99 < 50 ms | **p99 10.8 ms** at 12k spans/sec, 9.5 ms at 62k — pass. Rose to p99 ~72 ms only when three senders saturated the host |
| Trace-split re-encode overhead | < 20% of receiver CPU | — not measurable from `just load`. The receiver exposes no profile endpoint and `tracesplit` is internal to `apps/receiver`, so an honest figure needs a benchmark or pprof inside that module |
| Receiver memory | < 512 MB under sustained load | **36 MiB peak** (28 MiB at 12k spans/sec, 27 MiB idle) — pass, with two orders of magnitude of room |
| Span → queryable lag | p99 < 15 s | **p50 12.0 s, max 12.8 s** with tail sampling live (was 0.65–2.2 s write-through) — pass. The 10 s sampling window (ADR 0058) is now the floor, exactly as predicted; ADR 0054 required this be re-measured rather than assumed once the buffer returned |
| Trace-by-ID query | p95 < 500 ms | **p95 21.8 ms** (p50 16.7, p99 24.9), 60 random traces against an 11.2M-span table — pass. This is the budget `span_trace_lookup` exists for: without the time bound it derives, the query would lean on the bloom filter across every partition |
| Endpoint stats, 24h window | p95 < 300 ms | — |
| Log search, 1h window | p95 < 2 s | — |
| First screen load | < 2 s (PRD AC1.1) | — |
| Data loss under broker restart | zero | — |

**M1 gave the baseline.** Five budgets measured and passed, all with large margins; §4 said revise once against real numbers and then hold, so these targets now stand as the regression gate. The re-encode budget remains unmeasurable until the receiver exposes a profile endpoint — that is a gap in the receiver, not in the measurement. Disk in dev is bounded by a 3-day ClickHouse TTL rather than the 30-day production setting.

---

## 5. Core invariants (each needs a test that fails loudly)

1. **Tenant isolation** — a query cannot be constructed without a tenant filter. Enforced by the repository layer + a lint rule + a suite that tries to break it.
2. **No data loss** — ack after broker accept, idempotent consumers, DLQ for poison messages.
3. **Correct numbers** — metrics derived pre-sampling. Test: sample at 10%, assert counts stay exact.
4. **One internal format** — all doors normalize before the broker. Storage never learns that doors exist.
5. **Budgets hold** — §4 is enforced continuously, not audited occasionally.

---

## 6. Claude tooling to build (and when)

Context management matters more than the skills themselves. The lever is **scoped `CLAUDE.md`** (P1-05) plus splitting the PRD into `docs/prd/F01-*.md` per feature once more features are specified — so a session loads one feature, not thirty.

| Build | What it does | When |
|---|---|---|
| `spec-feature` | Writes a PRD feature in the exact F1 template | Before Phase 2 specs resume |
| `feature-research` | Analyzes a feature from the user's point of view + what the market ships | Alongside `spec-feature` |
| `ui-system` | Encodes the design spine and anti-pattern list so agents inherit the constraint | M0 (P1-08) |
| `new-service` / `new-endpoint` | Scaffolds to our conventions — tenancy, logging, tests, metrics pre-wired | After M1 proves the conventions |
| `review-nexovue` | Repo-specific review: tenant filter, unbounded queries, reversible migrations, error paths | After M2 |

Subagents: **architect**, **ui-designer**, **market-analyst**, **perf-auditor**. Already connected: Figma, Playwright, Chrome DevTools MCP *(Figma needs OAuth from an interactive session)*.
Rule: build a skill only after the task has repeated three times.

---

## 7. UI/UX spine

**Thesis:** this is a *reading* product, not a charting product. The primary object is a sentence — "Checkout is 4× slower than normal since 15:04." Everything else is evidence supporting a sentence.

Consequences: typography-led and editorial, not card-grid; density closer to a terminal or newspaper than a marketing page; charts as sparklines inside sentences rather than panels beside them; severity encoded by weight and shape, not color alone; dark mode first-class (this is read at 2am); keyboard-first with a command palette.

**Anti-patterns — the recognizable "AI-generated UI" signature, banned:** default shadcn styling, slate-gray palette, uniform rounded card grids, Lucide icons as the whole visual language, gradient hero accents, 16px padding everywhere, color as the sole carrier of meaning.

Mechanically: tokens exist before the first screen; raw hex outside `packages/ui` fails lint.

---

## 8. Risk register

| Risk | Mitigation |
|---|---|
| Go proficiency on the data plane | M1 is deliberately small; receiver and assembler are the learning surface before volume matters |
| Single-host sizing (Kafka + ClickHouse together) | Measure at M1, size at M7; kill switch (P1-52) before any external traffic |
| Attribute-map cardinality explosion | Promotion policy reviewed against real query logs after M3 |
| Per-tenant retention (ClickHouse TTL is table-level) | Open question in the architecture doc — decide before M7, not before M1 |
| Scope creep from Phase 2 | Anything touching findings/AI/alerts is out; the hooks list in §0 is the complete exception |
| Playwright resource cost | Isolated queue, hard timeouts, low concurrency (P1-35) |
| ~~Our demo app is too tidy to be realistic~~ | **Closed 2026-09-01 by deleting it.** The OTel Demo gate (P1-62) proved the point within minutes of first running — a log record that stopped ingest for every tenant, five services with three blank promoted columns, a queue edge kind never once derived. A demo we control cannot be the thing that tells us we are wrong, so it is gone and `just otel-demo up` is the traffic source |

---

## 9. Tracking

- This file is the source of truth. Update the `Status` column in place.
- Milestones mirror to GitHub milestones; tasks that take more than a day get an issue referencing their `P1-nn` ID.
- Measured numbers land in §4 as they are taken — the table is a log, not a wish.

## 7a. Deferred to after production

- **P1-26 · Self-instrumentation** — nexovue monitoring nexovue. Deferred 2026-08-26: worth doing, but not before the platform runs somewhere real. There is no production outage to survive, no external alerting to sit outside it, and no customer ingest to protect from a self-inflicted error storm. The rules that make it safe are settled in ADR 0053, so revisiting is a question of timing, not design.
  *While deferred:* log lines carry no `trace_id` or `span_id` — nothing produces spans. Per-request correlation still works, because `correlation_id` comes from middleware rather than OTel.

## 7b. Follow-ups surfaced while building M0

- **Split the worker back out when measurement says to.** ADR 0050 collapsed four worker containers into one because they were identical code; the pipeline names survive so that splitting is a `WORKER_PIPELINES` change per deployment, not a rewrite. The signal to split is one pipeline's memory or lag diverging from the others under the M7 load test.

- **The three Python telemetry modules are duplicated** between `api` and `scheduler`. The root fix is a `packages/py-platform` uv workspace member; it needs a root `pyproject.toml` change and was outside every lane's ownership.
- ~~**No Go driver is chosen for Postgres or Valkey.**~~ **Resolved in M1** — ADR 0055: `pgx/v5` + `pgxpool`, and `valkey-io/valkey-go` (the maintained rueidis fork) rather than `go-redis`. `/readyz` now probes all three.
- **Plan-aware rate limits are stubbed** at a fixed multiplier until `organizations.plan` carries real tiers.
- ~~**`logs` and `metrics` sort keys** were left as recorded.~~ **Resolved in M2** — ADR 0057: `project_id` is now second in `logs`, `metrics` and `endpoint_stats_1m`, applied while both tables were still empty, which ADR 0049 called the only moment it is free.

## 7c. Follow-ups surfaced while building M1

- **Mobile waterfall hides the `detached` and `skew` tags** for want of room. They survive in the detail panel, so the information is not lost, but a phone user never sees on the row that a span is orphaned or clock-skewed — exactly the two things that make a waterfall confusing. Needs a smaller affordance, not the same one shrunk.
- **The mobile span-detail sheet is a fixed 55dvh** with no drag-to-resize. Fine for a short span, cramped for one with many attributes.
- **`ALTER TABLE spans DELETE` does not cascade to `span_trace_lookup`**, leaving rows that claim a trace exists after its spans are gone. The detail query treats "lookup hit, no spans" as not-found so nothing breaks, and TTL clears them eventually, but a real purge path (P1-52, GDPR delete in Phase 2) must clean both.
- ~~**`last_used_at` on `api_keys` is never written.**~~ **Resolved in P1-13** — ADR 0081: the receiver records a use only on a credential-cache miss, so the write is throttled by the cache TTL rather than by a clock of its own, and it is queued to a background writer so no export waits on it. Measured under continuous traffic: one write per key per 30s, matching `RECEIVER_API_KEY_CACHE_TTL`. The column has existed since migration 0001; only the write was missing.
- **The receiver's rate limiter fails open** when Valkey is unreachable, on the reasoning that losing telemetry costs more than briefly losing the limit, with body caps and the broker still bounding damage. Deliberate, and worth revisiting once a paying tenant can be harmed by it.
- **The receiver exposes no profile endpoint**, which is what blocks the trace-split re-encode budget (§4) — permanently, not just today. `net/http/pprof` on the existing admin listener would settle it, but exposing profiles needs a deliberate decision about who can reach that port.
- **`resolveWindow` rounded the live window's end *down* to a bucket**, so the trace list silently omitted the newest 0–10 seconds, and `TraceListScreen` then froze that window at mount. Together they broke M1's exit condition at the last hop: onboarding said the first span had arrived, and the list it linked to was empty. Fixed in M1; noted because the shape recurs — a cache-stability trick that quietly changes what the query means.
- ~~**The trace list groups over `spans` on every request.**~~ **Looked at in P1-41, and it
  was worse than "not cheap": it failed.** Measured against 12.45M spans / 2.73M traces in the
  dev store, a 7-day window — a range the screen offers — needed **1.8 GiB** of aggregation
  state and ClickHouse killed it at the 2.5 GiB server cap. Not slow: a 500.
  The cause was naming each trace after its root span with `argMin` over two strings *inside*
  the group, which put those strings in every one of 2.73M entries. The group now holds only
  fixed-width numbers (**640 MiB**, 350 ms) and the two root strings are read for the fifty
  traces the page returns, which the `trace_id` bloom filter makes a 16 ms second query. The
  statement also carries `max_bytes_before_external_group_by = 256 MiB`, so a window large
  enough to threaten the server spills to disk (288 MiB, 770 ms) instead of failing; an hour
  never reaches the threshold and is unchanged.
  ~~**Still open, and this is the real fix.**~~ **Done** — ADR
  [0084](../architecture/decisions/0084-newest-first-is-a-sort-key-read-of-a-per-trace-row.md),
  migration `000028`. The list reads `trace_summaries`, one row per stored trace ordered
  `(tenant_id, project_id, start_time, trace_id)`, so a page costs the page. Re-measured on the
  same store, before and after back to back: **7 days 1100 ms / 290 MiB / 12.48M rows read →
  44 ms / 17.3 MiB / 148k**. The hour and the day got *slower* in wall clock — 9 ms → 13 ms and
  12 ms → 21 ms, two round trips where one small group-by was already trivial — and that is the
  trade: the cost stops being a function of the window, so ten readers of a week now pay ten pages
  rather than ten windows.
  The row is written **after** the sampler, from the rows the sampler released, so the list names
  only traces that can be opened — a departure from "derive before sample" that holds because a
  trace list is an index and not a metric; the counters and both rollups are untouched.
  Every column merges with `max`, `min`, `anyLast` or set union and none with `sum`, so a row
  written twice is the same answer twice — which is why it needed neither `insertdedup`'s token
  nor a watermark of its own, only the existing `span_written_offsets` ordering.
  **Two things it did not fix, deliberately.** The arbitrary-attribute filter still groups the
  window, because attribute paths are unbounded and no per-trace row can carry them — it is the
  only filter that still pays what everyone paid before. And a trace that gains a span after the
  sampler has forgotten it gets a second summary row: 962 of 2.73M here, merged on the page by
  `max`, so that trace's count on the screen is the more complete of the two rather than the sum.
  **Nothing else in the API had this shape**: every other `GROUP BY` in `apps/api/src` keys on a
  service, endpoint, host, query hash or time bucket, all bounded by what a customer has rather
  than by how much traffic they send.

## 7d. Follow-ups surfaced while building M2

Mostly from a research pass over M2's choices after the fact — checking what we built against
the reference implementations rather than only against our own reasoning.

- ~~**Probabilistic sampling uses `fnv64a(trace_id) % 100`.**~~ **Done.** Decides on W3C
  randomness now — the last 7 bytes of the trace id, or an explicit `rv` from `tracestate` when a
  producer upstream has already fixed it — compared against a rejection threshold, matching OTel's
  `pkg/sampling` arithmetic exactly. Measured keep rate 10.10% at 10%.
  **A correction to what this entry originally claimed:** OTel's tracestate path sits behind an
  alpha feature gate that is *off by default*, so upstream does not do this today either. The
  direction was right, the justification overstated.
  It also surfaced a real defect. The fallback for ids without usable randomness was FNV-1a, as
  OTel's legacy path uses — and a threshold comparison reads the *high* bits, which FNV leaves
  clustered for short structured input. 200 sequential ids at 10% kept **zero**. Now SHA-256
  truncated to the same 56-bit range: 9.81%.
- ~~**Confirm the slow-trace threshold measures the trace, not the root span.**~~ **Already
  correct** — `traceFacts` tracks earliest start and latest end across every span held, which is
  OTel's latency policy. No behaviour changed; a regression test now fails if anyone reduces it to
  the root span's duration.
- ~~**Masking has no `allowed_values` escape hatch.**~~ **Done**, following the reference's
  precedence: an allowlisted value survives every rule above it.
- ~~**Masking replaces rather than hashes.**~~ **Done.** A rule can now produce a stable pseudonym
  instead of a constant, so *"the same user across these requests"* stays answerable without saying
  who. HMAC-SHA-256, keyed — an unkeyed digest of an email or an account id is recovered from a
  candidate list, which is why PCI DSS v4 stopped accepting plain hashes of card numbers.
  **The key is derived per project**, not per deployment: one shared key would give a person the
  same pseudonym in every tenant's data, so anyone able to read two tenants could join them on it —
  the tenant-isolation invariant leaking out through a privacy feature. What that costs is
  correlating one person across a customer's own projects, and a project is an environment
  (ADR 0042), so it is correlating between staging and production. Nobody needs that.
  **Still true and worth saying in customer-facing copy:** a pseudonym is stable and joinable, so
  under GDPR this is pseudonymised personal data — still personal data. ADR 0059's refusal to claim
  compliance stands.
- ~~**M3 note: the redaction processor supports traces only.**~~ **Out of date, and corrected.**
  Upstream now reports beta for traces and alpha for logs and metrics, and documents appending audit
  attributes into a map body — so it does reach log bodies. Our own stage covers all three signals as
  of M3, and its default rule set is *more* protective than upstream's, which ships no default
  blocked values at all.
- ~~**`retryTransient` around Kafka calls duplicates franz-go's own retries.**~~ **Withdrawn —
  the evidence contradicts it.** franz-go does retry (`RequestRetries`, default 20, 250ms–2.5s),
  but before our wrapper existed the pipeline *died* on `commit offsets: unable to dial: lookup
  kafka: no such host` during a twenty-second outage, which those retries did not absorb. It covers
  a case theirs does not. The code now says so with the error quoted, so nobody deletes it as
  redundant.
- ~~**`loadgen`'s `ObserveFailure()` discards the span count.**~~ **Done.** A request now has three
  honest outcomes rather than two. *Not sent* is claimed only where it is provable — a dial failure,
  where no socket existed and nothing can have been published. Everything else an unreadable answer
  could mean defaults to *unknown*, because Go cannot tell a request the receiver never read from
  one it read and acted on, and over-reporting unknown widens a stated tolerance while
  under-reporting it hides an overcount.
  Offered spans are never lost again: `accepted + refused + unknown == offered`, with not-sent
  outside that sum. Latency percentiles cover only answered requests, so a chaos run's p99 is
  finally comparable to a healthy one's.
  The chaos test now uses it to **bound** the overcount rather than merely permit it. With
  `spans unknown` at zero, none of an excess came from the harness — the +512 that burned five runs
  becomes impossible to mistake for a defect again.
  **One caution that survives:** zero unknown does not license asserting exact equality. The
  receiver can still publish part of a request whose produce retries then exhaust, answer 503, and
  have the client republish what landed. Narrowed, not closed.
- ~~**One residual counter hole (ADR 0061).**~~ **Closed.** The counter insert no longer retries
  blind: every attempt after the first asks whether the write already landed before re-sending it.
  A ClickHouse block is atomic, so one point answers for the batch.
- **A pre-existing masking hazard, found while adding the allowlist.** `projectconfig` fell back to
  the defaults whenever a project's masking document failed to parse, on a comment claiming the
  defaults mask more. They do not: the defaults name no keys at all, so a rejected document
  silently stopped masking every custom key a project had asked for — the opposite of the stated
  intent. It now keeps the last good configuration for that project and only reaches the defaults
  when nothing has ever parsed.

## 8. Open scope questions

- **Network monitoring (category 10)** has no home in any phase. It needs eBPF kernel agents, which contradicts the standing decision not to build collection agents. Park it explicitly, or accept the contradiction deliberately?
- **SIEM / security (category 11)** is marked "evaluate later" in the vision doc and appears in no plan. Same call needed.

## 9. Scoping note — the four settings screens. **Decided 2026-08-31: Phase 1 is single-operator.**

### What a Phase 1 customer cannot do, by decision

This is stated scope, not a gap waiting to be discovered. A Phase 1 organization is **one person
running one project**, and the product should read that way rather than implying the missing pieces
are coming:

- **One operator per organization.** Phase 1 is deliberately single-operator. `/settings/members`
  does not ship, there are no invitations, and there is no way to add a second person. A team of
  five is five organizations with five isolated data sets.
- **One project per organization.** Signup mints exactly one, `production`, and nothing else can
  create another. `/settings/project` does not ship; there is no second environment for staging.
- **Retention is read-only.** `/settings/data` does not ship. The plan's ceiling is visible on
  `GET /usage`; the number itself cannot be changed in product. See *How retention actually
  behaves* below for why a writable control is a harder decision than it looks.
- **Only `/settings/api-keys` ships.** It is the one gap of the four that is a security gap rather
  than a convenience one: without it a customer with a leaked key must choose between stopping
  their own ingest and leaving the leak live.

Two of the three now have a home in **M8**, which is where the product becomes sellable rather
than merely working: `/settings/project` returns as P1-76, because one project per organization stops
being a scoping choice the moment someone pays, and the account surface §9 never covered — profile,
password reset, email verification, billing — is the bulk of that milestone. **`/settings/members`
does not return**, for the reason below: a role picker in front of enforcement that arrives in
Phase 2 is a promise the product would not keep. The survey that follows is the evidence the decision was taken from — what the code
actually serves, read on 2026-08-30 and re-checked on 2026-08-31.

**The whole of what the control plane can change today.** Every other endpoint is a read.

`POST /signup` · `POST /auth/login` · `POST /auth/logout` · `POST /api-keys/{id}/revoke` ·
`POST|PATCH|DELETE /checks` · `POST|DELETE /integrations` · `POST /deploys` and its webhook-secret
create/rotate/delete.

And one fact that shapes all four screens: **`signup/service.py` is the only code in the
application that creates a `User`, a `Project` or an `ApiKey` row.** Everything else is a hand-written
`INSERT`.

### `/settings/project`

**Served today:** `GET /projects` — the org's projects, for the top-bar selector. That is all.

**Not served at all:** create a project, rename one, change its slug, soft-delete one, or write
`retention_days`, `sampling_config` or `masking_config`.

**What a customer cannot do:** *create a second project.* Signup mints exactly one, `production`.
The model's own docstring says "a project is an environment — production, staging", and staging has
nowhere to go. They also cannot tune sampling: `apps/workers/internal/pipeline/sample/policy.go`
layers `projects.sampling_config` over the deployment default on every refresh, and nothing in the
API ever writes that column — so changing a sampling rate is a SQL `UPDATE` by us.

**Decisions it forces:** is more than one project in Phase 1 at all, or is one-per-org the Phase 1
shape · who may create, rename or delete a project · what deleting one does to telemetry already in
ClickHouse (`projects.deleted_at` exists; nothing drops partitions) · whether sampling is a customer
control or a support action · same question for masking, which is irreversible on data already
written.

**Size:** medium-to-large, and **needs API work** — create, patch, soft-delete, plus separate writes
for the two config documents if those are in scope. The largest of the four if they are.

### `/settings/api-keys`

**Served today:** `GET /api-keys?project_id` — id, name, `key_hint` (scheme plus four characters),
created, last used, revoked. `POST /api-keys/{id}/revoke` — owner or admin only, one-way by design,
and audited in the same transaction.

**Not served at all: creating a key.** There is no `POST /api-keys`. Keys exist only because signup
minted one.

**What a customer cannot do:** *replace a leaked key.* Revocation works and is correctly
irreversible — the module says so, and un-revoking would return a credential someone else holds to
service. But the replacement for a revoked key is a new key, and there is no way to make one. So a
customer with a leaked key chooses between stopping their own ingest and leaving the leak live,
until we run an `INSERT` for them. This is the sharpest gap of the four, and it is a security gap
rather than a convenience one.

**Decisions it forces:** who may create a key — the same owner/admin gate as revoke, or anyone ·
whether the secret is shown once (what the schema assumes: only a hash and a prefix are stored) or
re-revealable, which would need a schema change · whether "rotate" is its own action with an overlap
window, or just create-then-revoke by hand · whether live keys per project are capped · whether
revoking the last live key needs a confirmation, given that it stops ingest and the service
deliberately allows it.

**Size:** small screen, **one endpoint** (`POST /api-keys`) — more if rotation-with-overlap is
wanted. Cannot ship screen-only.

### `/settings/members`

**Served today:** nothing. No list, no invite, no role change, no removal. `GET /auth/me` returns the
one signed-in user and their organization.

**The schema is ready and unused:** `users.role` is an enum of `owner`/`admin`/`member`/`viewer`,
and `invited_by` and `invited_at` exist. No endpoint writes any of them except signup, for the owner.

**What a customer cannot do:** *add a second person to their organization, at all.* A five-person
team today means five organizations with five isolated data sets, which cannot see each other's
telemetry. Related: a role is read in exactly one place in the whole API — the revoke gate — and its
comment says broader enforcement is F26, in Phase 2. A members screen with a role picker would be
offering a setting that almost nothing reads.

**Decisions it forces:** whether Phase 1 is multi-user at all or deliberately single-operator ·
invitation by email (which needs an email sender this system does not have) or an admin-set password
· what each of the four roles actually gates, and whether shipping the picker before F26 enforces it
is a promise we would be breaking · whether a member may remove themselves, and what protects the
last owner · whether removing a member ends their live sessions.

**Size:** the largest by dependency rather than by screen. It needs an invite flow, probably email
delivery, and it forces the role-enforcement question a phase earlier than planned. **Not
screen-only.**

### `/settings/data`

**Served today:** `GET /usage` carries the organization's plan, including `raw_retention_days` and
`derived_retention_days` — read-only, and the *plan's ceiling* rather than the project's own number.

**Not served at all:** any write to `projects.retention_days` or `masking_config`; any export; any
"delete my data".

**How retention actually behaves today** — worth stating, because the decision turns on it.
Re-verified against the code and the migrations on 2026-08-31; the earlier wording here was right in
substance and wrong in three details, corrected below.

The Postgres view `project_retention` (migration `0005`) resolves
`LEAST(projects.retention_days, plans.raw_retention_days)` as `raw_retention_days` — a project may
ask for less than its tier grants, never more. `derived_retention_days` is taken straight from the
plan with no `LEAST`.

The worker does **not** read that view per row. `projectconfig` refreshes a whole snapshot from
Postgres on a ticker; `traceassembly`, `logwriting` and `metricwriting` each call
`Cache.Retention(projectID)` **once per Kafka message** and assign the answer to every row in it. So
a retention change reaches the stamp after at most one refresh interval, not instantly.

Of the ten TTL clauses in `deploy/clickhouse/migrations`, **eight** — `spans`, `logs`, the five
`metrics_*` tables and `span_trace_lookup` — read
`TTL toDateTime(received_at) + toIntervalDay(retention_days)`. The `toDateTime` wrap is load-bearing:
`received_at` is a `DateTime64`. The other **two**, the minute rollups `endpoint_stats_1m` and
`service_edges_1m`, differ on both halves: `TTL minute + toIntervalDay(derived_retention_days)` —
event time, not our clock, and the plan's derived number, not the project's raw one. "Every
ClickHouse table's TTL is the same expression" was false.

The retention value is also in every partition key —
`(toDate(received_at), retention_days)` on the eight, `(toYYYYMM(minute), derived_retention_days)` on
the two rollups — and `ttl_only_drop_parts` is `1` globally in
`deploy/clickhouse/config.d/memory.xml`. That is ADR 0072's design: a retention class expires as
whole parts rather than by rewriting them.

**Retention is therefore per row and fixed at the moment of writing.** This narrows what P1-68
leaves as "todo": the stamp is live, and what is deferred is regenerating the TTL for parts already
on disk.

**The consequence, and the decision taken on it: a retention change applies forward only.** It lands
on rows written after the worker's next config refresh. Shortening removes nothing already stored;
lengthening brings nothing back. A screen reading "retention: 7 days" while three-week-old spans are
still queryable is untrue unless the copy says which of the two it means — which is one reason
`/settings/data` does not ship in Phase 1.

**Why retroactive shortening is not offered.** Making it retroactive means altering the TTL of parts
already on disk, and with `ttl_only_drop_parts = 1` the next merge cycle drops those whole parts.
**That deletes stored customer telemetry.** P1-51 now exists, so there is an undo — but it is a
manual snapshot taken whenever somebody last ran `just backup`, not a continuous one, so the undo
recovers to that point and loses everything since. A control that can silently destroy a customer's
history on a mistyped number is still not a Phase 1 setting. Forward-only is the honest behaviour
and it is also the safe one.

**What a customer cannot do:** shorten retention after ingesting something they should not have,
and change the masking rules that would have prevented it. Both are SQL `UPDATE`s today.

**Decisions it forces:** whether a
project may ask for less than its plan grants, which the view already supports · whether masking is
editable in product at all, given that it is irreversible on data already written and that a
malformed document has already once silently stopped masking a project's custom keys (§7) · whether
there is a "delete everything in this project", and who may press it · whether the audit trail —
which already records key creation and revocation, sessions, integrations, checks and deploys — is
surfaced here, since **no endpoint reads it anywhere in the product**.

**Size:** the screen is small; the honest version needs `PATCH /projects/{id}` and settled copy.
Retroactive retention is P1-68 and is not small.

### Answered 2026-08-31

1. **Does Phase 1 ship any of these four?** Only `/settings/api-keys`, which is what closes the
   leaked-key gap. `/settings/project`, `/settings/members` and `/settings/data` do not ship.
2. **Is Phase 1 single-operator?** Yes, deliberately. One operator per organization, no invitations,
   one project per organization.
3. **Is a retention change forward-only, or does P1-68 come forward?** Forward-only, and retroactive
   shortening is not offered at all — see *Why retroactive shortening is not offered* above. P1-68
   does not come forward.

### Still open

4. Are sampling and masking customer controls, or support actions? Both are SQL `UPDATE`s by us
   today. M8's P1-76 brings the screen that would host them, so this stops being theoretical when
   that row starts — but the two are not the same question. Sampling is reversible and only changes
   what is kept next; masking is irreversible on data already written, and a malformed document has
   already once silently stopped masking a project's custom keys (§7d). They can be answered
   differently.

*Update log: plan created 2026-08-25. Scope decisions folded in 2026-08-25 (D9–D12; tasks P1-55 → P1-58). Next action: P1-01 → P1-09 (M0).*
