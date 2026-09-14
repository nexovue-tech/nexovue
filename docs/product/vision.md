# Product Vision & Scope

> **Owner:** Raj
> **Status:** Product intent. Written during scoping and kept as the statement of *what we mean to build* — it is not a status report. The source-derived `README.md` and `../architecture/` documents are the authority on what exists.
> **Purpose:** What we are building, for whom, and what is in or out of scope. Update as thinking evolves.
> **Companion docs:** `../architecture/system.md` (current architecture) · `feature-catalog.md` (features) · `../research/market-analysis-2026.md` (research)
> **Last updated:** 2026-09-14 — phases recorded (§0 below), door 3's state corrected, and the deployment shape decided.
> **Where the status lives:** this file is intent. [`feature-catalog.md`](feature-catalog.md) §4 is the one table saying what is built, what is specified, and which phase the rest belongs to.
>
> **How to read this file.** Everything here is intent unless it says otherwise. Nothing in it is evidence that a thing is built. Where a decision has since been implemented, the section says so and names the migration.

---

## Vision (one line)

An insight-first, full observability platform: it tells you **what broke and why**, instead of handing you an empty builder and fifty dashboards to maintain. The dashboards it composes for you are first-class; the work of assembling them is what it removes.

---

## 0. Where the product is (2026-09-14)

**Phase 0 is complete.** Every signal in the plan arrives, and each has a screen good enough to
investigate with: services, service map, traces and waterfall, errors, logs with a live tail,
infrastructure, database, RUM, synthetics and metrics — twelve screens sharing one time window, one
project selector and one refresh control. Signup to first span works, with verification, recovery, a
second factor, keys, projects, usage, allowances and Razorpay checkout behind it. Fifteen pages of
setup documentation are published without a login.

**What Phase 0 deliberately did not do** is the part this vision is actually about: it produces no
finding, notifies nobody of anything, admits no second person to an organization, and runs nowhere
but a laptop. The insight layer in §4 and §5b — the chain that is the differentiation — has not been
started.

**Phase 1 is proposed and not yet approved:** install (F37), invite (F26), alert (F21). The ordering
and the reasoning are in [`feature-catalog.md`](feature-catalog.md) §5.

**Two commercial facts that shape it.** nexovue is **both SaaS and self-hosted**, and there are **no
customers and no marketing yet**. That makes self-hosted the cheap first door — it costs nothing to
run, and the work is the same work hosted needs later.

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
- 5. Continuous profiles — line-level CPU/memory attribution. *Not built.*
- 6. Session replays — RUM-side signal. *Not built.*
- 7. Synthetic check results — from our own probe bots. **Built**: door 2 publishes them as OTLP metrics and logs inside the standard envelope, so they need no table and no worker of their own.

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
| 10 | Network monitoring | Service-to-service flows, DNS, latency maps — **out of scope**: no agent of ours, accepted as OTLP if the customer runs one, no screen | Datadog NPM |
| 11 | SIEM / security | Threat detection, audit trails — **out of scope**: not re-opened as a screen, the nearest thing we build is export | Splunk ES |

---

## 3b. How Data Gets In — the four "data doors"

**Important:** OTel is the backbone, but NOT every category collects via OTel.

| Door | Method | Used by |
|------|--------|---------|
| 1. **OTel ingestion (OTLP)** | Customer's services instrumented with OTel SDKs; OTel collector for host metrics & log files | APM, error tracking, log management, app metrics, infra metrics (agent path) |
| 2. **Our own probes** | We generate the data — bots hitting the site from multiple regions. Customer installs nothing | Synthetic monitoring |
| 3. **Browser snippet** | Lightweight JS script tag in the customer's site (OTel browser SDK is maturing; replay needs a custom DOM recorder) | RUM, session replay |
| 4. **Pull integrations (APIs/webhooks)** | We connect out or receive webhooks: cloud APIs (CloudWatch/GCP) for infra metrics, direct DB connections (pg_stat_statements) for DB health, GitHub/CI webhooks for deploy events, cloud audit logs for SIEM | Infra metrics (cloud path), database monitoring, deploy events, SIEM |

**Which of these exist (2026-09-14):** door 1 accepts traces, logs and metrics over OTLP/HTTP and
OTLP/gRPC. Door 2 runs HTTP, SSL and Playwright browser checks. Door 3 is built on both sides now —
a separate browser entrance under `/browser`, gated by public site keys and an origin allowlist,
**and** the RUM screens, which were still missing when this paragraph was first written. Door 4b
polls CloudWatch and `pg_stat_statements`, and deploy webhooks are built. Session replay is neither
built nor scoped; SIEM is decided out of scope.

**One thing door 3 still cannot do: be installed.** `@nexovue/browser-sdk` is `"private": true` with
no registry and no CDN, so RUM is documented and unobtainable. The decision (D6, 2026-09-14) is a
script tag first and npm after, and both wait on there being somewhere to serve it from — which is
what makes Phase 1's first item a deployment rather than a feature.

Special cases:
- **Continuous profiling** — new official OTel signal, support still early; historically own agents (Pyroscope-style).
- **Network monitoring** — eBPF kernel-level agents, not OTel. Decided out of scope 2026-09-07: accepted as OTLP from a producer the customer runs, never collected by us.
- **Database monitoring** — two layers: query spans via OTel traces (door 1) + deep DB stats via direct connection (door 4).

---

## 4. Platform Features (Raj's feature list)

> **None of the six is built, and together they are Phase 2.** They are the chain this product is
> differentiated by, and the honest position on 2026-09-14 is that Phase 0 built everything
> *underneath* them. Catalogue entries: F6, F21, F22, F4, F3, F5 — status in
> [`feature-catalog.md`](feature-catalog.md) §4.

| # | Feature | Catalogue | Notes |
|---|---------|---|-------|
| 1 | **Anomaly detection** | F6 | Automatic detection of unusual patterns in metrics/traces — no manual threshold setup. This is the trigger for everything below. |
| 2 | **Alerts & notifications — multiple connectors** | F21 | Teams, WhatsApp, Slack, email, SMS, webhooks, etc. Smart grouping to avoid alert fatigue. |
| 3 | **Case management** | F22 | When an error/issue happens → automatically create a case/incident. Case dashboard with lifecycle (open → investigating → resolved). Connectors: GitHub Issues, Jira, etc. — push cases to the tools teams already use. |
| 4 | **AI chatbot (with RAG)** | F4 | Chat with your system: "why was checkout slow yesterday?" RAG over the customer's own telemetry, past incidents, and docs — answers grounded in *their* data, not generic. |
| 5 | **AI insights** | F3 | Proactive plain-language findings: "Latency on /checkout jumped 4x at 14:32, correlated with deploy a41f2c9 and DB connection errors." Cross-signal correlation (metric → trace → log). |
| 6 | **AI solution** | F5 | Beyond diagnosis → suggested fix: "Probable cause: unindexed query in deploy a41f2c9. Suggested fix: add index on orders.user_id." Possibly draft the PR via GitHub connector. |

**How the features chain together (the incident pipeline):**

Anomaly detection → AI insight (what + why) → Case created automatically → Alert sent via connectors → AI solution suggested → Developer chats with AI bot for more detail → Case closed, learned for future RAG.

This pipeline IS the product's differentiation — competitors have pieces, not the chain.

---

## 5. Business / Pricing

> ✅ **DECIDED 2026-08-27 — the unit is the service.**
> 💵 **PRICED 2026-09-01, provisionally.** `apps/api/migrations/versions/0015_seed_plan_prices.py`.

Price per service monitored, with a volume allowance per service and a ceiling on the service-count
component. Four promises: the bill has a ceiling · never per seat · never
per host · over the allowance we **block and say so rather than bill**.

The first prices are rows in `plan_prices`, effective 2026-09-01, priced against what the market
charges rather than against cost. A repricing is a **new row with a later `effective_from`**, never
an edit to an existing one, so a past invoice can always be recomputed from what it was billed on:

| Plan | Per service / month | Included bytes per service | Service ceiling |
|---|---|---|---|
| free | $0.00 | 2 GiB | **3 services** |
| pro | $6.00 | 10 GiB | uncapped |
| scale | $5.00 | 25 GiB | uncapped |

Enterprise deliberately has no row: a tier priced by agreement has no list price, and the usage
screen shows no cost for it, which is the truth.

At $6, ten services cost $60/month against roughly $138 for the three hosts of Datadog
infrastructure and APM that would carry them — a service is finer than a host, so the per-unit price
has to sit below a per-host price. That is the comparison the number is built to win, and the one
worth re-checking when it changes.

**Still open, and the migration says so:** the pricing decision states both that we block above the allowance
*and* that overage stays live and bills. Those are two different products. `max_billed_services`
caps the free tier at three; what happens when a **paid** tier exceeds its included volume is
undecided, and the usage screen says in words that overage is not in the figure it shows.

M1's measurements are what made this decidable — marginal cost is **$0.0017 per million spans**
against Dash0's $0.60 list, so infrastructure is a rounding error against any defensible price. The
margin worry that framed the question was wrong.

Per-GB ingested was rejected with a full proposal built and compared, not waved away. It earns more
at the top end. It loses on two things: the same traffic bills across a 10× range depending only on
how richly a customer's SDK is configured, and it **taxes instrumentation** — which starves the
Phase 2 insight layer that is the whole differentiation.

**Still open:** the Phase 2 inference cost that could still break the unit, paid-tier overage
behaviour, and payments (below).

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
| 2026-08 | Insight-first, not dashboard-first. **Superseded 2026-09-12 — see below.** |
| 2026-08 | Build on OpenTelemetry; do not build collection SDKs/agents |
| 2026-08 | Derive application metrics from traces first (simpler setup) |
| 2026-08 | Two-view design: developer view + plain-language owner view |
| 2026-08 | Agency channel deferred; pricing model settled 2026-08-27 and priced 2026-09-01 |
| 2026-09-07 | Network monitoring and SIEM are out of scope. Categories 10 and 11 had no phase since 2026-08-25. Network telemetry is accepted as OTLP from a producer the customer runs — OBI, Beyla, Hubble, Tetragon — and never collected by an agent of ours, which is what keeps the founding no-agents decision intact rather than contradicting it. SIEM is not re-opened as a screen: the data overlaps with observability by roughly 80%, the *product* is detection content and certifications, and the nearest thing we build is export. |
| 2026-08 | OTel setup: customer owns instrumentation; we own the receiving side (OTLP endpoint + API key + setup docs). Two customer patterns: (A) SDK direct-to-endpoint via 2 env vars — the onboarding priority ("value in 10 minutes"); (B) SDK → customer's own collector → our endpoint (for batching, data scrubbing, host metrics). Same ingestion pipeline serves both. |
| 2026-09-15 | **AGPL-3.0, source public — with the browser SDK deliberately excluded.** The code licence, left open the day before, is closed. The server, workers, API and console go out under **AGPL-3.0**: anyone may run and modify them, but running a *modified* version as a network service obliges publishing the changes. That clause is the whole point — it is what stops someone taking nexovue, adding a feature and selling a closed hosted competitor, which is the one commercial risk the free-self-hosted decision creates. Plain GPL would not do it: its obligation triggers on *distribution*, and a hosted service distributes nothing. **`packages/browser-sdk` must NOT be AGPL** and ships permissive (MIT or Apache-2.0) instead: it executes inside the customer's own web page, so copyleft there could be read as reaching their site's source, which would make the SDK legally unusable by the customers it exists for — and D6 is already the decision to make that SDK installable. The rule generalises: anything running on our infrastructure may be AGPL; anything running inside a customer's application may not. **Source public rather than images-only**, because every OTel-native peer is readable — SigNoz, OpenObserve, Coroot, HyperDX — and a closed binary asking for a customer's entire telemetry stream is a reason to choose a competitor that has nothing to do with features. The protection argument for staying closed guards a risk that only exists after success, at the cost of the adoption needed to get there. **Low-stakes in one specific way:** sole copyright means future versions can be relicensed at will; only copies already distributed are permanent. **Verified before deciding, not after:** the dependency audit found no incompatible licence in any ecosystem — npm 51 production packages (MIT/ISC/Apache-2.0/BSD/Unlicense), Go (MIT/BSD-3/Apache-2.0), Python (MIT/BSD-3/Apache-2.0 plus duals); `symbolic` is MIT despite Sentry's relicensing elsewhere, and `pycurl` is LGPL-2.1-**or**-MIT so the MIT branch applies. Git history holds no real secret — `.env` was never committed, only `.env.example` templates carrying `nexovue_dev` against localhost. **Still required before the repo is actually flipped public:** a content-level secret scan (`gitleaks`/`trufflehog`) over full history, because the check done here matched filenames, not file contents. |
| 2026-09-15 | **Open-core is deferred, not rejected.** The commercial layer — a paid feature alongside the AGPL core, the shape SigNoz and OpenObserve both use — is not built now, for three reasons: it forces a guess about which features customers would pay for while there are no customers to ask; it adds a second build path and licence-key machinery to a Phase 1 deliberately kept minimal; and AGPL forecloses none of it, since a future paid feature is simply new code under its own licence rather than a relicensing of anything existing. The asymmetry decides the order: adding a paid feature later is ordinary, while withdrawing a free one is what makes people leave. Ship everything free, and build the paid feature the day a customer names it. |
| 2026-09-14 | **Self-hosting is free; hosting is what is paid for.** Decided by naming the two costs and asking which is worse to bear: *some people will run nexovue for years and never pay, and we will not know their names* against *we pay a cloud bill before a customer exists*. The second is worse, so the first is accepted — deliberately, not by default. Three flows follow: free self-hosted (₹0, and no licence machinery to build); setup and support on the customer's own servers, quoted per engagement, **needing no code at all**; and hosted per service, which is what the Razorpay checkout, plans, allowances and usage metering were already built for and which is the only one that grows without consuming founder time. Support work is to be accepted when offered and never built toward. **Images are published anonymously** — no key, no signup, no phone home — because a check at install destroys the ten-minute promise the setup docs are written around, and identity can be asked for later without breaking anyone. **Still open, and separate:** the code licence, which decides whether a competitor may sell nexovue as their own hosted service. That one is hard to reverse and belongs before the first image is published. |
| 2026-09-14 | **Both SaaS and self-hosted, and self-hosted opens first.** No customers and no marketing yet, and idle cloud infrastructure is not worth paying for before there is a reason. The resolution is to separate *deployment capability* — images, migrations, a restore actually performed, a runbook — which costs nothing to build and must exist before a customer, from *a running production*, which is the bill and can wait. A developer evaluating an observability tool will run `docker compose up`; the same images serve hosted later, so nothing is wasted. **Open:** what a self-hoster pays, since the Razorpay subscription flow assumes we collect monthly. |
| 2026-09-14 | **Phases named.** Phase 0 (every signal, every screen) complete. Phase 1 proposed as install → invite → alert, in the order a self-hoster hits the wall. Phase 2 is the insight layer this document is built on. Recorded so that "what is left" has one answer instead of being reconstructed each time. |
| 2026-09-12 | **Insights and dashboards are both first-class**, superseding "insight-first, not dashboard-first" above. The front door does not change: the Findings feed is still what a person lands on. What changed is that a dashboard-shaped overview is reachable, complete, and not a lesser mode — it is composed for the customer from rollups that already exist, rather than handed over as an empty builder. The earlier stance was read, correctly, as "a dashboard is something we tolerate", and that is not a product anyone would choose over the incumbent they already have. F20's builder is not foreclosed by this: an auto-composed dashboard is a fixed instance of what a builder would produce. |

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

**Commercial model — researched 2026-08-27. The billing unit and the free-tier ceiling closed before M6 shipped; the usage and cost screen is built. Payments remain open.**

- ~~**The billing unit.**~~ **RESOLVED — per service.** For the record, §5 recorded the direction as *per-site/per-service, not per-GB*. A market scan found **no vendor doing that**: the field is per-host (Datadog $35/host, Splunk $55/host, Dynatrace ~$58/8 GiB host), per-agent (AppDynamics — the closest existing thing), per-CPU-core, per-GB ingest (New Relic $0.40, Grafana Tempo $0.45, SigNoz $0.30, Better Stack €0.10) or per-signal-count (Dash0, which moved off GB **specifically because** GB pricing explodes with cardinality). The likely reason nobody prices per-service: **our cost tracks ingest volume and storage, not service count**, so per-service decouples price from cost — good for the customer, dangerous for margin. Tail sampling only half-helps, because metrics derive *pre*-sampling, so receiver, Kafka and worker cost track raw volume regardless of what we charge for. Also found: "predictable" is claimed by opposite units — Splunk markets per-host as the predictable one, SigNoz markets no-per-host-no-per-seat as the predictable one. **Predictability is a property set (no surprise overages, spend caps, in-product projection, no punitive dimension), not a unit.** We could achieve it on almost any unit.
- ~~**`usage_daily` counts spans, logs, metric_points and bytes, and nothing counts services.**~~ **DONE.** Migration 0010 added `usage_daily_services` — distinct `service.name` per project per day, which is the billable quantity the pricing decision names. `usage_daily`'s four counters stay as the guardrail meter, not the bill.
- ~~**No `plans` table exists.**~~ **DONE.** Migration 0005 added `plans`, holding each tier's raw and derived retention days, plus a `project_retention` view; migration 0013 added `plan_prices` and 0015 seeded it. The tiers are read, not hardcoded: the worker takes retention from the `project_retention` view (`apps/workers/internal/projectconfig/postgres.go`), and the receiver's allowance gate joins `organizations.plan` against `plan_prices` in one statement (`apps/receiver/internal/allowance/postgres.go`).
- ~~**Free-tier ceiling behaviour**~~ **RESOLVED — block, do not degrade**. For the record: — the market has a clear answer: **block, do not degrade**. Sentry rejects new events until the next period, and with a spend cap on, overages are blocked rather than billed, backed by spike protection and SDK-side sampling. That maps onto P1-52 (kill switch) — same mechanism, different trigger.
- **Payments.** **Stripe Managed Payments (Stripe's own merchant of record) excludes India**, and Stripe is invite-only here — the obvious default is off the table before the comparison starts. FIRA/FIRC is statutory, not preference: Rule 89(2) CGST Rules requires a BRC or FIRC as proof of foreign-currency receipt, with an LUT (Form RFD-11) filed annually to export without paying IGST upfront; SaaS export is zero-rated under §16 IGST Act. **Unresolved and not answerable from research:** with a merchant of record, the MoR is the legal seller and money flows customer → MoR → us, so the payout is arguably a payment *from the MoR* rather than an export of services to the customer. Whether that still qualifies as zero-rated export with clean FIRA is a question for a CA who does cross-border SaaS. Vendor-comparison searches returned almost entirely one MoR's own marketing, so no provider ranking is recorded here.

---

## 8. Parking Lot (ideas not yet placed)

- White-label owner status page (from agency discussion)
- Deriving business metrics (orders/min) for e-commerce owners
- Status page hosting (public status.mysite.com)
- **Sustainability / carbon monitoring (GreenOps):** show energy use + estimated CO₂ footprint of customer infrastructure. Technical: derived, not measured — instance type + CPU utilization → estimated energy → × grid carbon intensity of the cloud region (public datasets; reference: Cloud Carbon Footprint project, CNCF Kepler for per-container energy on K8s). Piggybacks on infra-monitoring data our door-4b pollers already fetch — no new doors needed. Fits our "translate technical → plain language" spirit; carbon waste ≈ cost waste (idle oversized instances), so it doubles as a cost-saving view. Differentiator candidate, not core wedge — demand today mostly from ESG-reporting companies.

- **Third-party dependency / vendor outage detection** — "is this broken because of us, or because
  a vendor we depend on is down." Different from F9's service map, which is built from our own
  traces and shows dependencies *between the customer's own services*; this would be about external
  vendors (Stripe, AWS, a payment processor) that never send us a span. Surfaced 2026-09-14 by
  UptimeRobot's acquisition of IsDown (29 Jul 2026), built for exactly this question — one
  acquisition, medium confidence, unshipped as of the last check, so this is "worth knowing exists,"
  not "worth building." For a team with no dedicated SRE, correctly identifying a vendor outage is
  the difference between a five-minute read and an hour lost debugging code that was never broken.

*(Add new ideas here so nothing is lost.)*
