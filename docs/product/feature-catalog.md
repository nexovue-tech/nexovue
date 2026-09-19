# Feature catalog

> **Owner:** Raj · **Status:** Draft — living document · **Last updated:** 2026-09-19 — F21 built; F17 built as a preview. Earlier the same day: F17 moved into Phase 1 Track 1 after F21 (§5). Earlier: 2026-09-16 — Phase 1 approved and reordered: F18 first, then F21, F26, F31 and F36, with the website and deployment as parallel tracks
> **How to use this doc:** every feature gets the same structure as F1 (the template): Description → User stories → Acceptance criteria → UI notes → Dependencies → Open questions. We fill features in one by one; a feature is "specified" when its acceptance criteria are testable.

> ⚠️ **Built and specified are different questions, and §4 answers both separately.**
> `🔲 TO SPECIFY` below means nobody has written this feature's acceptance criteria — it says
> nothing about whether the feature ships. Most of what ships was never specified; F1 is the only
> fully specified feature and it has no engine behind it. `🔶 PART SPECIFIED` means one half has
> criteria and the other does not, and the entry says which.
>
> Acceptance criteria are written in the present tense because that is what an acceptance criterion
> is — a statement to test against once built. Read every `AC` below as *must*, never as *does*.

---

## 1. Product Overview

**Vision:** An insight-first observability platform that tells users *what broke and why*, instead of handing them an empty builder and fifty dashboards to maintain. The dashboards it composes for them are first-class; the work of assembling them is what it removes. ([`vision.md`](vision.md) is the source; this line mirrors it.)

**Product goals:**
- G1: A developer goes from alert → understanding in minutes, not hours.
- G2: A user gets first value within 10 minutes of signup, without reading docs.
- G3: A non-technical owner can understand their application's health without help.
- G4: Cost is predictable and visible — never a surprise bill.

**Success signals (product-level):** time-from-finding-to-acknowledged; % of findings users rate as accurate/useful; onboarding completion to "first data received"; % of owners who check their status view weekly.

## 2. Personas

| Persona | Description | Primary surfaces |
|---|---|---|
| P1 — Dev | Full-stack developer, 2–15 person startup; also the ops person | Findings feed, finding detail, traces, errors, logs, chatbot |
| P2 — Indie | Solo founder / indie hacker; allergic to complexity and cost | Onboarding, findings feed, synthetic checks, usage/cost view |
| P3 — Owner | Non-technical application/website owner | Owner status page only |

## 3. Scope

**In scope:** every end-user-facing feature in §4 (the full observability platform, the insight layer and the owner layer).
**Out of scope (for this catalog):** internal architecture (see architecture doc) · pricing amounts · go-to-market · agency/white-label channel (parked) · timelines.

---

## 4. Feature status — the whole product, one table

> **Two columns, because they do not track each other.** *Built* is whether a customer can use it
> today. *Specified* is whether anyone has written testable acceptance criteria. Most of what ships
> was never specified, and the one fully specified feature (F1) has no engine behind it. Saying so in
> two columns is the only honest way to hold both facts.
>
> *Phase* is when the remaining work is planned, not when the built part shipped. Everything marked
> Phase 0 is done; §5 lists it.

| # | Feature | Built | Specified | Phase | Where it stands |
|---|---|---|---|---|---|
| **F1** | Findings feed | 🟡 Partly | ✅ | 2 | Screen, ordering and actions built and tested; **no engine produces a finding**, so it shows the healthy state |
| **F2** | Finding detail | 🔴 No | 🔲 | 2 | Needs the engine first |
| **F3** | AI insights | 🔴 No | 🔲 | 2 | The differentiator; nothing built |
| **F4** | AI chatbot (RAG) | 🔴 No | 🔲 | 2 | Compose reserves an `insight` service and Ollama; the package does not exist |
| **F5** | AI solution | 🔴 No | 🔲 | Later | After F3 earns trust |
| **F6** | Anomaly detection | 🔴 No | 🔲 | 2 | Feeds F1 and F21; worth little before alerts exist |
| **F7** | Owner status page | 🔴 No | 🔲 | Later | The P3 persona has no surface yet |
| **F8** | Service overview | ✅ Yes | 🔲 | 0 | Services list, ranking, trends, service detail |
| **F9** | Service map | ✅ Yes | 🔲 | 0 | Dependency graph with per-edge rate, latency and failures |
| **F10** | Trace waterfall | ✅ Yes | 🔲 | 0 | Trace list, waterfall, span detail, CSV export |
| **F11** | Errors screen | ✅ Yes | 🔲 | 0 | Grouping, stack traces, deploy correlation |
| **F12** | Logs screen | ✅ Yes | 🔲 | 0 | Search, facets, live tail with pause, CSV export (stored and on-screen) |
| **F13** | Infrastructure | ✅ Yes | 🔲 | 0 | Hosts and containers; carbon panel not built |
| **F14** | Database monitoring | ✅ Yes | 🔲 | 0 | Ships as a view inside Infrastructure, not a screen of its own |
| **F15** | RUM / Core Web Vitals | ✅ Yes | 🔲 | 0 | Screens built; **the browser SDK is unpublishable** (decision D6) |
| **F16** | Session replay | 🔴 No | 🔲 | Later | Neither built nor scoped |
| **F17** | Continuous profiling | ✅ Preview | ✅ | **1** | **Built 2026-09-19 as a preview** to the approved [spec](../features/profiling/spec.md) — see [status](../features/profiling/status.md); storage restated per busy core (D6) and met at ~73 MB a day. Before it: moved into Phase 1 Track 1, 2026-09-19, by founder decision. The OTel profiles signal entered public alpha in March 2026; its OTLP format is still *Development* (sent to `/v1development/profiles`) and will change, so the design must confine that format to the intake. Nothing collects profiles today |
| **F18** | Synthetic checks | ✅ Yes | ✅ | **1** | **Built 2026-09-17** to the approved [spec](../features/synthetic-checks/spec.md): API, multistep API, heartbeat, TCP and DNS checks created, edited, paused and deleted in the console; any method, bodies, files, auth from write-only secrets; assertions; import from cURL, OpenAPI and Postman; Run now; confirmation from another location; up/degraded/down/misconfigured state with a timeline; maintenance windows; per-run evidence. **Browser checks removed** (D13). Committed 2026-09-18 (`75b5424`) — see [status](../features/synthetic-checks/status.md) |
| **F19** | SLO tracking | 🔴 No | 🔲 | Later | |
| **F20** | Dashboards | 🟡 Partly | 🔶 | Later | The composed Overview is built and holds `/`; the **builder** is not |
| **F21** | Alerts & notification connectors | ✅ Yes | ✅ | **1** | **Built 2026-09-19** to the approved [spec](../features/alerts/spec.md) — see [status](../features/alerts/status.md). Before it: The largest gap in the product. Notification adapters and a scheduler exist to build on. **A live competitor already bundles detection + AI root-cause + code-fix suggestions** (Better Stack's AI SRE, confirmed 2026-09-14) — the gap on "tell me what broke and act on it" is wider than scoped in August |
| **F22** | Case management | 🔴 No | 🔲 | 2 | Depends on findings existing |
| **F23** | Onboarding | ✅ Yes | 🔲 | 0 | Signup → two env vars → first-span celebration; auto-created defaults not built |
| **F24** | Integration settings | 🟡 Partly | 🔲 | 2 | Keys, site keys, deploy webhooks, cloud and DB pollers, integration health all built; AWS role flow is not |
| **F25** | Usage & cost view | ✅ Yes | 🔲 | 0 | Usage, allowance, projected cost, billing, charge history |
| **F26** | RBAC / user management | ✅ Yes | ✅ | **1** | **Built 2026-09-19** as Stage 1 of [Team, API and admin access](../features/team-and-api-access/spec.md) (with F31 and F36): invite by email, accept, four roles on every route and screen, remove, transfer ownership, audit log screen — see [status](../features/team-and-api-access/status.md) |
| **F27** | Data controls | 🟡 Partly | 🔲 | 2 | Masking, plan retention, tenant purge and CSV export built; archival tiers and a GDPR surface are not |
| **F28** | Deployment / release tracking | 🟡 Partly | 🔲 | 2 | Deploy webhooks, chart markers and error-to-deploy linkage built; the release-comparison screen is not |
| **F29** | LLM / AI-app observability | 🔴 No | 🔲 | Later | `gen_ai.*` attributes already land and are queryable; no screen, no metric contract |
| **F30** | Public status page | 🔴 No | 🔲 | Later | |
| **F31** | Admin console (internal) | 🔴 No | 🔲 | **1** | `audit_log.impersonation_context` is in place and unused. **Moved from Later, 2026-09-16:** cloud customers are onboarded by hand through it ([ADR-008](../decisions/ADR-008-the-website-takes-the-first-payment.md)), so it is the first thing a paying customer depends on |
| **F32** | Account, organization & project access | ✅ Yes | 🔲 | 0 | Signup, verification, recovery, profile, TOTP second factor, active sessions, sign-out-everywhere |
| **F33** | Telemetry intake & data protection | ✅ Yes | 🔲 | 0 | OTLP HTTP and gRPC, browser intake, site keys, origin allowlist, masking, tail sampling, tenant isolation |
| **F34** | Metrics explorer | ✅ Yes | 🔲 | 0 | Catalogue with name/shape/readable filters, histograms, duration heatmaps. *Added 2026-09-14 — it shipped without a catalogue entry* |
| **F35** | Setup documentation (public) | ✅ Yes | 🔲 | 0 | Fifteen pages served unauthenticated at `/docs`, compiled from `docs/product/setup/`. *Added 2026-09-14* |
| **F36** | Customer read API | 🔴 No | 🔲 | **1** | The console API is **session-cookie only** and ingest keys are write-only, so **a customer cannot script against their own telemetry at all**. *Added 2026-09-14* |
| **F37** | Self-hosted install & operation | 🟡 Skeleton | 🔲 | **1** | `docker-compose.onprem.yaml` and the operations docs exist and are marked, correctly, as unverified. No images, no registry, no release, no runbook. **Licence decided and declared 2026-09-15: AGPL-3.0, browser SDK Apache-2.0; transitive licence audit and git-history secret scan both clean.** Delivered as the deployment track of Phase 1 ([ADR-006](../decisions/ADR-006-terraform-with-two-delivery-paths.md)). *Added 2026-09-14* |

**Named capabilities inside features (so they aren't lost):** BubbleUp-style attribute/outlier
analysis → F20 + F2 evidence · service ownership metadata → F8/F9 · telemetry-value transparency
→ F25 · cross-signal global search → F1/F4 · suspect-commit/release linkage on errors → F11 + F28 ·
log archival tiers → F27 · **sustainability/carbon panel → F13**.

**What the 2026-09-14/15 market research changed, and what it did not.** Two passes over the
August analysis ([`../research/market-analysis-2026-09.md`](../research/market-analysis-2026-09.md),
which states its own gaps). **Reconfirmed:** no vendor among twelve prices per service monitored, so
the pricing unit stays structurally unique; the insight-first position is unclaimed not just by
Datadog-class incumbents but by the OTel-native peer set that a buyer actually price-compares us
against; GreenOps remains nascent and owned by no observability vendor, Grafana included.
**Sharpened:** F21's urgency — Better Stack ships a live, purchasable agentic AI SRE doing
eBPF+OTel root-cause with code-fix suggestions, so the gap on "tell me what broke and act on it" is
wider than August assumed, and the differentiator left is *OTel-only, no agent* rather than *does AI
root-cause at all*. **Corrected:** Datadog's "$5 per 100 custom metrics" traces to a competitor's
blog, not Datadog's own page, which declines to publish a list price. **Still unanswered after two
passes, and not to be read as validated:** what small teams say is *missing* from tools they
rejected; whether free-self-hosted converts to paid anywhere; and whether a dashboard builder, SLOs
or session replay are genuinely mandatory in real buyer evaluations. The decision to skip F16, F19,
F20's builder and F7 in Phase 1 is therefore **carried, not confirmed** — close it before locking
Phase 2 scope.

**Deliberately NOT built (explicit decisions):** on-call scheduling and escalation → integrate via
PagerDuty/Opsgenie connectors in F21/F22, never our own · customer cloud FinOps → parked ·
monitoring-as-code CLI → parked · agency/white-label → parked · network monitoring and SIEM → out of
scope, 2026-09-07.

---

## 5. Phases

### Phase 0 — complete (2026-08 → 2026-09-14)

**What it was:** get every signal in, and give each one a screen good enough to investigate with.
Everything below ships and is exercised by tests; `just check` and the fourteen end-to-end specs
pass.

| Area | What shipped |
|---|---|
| **Getting data in** | OTLP over HTTP and gRPC, a separate browser entrance gated by site keys and an origin allowlist, Kafka buffering, masking before anything downstream reads a value, tail sampling, plan-derived retention, and tenant isolation enforced at a parsed chokepoint |
| **Investigating** | Services, service map, traces and waterfall, errors, logs with a live tail, infrastructure, database, RUM, synthetics, metrics — twelve screens sharing one time window, one project selector and one refresh control |
| **Our own probes** | HTTP, SSL-expiry and Playwright browser checks, broken into phases |
| **Account and money** | Signup to first span, email verification, recovery, TOTP second factor, active sessions, projects, ingest keys, usage, allowances, plans, Razorpay checkout and signed webhooks |
| **Reading it** | Fifteen pages of setup documentation, published at `/docs` without a login |
| **The console redesign** | Three passes and 79 tasks: a shared time window that survives a reload, absolute ranges, a pause and an interval, background refresh instead of cold reloads, filters on every screen, empty states that say which kind of empty, and a design-token guard that fails the build on an invented colour |

**What Phase 0 deliberately did not do:** produce a single finding, notify anybody of anything,
admit a second user to an organization, or run anywhere but a laptop.

### Phase 1 — approved 2026-09-16

**No release date.** Features are completed first; hosted deployment happens when a first cloud
customer pays ([ADR-008](../decisions/ADR-008-the-website-takes-the-first-payment.md)). Three tracks:
the first in strict order, the other two alongside it.

**Track 1 — features, in order**

| Order | Feature | Bar |
|---|---|---|
| 1 | **F18 — Synthetic checks** | **Complete to production level** — everything a customer expects of synthetic monitoring, specified in full before any code, through the SDD workflow |
| 2 | **F21 — Alerts & notification connectors** | Built 2026-09-19 |
| 3 | **F17 — Continuous profiling** | Added 2026-09-19 by founder decision, ahead of F26. Built on an alpha OpenTelemetry signal — see its §4 row |
| 4 | **F26 — Teams: invite, roles, membership** | |
| 5 | **F31 — Platform admin console** | Must exist before the first paying cloud customer: onboarding goes through it |
| 6 | **F36 — Customer read API** | |

**Track 2 — website redesign, in parallel.** A modern site for the product only, with SEO, pricing
(self-hosted free; cloud plans paid; no free cloud tier) and the first payment
([ADR-008](../decisions/ADR-008-the-website-takes-the-first-payment.md)). Outside this catalogue:
`nexovue.com` is a separate Next.js application on Firebase.

**Track 3 — production readiness and deployment, in parallel.** F37 plus the hosted infrastructure:

- CI building multi-arch images, **published publicly on Docker Hub**
- The self-hosted install, runnable and documented
- Terraform, with Ansible for Linux servers and Helm for Kubernetes ([ADR-006](../decisions/ADR-006-terraform-with-two-delivery-paths.md))
- The preparations for moving between clouds ([ADR-007](../decisions/ADR-007-migrating-between-clouds.md)) and regions ([ADR-009](../decisions/ADR-009-regions-are-cells.md))
- **A trial run on Oracle Cloud.** Always Free is 2 OCPUs / 12 GB since June 2026 — a trial and staging environment, not one for a paying customer
- The blockers in [`../operations/production-readiness.md`](../operations/production-readiness.md)

Docker Hub allows 100 anonymous pulls per six hours **per IP**. A single self-hosted install pulls
about a dozen images and is unaffected; nodes or runners sharing one NAT address share that budget, so
our own deployments pull authenticated.

### Phase 2 and later

Phase 2 is the insight layer the vision is built on — anomaly detection, the insight engine, findings
with something behind them, cases, and the chatbot — plus the release-comparison screen, the AWS role
flow and data archival. Everything marked *Later* in §4 has no phase and is not scheduled.

---

## 6. Features

### F1 — Findings Feed (home screen) ✅ SPECIFIED — TEMPLATE FEATURE

**Description**
The default screen after login. A reverse-chronological, priority-weighted feed of *findings*: plain-language statements of what broke (or is degrading) and why, generated by the insight engine. Replaces dashboard sprawl as the product's front door. When nothing is wrong, the feed says so explicitly and shows a calm summary — "all quiet" is a feature, not an empty state.

**User stories**
- US1.1 — As a Dev (P1), when I open the app, I see the most important current problems first, in plain language, so that I know within seconds whether anything needs my attention.
- US1.2 — As a Dev, when a new finding is generated while I'm looking at the feed, it appears without a page refresh, so that I can trust the feed during an incident.
- US1.3 — As a Dev, I can filter the feed by service, severity, and status (active / resolved / all), so that I can focus during triage.
- US1.4 — As a Dev, when everything is healthy, I see an explicit "all systems normal" state with a brief health summary (services up, checks passing), so that silence is information, not doubt.
- US1.5 — As an Indie (P2), I see findings written without assumed SRE vocabulary (no unexplained "p99", "cardinality", "error budget"), so that I understand them without googling.
- US1.6 — As a Dev, I can mark a finding as acknowledged or resolved from the feed, so that my team sees what's being handled (feeds case status).
- US1.7 — As a Dev, I can tell at a glance which findings are new since I last looked, so that repeat visits are fast.

**Acceptance criteria**
- AC1.1: Feed is the authenticated default route. Loads initial content in ≤ 2s on a typical connection.
- AC1.2: Each finding card shows: severity (critical/warning/info), plain-language title, affected service(s)/endpoint(s), start time (relative + absolute on hover), impact estimate when available ("~12% of checkout requests affected"), current status, and probable-cause one-liner when the insight engine produced one.
- AC1.3: Finding titles are plain-language: subject + what changed + magnitude + since when. Example: "Checkout is 4× slower than normal since 15:04." Forbidden in titles: raw metric names, internal IDs, unexplained percentile jargon.
- AC1.4: New findings appear in the feed within 30 seconds of the finding being created, without manual refresh (live update path).
- AC1.5: Feed orders by: active before resolved, then severity, then recency. Ordering is deterministic and documented.
- AC1.6: Filters (service, severity, status) combine and persist per user across sessions.
- AC1.7: Empty/healthy state shows: "all systems normal" statement + count of services reporting + synthetic checks passing + last-finding timestamp. It never shows a blank page or spinner-forever.
- AC1.8: Clicking a finding opens F2 Finding Detail. Acknowledge/resolve actions available on the card and reflected in F22 Case management.
- AC1.9: A finding whose root incident continues does NOT duplicate — it updates (duration, impact) on the same card (grouping/dedup requirement on the insight engine).
- AC1.10: Feed is fully usable on mobile-width screens (the paged-at-2am scenario).

**UI notes**
Card-based feed; severity color accents; sparkline of the affected signal on each card optional but desirable. Prominent global search/ask box (entry to F4 chatbot). No charts wall — one feed, one focus.

**Dependencies**
Insight engine (findings generation, dedup/grouping) · anomaly detection · live-update path (WebSocket/pub-sub) · F2, F22, F4.

**Open questions**
- Do resolved findings auto-archive from the default view after N days?
- Is impact estimate shown when confidence is low, or hidden below a threshold?
- Per-user vs per-project read/unread state for "new since last visit"?

---

### F2 — Finding Detail Page 🔲 TO SPECIFY
One finding's full story: what was detected, evidence (charts, linked traces, correlated logs, deploy events), AI probable cause with confidence, AI suggested fix (F5), actions (create/view case, ask chatbot, resolve). *The evidence-visible, human-in-the-loop principle from the market analysis lives here.*

### F3 — AI Insights 🔲 TO SPECIFY
The proactive finding-generation surface rules: cross-signal correlation presented in plain language; how insights differ from raw anomalies (an insight = anomaly + context + cause hypothesis).

### F4 — AI Chatbot (RAG) 🔲 TO SPECIFY
"Ask your system anything" panel available on every screen. Answers grounded in the user's telemetry (live tool-calling) + knowledge base (resolved findings, runbooks). Must cite/link the data it used.

### F5 — AI Solution 🔲 TO SPECIFY
Suggested fix with explanation on finding detail; optional draft-PR flow via GitHub connector; human-in-the-loop gating (never auto-applies without explicit action).

### F6 — Anomaly Detection (user-facing surface) 🔲 TO SPECIFY
What the user sees/controls: sensitivity settings, muting, per-endpoint baselines visibility, feedback ("this was a false alarm" → tunes system).

### F7 — Owner Status Page 🔲 TO SPECIFY
Plain-language health view for P3: overall badge, per-area status, human sentence for current issues, "your developer has been notified," update timestamps. Zero jargon. Shareable/inviteable.

### F8 — Service Overview 🔲 TO SPECIFY
All services with health at a glance (request rate, error rate, p95 trend); drill into endpoints ranked by slowness/errors.

### F9 — Service Map 🔲 TO SPECIFY
Auto-discovered dependency graph from traces; edges show call rate/error/latency; click-through to services.

### F10 — Trace Waterfall 🔲 TO SPECIFY
Span waterfall for one request; inline correlated logs; error highlighting; link from findings/errors.

### F11 — Errors Screen 🔲 TO SPECIFY
Grouped exceptions: type, message, count, trend, first/last seen, affected endpoints; stack traces; link to traces and case creation.

### F12 — Logs Screen 🔲 TO SPECIFY
Search + filters (service, severity, time, trace); live tail mode; pattern grouping view; "logs for this trace" entry path.

### F13 — Infrastructure Monitoring 🔲 TO SPECIFY
Hosts/containers/K8s: CPU, memory, disk, network; per-service resource context in finding evidence; sustainability panel — estimated energy use + carbon footprint (derived: utilization × instance type × regional grid carbon intensity), doubling as an oversized/idle-instance cost signal.

### F14 — Database Monitoring 🔲 TO SPECIFY
Query performance (slow queries, mean time, calls), connection health; "this exact query is your bottleneck" surface.

### F15 — RUM / Core Web Vitals 🔲 TO SPECIFY
LCP/INP/CLS + page-load breakdown by page, geo, device; JS errors; correlation to backend traces.

### F16 — Session Replay 🔲 TO SPECIFY
Playback linked from errors/findings; PII masking on by default; retention rules.

### F17 — Continuous Profiling ✅ BUILT (PREVIEW)
Specified, designed and built 2026-09-19: [`spec.md`](../features/profiling/spec.md),
[`design.md`](../features/profiling/design.md), [`plan.md`](../features/profiling/plan.md), ADR-015. OTLP
profiles from the eBPF profiler or the `pprof` receiver; a Profiles screen with flame graph, search, zoom,
filters, timeline and compare; a span's *Profile* section; a Profiling strip on the service page; samples
metered, not billed, while the signal is in preview. Linking from finding evidence waits for F1.

### F18 — Synthetic Checks ✅ SPECIFIED
Specified, designed and built through the SDD workflow: [`spec.md`](../features/synthetic-checks/spec.md),
[`design.md`](../features/synthetic-checks/design.md), [`plan.md`](../features/synthetic-checks/plan.md). Browser
checks, screenshots and customer scripts are out of scope by founder decision (spec D13, D16).

### F19 — SLO Tracking 🔲 TO SPECIFY
Define targets on services/endpoints; error-budget status; budget-burn alerts into findings feed.

### F20 — Dashboards 🔶 PART SPECIFIED
**Amended 2026-09-12: insights and dashboards are both first-class.** The earlier entry called this
an "optional mode" and a builder that is "never the front door", which read as *a dashboard is
something we tolerate*. It is not: a dashboard-shaped overview is reachable, complete, and composed
for the customer rather than handed over empty.

Two halves, at different stages:

- **The composed overview — built, 2026-09.** Auto-composed RED and USE panels from rollups that
  already exist, holding `/` until the insight engine exists. Specified in
  [`../features/console-redesign/`](../features/console-redesign/) (OD2 and
  `wireframes-dashboard.md`) and shipped as T30–T34 of that feature's task plan.
- **The builder — still to specify.** Not foreclosed by the above: an auto-composed dashboard is a
  fixed instance of what a builder would produce, so the composed overview is the first step toward
  one rather than a substitute for it.

**The front door is unchanged**: the Findings feed (F1) is what a person lands on. What changed is
that the other surface stopped being a lesser one.

### F21 — Alerts & Notification Connectors ✅ DESIGNED
Specified and approved 2026-09-18: [`docs/features/alerts/spec.md`](../features/alerts/spec.md); designed and
approved 2026-09-19: [`design.md`](../features/alerts/design.md). Email, Slack,
Microsoft Teams, Discord, PagerDuty and signed webhooks; alerts on check state, new error groups, service error
rate and p95, and a service going silent; one incident per breach, flap guard, burst cap, mute, quiet hours and
maintenance windows. **WhatsApp, SMS and a native Opsgenie connector are dropped** (D6): SMS and phone reach a
person through PagerDuty, and Opsgenie ends support on 5 April 2027.

### F22 — Case Management 🔲 TO SPECIFY
Auto-created cases from findings; list/detail with lifecycle (open → investigating → resolved); GitHub Issues/Jira connectors (create + status sync); case timeline.

> **Not a helpdesk.** A case here is an *incident* raised from a finding, never a customer support ticket. Customer support tooling is unplaced — see the commercial-model open questions in `vision.md` §7.

### F23 — Onboarding 🔲 TO SPECIFY
Signup → project → "2 env vars" screen → live first-span celebration → auto-created defaults (service overview, suggested synthetic check from domain, alert presets). The 10-minute promise, specified.

### F24 — Integration Settings 🔲 TO SPECIFY
API keys management; cloud integrations (AWS role flow); DB monitoring connection; deploy webhooks (GitHub setup); per-integration health/status.

### F25 — Usage & Cost View 🔲 TO SPECIFY
Per-project ingestion (spans/logs/GB), current usage vs plan, projected cost — the pricing-predictability gap feature, in-product.

### F26 — RBAC / User Management 🟡 HALF BUILT — TO SPECIFY

**The access-control half is built and the membership half is not, which is the opposite of how this
entry used to read.** `auth/policy.py` carries `require_role`, and mutating routes are gated by
`MayAdminister`, `MayCommitSpend` and `ROLES_THAT_MAY_END_THE_ORGANIZATION`. The
`owner/admin/member/viewer` enum has been in migration 0001 since the beginning.

What is missing is **a second person**. There is no invite, no membership management, no way to
change anyone's role — so an organization holds exactly the one user who signed up, and every role
gate enforces a role nobody can hold or change.

So the work here is **Teams**: invite by email, accept into an organization, list members, change a
role, remove someone. Plus owner-view-only invitations for P3 once F7 exists.

**Why it is Phase 1 rather than later:** retrofitting multi-user costs more the longer other
features are built while one-user-per-org is quietly true. *(Corrected 2026-09-14 after reading
`auth/policy.py`.)*

### F27 — Data Controls 🔲 TO SPECIFY
Retention settings per plan; PII masking rules configuration; data export/delete (GDPR surface); log archival tiers.

### F28 — Deployment / Release Tracking 🔲 TO SPECIFY
Releases list with deploy markers on all charts; before/after comparison per release (latency, error rate); post-deploy regression surfaced as a finding; suspect-release linkage on errors. Powered by deploy events (GitHub/CI webhooks).

### F29 — LLM / AI-App Observability 🔲 TO SPECIFY
UI lens for customers building on LLMs: token usage, model latency and cost per endpoint/model, prompt/completion traces (gen_ai.* attributes), error rates of AI calls. Fast-growing category; architecture already carries the data.

**Checked 2026-09-07, and "architecture already carries the data" is literally true.** `spans.span_attrs` is a ClickHouse `JSON` column, so `gen_ai.*` attributes from an instrumented application land, store and are queryable today — they appear in Traces and on the Service map. **No door is needed**: doors are organised by how data arrives, and `gen_ai` is a kind of span rather than a kind of entrance. What is missing is a screen and a metric contract.

**Scope line, so this is not read as "build MLflow".** An app *calling* a model in production is ours. *Building* models — experiment tracking, model registry, artifacts, evaluation — is a different product, a different buyer and not observability. MLflow exports OTLP `gen_ai.*` (`MLFLOW_ENABLE_OTEL_GENAI_SEMCONV`), so it is a **producer feeding door 1**, not a competitor.


### F30 — Public Status Page 🔲 TO SPECIFY
Customer-publishable status.mysite.com: components, current status, incident history, subscribe-to-updates. Table stakes per market analysis; shares the plain-language engine with F7 (owner view = private; this = public).

---

### F31 — Admin Console (internal) 🔲 TO SPECIFY
Our own back-office, not a customer surface: find an organization, see its usage against plan, comp or suspend an account, and **impersonate a user to reproduce what they are seeing**. Impersonation is reported as the highest-value piece and the one most often missed early — it requires explicit consent, a time bound, and a full audit trail.

**Schema hook — taken.** ✅ Migration `0004_audit_log_non_user_actors` added `audit_log.impersonation_context` (the impersonation session id) and `audit_log.actor_label` (for actors who are not rows in `users`, such as our own staff). The window closed correctly: the field was in place before audit entries started being written, so nothing has to be backfilled. F31 still has to *use* it — nothing sets the column today.

### F32 — Account, Organization & Project Access 🟨 EXISTS — TO SPECIFY

The entry and workspace-management flow: signup, email verification, login/logout, password
recovery/reset, profile/session security, organization creation, first project, and project selection.
Signup currently creates the organization, owner, first project, and first secret key in one
transaction. This feature does **not** imply multi-user membership or roles; those remain F26.

### F33 — Telemetry Intake & Data Protection 🟨 EXISTS — TO SPECIFY

Customer setup and trust boundary for sending telemetry: secret server keys, public browser site
keys with origin allowlists, OTLP HTTP/gRPC intake, browser RUM intake, project masking rules,
tail-sampling/retention behavior, and tenant data isolation. This is the product-facing contract for
getting data safely into Nexovue; its internal pipeline architecture belongs in `docs/architecture/`.

### F34 — Metrics Explorer ✅ EXISTS — TO SPECIFY

Every metric a project has reported, ranked by traffic, narrowed by name, shape (sum, gauge,
histogram) and whether this console can draw it. A histogram opens as a distribution over the
window; a duration metric opens as a heatmap.

**Added to this catalogue 2026-09-14**, having shipped without an entry — the index listed F8–F15
for screens and no metrics screen among them.

**The invariant this screen exists to respect:** every OTLP histogram is *cumulative* — each point
is the running total since `start_timestamp` — so summing across a window over-counts by the export
rate. At a ten-second export over an hour that is 360×. The queries take differences between
consecutive points rather than sums.

### F35 — Setup Documentation (public) ✅ EXISTS — TO SPECIFY

Fifteen pages covering both audiences the founder chose in September: a service already exporting
OpenTelemetry (two variables and a restart) and one that has never been instrumented (per language,
per platform, the Collector, the browser SDK, and what each refusal code means).

Served at `/docs` **without a login**, deliberately: somebody evaluating nexovue needs to know what
instrumenting their service costs before they sign up, and somebody whose telemetry stopped at three
in the morning should not be asked to sign in to read the troubleshooting page.

Compiled at build time from `docs/product/setup/`, which stays the source — editing a page publishes
it. **Added 2026-09-14.**

### F36 — Customer Read API 🔴 NOT BUILT — TO SPECIFY

A door for a machine that wants to *read*. Today there are two doors and neither is that one: the
receiver takes an ingest key and is **write-only**, and the console API is authenticated by a
**session cookie**, which only exists because a human typed a password into a browser. So a customer
cannot fail a deploy on their own error rate, post last week's numbers to their own channel, pull
metrics into their own warehouse, or join latency against revenue they hold and we never will.

**User stories** are recorded in the Phase 2 candidate set; the four that force the design:

- As a platform engineer, I want a key that reads staging and cannot read production → a key is
  **scoped to a project**, not the organization
- As an owner, I want to see what a key last read and when → keys carry **usage history**, and reads
  are **audited**
- As an owner, I want to revoke a leaked key immediately → **shown once, revocable**, like ingest keys
- As a platform engineer, I want a nightly warehouse pull → **pagination**, and a rate limit that
  permits bulk reads

**The decision that gates it:** `api_keys` has no column expressing what a key may *do* — a key has
only ever meant "may write telemetry to this project". Three shapes were considered (scopes on the
existing key, a separate read key, personal access tokens inheriting a user's role); the
recommendation is a **separate read key**, because it leaves the receiver's hot path untouched and
does not depend on F26.

**Not this feature:** embedding a nexovue dashboard inside a customer's product. That is our UI
elsewhere, not our data elsewhere — different decisions about who sees what without a login, and no
shared code. **Added 2026-09-14.**

### F37 — Self-hosted Install & Operation 🟡 SKELETON — TO SPECIFY

nexovue is **both SaaS and self-hosted**, which makes installing and running it a customer-facing
surface with its own users — an operator, not a developer — and its own stories: *as an operator I
want one command to a working install*, *… to restore last night's backup and believe it*, *… to
upgrade without losing data*, *… to know before I start what it will cost me to run*.

**What exists:** `deploy/compose/docker-compose.onprem.yaml` (279 lines, Caddy for TLS, infra and
app split) and eight operations documents — deployment, production readiness, backup and restore,
disaster recovery, rollback, incident response, monitoring, alerting.

**What is honest about it:** `docs/operations/deployment.md` marks the on-prem compose as *"a
skeleton [that] must not be used as a production deployment procedure"*, and records what is
missing — no hosting target, no image registry, no CI/CD, no staging, no immutable release process,
no runbook. There is no `just` recipe for it; it has never been run.

**What the operator is being asked to run**, which the spec has to be honest about: five stateful
systems — ClickHouse, Kafka, Postgres, Redis, SeaweedFS.

**What a self-hoster pays: nothing — decided 2026-09-14.** Free to self-host, paid to have us host
it. So this feature builds **no licence machinery**: no key issuance, no validation, no expiry, no
phone home. Images are published anonymously, because a check at install destroys the ten-minute
promise the setup docs are written around.

Two consequences this spec has to carry: the **billing screen** has nothing to show in a self-hosted
install and must not try to reach Razorpay, and the **ingestion allowance gate** is off — it is the
operator's own disk and not ours to cap.

**The licence: AGPL-3.0, source public — decided 2026-09-15, recorded in [ADR-004](../decisions/ADR-004-agpl-with-a-permissive-browser-sdk.md).** The server, workers, API and console
go out under AGPL-3.0; **`packages/browser-sdk` ships permissive (MIT or Apache-2.0) and must not be
AGPL**, because it executes inside the customer's own page and copyleft there would make it legally
unusable by the customers it exists for. The rule this spec has to hold: anything running on our
infrastructure may be AGPL, anything running inside a customer's application may not.

**Precedent, from the 2026-09-14 research pass** — the menu the peer set actually uses, none of
which changed in the preceding twelve months (checked against each project's `LICENSE` commit
history, not a survey): OpenObserve AGPL-3.0 core + commercial add-on, SigNoz MIT core + commercial
`ee/`, ClickStack/HyperDX Apache-2.0 and MIT, Coroot Apache-2.0, Uptrace AGPL-3.0, Dash0 proprietary
with no self-hosting at all. **Every OTel-native peer but one is readable**, which is why
source-public rather than images-only: a closed binary asking for a customer's whole telemetry
stream is a reason to pick a competitor that has nothing to do with features.

**Two pre-flight items this feature owns**, both cheap and both before publication rather than
after:

1. **Dependency licence audit — done 2026-09-15, clean.** No incompatible licence in any ecosystem:
   npm's 51 production packages are MIT/ISC/Apache-2.0/BSD-3/Unlicense; Go is MIT/BSD-3/Apache-2.0;
   Python is MIT/BSD-3/Apache-2.0 plus duals. The two worth not assuming: `symbolic` is MIT despite
   Sentry relicensing other projects to BSL, and `pycurl` is `LGPL-2.1-only OR MIT`, so the MIT
   branch applies. Caveat recorded honestly: direct dependencies were checked, not the full
   transitive tree — `go-licenses report ./...` and `pip-licenses` close that.
2. **Content-level secret scan over full git history — NOT yet run, and required.** A filename-level
   check found `.env` was never committed (only `.env.example`, carrying `nexovue_dev` against
   localhost), but that matched names, not contents. A key pasted into a source file, a fixture or a
   commit message would not have shown up. `gitleaks detect` or `trufflehog git file://.` before the
   repo is flipped, because git history cannot be un-published — a leaked credential means rotating
   it, not deleting it.

**Open-core is deferred, not foreclosed.** No licence machinery, no `ee/` split, no key checking in
this feature. A future paid feature is new code under its own licence, not a relicensing of anything
existing — the shape OpenObserve already runs. **Added 2026-09-14, licence resolved 2026-09-15.**


## 7. Non-Functional Requirements (pointer)

To be written as a companion one-pager: performance targets (feed load, query latency, finding freshness), availability, durability (no data loss between receiver and storage), multi-tenant isolation guarantees, security & privacy commitments. *(Referenced by acceptance criteria such as AC1.1 and AC1.4.)*

## 8. Glossary

**Finding** — a generated, plain-language statement of a detected problem with evidence. **Case** — the workflow object created from a finding. **Signal** — traces, logs, metrics, events. **Owner view** — the P3-facing plain-language surface.

---

*Update log: F1 specified (template). 2026-09-14 — §4 became one status table covering every
feature; §5 records Phase 0 as complete and proposes Phase 1; F34 (metrics explorer) and F35 (setup
documentation) were added for capabilities that had shipped without an entry; F36 (read API) and F37
(self-hosted install) were added as Phase 1–2 scope; F20 and F26 were corrected against the code.*

*Next to specify: whichever of F37, F26 or F21 Phase 1 starts with — each needs a `spec.md` before
any design or code.*
