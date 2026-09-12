# Design: Console redesign — the layout system

## Status

Awaiting approval

This document covers what is **common to every screen**: the archetypes, the row anatomy, how a
trend is shown, the density scale, and the shell. Per-screen wireframes follow in batches of three
or four, each reviewed separately. Designing twenty screens before agreeing the system underneath
them would make every one of them a separate argument.

## Context and goal

The specification's finding was that form does not follow data: fourteen of sixteen feature areas
render text and tables, while the rollups behind them carry per-minute counts and quantile state.

The goal of the layout system is that **a screen's shape is decided by its data, not invented per
screen**. Twenty independently designed screens drift; nine archetypes do not.

[user-flows.md](user-flows.md) traces what the two console-using personas actually do. It found that
the Dev's core loop takes four hops, that context is lost at every one of them, and that the loop has
neither a beginning (no alert exists) nor an end (no deploy screen exists). Those findings drive the
information architecture below.

## Proposed design

### 1. Information architecture

Founder decision, 2026-09-12: **merges and splits are allowed; the grouping is not rebuilt from
scratch.** Renaming navigation before a single user reports being lost would be designing against a
guess. Four changes follow from [user-flows.md](user-flows.md), and each is approved individually.

**IA1 — Selection travels between screens.** The flows' clearest finding: a Dev who has chosen
`checkout-api` loses it on every subsequent screen and re-applies the filter by hand. Scope becomes a
property of the session, not of each screen. This is the single highest-value IA change and it
touches every archetype.

**IA2 — `/database` and `/infrastructure` merge into one destination.** Both are thin, both are
polled rather than pushed, and the flows show `/database` is reached *from a trace* far more often
than browsed. One destination with two views, reachable scoped from a span, rather than two
top-level entries that each answer half a question.

**IA3 — Deploy events get markers, not a screen.** `deploy_events` is ingested, stored, and served
by the API today, and **no screen consumes it** — a whole capability invisible. "Did a deploy cause
this?" is the most common real cause and currently unanswerable. The cheapest honest answer is a
marker on every time series plus a list where deploys are the subject, not a new top-level entry
competing with the signals.

**IA4 — The post-onboarding destination changes.** A new customer currently lands on `/traces`, the
most technical screen in the product, when the question they had was "did it work." The destination
becomes a confirmation.

**Not changed:** the Signals / Surfaces / Account grouping, and every other screen's identity. The
option to regroup around the user's job — Investigate / Watch / Operate — was considered and
deferred, not rejected; it is the right conversation once real users report where they get lost.

### 2. Ten archetypes cover twenty-one routes

| Archetype | Data shape it serves | Routes |
|---|---|---|
| **Dashboard** | composed RED/USE panels over an entity or the whole system | `/` (Overview), `/services/$name` |
| **Feed** | ordered statements, newest first | Findings — takes `/` once the insight engine exists |

The duration heatmap does **not** become an eleventh archetype. OD7 resolved it as a conditional
panel inside Dashboard, drawn only where a customer sends OTLP histograms, so it is a panel variant
rather than a shape of screen.
| **Ranked entities** | one row per entity + its rollup | `/services`, `/errors`, `/synthetics`, and the merged database/infrastructure destination (IA2) |
| **Event stream** | high-volume rows, time-ordered | `/logs`, `/traces` |
| **Entity detail** | one entity, its series, its children | `/services/$name`, `/traces/$traceId` |
| **Graph** | nodes and weighted edges | `/service-map` |
| **Distribution** | a measured population | `/rum`, and the histogram-metrics reader |
| **Meter** | consumption against a limit | `/usage` |
| **Form** | control-plane CRUD | `/settings/*` (4) |
| **Gateway** | outside the shell, no project context | `/login`, `/signup`, `/onboarding`, recovery |

**Ranked entities is the highest-leverage archetype** — five screens, a quarter of the product.
Getting its row right is most of the redesign. Overview reuses it rather than inventing a second way
to list services, which is what makes a new screen cost three panels instead of a new design.

### 3. The row is a sentence

The design system's thesis is that the primary object is a sentence and everything else is evidence.
A ranked-entity row applies that literally — it reads left to right as one:

```
●  checkout-api            1.2k/min    4.1% ↑    p95 340 ms    ▁▂▃▅▇▅▃▂
│  │                       │           │         │             │
│  entity, the subject     rate        error     latency       trend, inline
status: absent when healthy                                    (the evidence)
```

Read aloud: *"checkout-api, twelve hundred a minute, four point one percent failing and rising, p95
three hundred forty milliseconds."* The sparkline is the evidence for "and rising" — it sits **in**
the sentence, not in a panel beside it.

Rules that follow:

- **Health is absent, not green.** No status mark on a healthy row. Colour means something needs you.
- **The trend is part of the row, never a separate screen.** This is the specific fix for the
  fourteen screens that show numbers with no history.
- **An unknown is an em dash, a zero is `0`.** Never the same glyph.
- **Ranking is by absolute impact, not rate.** A 50% error rate on four requests ranks below 4% on
  twelve thousand. The services API already ranks worst-first by absolute error count; the row makes
  that visible rather than leaving it implied.

### 4. Context: the delta, and why most numbers carry none

OD8 accepted: a reading with a rollup behind it can show its change against the previous equivalent
window. The rollups already hold that window, so this is a second read, not new storage.

**The delta appears only where the change is significant.** This is the harder half of the decision
and it follows from the same rule as health:

```
●  checkout-api        1.2k/min      4.1% err ↑2.8×      340 ms       ▁▂▃▅▇▅▃▂
   │                   │             │                   │
   unremarkable —      unremarkable  changed, and        unremarkable
   no annotation                     says so
```

Annotating every number would put three arrows on every row of a fifty-row list, and a reader
scanning for what moved would have to read all one hundred and fifty to find out nothing moved. An
absent delta is information: **this is normal.** That is the same argument as a healthy row carrying
no status mark, applied to change instead of state.

Three rules:

- **A delta is a ratio or a percentage, never a bare difference.** "4.1% errors, up 2.8×" is
  readable; "up 2.6 percentage points" is arithmetic the reader has to finish.
- **No prior window, or one too thin to compare, renders no delta** — never zero, never "0%". The
  em-dash rule applied to change.
- **What counts as significant is one threshold defined per measure, not per screen**, so the same
  movement reads the same way everywhere.

### 5. Filters: one contract per archetype

OD9 accepted. Today traces accepts seven filters and logs five, while service map, synthetics,
database, infrastructure, RUM and usage accept none beyond time and limit — an asymmetry invisible
from inside and glaring from outside.

Each archetype declares its filter contract once, and its screens inherit it:

| Archetype | Filter contract |
|---|---|
| **Ranked entities** | a state filter (only unhealthy) · a sort choice · a kind filter where the entity has kinds |
| **Event stream** | free text · entity · a severity or status floor · a magnitude floor |
| **Entity detail** | inherits the parent's selection; no filters of its own |
| **Graph** | a volume floor and a failing-only toggle, because an unfiltered map of fifty services is a hairball |
| **Distribution** | every breakdown dimension must also work as a filter — on RUM today `device_class` and `route` can group but cannot narrow |
| **Meter, Form, Gateway, Dashboard** | none; these are not lists |

**A filter that excludes everything produces the filtered-empty state**, which names the filter and
offers to clear it — never the never-had-data state, which would tell a customer with eleven services
that they have none.

**Selection travelling (IA1) is not a filter.** Scope arrives from the previous screen and is shown
as context, not as a chip the reader has to notice they set.

### 6. Sparkline — the component the design system named and never built

One component, used by at least eight screens (AC2). Deliberately minimal:

- Inline size, aligned to the row's baseline; no axes, no gridlines, no legend, no tooltip.
- Drawn from a rollup, never from raw rows.
- Single `--color-series-1` stroke by default; `--color-critical-solid` when the series it summarises
  is the error series.
- **A fixed window shared across the screen**, so two rows are comparable. A per-row auto-scaled
  sparkline is a lie by omission — it makes a flat service and a spiking one look identical.
- Below a minimum number of buckets it renders nothing rather than a misleading line, and the row
  falls back to the em dash rule.

`uPlot` is already a dependency and draws the two existing charts. Whether a sparkline uses it or is
hand-drawn SVG is an implementation choice for the plan, not a design decision — it is a stroke over
a fixed window either way.

### 7. Density scale

Three row densities, chosen by archetype rather than by user preference:

| Density | Row height | Used by |
|---|---|---|
| **Stream** | tightest | Event stream — logs, traces list |
| **Entity** | medium | Ranked entities |
| **Statement** | loose | Feed, and any screen whose primary object is prose |

This is the reading-product thesis made concrete: the sentence gets room, the evidence gets packed.
A user-switchable density was considered and rejected in the specification; it can return as a
reader preference later without changing this scale.

### 8. The shell

The sidebar's three groups become four, because the front door is no longer a placeholder:

```
  ── (no label)        Overview          ← holds / until the insight engine exists
  ── (no label)        Findings          ← its own entry from the start
  SIGNALS              Services · Service map · Traces · Errors · Logs
  SURFACES             RUM · Synthetics · Infrastructure    ← Database merged in (IA2)
  ACCOUNT              Usage · API keys · Projects · Billing · Profile
```

Fourteen sidebar entries become thirteen. Deploys gains no entry (IA3) — it appears as markers on
every series and as a list within the screen where deploys are the subject.

The dashboard surface's placement is **blocked on OD2** — until it is defined, there is nowhere
correct to put it. Everything else in this document is independent of that answer.

Unchanged: the drawer below `md`, the dark scrim in both themes, the 404 rendering inside the shell,
and the command palette. Those were decided, are built, and the redesign gives no reason to revisit
them.

### 9. Six states, defined per archetype

Defining states nine times instead of twenty is the point of having archetypes. Each archetype
specifies all six once; a screen inherits them and overrides only where its data genuinely differs —
for example, "empty" on `/services` means no telemetry has ever arrived, which is an onboarding
moment, while "empty" on `/logs` usually means the filter is too narrow. Those are different screens
and will be written as such in the wireframes.

## Data flow and interfaces

No change to the transport: `shared/api-client.ts` remains the only caller, `app/query-client.ts`
the only place status codes are interpreted, and imports continue to flow `shared → features → app`.

New reads the archetypes require, to be specified per screen:

- A **rollup series per listed entity** for the ranked-entity row. `/services/trend` already returns
  bucketed series for one service; the row needs the same shape for a page of entities in one
  response, or the screen makes N requests, which it must not.
- A **reader for the three unread metric tables** (AC5) feeding the Distribution archetype.
- **Filters for the twelve stored-but-unshown dimensions** (AC4), which are predicates on existing
  columns rather than new data.

## Data and migration impact

None expected. Every archetype is served by data already stored and already rolled up. If a screen
turns out to need a rollup that does not exist, that is raised as a schema change with its own
migration record rather than absorbed silently.

## Performance and scale

- **The batched-series read is the one real risk.** A ranked list of fifty services each fetching its
  own trend is fifty ClickHouse reads per view. The row's series must arrive with the list.
- Sparklines read rollups, never raw `spans` or `logs`. NFR6 — no screen may increase bytes scanned.
- Virtualization stays wherever it is today; the Event stream archetype requires it.
- A promoted typed column is preferred over a JSON attribute lookup for anything filtered, which is
  what makes the twelve new filters cheap.

## Failure handling

Unchanged and already correct: one interpretation point for status codes, `401` clears the session
centrally, `403` to the route boundary, `429` toasts honouring `Retry-After`, and every user-visible
error carries its correlation id.

What the redesign adds is the **partial** state — some rows have a trend and others do not, because
a rollup is thin or a series is too short. The row renders, the sparkline does not, and the reading
falls back to the em dash. A screen must never hide a row because its evidence is incomplete.

## Security, tenancy, and privacy

No new tenancy surface. Every read goes through the existing chokepoint, and no new ClickHouse
statement is introduced by the layout system itself — only by the per-screen designs, each of which
carries `tenant_id = {tenant_id:String}` as a module-level constant like every other.

One privacy note for the wireframes: `country` and `device_class` on web vitals are stored and
currently shown nowhere. Displaying them is a product choice about what a customer's own dashboard
reveals about their users, not merely a design choice.

## Observability and operations

No change. The console has no telemetry of its own beyond the `traceparent` header the API client
already sends.

## Alternatives and tradeoffs

**Design twenty screens independently.** Rejected: they drift, and each becomes its own argument.
The archetype system is what makes twenty screens one decision repeated rather than twenty.

**Adopt a component library now.** Rejected — it is on the design system's banned list, the reason is
recorded, and the platform supplies what headless libraries existed for. Revisit only if a specific
control proves genuinely hard, never on principle.

**Per-row auto-scaled sparklines.** Rejected above: comparability is the whole value of putting a
trend in a list.

**A dashboard mode toggle.** Rejected in the specification.

**Adopting the `ui-ux-pro-max` skill pack as a design source.** Evaluated 2026-09-12 and rejected as
a source; retained as a *check*. Its `--design-system` mode generates palettes and font pairings that
would not survive `verify-contrast.js`, its suggested type scale assumes a 16px base against this
product's deliberate 13px, and its styling guidance is built on shadcn/Radix, which
`design-system.md` bans and ADR-0032 already decided against. The pack's own instructions say its
results are recommendations that never override repository rules, which is the correct precedence.

Two of its guidelines are adopted as per-screen delivery checks, because both apply here and neither
is recorded anywhere else:

- **Focus must not be obscured** — a sticky column header must never cover a focused row. Directly
  relevant: five redesigned screens will have both.
- **Keyboard order through a virtualized list** — tab order must follow visual order, and a row not
  in the DOM cannot receive focus. Applies to the nine screens already using `useVirtualizer`.

**Charts as panels beside the data**, the conventional observability layout. Rejected as the default:
it is the "fifty dashboards" shape, and the research on operational dashboards finds information
overload the most common failure. Panels remain available to the dashboard surface once OD2 defines
it — as a deliberate surface, not as the default form of every screen.

## Specification impact

None. This design implements the approved specification without amending it.

Two open decisions still block two wireframes, and neither blocks this document:

- **OD1** — what a finding card shows when nothing produces findings. Blocks the Feed wireframe.
- **OD2** — what the dashboard surface concretely is. Blocks its wireframe and its place in the
  sidebar.
