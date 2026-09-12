# What the customer cannot see, and cannot ask

> **Status:** Analysis, awaiting review. Written from the customer's side: not "what do we store and
> fail to draw" — that is AC4 — but **"what would a customer reasonably want, and can the product
> answer it at all?"**
>
> Every filter below was read from the routers' own signatures.

## Headline: the filter surface is wildly uneven

| Screen | Real filters today (beyond time and limit) | Count |
|---|---|---|
| **Traces** | service · operation · status · min_duration · max_duration · attr + attr_value · cursor | **7** |
| **Logs** | q (substring) · service · min_severity · trace_id · cursor | **5** |
| Services | service_name | 1 |
| Errors | service_name | 1 |
| Service map | — | **0** |
| Synthetics | — | **0** |
| Database | — | **0** |
| Infrastructure | — | **0** |
| RUM | — (`dimension` is a breakdown, not a filter) | **0** |
| Usage | — | **0** |

A customer can slice traces seven ways and synthetics not at all. That asymmetry is invisible to us
because traces was built first and carefully; to a customer it reads as the product losing interest
after the second screen.

---

## The single biggest gap: no number has any context

**"Is 4.1% errors bad?"** Nothing in the product answers that. There is no period-over-period
comparison anywhere in the API — verified: every apparent match for *compare* / *baseline* is
`hmac.compare_digest` or a code comment.

So every number on every screen is context-free. The customer must already know what normal looks
like, which is precisely the knowledge a monitoring product is supposed to supply.

What is missing, in the customer's words:

- *"Is this worse than yesterday? Than last week?"* — no previous-period overlay or delta.
- *"Is this normal for a Tuesday at 3pm?"* — no baseline or seasonality.
- *"It got worse — when exactly?"* — no change-point marker.

**Cost:** moderate and mostly free of new storage. The rollups already hold every prior window; a
second read over the shifted window and a delta beside each number would answer the first bullet
entirely. Baseline and change-point are real work and arguably belong to the insight engine.

---

## Category A — stored, not shown

Tracked as AC4 in `spec.md`, eleven dimensions. Not repeated here.

## Category B — data exists, no filter exposed *(API work only, no storage change)*

These are the cheapest wins in the whole redesign. Every column below is already stored and already
indexed by the sort key or promoted.

| Screen | Customer cannot ask | Column that exists |
|---|---|---|
| Services | "only services with errors" · "sort by latency not errors" · "only HTTP, not queues" | `error_count`, `latency_quantiles`, `span_kind` |
| Errors | "only unresolved" · "only new since the deploy" · "sort by first-seen" | `first_seen`, `last_seen` |
| Synthetics | "only what's down" · "only browser checks" · "TLS expiring soon" | `up`, `check_type`, `tls.days_remaining` |
| Database | "only this server" · "slower than 100ms" | `server`, `latest_mean_ms` |
| Infrastructure | "only containers" · "only stale" · "over 80% CPU" | `kind`, `stale`, `cpu_utilization` |
| Service map | "only failing edges" · "hide low-volume links" | `error_rate`, `calls_per_minute` |
| RUM | "only mobile" · "only this route" — device and page are a *breakdown*, never a filter | `device_class`, `route` |
| Logs | "exclude this noisy pattern" — only positive substring match exists | `body` |
| Traces | "traces where this span was slow", not "traces containing this service" | `duration_ms` per span |

## Category C — not stored at all *(needs ingestion or schema work)*

| Customer question | What is missing | Cost |
|---|---|---|
| *"Who do I page for this service?"* | Service ownership — team, on-call, repo, runbook link. The catalog names it under F8/F9; nothing stores it. | Low. A Postgres table keyed by service name. No telemetry change. |
| *"How many customers did this affect?"* | No session or user dimension on spans. `web_vitals` has `session_id`; traces do not, so blast radius is countable in the browser and not in the backend. | Medium. A promoted column, populated from an OTel attribute the customer already sends. |
| *"Is staging different from production?"* | A project *is* an environment, and nothing compares two. Every screen is scoped to exactly one. | Medium. Cross-project reads break the current tenancy chokepoint's shape and must be designed, not bolted on. |
| *"Tell me when this happens again."* | No alerting. F21, not built. | High — its own feature. |
| *"Save this filter, I use it daily."* | No saved views. A customer who builds a seven-way trace filter cannot keep it. | Low. Postgres, per-user. |
| *"Give me this as CSV."* | No export anywhere. | Low per screen. |
| *"Which deploy caused this?"* | `deploy_events` is ingested, stored and served — and no screen consumes it. | Already in scope as IA3. |

---

## What this means for the redesign

**The wireframes are not wrong, but they are complete only against what the product can answer
today.** Three of the four batches drew screens that show a number with no way to tell whether it is
good. That is the gap a customer would name first, and no amount of layout fixes it.

Recommended ordering, cheapest and highest-value first:

1. **Category B filters** — pure API work over existing columns, and it takes seven screens from zero
   filters to usable. Nothing in the redesign is blocked on it, and nothing else is this cheap.
2. **Previous-period delta** — one extra read per screen against rollups that already exist, and it
   gives every number on every screen the context it currently lacks.
3. **Service ownership** — a small Postgres table that answers the question every incident ends with.
4. **Session dimension, cross-project comparison, saved views, export** — each its own change record.
5. **Alerting** — its own feature, and arguably the insight engine's problem rather than the
   console's.

Items 1 and 2 are the ones I would put in this feature. **3 onward should be their own records**, or
the console redesign quietly becomes a platform redesign and never ships.
