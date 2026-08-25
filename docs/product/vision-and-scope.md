# Product Vision & Scope

> **Owner:** Raj
> **Status:** Ideation / scoping. No code, no prototypes, no timelines yet.
> **Purpose:** What we are building, for whom, and what is in or out of scope. Update as thinking evolves.
> **Companion docs:** `../architecture/system-architecture.md` (technical design) · `requirements.md` (features) · `../research/market-analysis-2026.md` (research)
> **Last updated:** 2026-08-15

---

## Vision (one line)

An insight-first, full observability platform: it tells you **what broke and why**, instead of showing you 50 dashboards.

---

## 1. Users & Buyers — FOCUS: 3 users

| # | Role | Who | What they need |
|---|------|-----|----------------|
| 1 | **Primary user** | Full-stack developer at a small startup (2–15 people) | Root cause, fast. "What broke, where, why." |
| 2 | **Secondary user** | Indie hacker / solo founder | Free tier, zero-config setup, value in under 10 minutes |
| 3 | **Viewer** | Non-technical application/website owner | Plain-language status: "Is my site okay? Are customers affected?" |

> ⏸️ **Deferred:** Agency / dev-shop channel (white-label, multi-site). Revisit later.

**Core product principle:** One incident, one data source, two languages — technical view for the developer, plain-language view for the owner. The auto-translation between them is the killer feature.

---

## 2. Telemetry Signals — FOCUS: what we collect

All via **OpenTelemetry** (OTLP protocol, community SDKs — we do **not** build collection agents).

| # | Signal | What it is | Answers |
|---|--------|-----------|---------|
| 1 | **Traces** | Request journeys stitched across microservices by trace ID (spans + context propagation) | *Where* is the problem |
| 2 | **Logs** | Records from inside each service, correlated to traces via trace ID | *Why* it happened |
| 3 | **Metrics** | **Application metrics**: request rate, error rate, p95/p99 latency — emitted by SDK **or derived from traces (do that first)**. **Infrastructure metrics**: CPU, memory, disk, network — from host agent (OTel collector `hostmetrics`), Kubernetes stats, or cloud APIs | *That* something is wrong (detection) |
| 4 | **Deploy & change events** | Deploys, config changes, feature flags | Correlation ("spiked 2 min after deploy X") |

**Later signals:**
- 5. Continuous profiles — line-level CPU/memory attribution
- 6. Session replays — RUM-side signal
- 7. Synthetic check results — from our own probe bots

---

## 3. Product Categories — full platform surface

| # | Category | What it does | Reference tools |
|---|----------|--------------|-----------------|
| 1 | Synthetic monitoring | Bots run fake user flows (e.g. checkout) every minute from multiple regions; catches downtime with zero instrumentation | UptimeRobot, Pingdom, Checkly |
| 2 | Error tracking | Capture, group, and alert on code exceptions with stack traces | Sentry |
| 3 | APM | Backend performance: slow endpoints, slow DB queries, error hotspots; built on traces | Datadog APM, New Relic |
| 4 | Log management | Ingest, store, search logs; auto-correlate to traces | Splunk, Loki |
| 5 | RUM | Browser/mobile: page load (Core Web Vitals), JS errors, real user experience | Datadog RUM, Sentry |
| 6 | Infrastructure monitoring | Host/container/Kubernetes CPU, memory, disk, network | Datadog Infra, Grafana |
| 7 | Session replay | Video-like playback of user sessions, linked to errors | FullStory, LogRocket |
| 8 | Database monitoring | Query performance, slow query analysis, connection pool health | Datadog DBM, pganalyze |
| 9 | Continuous profiling | Line-level CPU/memory flame graphs | Pyroscope |
| 10 | Network monitoring | Service-to-service flows, DNS, latency maps | Datadog NPM |
| 11 | SIEM / security | Threat detection, audit trails — *evaluate later, may stay out of scope* | Splunk ES |

---

## 3b. How Data Gets In — the four "data doors"

**Important:** OTel is the backbone, but NOT every category collects via OTel.

| Door | Method | Used by |
|------|--------|---------|
| 1. **OTel ingestion (OTLP)** | Customer's services instrumented with OTel SDKs; OTel collector for host metrics & log files | APM, error tracking, log management, app metrics, infra metrics (agent path) |
| 2. **Our own probes** | We generate the data — bots hitting the site from multiple regions. Customer installs nothing | Synthetic monitoring |
| 3. **Browser snippet** | Lightweight JS script tag in the customer's site (OTel browser SDK is maturing; replay needs a custom DOM recorder) | RUM, session replay |
| 4. **Pull integrations (APIs/webhooks)** | We connect out or receive webhooks: cloud APIs (CloudWatch/GCP) for infra metrics, direct DB connections (pg_stat_statements) for DB health, GitHub/CI webhooks for deploy events, cloud audit logs for SIEM | Infra metrics (cloud path), database monitoring, deploy events, SIEM |

Special cases:
- **Continuous profiling** — new official OTel signal, support still early; historically own agents (Pyroscope-style).
- **Network monitoring** — eBPF kernel-level agents, not OTel.
- **Database monitoring** — two layers: query spans via OTel traces (door 1) + deep DB stats via direct connection (door 4).

---

## 4. Platform Features (Raj's feature list)

| # | Feature | Notes |
|---|---------|-------|
| 1 | **Anomaly detection** | Automatic detection of unusual patterns in metrics/traces — no manual threshold setup. This is the trigger for everything below. |
| 2 | **Alerts & notifications — multiple connectors** | Teams, WhatsApp, Slack, email, SMS, webhooks, etc. Smart grouping to avoid alert fatigue. |
| 3 | **Case management** | When an error/issue happens → automatically create a case/incident. Case dashboard with lifecycle (open → investigating → resolved). Connectors: GitHub Issues, Jira, etc. — push cases to the tools teams already use. |
| 4 | **AI chatbot (with RAG)** | Chat with your system: "why was checkout slow yesterday?" RAG over the customer's own telemetry, past incidents, and docs — answers grounded in *their* data, not generic. |
| 5 | **AI insights** | Proactive plain-language findings: "Latency on /checkout jumped 4x at 14:32, correlated with deploy a41f2c9 and DB connection errors." Cross-signal correlation (metric → trace → log). |
| 6 | **AI solution** | Beyond diagnosis → suggested fix: "Probable cause: unindexed query in deploy a41f2c9. Suggested fix: add index on orders.user_id." Possibly draft the PR via GitHub connector. |

**How the features chain together (the incident pipeline):**

Anomaly detection → AI insight (what + why) → Case created automatically → Alert sent via connectors → AI solution suggested → Developer chats with AI bot for more detail → Case closed, learned for future RAG.

This pipeline IS the product's differentiation — competitors have pieces, not the chain.

---

## 5. Business / Pricing

> ⏸️ **Deferred — think later.** Direction noted so far: per-site/per-service pricing (predictable), not per-GB (the anti-Datadog). Free tier for indie devs. SaaS platform.

---

## 5b. Processing Pipeline (what happens after a span arrives)

1. **Ingestion gateway** — API-key auth, per-key rate limits, OTLP validation. Tags every span with customer/project ID (multi-tenant from the first millisecond).
2. **Buffer queue** (Kafka-style) — absorbs traffic spikes so nothing is lost; lets processors restart/upgrade safely.
3. **Processing workers** — trace assembly (buffer spans, join by trace ID until the trace is complete), enrichment (deploy version, geo, service metadata), **tail sampling** (whole trace visible → keep 100% of error/slow traces, ~1–10% of boring ones — the single biggest storage-cost lever).
4. **Storage** — ClickHouse-style columnar DB for recent data (fast queries, ~10x compression); object storage (S3-style) for cheap long retention.
5. **Derived metrics + anomaly detection** — compute request rate / error rate / p95 per endpoint from traces in real time; detectors continuously compare against learned baselines. Detection runs on cheap aggregates, never raw spans.
6. **Insight engine** — when a detector fires: pull relevant traces + logs + deploy events → probable cause → plain-language finding → feeds the incident pipeline (case → alerts → AI solution → chatbot RAG).

Key design points:
- Fork after processing: full traces go DOWN to storage (for investigation); derived metrics go SIDEWAYS to watchers (continuous, cheap).
- Stages 1–4 have strong open-source building blocks (OTel collector, Kafka, ClickHouse). **Our unique value = stages 5–6.**

---

## 6. Decisions Log

| Date | Decision |
|------|----------|
| 2026-08 | Insight-first, not dashboard-first |
| 2026-08 | Build on OpenTelemetry; do not build collection SDKs/agents |
| 2026-08 | Derive application metrics from traces first (simpler setup) |
| 2026-08 | Two-view design: developer view + plain-language owner view |
| 2026-08 | Agency channel and pricing model deferred |
| 2026-08 | No code / prototype yet — focus on understanding & scoping |
| 2026-08 | OTel setup: customer owns instrumentation; we own the receiving side (OTLP endpoint + API key + setup docs). Two customer patterns: (A) SDK direct-to-endpoint via 2 env vars — the onboarding priority ("value in 10 minutes"); (B) SDK → customer's own collector → our endpoint (for batching, data scrubbing, host metrics). Same ingestion pipeline serves both. |

---

## 7. Open Questions (update as they come)

*(Resolved — recommendations accepted:)*
- ✅ First slice of categories: **synthetic + error tracking + APM basics** (synthetic = zero-instrumentation value; error tracking = most-loved dev feature; APM nearly free once traces flow)
- ✅ Case management: **thin layer first** — cases as Postgres rows + simple list/detail UI; connectors push to GitHub Issues/Jira. Our value = auto-creating the case with the finding attached, not lifecycle management.
- ✅ RAG chatbot index: **knowledge, not telemetry** — embed resolved findings + resolution notes, customer runbooks/docs, service descriptions. Raw telemetry queried LIVE via tool-calling against ClickHouse. RAG for knowledge, tools for data.
- ✅ Anomaly detection: statistical first (seasonality-aware baselines, sustained deviation) — see architecture doc decisions.
- ✅ Onboarding: Pattern A env vars → live first-span celebration → then AUTO-CREATE everything (default service overview, suggested synthetic check from their domain, alert presets). Zero required config before first value.
- ✅ Ingestion protection: per-key rate limits in receiver (architecture doc).

*(Still open:)*
- What exact metrics define "value in 10 minutes" success during onboarding tests?

---

## 8. Parking Lot (ideas not yet placed)

- White-label owner status page (from agency discussion)
- Deriving business metrics (orders/min) for e-commerce owners
- Status page hosting (public status.mysite.com)
- **Sustainability / carbon monitoring (GreenOps):** show energy use + estimated CO₂ footprint of customer infrastructure. Technical: derived, not measured — instance type + CPU utilization → estimated energy → × grid carbon intensity of the cloud region (public datasets; reference: Cloud Carbon Footprint project, CNCF Kepler for per-container energy on K8s). Piggybacks on infra-monitoring data our door-4b pollers already fetch — no new doors needed. Fits our "translate technical → plain language" spirit; carbon waste ≈ cost waste (idle oversized instances), so it doubles as a cost-saving view. Differentiator candidate, not core wedge — demand today mostly from ESG-reporting companies.

*(Add new ideas here so nothing is lost.)*
