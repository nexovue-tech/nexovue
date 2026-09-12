# Wireframes — Surfaces, account and gateway (batch 3 of 3)

> **Status:** Awaiting approval. The remaining ten routes across four archetypes — Distribution,
> Meter, Form, Gateway. Every field read from the API's own schemas.

## An AC4 correction: one of the twelve cannot be shown

`spec.md` AC4 requires each of twelve stored-but-unshown dimensions to be visible, filterable, or
deliberately hidden with a reason. **`web_vitals.country` cannot be any of those.** `BreakdownDimension`
carries two members and its docstring says why:

> *"`web_vitals.country` is a promoted column with no producer — there is no GeoIP anywhere in the
> system — so a `GEO` member would name a question the store cannot answer."*

The column exists; nothing populates it. The honest answers are to drop the column or to add GeoIP,
and both are founder decisions. **AC4 should be amended to eleven dimensions**, with `country`
tracked separately as a schema question rather than a design one.

---

## 11 · RUM — `/rum` *(Distribution)*

**Answers:** how does the site actually feel to real users?
**Does not show:** synthetic timings — a probe is not a user.

**Reads:** `GET /rum/web-vitals` → `WebVitalSummary` (`name`, `p75`, `readings`)
· breakdown by `PAGE` or `DEVICE` → `WebVitalGroup` (`key`, `is_route_template`), `truncated`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Real user monitoring                    [ Last 24h ▾ ]  [ By page ▾ ]    │
├──────────────────────────────────────────────────────────────────────────┤
│  LCP        INP        CLS        FCP        TTFB                        │
│  2.8 s ▪    184 ms     0.04       1.2 s      420 ms                      │
│  needs      good       good       good       good                        │
│  12,408 readings                              ← p75, with its own n      │
├──────────────────────────────┬─────────┬─────────┬────────┬──────────────┤
│ PAGE                         │     LCP │     INP │    CLS │     READINGS │
│ /checkout          template  │  4.1 s ●│  310 ms │   0.02 │        3,204 │
│ /products/{id}     template  │  2.2 s  │  120 ms │   0.01 │        6,180 │
│ /promo/summer-sale path      │  2.9 s  │       — │   0.18 │            7 │  ← n too small
└──────────────────────────────┴─────────┴─────────┴────────┴──────────────┘
```

**`readings` is a first-class column, not a footnote.** The schema's own comment: *"A p75 of four
readings is not a measurement, and a screen showing one has to be able to say so."* Below a threshold
the row keeps its identity and renders em dashes — the partial state, designed into the API rather
than invented here.

**`is_route_template` is shown as a label.** A grouped `/products/{id}` and a raw
`/promo/summer-sale` are different kinds of claim, and collapsing them would let a single-visit URL
sit beside an aggregate as though they were comparable.

**Good / needs-improvement / poor uses the published Core Web Vitals thresholds**, and the word
appears next to the number — the rating is not carried by colour alone.

**States.** *Empty* → "No browser data yet" with the snippet, since door 3's receiving side is built
and the screens were the missing half. *Truncated* → surfaced, as on infrastructure.

---

## 12 · Metrics — a reader for the three unread tables *(Distribution)*

**Answers:** what is my application's own instrumentation reporting?
**Does not show:** anything nexovue derives itself — those are the other screens.

`metrics_histogram`, `metrics_exp_histogram` and `metrics_summary` are written, masked and retained
today with **no reader at all** (AC5). A customer sending an OTLP histogram — the standard shape for
latency from every OTel SDK — has it stored and can never see it.

**The pattern already exists.** `GET /infrastructure/signals` → `SignalSummary` (`metric_name`,
`unit`, `points`, `series`, `nodes`, `last_seen`) is a metric catalogue. This screen is that
catalogue widened past host metrics, plus a reader per OTLP shape.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Metrics                                  [ Last 6h ▾ ]  [ Search… ]      │
├──────────────────────────────┬──────────┬─────────┬────────┬─────────────┤
│ METRIC                       │    SHAPE │  SERIES │ POINTS │   LAST SEEN │
│ http.server.duration      ms │ histogram│      48 │ 12,400 │      2m ago │
│ db.client.connections.used   │     gauge│       6 │  1,880 │      2m ago │
│ queue.depth                  │       sum│       3 │    940 │     14m ago │
└──────────────────────────────┴──────────┴─────────┴────────┴─────────────┘
     ↓ selecting a histogram
┌──────────────────────────────────────────────────────────────────────────┐
│ http.server.duration · 48 series · ms                                    │
│   ▁▂▅█▇▄▂▁                        p50 84 ms · p95 340 ms · p99 1.2 s     │
│   0   50  100  250  500  1k  2.5k+     ← explicit bucket bounds          │
└──────────────────────────────────────────────────────────────────────────┘
```

**A histogram is drawn as a histogram**, over its own explicit bucket bounds — not resampled into a
line. Percentiles are computed from the buckets and labelled as estimates, because that is what a
bucketed percentile is.

**Scope note.** This is the largest genuinely new screen in the redesign, and OD4 answered "screen"
without settling what the screen is. The minimum that discharges AC5 is the catalogue plus the
histogram reader; `metrics_summary` and `metrics_exp_histogram` could reasonably be listed-but-not-
drawn in a first pass, and that is a founder call.

---

## 13 · Usage — `/usage` *(Meter)*

**Answers:** what am I using, and will it cost me?
**Does not show:** the invoice — that is billing.

**Reads:** `GET /usage` → `UsageTotals`, `ProjectDayUsage[]` (`settled`), `BillingPeriodUsage`
(`services`, `billed_services`), `PlanSummary`, `PlanPriceSummary` (`included_bytes_per_service`,
`max_billed_services`, `browser_share_percent`).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Usage · 1–13 September                                                   │
│                                                                          │
│ 11 services monitored · 8 billed          ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░  62% of     │
│                                                                          │
│ Spans 2.4M · Logs 880k · Metric points 1.2M · 14.2 GB ingested           │
├──────────────────────────────────────────────────────────────────────────┤
│ 13 Sep  ░░░  provisional          ← settled: false, and says so          │
│ 12 Sep  ▓▓▓▓▓▓▓▓                                                         │
│ 11 Sep  ▓▓▓▓▓▓▓                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**`settled: false` is rendered, not hidden.** Today's counters are provisional; a screen that draws
them identically to a settled day invites a customer to argue about a number that was always going
to move.

**`services` and `billed_services` are both shown.** They differ when `max_billed_services` caps the
count, and a customer who monitors 11 while paying for 8 should be able to see that rather than infer
it.

**Flow B put this screen too late.** "Will this cost me" is a first-day question for the Indie
persona and it is currently filed under account navigation. The onboarding confirmation should link
here directly.

**States.** *Partial* is the default — the current period is always unsettled.

---

## 14–17 · Settings — `/settings/*` *(Form)*

**Reads:** `apikeys` GET · POST · POST `/{id}/revoke` · `projects` full CRUD · `billing` GET ·
`profile` PATCH.

Four screens, one archetype, and the archetype's job here is mostly **danger and irreversibility**:

- **A secret key is shown once.** After creation only a hint remains. The create response is the only
  time the plaintext exists, and the screen must treat that moment as the product surface it is —
  not a toast that can be dismissed before it is copied.
- **Revocation is one-way and idempotent.** There is no un-revoke. The confirm states what stops
  working, not "are you sure".
- **Deleting a project revokes every credential it holds immediately**, because the receiver resolves
  an ingest key without joining `projects`. The confirm must say that — a soft-delete flag alone
  would not stop data arriving, and a customer who thinks deletion is reversible is wrong in a way
  that costs them.
- **Site keys are public by design and secret keys are not.** The two must never be presented as
  variants of one thing; the browser SDK refuses a secret key pasted into a page and treats it as
  leaked.

The *dangerous* state from the archetype does most of the work on these screens, and it is the one
state a Form archetype usually gets wrong.

---

## 18–20 · Gateway — `/login`, `/signup`, `/onboarding` *(Gateway)*

Outside the shell: no project, no navigation, no time range.

**Onboarding is the one that matters.** It is the Indie persona's entire first impression, and
Flow B found it currently ends by dumping a new customer on a raw trace list.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Send your first span                                                     │
│                                                                          │
│  OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com                  │
│  OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_…      [ Copy ]       │
│                                                                          │
│  ◐ Waiting for your first span…                                          │
└──────────────────────────────────────────────────────────────────────────┘
             ↓ on arrival
┌──────────────────────────────────────────────────────────────────────────┐
│ ✓ checkout-api is reporting.  18 spans in the last minute.               │
│   [ See your traces ]   [ Add an uptime check ]   [ What this costs ]     │
└──────────────────────────────────────────────────────────────────────────┘
```

**The confirmation is the destination (IA4)**, not the trace list. It names the service that
arrived, which is the actual answer to "did it work," and offers the two things Flow B showed the
Indie persona needs next — a synthetic check, and the cost question.

**The first-traces carve-out is why this works.** The tail sampler unconditionally keeps a project's
first N traces, so the trace the customer just sent is actually there to be found rather than
sampled away before anyone looks.

**States.** *Loading* is the honest one here — the poll may legitimately run for minutes while a
customer edits a deploy config, and it must not look stalled.

---

## Filters and deltas — added by OD8/OD9 (accepted 2026-09-13)

**RUM's gap is the sharpest in the product.** `device_class` and `route` can group the readings and
cannot narrow them: a customer can see mobile beside desktop but cannot ask for mobile alone. The
Distribution contract requires every breakdown dimension to work as a filter, so both become filters
as well as dimensions.

**Usage** gains a per-project filter; today `ProjectDayUsage` is returned per project and the screen
cannot isolate one.

**Deltas on RUM are the most valuable in the console** — a p75 LCP of 2.8 s means little alone and a
great deal beside "up 40% since Tuesday". Core Web Vitals are exactly the measure a customer tracks
over time rather than in the moment.

## Open questions for this batch

1. **`country`: drop the column, or add GeoIP?** AC4 needs amending either way.
2. **How much of the Metrics screen is in scope?** Catalogue plus histogram reader discharges AC5;
   `metrics_summary` and `metrics_exp_histogram` could be listed-but-not-drawn first.
3. **Does onboarding's confirmation need a backend read**, or is polling the trace list enough once
   it reports the service name rather than a bare non-empty result?
