# Wireframes — The dashboard surface (batch 4)

> **Status:** Awaiting approval. Resolves OD2. Founder decided 2026-09-13: auto-composed RED/USE,
> no builder; Overview is its own entry and holds `/` until the insight engine exists.

## What this is built on

The two standard frames for observability dashboards are **RED** (Rate, Errors, Duration) for
services and **USE** (Utilization, Saturation, Errors) for infrastructure. Nexovue already stores
both, pre-sampling, and draws neither:

| Frame | Already stored in | Fields |
|---|---|---|
| **RED** | `endpoint_stats_1m` | `request_count`, `error_count`, `latency_quantiles(0.5, 0.95, 0.99)` |
| **USE** | `NodeSummary` | `cpu_utilization`, `memory_utilization`, `filesystem_utilization`, `disk_bytes_per_second`, `network_bytes_per_second` |
| Dependencies | `service_edges_1m` | `call_count`, `error_count`, quantiles, `callee_kind` |

No builder, no query language, no template variables. A panel set is composed from the entity you
are looking at, which is what makes this affordable.

---

## 21 · Overview — `/` *(new route)*

**Answers:** how is the whole system running right now?
**Does not show:** individual traces, logs, or anything requiring a choice of entity first.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Overview                                          [ Last 1h ▾ ]          │
│                                                                          │
│  11 services · 1 degraded      8.4k req/min      1.2% errors    p95 210ms│
│                                                                          │
│ ┌─── RATE ──────────────┐ ┌─── ERRORS ───────────┐ ┌─── DURATION ──────┐ │
│ │       ╱▔▔╲            │ │           ╱▔         │ │  p99 ─────╱▔      │ │
│ │  ────╱    ╲───        │ │  ────────╱           │ │  p95 ────────     │ │
│ │                       │ │                      │ │  p50 ────────     │ │
│ └───────────────────────┘ └──────────────────────┘ └───────────────────┘ │
│                              ┊ deploy v2026.9.11 — on all three          │
├──────────────────────────────────────────────────────────────────────────┤
│ WORST SERVICES                                  ← the ranked-entity row  │
│ ● checkout-api    1.2k/min   4.1% err   340 ms   ▁▂▃▅▇▅▃▂                │
│ ▪ payment-gateway 1.2k/min   1.8% err   512 ms   ▁▁▂▁▂▁▂▂                │
├──────────────────────────────────────────────────────────────────────────┤
│ INFRASTRUCTURE                    SYNTHETICS            BROWSER          │
│ 6 nodes · 1 critical              3 checks · 1 down     LCP 2.8s ▪       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Three RED panels, then the archetypes.** The top row is the dashboard; below it, Overview reuses
the ranked-entity row rather than inventing a second way to list services. That is the archetype
system paying for itself — a new screen costs three panels, not a new design.

**One deploy marker spans all three panels** (IA3). A deploy that moved rate, errors and latency
together is the single most legible thing a dashboard can show, and `deploy_events` is served today
with nothing drawing it.

**The bottom strip is one line per surface**, not panels. Infrastructure, synthetics and browser each
get a count and a worst-case, linking to their own screen. A dashboard that tries to show everything
is the failure mode the research warns about — information overload is the most common dashboard
defect, at 46.7% of users.

**Overview holds `/` until the insight engine exists.** When Findings is real, `/` moves and Overview
keeps this entry. `apps/web/src/app/routes/route-manifest.ts` currently says *"a dashboard must never
become the front door"* — that comment records the superseded stance and must be updated with this
change, along with `route-manifest.test.ts`, which enforces router/sidebar agreement.

**States.** *Empty* → no telemetry at all, which is the onboarding moment again. *Partial* → a
surface with no data shows an em dash in the strip, never a zero.

---

## 9 (revised) · Service detail — the full RED panel set

Batch 2 gave this screen one chart. Under Option A it becomes the per-service dashboard:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ← Services   checkout-api      1.2k/min · 4.1% err · p95 340 ms   [1h ▾] │
│ ┌─── RATE ─────────┐ ┌─── ERRORS ───────┐ ┌─── DURATION p50/95/99 ─────┐ │
│ └──────────────────┘ └──────────────────┘ └────────────────────────────┘ │
│ ┌─── DURATION DISTRIBUTION ────────────────────────────────────────────┐ │
│ │  see the constraint below — this panel is conditional                │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────┬────────────────────────────────────────────┤
│ OPERATIONS  (EndpointHealth)│ DEPENDENCIES  (ServiceEdge)                │
│ POST /checkout  840/min 5.8%│ → payment-gateway  1.2k/min  4.1%  ●       │
│ GET  /checkout/{id} 360 0.2%│ → postgres     db  3.1k/min  0.0%          │
└─────────────────────────────┴────────────────────────────────────────────┘
```

Matches the standard service-overview composition — RED trends, a distribution, an operations table
and a dependencies table — built entirely from rollups that exist, plus the quantile exposure already
approved.

**Dependencies come from `service_edges_1m`, filtered to this service.** The same data the service map
draws as a graph, drawn here as a list, because "what does this one service call" is a list question.

---

## 14 (revised) · Infrastructure — the USE panels

`NodeSummary` already carries utilization for CPU, memory and filesystem, plus
`disk_bytes_per_second` and `network_bytes_per_second`. Four panels per node, composed the same way.

**Saturation is the missing letter.** USE wants utilization *and* saturation — queue depth, run
queue, IO wait — and nothing in `NodeSummary` carries it. The panels are honest about being U and E
without S rather than relabelling utilization as saturation.

---

## The heatmap: a schema change, raised not assumed

A duration heatmap is the one panel in the standard set that **cannot be drawn from what nexovue
stores**, and `design.md` says a needed schema change is raised rather than absorbed silently.

| Source | Has buckets? | Usable? |
|---|---|---|
| `endpoint_stats_1m.latency_quantiles` | No — `AggregateFunction(quantiles(0.5, 0.95, 0.99))` | **No.** Three quantiles cannot reconstruct a distribution |
| `metrics_histogram` | Yes — `bucket_counts`, `explicit_bounds` | Only if the customer sends OTLP histograms |
| `spans.duration_ms` | Raw | **No.** Post-sampling, so the shape would be wrong |

Three options, all founder decisions:

1. **Conditional panel.** Draw it only when the customer sends OTLP histograms; otherwise the panel
   is absent, not empty. Free, and it gives the three unread metric tables a second reader.
2. **New pre-sampling histogram rollup.** A `duration_histogram_1m` written by `derive` alongside the
   counters it already computes. Correct for every customer, and the only schema change this redesign
   would require — a migration, a worker change, and a new write path in the hot loop.
3. **Drop the heatmap.** The three duration percentile lines already answer most of what it shows.

**Recommendation: 1 now, 2 only if customers ask.** The percentile lines carry the common case, and
adding a write to the pre-sampling path — the one place in the system where correctness is most
load-bearing — is a poor trade for a panel nobody has asked for yet.

---

## Consequences for the approved documents

| Document | Amendment |
|---|---|
| `spec.md` scope | 20 routes → **21**; Overview added |
| `spec.md` AC4 | twelve dimensions → **eleven**; `country` has no producer |
| `design.md` §2 | nine archetypes → **ten**; Dashboard added (Heatmap only if option 2) |
| `design.md` §6 | Overview above Signals; Findings keeps its entry, `/` moves later |
| `route-manifest.ts` | the "never the front door" comment records a superseded decision |
| Chart count | 6 → **~20** |
