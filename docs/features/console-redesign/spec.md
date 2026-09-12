# Feature: Console redesign

## Status

Proposed

Awaiting founder approval. No design or implementation work begins until this document is approved.

## Business objective

The console shows what nexovue stores, but not in the form the data wants. Fourteen of sixteen
feature areas render text and tables only; two draw a chart. Several stored dimensions are displayed
nowhere, and three tables are written and retained with no reader at all.

This is not a data limitation. `endpoint_stats_1m`, `service_edges_1m` and `web_vitals_1m` are
per-minute rollups carrying counts and quantile state, computed **before** sampling, and the
`ServiceTrend` endpoint already serves bucketed series. **Most screens could show an accurate time
series today with no backend change.** The gap is that `Sparkline` — the component the design system
names as how this product shows a trend — was never built.

The objective is a console where each screen's form follows its data, the visual language stays
deliberately ours rather than a generated default, and both the insight surface and the dashboard
surface are first-class.

## Scope

**All 20 routes**, redesigned in place. No rewrite: the `shared → features → app` import direction,
`shared/api-client.ts`, `app/query-client.ts`, virtualization, tenancy handling and the token layer
are retained. What changes is layout, composition, what each screen shows, and whether it charts.

| Group | Routes |
|---|---|
| Front door | `/` (Findings) |
| Signals | `/services`, `/services/$name`, `/service-map`, `/traces`, `/traces/$traceId`, `/errors`, `/logs` |
| Surfaces | `/rum`, `/synthetics`, `/database`, `/infrastructure` |
| Account | `/usage`, `/settings/api-keys`, `/settings/projects`, `/settings/billing`, `/settings/profile` |
| Outside the shell | `/login`, `/signup`, `/onboarding`, and the password-recovery screens |

Also in scope:

- **`Sparkline`**, and the other components the design system names but never built, where a screen
  needs them: `TimeRangePicker`, `FilterBar`, `VirtualTable`, `KeyValueList`, `StackTrace`,
  `JsonTree`, `SeverityBadge`, `StatusDot`, `Drawer`.
- **Stored dimensions shown nowhere today**: `device_class`, `country`, `navigation_type` on web
  vitals; `span_kind`, `rpc_system`/`rpc_method`/`rpc_status`, `deployment_version`, `links`,
  `events` on spans; `timestamp_source` on logs; `server_peer_kind` on edges.
- **A reader for `metrics_histogram`, `metrics_exp_histogram`, `metrics_summary`**, which are
  written, masked and retained today with no screen.
- **Backend work required by the above** — new or widened API endpoints, and the queries behind
  them. No ingestion, schema or storage change is anticipated; if one proves necessary it is raised
  rather than assumed.

## Out of scope

- The insight engine itself. This specification covers the **Findings screen**, not what produces
  findings. See Open decisions.
- Any capability with no data behind it: session replay, continuous profiling, SLOs, alert
  connectors, case management, RBAC, the public status page.
- A user-switchable "plain vs modern" visual mode. Considered during exploration and rejected: it
  doubles the design surface permanently and defers the decision the product has to make. Display
  density may be revisited separately as a reader preference.
- Migration of the token layer. `packages/ui` and its APCA/WCAG verification are unchanged.

## Functional requirements

**FR1 — Form follows data.** Every screen's presentation is derived from the shape of what it reads:
a per-minute rollup is drawn as a series; a per-row signal is a dense list; a one-row-per-entity
reading is a ranked list carrying its own trend; a graph is a graph.

**FR2 — A number that has history shows it.** Any reading with a rollup behind it carries its trend
inline, not on a separate screen and not in a panel beside it.

**FR3 — Both surfaces are first-class.** The Findings feed is the front door. A dashboard-shaped
overview is reachable, complete, and not treated as a lesser mode.

**FR4 — Every screen answers one question.** Each states, in its own specification section, the
question it answers and what it deliberately does not show.

**FR5 — Six states per screen.** Loading, empty, error, partial, dangerous, success, per
`docs/design/principles.md`.

**FR6 — Stored dimensions are reachable.** Every dimension listed in Scope is filterable or visible
on at least one screen, or is explicitly recorded as deliberately hidden.

**FR7 — Reuse before invention.** A screen uses an existing shared component, or promotes one to
`shared/` when a third feature needs it.

## Non-functional requirements

**NFR1 — Tokens only.** No raw hex, `rgb()`, `hsl()`, or default framework palette name.
`verify-tokens.js` and `verify-contrast.js` continue to pass unchanged.

**NFR2 — The banned list holds.** No uniform rounded-card grids, no gradient hero accents, no icons
carrying meaning alone, no colour as the sole carrier. `docs/design/design-system.md` governs.

**NFR3 — Density is a feature.** Information hierarchy is explicit; a screen may be dense but must
not be crowded. The sentence gets room; the evidence gets packed.

**NFR4 — Usable at phone width.** Every screen, including the trace waterfall and the service map.

**NFR5 — Import direction unchanged.** `shared → features → app`; no feature imports another.

**NFR6 — No regression in read cost.** A redesigned screen must not increase bytes scanned per view.
Promoted typed columns are preferred over JSON attribute lookups on anything filtered.

## Acceptance criteria

- **AC1:** Every one of the 20 routes has an approved wireframe naming its data sources, its
  components, and all six states, before that screen's implementation begins.
- **AC2:** `Sparkline` must exist in `shared/components/` and must be used by at least the services,
  service detail, errors, RUM, synthetics, database, infrastructure and usage screens.
- **AC3:** Every screen reading a rollup must render a time series or sparkline from it. A screen
  that deliberately does not must record why in its wireframe.
- **AC4:** Each of `device_class`, `country`, `navigation_type`, `span_kind`, `rpc_system`,
  `rpc_method`, `rpc_status`, `deployment_version`, `links`, `events`, `timestamp_source` and
  `server_peer_kind` must be visible or filterable on a named screen, or listed in that screen's
  wireframe as deliberately hidden with a reason.
- **AC5:** `metrics_histogram`, `metrics_exp_histogram` and `metrics_summary` must each have a
  reader, or a recorded decision to stop storing them.
- **AC6:** Every screen must render all six states, and each state must be covered by a test.
- **AC7:** A missing or negative reading must render as an em dash on every screen. A real zero must
  be distinguishable from an unknown.
- **AC8:** `just check` must pass with `verify-tokens.js` and `verify-contrast.js` reporting no
  drift, and no default palette name anywhere in `apps/web`.
- **AC9:** Every screen must be usable at 375px width with no horizontal scroll outside a table,
  diagram or code block in its own scroll container.
- **AC10:** `route-manifest.test.ts` must continue to pass: the sidebar must never offer a path the
  router does not serve.
- **AC11:** No screen may import another feature, and no raw colour may appear in any component.
- **AC12:** Each screen's wireframe must state the question it answers and what it deliberately does
  not show.

## Dependencies and constraints

**Recorded decisions this feature contradicts.** The founder decided on 2026-09-12 that insights and
dashboards are both first-class. Three places record the earlier stance and must be updated as part
of this feature, not left to disagree with it:

| Where | What it says today |
|---|---|
| `docs/product/vision.md` Decisions Log | "2026-08 — Insight-first, not dashboard-first" |
| `docs/product/feature-catalog.md` F20 | Dashboards are "an option, never the front door" |
| `apps/web/src/app/routes/route-manifest.ts` | "a dashboard must never become the front door" — in code, with a test |

**Governing documents:** `docs/design/design-system.md`, `docs/design/principles.md`,
`docs/engineering/frontend.md`.

**Data available:** `spans`, `logs`, five metric tables, `web_vitals`; rollups `endpoint_stats_1m`,
`service_edges_1m`, `web_vitals_1m`, `trace_summaries`. Postgres for projects, keys, checks,
integrations, deploy events, usage and billing.

**Constraint:** the Findings screen has no data source. Nothing produces findings today.

## Open decisions

**OD1 — What is on a finding card, given nothing produces findings?** F1's specification describes
the card. That description would become the de facto requirement for an insight engine nobody has
designed. Recommendation: design the shell and the feed's structure, mark the card provisional and
unvalidated, and do not let it silently become the engine's specification.

**OD2 — What is the dashboard surface, concretely?** "Both first-class" is a direction, not a
screen. Options: a per-service overview auto-composed from rollups; a builder (F20); or a fixed
overview page. These differ by an order of magnitude in cost.

**OD3 — Sequencing against the insight engine.** This feature is large and competes directly with
building the differentiator. Whether it ships first, second, or interleaved is a founder decision.

**OD4 — Screen or stop storing** the three unread metric tables. The founder answered "screen"; what
that screen *is* remains open, since no existing screen reads a histogram.

**OD5 — Do the auth and onboarding screens change?** They sit outside the shell and are the first
thing a customer sees. Including them widens the scope; excluding them leaves the first impression
untouched by the redesign.

**OD6 — Display density as a reader preference.** Rejected for this feature. Recorded here so it is
not re-explored from scratch.
