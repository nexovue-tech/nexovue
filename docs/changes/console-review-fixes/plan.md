# Plan: console-review-fixes

> **Status:** Approved by the founder, 2026-09-20 — "yes start fix them all".

One document: the order of the work and its tasks. Each task names the finding it closes
(`docs/quality/console-review-2026-09-20.md`), the file it changes, and the test that proves it.

## Order

Truth first: the two screens that state something untrue are worth more than every layout finding
put together. Then the phone, which is where a screen is unusable rather than untidy. Then the
column rule, which touches the most files and is the least risky. Copy last, because it is the
cheapest to check and the easiest to re-read once everything else has settled.

## Step 1 — No screen claims more than it knows

*Judgeable:* open Findings on a project with a failing check — the headline names it.

- [x] **T1** (D1) — `features/findings/FindingsScreen.tsx` · the healthy state is healthy only when
  its own evidence agrees: failing checks or open errors change the headline and the tone · proved
  by `FindingsScreen.test.ts`
- [x] **T2** (D2) — `apps/scheduler/src/nexovue_scheduler/alerts/send_test.py` · a test send records
  a `notification_attempts` row, as the outbox does · proved by a live scheduler test
- [x] **T3** (D4) — `features/synthetics/CheckDetailScreen.tsx` · the phase breakdown says which
  runs it covers, and does not narrate a server answering when none did · proved by its test
- [x] **T4** (D5) — `features/logs/LogsScreen.tsx` · the buffer download names what it holds ·
  proved by `LogsScreen.test.ts`

## Step 2 — The console works on a phone

*Judgeable:* every screen at 390px with nothing off-screen and the settings rail out of the way.

- [x] **T5** (D3) — `features/traces/TraceListScreen.tsx` (filter row) · the attribute filter wraps ·
  proved by a width assertion in its test
- [x] **T6** — `shared/settings/SettingsLayout` · the rail collapses below `md` · proved by its test
- [x] **T7** — `features/errors/ErrorsScreen.tsx` · the exception keeps the width on a phone;
  the trend column goes · proved by its test
- [x] **T8** — `app/layout/TopBar.tsx`, `Sidebar.tsx` · the drawer button and the nav rows are big
  enough to hit · proved by a size assertion

## Step 3 — One column rule

*Judgeable:* navigate Alerts → Settings → Platform; the left edge does not move.

- [x] **T9** — `features/alerts/AlertsScreen.tsx`, the settings layout, `features/admin/*` · every
  screen is left-aligned at `px-4`; a screen caps its text measure, not its column
- [x] **T10** (D6) — the chart component · the last x-axis label is not clipped
- [x] **T11** (D7) — `features/traces`, `features/errors` · a numeric column's heading is aligned
  with its values
- [x] **T12** — Overview's worst-services table gets column headings; RUM's card grid and breakdown
  columns stop wrapping values onto two lines; the new-check grid seats five cards; sparklines get
  the gutter every other column has; the waterfall axis uses one unit

## Step 4 — Say the right thing

- [x] **T13** (D8) — `app/routes/route-manifest.ts`, `Sidebar.tsx` · the operator sees the platform
  section and nothing else
- [x] **T14** (D9) — `superadmin/seed.py` · the seeded operator is verified
- [x] **T15** (D10) — the copy table: Infrastructure, the 404, API tokens' plural, Settings →
  General, the two docs callouts, the duration spacing
- [x] **T16** (D11) — `AccountScreen.tsx`, `ApiKeysScreen.tsx` + `SiteKeysSection.tsx` · no page
  asks the same question twice

## Step 5 — Close out

- [x] **T17** — `just check` and the web suite read for counts; a second screen tour at both widths
  to see the fixes; `/review-diff`; the review document marked with what was fixed.

## Questions

None blocking. D12 (React Flow's attribution) and signup's terms link are founder decisions, named
in `change.md` as out of scope.

## Outcome

Built 2026-09-20, in the planned order. What differed from the plan:

- **T9's "one column rule" was applied to three places, not four.** Alerts keeps its `max-w-4xl`
  body and its header is now capped to match it; Settings and the platform screens lost their
  `mx-auto`. The signal screens were already right.
- **Two review findings were withdrawn rather than fixed** — RUM's card grid and the sparkline
  gutter were not defects, which measurement showed and the review now records.
- **Three things were fixed that the plan did not list**, each named in the review's product-gap
  section: API tokens list live tokens above revoked ones, Infrastructure's empty state is two
  sentences and a link, and Metrics names its last column instead of repeating "no chart yet".
- **The top bar drops the project selector and the range picker for the operator** — D8 was about
  the sidebar, and the same argument applies one row up.
- **D2 needed a migration.** The plan said "record the attempt"; doing that without a way to tell a
  test from an alert would have left the screen inferring it from an attempt whose batch matched no
  notification. `0036` adds the column instead.

**Validation** in [status.md](status.md).
