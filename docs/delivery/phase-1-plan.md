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
| D12 | **Cold tier built in Phase 1** (P1-57) | Raw TTL is 30 days; without tiering the data is simply deleted. Interim: raise raw TTL on the dev instance until P1-57 lands, so nothing is lost before the tier exists |

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
| P1-06 | ADR process | `docs/architecture/decisions/` format; backfill from the decisions log | todo |
| P1-07 | **Normalized envelope** | `packages/contracts` per `docs/architecture/ingestion-envelope.md` — protobuf envelope wrapping untouched OTLP bytes; `buf` codegen Go + Python; `buf breaking` in CI; 8 golden fixtures | todo |
| P1-59 | **Control-plane schema** | Postgres tables per `docs/architecture/tenancy-and-data-model.md`; Alembic migrations; org/user/project/api_key seed for local dev | todo |
| P1-08 | Design tokens | `packages/ui` OKLCH token scales per `docs/design/design-system.md`; validate under APCA + colourblind simulation; lint gate banning default Tailwind palette names | todo |
| P1-09 | Compose baseline | Kafka (KRaft), ClickHouse (JSON-type capable version, pinned), Postgres, Redis, SeaweedFS with volumes + healthchecks | todo |
| P1-10 | Migrations | golang-migrate for ClickHouse + Alembic for Postgres; both reversible | todo |
| P1-63 | App shell & routes | Vite + React + TanStack Router; global layout; every Phase 1 route rendering a `ComingSoon` template naming its delivering task; `/api/v1/` from the first route | todo |
| P1-64 | Config, validation & health | Typed config validated at boot per service; `/healthz` (no dependency checks) and `/readyz` (with them) | todo |
| P1-65 | Backend cross-cutting | RFC 9457 problem+json with correlation ids; API rate limiting distinct from ingest; pool sizes, session-per-request, Redis sessions | todo |
| P1-66 | Logging & UI handling | One JSON log contract across three languages; React 19 root handlers, boundaries, one query-error mapper | todo |
| P1-67 | Documentation reconciliation | Fifteen corrections applied with their owning task | todo |

### M1 — Walking skeleton
*Exit: demo app emits a span; it is visible in the browser as a trace waterfall; the whole path runs from `docker compose up`, and one e2e test proves it end to end in CI.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-11 | Demo app | `tools/demo-app` — fake shop emitting real traces, with fault injection ("break checkout DB now") | todo |
| P1-12 | Receiver: OTLP/HTTP | `/v1/traces`, protobuf parse via official OTel libs, **split batches one envelope per trace_id**, produce to Kafka keyed by trace_id | todo |
| P1-13 | API keys + tenancy | SHA-256 key hash (never argon2 — hot path), lookup by prefix, single cached lookup resolving org_id + project_id; every envelope tagged at the edge | todo |
| P1-14 | Rate limiting | per-key Redis counters; reject over-limit before any cost | todo |
| P1-15 | Trace assembler | `franz-go` consumer, partition by trace_id, in-memory join, batched insert via `ch-go` | todo |
| P1-16 | Query API | FastAPI `GET /traces`, `/traces/{id}` through a repository that cannot omit the tenant filter | todo |
| P1-17 | Web shell | routing, layout, session auth, trace list + waterfall screen | todo |
| P1-60 | E2E harness | Playwright against the running compose stack in CI; first critical path: signup → org/project/key created → first span → trace visible | todo |
| P1-18 | Load generator | `tools/loadgen` wrapping `telemetrygen` (OTLP/HTTP + gRPC, no app needed); first performance numbers recorded in this doc | todo |

### M2 — Spine hardening (the part that makes it a product, not a demo)
*Exit: the five core invariants in §5 each have a test that fails loudly when broken.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-19 | OTLP/gRPC | port 4317 alongside 4318 | todo |
| P1-20 | No-loss semantics | ack only after broker accept; idempotent consumers; dead-letter queue | todo |
| P1-21 | Tail sampling | 100% error + slow traces, 10% healthy; per-tenant tunable | todo |
| P1-22 | Exact counters | metrics derived from **all** traces before sampling → `metrics` table | todo |
| P1-23 | Materialized view | `endpoint_stats_1m` (Phase 2 anomaly-detection hook) | todo |
| P1-24 | PII masking | masking step in workers; rules configurable in Postgres | todo |
| P1-25 | Tenant isolation suite | tests that actively try to leak across tenants and must fail to | todo |
| P1-26 | Self-instrumentation | every service exports OTel to our own receiver — we are customer zero | todo |
| P1-27 | Chaos test | kill broker and ClickHouse mid-load, restart, assert zero loss | todo |

### M3 — Widen door 1 (logs + metrics + errors)
*Exit: all three signal shapes flow through the same pipeline and are queryable together.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-28 | Logs pipeline | `/v1/logs` → log-writer → `logs` table with tokenbf text index | todo |
| P1-29 | Metrics pipeline | `/v1/metrics` → `metrics` table | todo |
| P1-30 | Error extraction | `exception_type` / `exception_message` promoted to columns; grouping view | todo |
| P1-31 | Log↔trace correlation | "logs for this trace" query path both directions | todo |
| P1-32 | Log pattern grouping | Drain-style templating → `pattern_id` on logs *(optional — drop if it delays M4)* | todo |
| P1-55 | Service dependency edges | Go worker derives caller→callee edges (call rate, error rate, latency) from cross-service parent/child spans → small ClickHouse table. No new collection | todo |

### M4 — Doors 2 and 4 (our probes, webhooks, pollers)
*Exit: data arrives from sources the customer installed nothing for.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-33 | Celery + our own Beat scheduler | `Scheduler` subclass reading check definitions from Postgres (UI edits config, no redeploy); Redis broker. No community DB-scheduler dependency | todo |
| P1-34 | Uptime + SSL checks | HTTP/latency-breakdown + certificate expiry → normalized metrics → Kafka | todo |
| P1-35 | Browser flow checks | Playwright on an isolated queue (low concurrency, bigger containers) | todo |
| P1-36 | Deploy webhooks | GitHub/CI → `deploy_events` in Postgres (Phase 2 correlation hook) | todo |
| P1-37 | Cloud + DB pollers | CloudWatch metrics; `pg_stat_statements` read-only; credentials encrypted at rest | todo |

### M5 — Door 3 (browser / RUM)
*Exit: real user data from a script tag, correlated to backend traces.*

| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-38 | Public site keys + CORS | non-secret per-site key path on the receiver | todo |
| P1-39 | Browser SDK ingestion | OTel browser SDK → spans/metrics; Core Web Vitals (LCP/INP/CLS) | todo |
| P1-58 | Source maps | upload + store source maps per release; symbolicate JS stack traces. Without this, RUM errors are minified noise | todo |

### M6 — Dashboard UI surface
*Exit: every signal we collect has a competent screen, each covered by an e2e critical path, and the whole set survives the OTel Demo realism gate. This is the credibility layer, built once, not polished forever.*

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
| P1-61 | E2E screen coverage | Critical path per screen against the running stack; runs in CI on every PR | todo |
| P1-62 | **OTel Demo realism gate** | Run the OpenTelemetry Demo (18 services, ~10 languages) against our receiver and confirm the screens hold: service map readable at 18 nodes, trace search survives real attribute cardinality, promoted columns survive semconv variation across languages. Not a daily dependency — needs 4–8 GB, so optional services stop and the Docker allocation is raised | todo |
| P1-49 | Onboarding | signup collects org details + one email → transaction creates org, owner user, `production` project and first key → two env vars → live first-span celebration. User never sees the words org or project | todo |

### M7 — Phase 1 exit
| ID | Task | Deliverable | Status |
|---|---|---|---|
| P1-50 | Load + soak | 24h soak against the budgets in §4; results recorded here | todo |
| P1-57 | Object storage cold tier | **SeaweedFS** in compose for local dev; **AWS S3 in deployment**; ClickHouse-native S3 tiering past the hot window + tested restore. Generic S3 API only | todo |
| P1-51 | Backup/restore | runbook + tested restore for ClickHouse and Postgres volumes | todo |
| P1-52 | Ingest kill switch | per-tenant stop valve for runaway ingestion | todo |
| P1-53 | Security pass | key hashing, TLS at the edge, secret handling, dependency audit | todo |
| P1-54 | Customer docs | setup guide for both onboarding patterns (SDK direct, own collector) | todo |

---

## 4. Performance budgets (measured by `just load`, not estimated)

**Target machine: Apple M4 Pro, 24 GB, full stack co-resident in Docker** (see `dev-environment.md`). On one laptop these are a **regression gate, not a capacity model** — production numbers are re-measured when a production host exists.

| Budget | Target | Measured |
|---|---|---|
| Receiver throughput | ≥ 10,000 spans/sec sustained, single instance | — |
| Receiver accept latency | p99 < 50 ms | — |
| Trace-split re-encode overhead | < 20% of receiver CPU | — |
| Receiver memory | < 512 MB under sustained load | — |
| Span → queryable lag | p99 < 15 s | — |
| Trace-by-ID query | p95 < 500 ms | — |
| Endpoint stats, 24h window | p95 < 300 ms | — |
| Log search, 1h window | p95 < 2 s | — |
| First screen load | < 2 s (PRD AC1.1) | — |
| Data loss under broker restart | zero | — |

Numbers are provisional until M1 gives a baseline on the M4 Pro; revise once, then hold. Disk in dev is bounded by a 3-day ClickHouse TTL rather than the 30-day production setting.

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

## 8. Open scope questions

- **Network monitoring (category 10)** has no home in any phase. It needs eBPF kernel agents, which contradicts the standing decision not to build collection agents. Park it explicitly, or accept the contradiction deliberately?
- **SIEM / security (category 11)** is marked "evaluate later" in the vision doc and appears in no plan. Same call needed.

*Update log: plan created 2026-08-25. Scope decisions folded in 2026-08-25 (D9–D12; tasks P1-55 → P1-58). Next action: P1-01 → P1-09 (M0).*
