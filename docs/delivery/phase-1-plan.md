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
├─ tools/               loadgen, demo-app
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
| P1-11 | Demo app | `tools/demo-app` — three roles calling each other over HTTP, so traces cross real service boundaries; OTel configured only by standard `OTEL_*` vars; four named faults toggled at runtime | ✅ done |
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
| P1-30 | Error extraction | `exception_type` / `exception_message` promoted to columns; grouping view | **done** — `exception_type`/`exception_message` are columns on `spans` and `logs`; `apps/api/.../errorgroups` serves the grouping, covered by `test_errorgroups.py` |
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

### M5 — Door 3 (browser / RUM)
*Exit: real user data from a script tag, correlated to backend traces.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-38 | Public site keys + CORS | non-secret per-site key path on the receiver | todo |
| P1-39 | Browser SDK ingestion | OTel browser SDK → spans/metrics; Core Web Vitals (LCP/INP/CLS) | todo |
| P1-58 | Source maps | upload + store source maps per release; symbolicate JS stack traces. Without this, RUM errors are minified noise | todo |

### M6 — Dashboard UI surface
*Exit: every signal we collect has a competent screen, each covered by an e2e critical path, and the whole set survives the OTel Demo realism gate. This is the credibility layer, built once, not polished forever.*

**Every screen task below replaces an existing `ComingSoon` placeholder** — the route, the layout and the navigation entry already exist from P1-63. None of them creates a route, and none rebuilds the shell.

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-40 | Service overview | request rate, error rate, p95 trend; endpoints ranked by slowness/errors | todo |
| P1-41 | Trace explorer | search + filters over the M1 waterfall | todo |
| P1-56 | Service map | dependency graph from P1-55 edges; edge call rate/error/latency; click-through to a service | todo |
| P1-42 | Errors screen | grouped exceptions, counts, first/last seen, stack traces | todo |
| P1-43 | Logs screen | search, filters, **live tail** via WebSocket ← Kafka/Redis pub-sub | todo |
| P1-44 | Infrastructure screen | hosts/containers: CPU, memory, disk, network. K8s metrics are ingested and queryable, but pod/node/namespace views are Phase 2 | todo |
| P1-45 | Synthetics screen | check list, status, latency trends, failure detail with screenshot | todo |
| P1-46 | Database screen | slow queries, mean time, calls, connection health | todo |
| P1-47 | RUM screen | Core Web Vitals by page/geo/device; JS errors | todo |
| P1-48 | Usage & cost view | per-tenant ingestion counters → usage table → projected cost in-product | todo |
| P1-61 | E2E screen coverage | Critical path per screen against the running stack; part of `just check` | todo |
| P1-62 | **OTel Demo realism gate** | Run the OpenTelemetry Demo (18 services, ~10 languages) against our receiver and confirm the screens hold: service map readable at 18 nodes, trace search survives real attribute cardinality, promoted columns survive semconv variation across languages. Not a daily dependency — needs 4–8 GB, so optional services stop and the Docker allocation is raised | todo |

### M7 — Phase 1 exit
| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-50 | Load + soak | 24h soak against the budgets in §4; results recorded here | todo |
| P1-57 | Object storage cold tier | **SeaweedFS** in compose for local dev; **AWS S3 in deployment**; ClickHouse-native S3 tiering past the hot window + tested restore. Generic S3 API only | todo |
| P1-51 | Backup/restore | runbook + tested restore for ClickHouse and Postgres volumes | todo |
| P1-68 | Apply plan retention | `plans` table holding the tier days from [ADR 0071](../architecture/decisions/0071-plan-tiers-and-retention.md); a job regenerating the conditional TTL expression per ADR 0038; a downgrade path that warns before narrowing retention, because shortening a TTL drops the parts with no undo. Deferred deliberately — nothing about it is a one-way door, and until it exists every tenant gets the fixed `pro`-shaped retention, which over-serves rather than under-serves. `scale` additionally needs P1-57 | todo |
| P1-52 | Ingest kill switch | per-tenant stop valve for runaway ingestion | todo |
| P1-53 | Security pass | key hashing, TLS at the edge, secret handling, dependency audit | todo |
| P1-54 | Customer docs | setup guide for both onboarding patterns (SDK direct, own collector) | todo |

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
| Our demo app is too tidy to be realistic | It is 2–3 services we control; real customers send messy polyglot telemetry. The OTel Demo gate (P1-62) is the counterweight before M6 is called done |

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
- **`last_used_at` on `api_keys` is never written.** Updating it would be a database write per ingest request on the hot path. It needs a cheaper shape — sampled, batched, or derived from usage counters — before anything can show a customer when a key was last seen.
- **The receiver's rate limiter fails open** when Valkey is unreachable, on the reasoning that losing telemetry costs more than briefly losing the limit, with body caps and the broker still bounding damage. Deliberate, and worth revisiting once a paying tenant can be harmed by it.
- **The receiver exposes no profile endpoint**, which is what blocks the trace-split re-encode budget (§4) — permanently, not just today. `net/http/pprof` on the existing admin listener would settle it, but exposing profiles needs a deliberate decision about who can reach that port.
- **`resolveWindow` rounded the live window's end *down* to a bucket**, so the trace list silently omitted the newest 0–10 seconds, and `TraceListScreen` then froze that window at mount. Together they broke M1's exit condition at the last hop: onboarding said the first span had arrived, and the list it linked to was empty. Fixed in M1; noted because the shape recurs — a cache-stability trick that quietly changes what the query means.
- **The trace list groups over `spans` on every request.** Correct and cheap at M1 volume because the time window is bounded by the sort key, but it is a full group-by, and P1-41 (trace explorer, M6) is where it needs a real look.

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

*Update log: plan created 2026-08-25. Scope decisions folded in 2026-08-25 (D9–D12; tasks P1-55 → P1-58). Next action: P1-01 → P1-09 (M0).*
