# Design: Dashboards (F20)

## Status

**Awaiting approval.** Proposed 2026-11-02, against [`spec.md`](spec.md), also Proposed. Nothing here is approved, and no
code has been written. The feature's lifecycle status is in [`status.md`](status.md).

**How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present. One
throwaway check was run (route order, below). No test, query, browser or load test was run. Every figure marked *guess*
or *arithmetic* is not a measurement. uPlot's and dnd-kit's facts come from their installed files and npm metadata,
read on 2026-11-02, and neither has been tried here. Changed after two rounds of persona review: see [`status.md`](status.md), *Reviews*.

## Words used here

Those in [`spec.md`](spec.md), and:

| Term | Meaning |
|---|---|
| **Slot** | One place in the limiter's count of requests in flight (`ratelimit/limiter.py:78-87`). The batch keeps the one the middleware took, and claims no more (D43) |
| **Bucket** | One allowance a request is counted in. A signed-in read counts in two: the person's and the organization's. Both cap slots on the telemetry tier (`policy.py:92,99`), so the middleware may wait 5 s in each |
| **Scan key** | What makes two panels want the same read: the statement, the project, the window and the service |
| **Optimistic lock** | Saving only if nobody changed the row since you read it, instead of holding a lock while someone edits |
| **Connector limit** | How many connections the API process holds open to ClickHouse: 8 (`settings.py:209`, `lifespan.py:231-238`) |
| **Rider** | The quota place the middleware already took for the request, which the batch's first scan uses |
| **Permit** | One of the 4 scans a batch may run at once |
| **Leader** | The request whose scan actually reads the store. It pays for it. Others wanting the same scan at that moment are **joiners**, and pay nothing |

## Context and goal

See the specification. Three pieces of existing code decide most of this design:

1. **The limiter counts HTTP requests, not work** (`middleware/ratelimit.py:158-214`). The tier's comment says why that is
   usually fine, and one batch route breaks it.
2. **Every number a panel needs already has a statement, a service function and tests.** F20's main saving is reusing them,
   and its main rule is that it must not grow a second way to compute any number.
3. **`TrendChart` is used across the console** (`shared/components/TrendChart.tsx`). Every change to it must be additive.

## User experience

### Journeys

**A team lead's first dashboard.**
Sidebar → *Dashboards* → the empty list offers *Start from Overview*, *Service health*, *Infrastructure*, then a blank one →
*Start from Overview* opens a draft with real numbers → remove what is not needed, add a panel → *Save*, naming it → the
dashboard. No blank page, and no permanent row before *Save*.

**An on-call engineer at 3 a.m.**
Alert → finding or alert incident → *Dashboards showing this service*, or a link in an incident channel → opens with the sender's project and,
for "these exact times", the sender's window. The top bar matches, and a line says this comes from a link → header says
"production · times in IST" → the "In this window" strip names the deploy and the finding → move the cursor on one chart, all
show 02:14 → drag a range, all panels on screen follow, and browser Back returns → the panel's title link opens the service with the window kept. Three clicks
at most from link to service. Leaving, the console is back on the reader's own project and window.

**A developer on a service.**
`/services/checkout` → *Start a dashboard from this service* → *Service health* draft with the picker on `checkout` →
save → afterwards, one dashboard serves every service by picking. If the team already has one, saving says so and offers
to open it.

**An admin cleaning up.**
List → sort by last opened → *not opened for 30 days* tags → open, see owner → delete, told exactly what goes and that it can
be restored for 30 days.

### What it deliberately is not

The front door stays the sentence-led feed (principles rule 1). A dashboard is reached on purpose, for a person who knows
what they want to watch.

## UI structure

### Screens

| Route | Screen | Owns | Reuses |
|---|---|---|---|
| `/` | `FindingsScreen`, with a real no-telemetry state (section 1) | Everything it owns today, including `SummaryState` | `EmptyState` |
| `/overview` | `OverviewScreen`, unchanged except false comments | | |
| `/dashboards` | `DashboardListScreen` | The list and its states (below); Recently deleted | `DataTable`, extended with sort headers, `EmptyState`, `InlineFailure`, `SkeletonRows`, `useCan` |
| `/dashboards/new` | `DashboardEditScreen` with a draft from `?template=` or a file | Draft; validation; first save | `UnsavedChanges`, `Permitted`, `TextField`, `SelectField` |
| `/dashboards/$dashboardId` | `DashboardScreen`, also full-screen with `?display=1` | Read view; picker; copy link; page-level states; "In this window" strip; detection line; "could not be read" line | `TrendChart`, `DataTable`, `Delta`, `Skeleton`, `InlineFailure`, `Refused`, `SelectField` |
| `/dashboards/$dashboardId/edit` | `DashboardEditScreen` | Save conflict; delete, duplicate, versions and purge, through `DashboardActions`. On founder decision 1's fallback there is no edit screen, and the read view mounts `DashboardActions` instead (plan, TF.1) | Same |

`NavPath` (`route-manifest.ts:18-34`) gains `/overview` and `/dashboards`, and loses `/findings`. `/dashboards/new`,
`/dashboards/$dashboardId` and `.../edit` join the list of screens reached by following something
(`route-manifest.test.ts`). Home becomes Findings (`/`), Overview, Alerts; *Dashboards* joins it.

### What each screen answers

| Screen | The question it answers | What it deliberately does not show or do |
|---|---|---|
| Dashboard view | What is the state of the things my team chose to watch, at this moment or that one, and can I trust it? | No interpretation beyond repeating the feed's words; no edit controls for a viewer; no other dashboards; no per-panel refresh |
| Builder | What will this page show, and how much will it cost to read? | No free placement, no query, no plan talk in its limits, no write until Save |

### Dashboard view

```
┌ Checkout on-call                      production · last 1 hour · times in IST ┐
│ What the checkout team watches.  Owner: Asha · Every minute · Last good 02:15   │
│ Service [ checkout ▾ ]   Runbook ↗    [Copy link ▾] [Export] [Edit] [Full screen]│
│ In this window: [critical] checkout is slower since 02:12 · checkout v2.1 at 02:11 │
│ 6 of 30 panels could not be read                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│ ┌ checkout · Rate ──────┐ ┌ checkout · Errors ────┐ ┌ checkout · Latency ───┐   │
│ │ ╱╲__┆_                │ │ __┆_╱╲                │ │ ░░░┆░░                │   │
│ │ 02:14  412/min        │ │ 02:14  0.2%           │ │ 02:14  p95 88 ms      │   │
│ └───────────────────────┘ └───────────────────────┘ └───────────────────────┘   │
│ ┌ checkout · Worst endpoints ─────────┐ ┌ Note ──────────────────────────────┐  │
│ │ …                                   │ │ Check the queue before restarting. │  │
│ └─────────────────────────────────────┘ └────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

CSS grid of 12 columns from `lg`, with rows computed in the client by a pure function, `layoutRows`: `width` is a share of the row,
and the last panel in each row grows to fill it, so a row never leaves empty columns on the right (the founder's standing rule; AC67).
No container is capped. **Below `xl`,** a time panel of width 3 or 4 spans 6 columns, because at 1024 px with the 208 px sidebar
(`Sidebar.tsx:42`, `w-52`) and 16 px padding the content is about 784 px, so width 3 leaves a plot of at most about 114 px after the 52 px
axis and 24 px overhang (`TrendChart.tsx:24,35`; *arithmetic*). A table of width 3 or 4 spans 6 too, since at 1024 px width 3 is about 196 px, and `DataTable` scrolls rather than squeezes (`DataTable.tsx:12-14`); from `xl` a table takes at least 4. Numbers keep width 3. **Below `lg`,** every panel is full width. `layoutRows` applies this, and the builder's preview uses it too.
**On a phone,** header actions move into one menu, a sticky line keeps the picker, the stale state and the "could not be read" count in reach, and panels are drawn
through an `IntersectionObserver` as they scroll in, since 30 canvases and up to 360 KB is a lot for a phone.

Heights: `small` 96 (number and note panels only: a chart that small has about 48 px of plot after 8 px padding and the 40 px axis, `TrendChart.tsx:33,37,382`; *arithmetic*), `medium` 132 (`CHART_HEIGHT`), `large` 240 px (*guesses*). A table's height follows its rows, up to 20. The stale
banner uses the design system's largest heading size, to read across a room.
**Deliberately not shown:** edit buttons for a viewer; other dashboards; per-panel refresh; sentences that interpret the numbers, which
is the findings feed's job. A list of facts in the strip is not interpretation.

**The "In this window" strip** is plain text from the panel context. Open findings first, as the feed's own sentence with its severity badge (design system: critical comes first and is heaviest; `design-system.md:123-126`), linked to `/findings/$findingId`. This keeps the product's first rule: the reason for the page is its first thing to read. Then closed findings, marked "ended 02:40", then alert incidents, then releases, as
"checkout v2.1 at 02:11" or "checkout v2.1 first seen in telemetry 02:11", collapsing past 5 (*guess*) with a count. It is the legend for rules and shading (as Overview's caption
is, `OverviewScreen.tsx:163-169`), and the keyboard and screen-reader route to them. When it lists a finding, one line says "Findings are judged from their own readings, so a number in a finding can differ slightly from this chart" (FR70), because `service_stats_1m` judges them while charts read `endpoint_stats_1m`. An empty strip with detection not judging says why (below), and never reads as calm.

**Detection's line.** The context carries detection's state from `detection.service.project_status` (`detection/service.py:143`, Postgres only). `detectionHeadlineFor` (`features/findings/headline.ts`) moves to `shared/detection/headline.ts`, since a feature may not import a feature. The types it takes from `findings/api.ts` (`headline.ts:5`) move with it, since `shared/` may not import a feature either. Whenever it has something to say, the line shows above the grid, as it does on the feed, whose comment says why: "detection is not running" matters most when the list looks quiet (`FindingsScreen.tsx:149-152`). An `open_findings` panel then shows `—` and the reason, never 0. So during a trial's first 3 days, a dashboard says "Learning your services' normal. Judging starts on 14 Nov", and no chart reads as all-clear.

**"6 of 30 panels could not be read."** A refused scan lands on the lowest panels, below the fold by design, and the live region is not visible. So one line in the header, and in the phone's sticky line, states it, as a link moving focus to the first such panel. It is hidden when all are read.

**Full screen** (`?display=1`) hides the sidebar and top bar and keeps the project line, age, banners, paused chip and "could not be read" line. "Leave full screen" appears on focus or pointer movement, Tab moves focus to it, and Escape leaves. The page scrolls normally, and docs recommend one screenful for a wall. Docs recommend a dedicated
viewer account: a wall on a member's login holds edit rights in an unattended room, and spends that person's quota.

### The view at 375 px

```
┌──────────────────────────────┐
│ ☰  production · last 1 hour ⌄│  top bar, two rows, about 86 px (arithmetic)
│    Times in IST   Pick a range│
├──────────────────────────────┤
│ Checkout on-call          ⋯  │  ⋯ holds copy link, export, edit, full screen
│ Runbook ↗ wiki.example        │  links stay outside the menu
│ Service [ checkout ▾ ]        │  ┐ sticky while scrolling:
│ Last good 02:15 · 2 of 30 ⚠   │  ┘ picker, age, and the count
│ [critical] checkout is slower │
│ since 02:12 · +2 more ⌄       │
├──────────────────────────────┤
│ checkout · Rate               │
│ ╱╲__┆_                        │
│ 02:14  412/min  [View table]  │
│ …                             │
└──────────────────────────────┘
```

Every touch control below `md` is at least 44 px, as the top bar's and sidebar's are (`TopBar.tsx:39-42`, `Sidebar.tsx:74`): toolbar buttons, "Read again", "View as table", the menu. The builder at 375 px is the same list, with the toolbar's actions under one menu per panel, moves reading Up and Down, and the side sheet full screen:

```
┌──────────────────────────────┐
│ Editing: Checkout on-call     │
│ 12 of 30 · 18 of 40  [Save]   │
├──────────────────────────────┤
│ checkout · Rate      [⋯ Up|Down]│
│ (live panel)                  │
│ [ + Add a panel ]             │
└──────────────────────────────┘
```

### The refresh control's states

| State | Control shows | Pause exists? | Page line | Banner may fire? |
|---|---|---|---|---|
| Live | "Every minute", the tab's interval choice (FR71), "Read now", the age | Yes | Nothing unless a condition holds | Yes, after three missed intervals, at most 5 minutes |
| Paused | Filled "Paused · showing 02:14", on the project's clock | Yes, as Resume | Nothing extra | No. The person asked to stop |
| Fixed window | "Fixed time", no interval | No: a control over nothing is banned (`refresh.ts:141-146`) | "Fixed time: 02:00 to 03:00. Back to live" | No: nothing is expected to change |
| Refresh off | "Refresh off · showing 02:14", "Read now" | No | Nothing extra | No |
| Returned from a hidden tab | As live, "reading…" | Yes | Nothing, until the first read answers | Not until that read answers |
| Full screen | As above, in the page's own line, including the paused chip | Keyboard only | As above | As above |

**The kind must be checked in this order.** `RefreshControl.tsx:45` returns for a declared stream before `:47` checks `live.isAbsolute`. The new kind checks fixed first, or a fixed window would show "Every minute" over a page that never refreshes.

### The refresh control

`RefreshControl` (`app/layout/RefreshControl.tsx:31`) reads the console-wide preference, and offers 5 and 10 seconds. Leaving
it there would show "10 seconds" over a page refreshing each minute, the defect its own comments forbid
(`shared/refresh.ts:138-163`). `shared/refresh.ts` already has a way for a screen to declare what the control means while it
is mounted (`declareStream`, `NOTHING_LIVE`). A third declared kind is added: `{ kind: "fixed-interval", label, lastGoodAt, zone }`. The
control then follows the state table above.

**Its age comes from `lastGoodAt`, not `useNewestData`.** `useNewestData` takes the newest answer of any query in the cache
(`RefreshControl.tsx:157-169`), and a batch with ClickHouse down still answers 200, so the top bar would say "updated just now" over a page of
failures. `lastGoodAt` is the newest panel `read_at` at which at least one scan panel was `ok`. The stale banner fires on that value too, not
on request failure. The age line is hidden on a phone (`:75`), so the page's own "Last good reading 02:14" line appears from the first
failed read at every width. With refresh off: "Refresh off · showing 02:14" at all times. Three intervals are capped at 5 minutes (*guess*),
so a 5-minute refresh does not hide a failure for 15.

**The readings have an age of their own.** Each time panel carries `data_ends_at`. A live page whose newest `data_ends_at` is over 10 minutes old
(*guess*) says "Newest telemetry here is 12 minutes old. The pipeline may be behind, or nothing is sending" (FR54). "Updated" means the answer
arrived, and without this a stalled collector or Kafka looks calm.

### Panel states

The panel owns these. Status 401, 403, 404, 429 and 5xx of the request itself stay central (`app/query-client.ts`,
principles rule 3), with two exceptions on the batch read only, through its query's `meta`: 503 (no retry, no toast) and 429 (FR45's sentence, no toast).

| State | Means | Keeps last line? |
|---|---|---|
| First load | A block skeleton at the panel's height, `aria-hidden`. After 3 s (*guess*): "Still reading. This can take up to 25 seconds" once, at page level | |
| `ok` | The chart, number or table. Sparse series use `showPoints` below the threshold `FindingScreen` uses (`DOTS_BELOW`, moved to `shared/`), since a lone reading is otherwise invisible | |
| `ok`, series combined | "Combined from 37 series" | |
| `ok`, late start | "Data starts at 14 Oct", or "Your plan keeps 30 days" where the context gives a plan's retention | |
| `empty`, `needs_service`, `not_in_project`, `ambiguous`, `series_changed`, `too_many_series`, `window_too_wide`, `over_limit`, `paused_by_operator`, `unknown_kind` | Words by kind (table below) | No |
| `refused` | "Too many reads just now. Reading again in 20 s", while one is scheduled. After the third try, "Read again" and the correlation id | Yes, under the strip, for one interval, at most 5 minutes |
| `timed_out` | "The database took too long to answer", read again, and the correlation id | Same |
| `failed` | "Could not read this", read again, and the correlation id | Same |
| Page-level | See *Page-level states* | |

**The strip.** A filled warning strip inside the panel: "Could not read since 02:14. Line below is old." After one refresh interval, or 5 minutes
with refresh off (*guess*), the line goes and only that sentence stays, and the panel shrinks once. It regrows only after a successful read, so a page does not move under a person reading it when reads flap (`principles.md:60`). **Dimming alone is not used**: it is weak on a dark canvas at night, and the
time label would be the only signal. A change of service, project or window drops kept readings, since the client merges only by the same query key.
**On a fixed window, the line stays with no time limit,** under "Could not read again. This chart is from 02:40 and covers a closed period", because nothing about it became wrong.

**A number with no data shows `—`, never 0** (principles rule 2). A `failed` panel has no `series` key at all, so a drawing mistake cannot paint zeros.
`project_number` over a window where `service_count == 0` is `empty`, because `_to_totals` answers zeros there (`services/service.py:134-151`). `—` means
unknown on number panels, and a real "0 errors" and "0%" are shown as such. Tables keep `formatErrors`' dash for zero only under `ok`, the named exception
(`principles.md:36-49`), and a test covers one page with both.

**A compared number.** The compare read returns `previous_coverage`. With no earlier window, or data starting more than 5% (*guess*) into it: the number,
"Not enough history to compare", no arrow. Panels read `endpoint_stats_1m`, which keeps the plan's derived retention, so Free's 30 days is what partly covers a 30-day window's comparison. `service_stats_1m`'s 35 days does not apply: only detection reads it (`000035_service_stats_1m.up.sql:31-32`).

**Latency is an estimate on every rollup panel.** Every one merges one quantile state (`services/queries.py:47`, `000008_endpoint_stats_1m.up.sql:22`), and ClickHouse's docs call `quantile` non-deterministic, with a reservoir of up to 8,192. Counts are exact. Table headers and readouts mark latency "(est.)", and the docs explain it.

**Words by kind.**

| Kind | `empty` | Other states |
|---|---|---|
| Service trend, number, endpoint trend | "No requests in this window. Last seen 3 hours ago." | `not_in_project`: "No record of checkout in staging". Action, for anyone: choose another project |
| Project trend, number | "Nothing reported in this window." As Overview's `flatAtZero` sentences on a flat-zero metric (`overview-plots.ts:51-76`): "Nothing failed in this window" | |
| `top_services_trend`, tables of services and endpoints | "No services reported in this window." | |
| `node_trend` | "node-3 has not reported in this window." A builder hint says a host name an autoscaler replaces is better served by `top_nodes_trend` | `window_too_wide`: "This chart can show at most 7 days", button "Show the last 7 days" |
| `top_nodes_trend` | "No machine has reported this metric in this window." | Same |
| `check_trend` | "This check has not run in this window." | `not_in_project`: "No check named Login in staging". `ambiguous`: see the viewer's words below |
| `statement_trend`, statements table | "No statement reported in this window." A statement's hash can change when a table is recreated (*from memory of Postgres's documented behaviour, not checked*): the words stay true, and the list counts it as not reading | |
| `error_groups_table` | Service-scoped: reads `service_last_seen`, as `service_trend` does. Seen in the window: "No errors in this window." Good news, said as such. Not seen: "checkout sent nothing in this window. Last seen 3 hours ago." Project-wide: "No errors in this window", under FR54's line when that applies | |
| `web_vitals_table` | "No browser readings in this window." | |
| `dependencies_table` | "No dependencies seen in this window." With `failing_only`: "No dependency failed in this window." | |
| `metric_series` | "This metric has not reported in this window." | `series_changed`: "orders_processed still reports, under different attributes. Choose again". `too_many_series`: "This metric has more than 1,000 series, too many to combine. Choose one series" |
| `open_findings` | With detection judging: the real count, including a real 0. Otherwise `—` and detection's line | |
| Any | | `over_limit`, for an editor: "This dashboard asks for more reads than one page may make. Remove N panels"; for a viewer: "... Ask Asha, or someone who can edit it, to remove panels". `unknown_kind`: "This panel type is no longer available". `paused_by_operator`: "Dashboards paused by your administrator" |

**Words follow the reader's role.** `useCan(EDIT_DASHBOARDS)` chooses. `ambiguous`, for someone who may configure checks: "Two checks in staging are named Login. Rename one"; for anyone else: "Two checks in staging are named Login, so this panel cannot tell which to read". Never-reported page state: setup steps and link for those who can act, "Nobody has connected staging yet. An owner or admin can set it up" for a viewer. A support view reads as a viewer.

**One name for each idea on this screen.**

| Idea | The one word | Replaces |
|---|---|---|
| A window that does not move | "Fixed time", including the top bar's label while a dashboard is mounted | "Pinned range" (`RefreshControl.tsx:55`), "exact times" remains only inside the copy menu's sentence |
| Going back | One "Back to live", on the page line. The top bar's control acts on the same override | `WindowControl.tsx:47-53` alongside the page's |
| Quota | "Too many reads just now" on the panel and for a whole-request 429 here. The batch query's `meta` skips the central toast | "Rate limited by the API." (`query-client.ts:82`) stays on other screens |
| Last success | "Last good reading" | "Last good" |

**A panel's title.** A generated default always names what it shows, with the picker's value resolved ("checkout · Latency"), and links to the service with
project and window kept (FR35). The service name in the title is the click target, because lines on a canvas cannot be, and a click on a plot means drag.

**Page-level states.** Above the grid. **At most one severe line at a time, by this order, and context lines merge into one compact line.** The stale banner is the later, louder form of the outage line, not a second message. Offline stays in the shell (`AppShell.tsx:55-69`), and this page shows nothing further while it holds. Affected panels follow FR31, and shrink only as it says:

| Priority | Condition | Words | Action |
|---|---|---|---|
| Severe 1 | Offline | The shell's strip (`AppShell.tsx`) | |
| Severe 2 | No scan panel read for three intervals (live) | "NOT CURRENT. Last good reading 02:14" at the largest heading size, `role="alert"` | One "Read again" |
| Severe 2 | Every scan panel failed or timed out, or the batch answered 5xx | "Could not read telemetry since 02:14. Notes and findings below are current." | One "Read again", naming what it re-reads, with the correlation id |
| Severe 2 | The browser gave up at 25 s | "The page did not answer within 25 seconds." | "Read again" |
| Severe 3 | Newest telemetry over 10 minutes old (FR54) | "Newest telemetry here is 12 minutes old. The pipeline may be behind, or nothing is sending" | |
| Severe 3 | Detection not judging (FR63) | The feed's own line | |
| Severe 4 | First read past 3 s | "Still reading. This can take up to 25 seconds" | |
| State | Picker unset | "Choose a service to fill this dashboard". Affected panels shrink to a dash | The picker, in the message |
| State | Never reported (`ever_reported` false) | "Nothing has reported to staging yet", and how to start, by role | Setup link |
| State | Reported before, nothing in this window | "Nothing reported in this window. Last seen 3 hours ago" | "Show the last 24 hours" |
| Context | From a link | "Showing staging from this link. You were on production." | "Back to production" |
| Context | Fixed time | "Fixed time: 02:00 to 03:00." | "Back to live" |
| Context | Zoomed by a drag | "Zoomed to 02:10 to 02:20." | "Back to 02:00 to 03:00" |
| Whole page | Project 404 | "This link is for a project you cannot open" | View against mine |
| Whole page | Dashboard 404 | "This dashboard does not exist or is no longer available". "Deleted" only if this tab had already loaded it | Link to the list, restore for an editor |

At 375 x 667 with every condition active, the top of the first panel must stay visible (AC101): one severe line, one context line, the sticky line, then the panel. The top bar is about 86 px there (`TopBar.tsx:28-32`; *arithmetic*).

**Telling the two 404s apart.** `/dashboard-panels/read` and `context` take both ids. The 404 carries a problem type of `dashboard-not-found` or `project-not-found`, registered in
`errors/problems.py` beside `NOT_FOUND`, and an unknown and a foreign id still answer identically (NFR1).

**Screen readers.** Each panel is a `<section>` labelled by an `<h2>` holding its title, as Overview's are (`OverviewScreen.tsx:227-228`), so a reader can jump between 30. One page-level live region says "Dashboard loaded, 28 of 30 panels read", on change only and never per refresh. The stale banner has `role="alert"`. Each panel's
`description` carries its title, service and latest value, not just its name. A counting "in 20 s" is not announced.

### The builder

Same grid, in place, so a person edits what they will see.

```
┌ Editing: Checkout on-call          12 of 30 panels · 18 of 40 reads [Cancel] [Save]┐
│ Title [ … ]  Description [ … ]  Refresh [1 minute ▾]  Range [yours ▾]  Picker [on] │
│ Links [ Runbook · https://… ]                                 [ Versions ]         │
├─────────────────────────────────────────────────────────────────────────────────┤
│ ┌ checkout · Rate      [Earlier|Later|Wider|Narrower|Taller|Shorter|⋯]        ┐    │
│ │ (live panel)                                                               │    │
│ └────────────────────────────────────────────────────────────────────────────┘    │
│ Removed Latency. Undo                                                           │
│ [ + Add a panel ]                                                               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

- **Add a panel** opens a side sheet, a native `<dialog>`: `clamp(28rem, 40vw, 60rem)` wide (*guess*), so it follows the founder's window rule and only its sentences are capped, full screen on a phone, a focus trap and Escape from the platform, and focus returned to the button
  that opened it. `Drawer` is named in the design system and not built (`design-system.md:356`), so T3.3 builds this one, and it stays in `features/dashboards/` until a second screen wants it. First what it shows,
  grouped by question ("How is a service doing", "Machines", "Checks", "Errors", "Text"), then settings. Service, node, check and statement values use a native `<select>` (`SelectField`) with escaped labels and raw values, plus a separate "Type a name" field for a service that has not reported. A `<datalist>` cannot do this: by the HTML standard, an option's value is both what the input shows and what it submits, so it could not show `[U+202E]` and hold the raw name (read from the standard, not run; T3.3 tries phone Safari and Chrome). A chosen value holding escaped characters warns, since `checkout` plus a zero-width character looks identical, and anyone with a browser key can make one. A typed value shows through the escape function beside the field.
  The view's dashboard-wide picker uses the same select. Choices are real values, read from the displayed project.
- **Apply** previews that one panel inside the sheet, since in place may be hidden on a narrow screen, through the preview route with `panels=`, once per Apply and never per keystroke, since each is a real scan. It draws at the panel's real width when that fits, and otherwise says "Shown narrower than on the page".
- **A toolbar per panel, with one tab stop and arrow keys,** following `Tabs` (`design-system.md:347`), so 30 panels are not 240 tab stops. Each action has a name: `aria-label="Move Rate later"`. Less-used
  actions go in a menu on narrow panels. At the ends, the button is `aria-disabled` rather than `disabled`, so focus is not lost. After a move, focus stays on the same action. After add or remove,
  focus follows `EditableRows`' rule (`EditableRows.tsx:58-77`). A polite live region says every change: "Rate moved to position 3 of 12", "Rate is now 6 wide", "Rate removed. Undo available".
- **Remove can be undone** within the draft, without Cancel losing the rest.
- **Below `lg`:** moves read Up and Down, and Width is hidden, since it has no visible effect there.
- **Reached a cap:** "A dashboard holds 30 panels, because each one reads the database. Split it into two." Same for reads: "This dashboard would need 44 reads, and one page may make 40. Remove panels or use the picker." It never mentions a plan.
- **Below `md`:** editing still works, since buttons need no precision. Drag is not offered on touch.
- **States owned:** validation inline and in a summary; unsaved changes (`useUnsavedChanges`); dangerous, for delete, through the shared inline `ConfirmPanel` (`shared/components/ConfirmPanel.tsx`); conflict.
- **Versions** lists the kept versions with who and when. "Restore this version" opens it as a draft, and saving makes a new version. An owner or admin also sees "Remove at once" (FR68).

**The six states of everything the builder opens** (`console-redesign/spec.md`, AC1):

| Surface | Loading | Failed | Empty | Success | Refused or dangerous | Viewer |
|---|---|---|---|---|---|---|
| Panel picker | Skeleton rows | "Could not list services. Type a name instead", with the typed field working | "No services have reported yet. Type a name" | Value chosen, sheet moves to settings | Value with escapes warns | Not reachable |
| Preview | Skeleton at height | "Could not read this", read again, correlation id. The draft is unaffected | The panel's own `empty` words | The panel, and "Applied" | `refused`: its words, and Apply stays available | Not reachable |
| Versions | Skeleton rows | "Could not read the history" and retry | "No earlier versions yet" | "Restored as a draft. Save to keep it" | "Remove at once": `ConfirmPanel` naming the version; admins only | Reads the list, no action |
| Recently deleted | Skeleton rows | "Could not read the list" and retry | "Nothing deleted in the last 30 days" | "Restored *Checkout on-call*" | Title in use: asks for a new one. At the cap: "This organization holds 1,000 dashboards. Delete one first" | Reads the list, no action |
| Import | "Checking the file" | "Could not check the file" and retry | N/A | Draft opens, "Imported, not yet saved" | Reasons in words, nothing opened | Not offered |

### The list

**The question it answers:** which dashboard do I want, and which can go? **It deliberately does not show** panels, numbers or previews.

```
┌ Dashboards                                       [Filter by title…] [+ New] [Import]┐
│ Title ▲           Owner    Panels  Last opened ▾  Last changed                      │
│ Checkout on-call  Asha     12      2 hours ago    3 days ago                        │
│ Old staging       Former…  30      41 days ago · not opened for 30 days   …         │
│                                                        [Recently deleted (2)]       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Sort headers are real buttons with `aria-sort`, an extension of `DataTable` (`DataTable.tsx:15-37` has no sort prop today), proved by a new `shared/components/DataTable.test.ts`, since none exists. The filter is client-side over up to 1,000 rows, meeting the console's filter
rule (`console-redesign/spec.md`, AC15). On a phone, rows show title and last opened, and the rest under them. States: loading; failed; empty for an editor (starts in FR39's order); empty for a viewer, "No dashboards yet. Ask a member to create one", with no action;
no results, with "Clear filter".

**Delete** (`ConfirmPanel`): "*Checkout on-call*, 12 panels, last opened 2 hours ago. It can be restored for 30 days." **Conflict:** "Asha saved this 2 minutes ago", reload or save as a copy, with the draft kept. **Copy link menu:** "Link to these exact times (02:00 to 03:00)" and
"Link that stays live (last hour)", exact times first when the window was dragged or fixed, and a line saying a link shows the service's name. A toast says which was copied. **Export** warns the file holds notes, links, service names and host names. Rule 3 of the principles is met by these, ahead of any React. **Not reading:** a row shows "3 panels not reading" where the last stamped read found `not_in_project`, `series_changed`, `ambiguous`, `over_limit` or `unknown_kind`. Transient states do not count.

### Chart upgrades

All optional props with today's behaviour as the default (AC43).

| Gap (`TrendChart.tsx`) | New prop | Default |
|---|---|---|
| Fixed height (`:21,188,372`) | `height?: number`, read in both the options and the `ResizeObserver` | `CHART_HEIGHT` |
| y axis from 0 (`:395`) | `yRange?: "from-zero" \| "fit"` | `"from-zero"`. `"fit"` is for gauges, with headroom both ways |
| One colour (`:41,426-432`) | `distinctColours?: boolean`, using `--color-series-1…6`, each probed from tokens, since the probe reads one line colour today (`:211,224`) | false. Top-N is capped at 5 lines. `DASHES` has three patterns (`:41`), so the prop adds two, because rule 6 forbids colour alone |
| No value at the cursor (`:377,385`) | `readout?: boolean`, with `live: true`, cursor points, a `value` on every series through `formatValue` (otherwise uPlot prints a raw number, `uPlot.esm.js:3626`), and a zone-labelled time. **uPlot's own legend stays off** (`legend.show: false`), because its labels are mouse handlers that hide or isolate lines on click (`uPlot.esm.js:3259-3279`), and they cannot be links. A React component reads uPlot's `setCursor` hook: one fixed line for a single series; for several, one row per line up to 5, with that height reserved, each name a real link and its value beside it, ordered by current value | false |
| No linked cursor | `syncKey?: string`, passed to uPlot's `cursor.sync.key`, with `filters.pub` stopping mouse-down and mouse-up (below) | absent. uPlot's type file documents this (`uPlot.d.ts:507-520,592`). **Not tried at 30 charts** |
| Findings not named | `spans` and `markers` gain an optional id and name, so hovering or focusing the strip's entry marks it, and the readout names what is at the cursor. `findingSpans` keeps the id (`service-detail.ts:137-150` drops it). Reported markers keep today's dashed rule (`:259`); a `seen` marker uses a dotted one | as today |
| No touch | Under `readout`, on a coarse pointer, a touch handler calls `setCursor`, since uPlot's built file has no touch handling (0 matches for `touchstart`). **Not tried.** If T4.10 finds it unusable, a phone uses "View as table" | off |
| Canvas only (`:214`) | No prop. The panel renders a "View as table" toggle using `DataTable`, opening at full row width, with a column for what the finding or release was at each time | |
| Newest bucket still filling | No prop. The panel drops the marked bucket before drawing, and shows it as "so far" in the readout and table (FR66) | |

**One drag must mean one window.** uPlot replays mouse-down and mouse-up to synced charts (`uPlot.esm.js:5504-5535,5817`), each computes its own selection, and `setSelect` is the hook that calls `onSelectRange` (`TrendChart.tsx:370`). Thirty charts could
give thirty slightly different windows and thirty charged batches. So `cursor.sync.filters.pub` stops publishing those two events. Read from source, not run; T4.10 checks one drag gives one window change, one address change and one batch.

`chartOptions` (`:345`) moves to `trend-chart-options.ts` and is exported, so these can be tested as plain data, since jsdom has
no canvas. T4.3 first exports it unchanged and records its output, so T4.4's test compares against a real baseline and not moved code against itself. A text search of uPlot's built file finds no `innerHTML`, which supports treating
labels as safe text; AC35 tests it in a browser.

## Proposed design

### 1. The front door

`app/router.tsx:243-253`: the route with path `/` takes `FindingsScreen`; a new `overviewRoute` serves `/overview`; the `/findings` list route is deleted, since nexovue is pre-deployment. **One message links to it:** the project-level detection notice,
"detection stopped" and "running again" (`alerts/messages.py:378`, asserted at `test_alert_messages.py:416`). It changes to `{base}/` in the same step, and a new test holds every link the scheduler composes against the console's routes (FR43). `FindingScreen.tsx:75` links back to `/`.
By then the feed carries more. F22's `?case=` search moves with it to `/`, so its filters and its *Back to Assigned to me* link read `/?case=mine`; F4's `list_findings` link (`ai-chatbot/design.md`, *Where each step leads*) changes to `/`; and F3's `/findings/sample` and `/findings/$findingId` stay where they are. FR43's test holds them. `Sidebar.tsx:50` and `AcceptInviteScreen.tsx:48` already go to `/`, and now land correctly. Sidebar's Findings item must also be active on `/findings/$findingId`, since `/` is matched exactly (`Sidebar.tsx:69`) and today's prefix match stops applying. `router.tsx:759`'s "Back to Overview" and the sidebar comment at `:47-49` change.

**A real empty state is needed.** `SummaryState` does not cover a project with no telemetry: `/services/overview` always returns `totals`, with zeros and `service_count: 0` (`services/service.py:141-151`), and `select` passes it on (`findings/api.ts:234`), so `headlineFor` never
reaches "Nothing has reported" (`headline.ts:33`). A new customer would land on "Here is what this project reported", "0 services reporting" and three zeros, the screen the code's own comment warns about. Step 1 treats `service_count === 0` as nothing reporting in this window: hides the readings, says how to start, links setup and suggests widening the window,
as Overview does (`OverviewScreen.tsx:146-152`). **With telemetry and no findings, the feed adds Overview's three charts under the summary, and a prominent link to Overview.** `useProjectSummary` already reads `/services/overview`, whose answer holds `points` (`services/schemas.py:147-155`), and only its `select` keeps `totals` alone, so this costs no scan. `buildOverviewPlots` and its `OverviewPoint` type move from `features/overview/` to `shared/overview-plots.ts`, because `shared/` may not import a feature. While learning, F6's line above it says when judging starts. So the first days of a trial show proof that telemetry arrived, and the sentence still leads.

### 2. Two packages, for a reason the tests create

`test_ratelimit_coverage.py:48-60` decides per **file** whether a router reads ClickHouse, and then judges **every prefix in that
file**. Putting both routers in `dashboards/router.py` would pull `/dashboards` onto the telemetry tier, or fail the test. It
also reads only files named `router.py` (`:38-41`). So:

| Package | Router prefix | Holds | Tier |
|---|---|---|---|
| `nexovue_api/dashboards/` | `/dashboards` | CRUD, versions, restore, purge, validate and the panel context, Postgres only. `router.py`, `service.py`, `queries.py`, `schemas.py`, `definition.py` (the validator), `compile.py`, `templates.py` | Default |
| `nexovue_api/dashboardpanels/` | `/dashboard-panels` | The batch read: `GET /read`, `POST /preview`. `router.py`, `service.py`, `scans.py`, `shared_reads.py`, `schemas.py` | Telemetry, by adding `f"{API_PREFIX}/dashboard-panels"` to `TELEMETRY_PATHS` |

**Direction:** `dashboardpanels` imports from `dashboards` (the definition and the compiler), never the reverse, because saving must count worst-case scans (FR7) without importing a module that reads ClickHouse. The `dashboards` router must not name `TenantRepository`, so the coverage test keeps it on the default tier.

**The batch has its own prefix, on purpose.** The first design used `/dashboards/panels:read`, which needed the panels router included before `/dashboards/{id}`, or every read answered 422 (run in a throwaway app). It would have been the first colon path in the repository, and its safety rested on one line's order in `main.py`. A prefix outside `/dashboards` removes the trap, needs no ordering test, and still satisfies the coverage test (`test_ratelimit_coverage.py:30,48-60`). The foundation allows a rename if the reason is given (`phase-2-foundation.md:422,479`). Inside `dashboards/router.py`, `/templates` and `/validate` are declared before `/{id}`, and AC24 goes through the real app.

### 3. The definition

**Two shapes, named.** The stored **definition** has no title or description, which are columns. A **dashboard document**, used by a file, by `POST` and by `PUT`, wraps it:

```json
{
  "title": "Checkout on-call",
  "description": "What the checkout team watches.",
  "definition": {
    "schema_version": 1,
    "refresh_seconds": 60,
    "default_range": null,
    "service_picker": true,
    "links": [{"label": "Runbook", "url": "https://wiki.example/checkout"}],
    "panels": [
      {"id": "p_01", "kind": "service_trend", "title": "Latency", "width": 4, "height": "medium",
       "settings": {"service": {"picker": true}, "signal": "latency"}},
      {"id": "p_02", "kind": "check_trend", "width": 4, "height": "medium",
       "settings": {"check": "Login", "signal": "uptime"}}
    ]
  }
}
```

`dashboards/definition.py` is one Pydantic discriminated union on `kind`, with `extra="forbid"` throughout, and is the only way a
definition enters. The validator takes the document, and its allowed values are the spec's table, which parametrizes its test. A time panel saved `small` is refused. Titles, labels and panel titles refuse control and invisible characters and NUL, since Postgres JSONB cannot store NUL and an invisible character defeats the `lower(title)` unique index. **Service settings are either a literal name (at most 255 characters, matching `metrics/router.py`'s bound) or
`{"picker": true}`**, and a picker reference requires `service_picker: true`. Panel ids are generated by the client, unique within
the definition, and matched against a short pattern. **Link addresses must parse as `https`**, with no user name or password and no query key matching the masking rule's credential markers (`mask/rules.go`), so `javascript:` and `https://wiki.example@evil.test/` are refused here and on import. The console draws them with `rel="noopener noreferrer"`, with the host as text beside the label, because a label alone tells a viewer at 3 a.m. nothing about where it goes.
**`default_range`** is checked against one constant, `dashboards/ranges.py`, which `/dashboard-panels/read`'s `range` also uses, held by a test to `shared/selection.ts`'s ids plus `30d`.
**Saving compiles the draft and counts its worst case** (`compile.py`, treating each picker reference as a distinct service, and a chosen-series metric panel as 2) and refuses above `MAX_SCANS_PER_READ`, naming the count. It also writes `panel_count`, `service_names` and `uses_picker` from the compiled definition, in the same transaction.

**Checks and SLOs are stored by name**, resolved in the displayed project at read time. So one dashboard works on staging and production (D5), and a file holds no ids (FR42). Check names are not unique within a project (`models.py`, `Check` has no such constraint), so two matches read `ambiguous`. F19's SLO names are unique per project (`slo-tracking/design.md`, *Data and migration impact*). A statement is its hash and server, since no statement text exists (`dbstats/queries.py:13-18`), with an editable label so a person can name it.

**Reading a stored definition is more forgiving than writing one.** Read parses per panel: an unknown kind or an invalid panel
becomes `unknown_kind` for that panel, and the rest of the page draws (FR8). A stored definition over a lowered scan cap gives `over_limit` for the panels past it, since a retry would not help. Write refuses the whole document (FR7). Versions: a
`schema_version` above the code's is refused on import, and stored documents are upgraded by a plain function at read time when
version 2 ever exists. Nothing here builds that function now.

### 4. How one panel becomes a read

`dashboards/compile.py` maps each kind's settings to two things: **pre-reads**, Postgres lookups run in sequence in step 2, and **`ScanKey`s**, frozen dataclasses naming a reader and its
arguments. `dashboardpanels/scans.py` holds one entry per reader, each calling an existing service function with only a repository. A compare is a second `ScanKey` over the previous window. So a kind that needs both, such as `check_trend` (find the check by name), declares a pre-read rather than being a special case, and its scan count is known at save. F19's `slo_budget` is a pre-read alone, with no scan: F19's budget function reads Postgres only, including the rate stored at its last evaluated minute (`slo-tracking/design.md`, *API*).

| Reader | Calls | Statement | Source |
|---|---|---|---|
| `project_trend` | `services.service` (extracted half of `project_overview`) | `OVERVIEW_TREND_SQL` (`services/queries.py:247`) | Rollup |
| `project_totals` | Extracted other half | `OVERVIEW_SQL` (`:238`) | Rollup |
| `service_trend` | `services.service.service_trend` with a name | `TREND_SQL` (`:147`) | Rollup |
| `service_totals` | `services.service.get_service` (`services/service.py:220`) | `services_sql` | Rollup |
| `top_services_trend` | New function | New `TOP_SERVICES_TREND_SQL` | Rollup |
| `endpoint_trend` | New function | New `ENDPOINT_TREND_SQL`, bound to one service and endpoint | Rollup (`endpoint_stats_1m`) |
| `services_ranked` | `services.service.list_services` | `services_sql` | Rollup |
| `endpoints_ranked` | `services.service.list_endpoints` | `endpoints_sql` | Rollup |
| `dependencies` | `servicemap.service.get_service_map` | | Rollup (`service_edges_1m`) |
| `web_vitals` | `rum.service.summarize_web_vitals` | | Rollup (`web_vitals_1m`) |
| `node_trend` | New `infrastructure.service.node_trend` | New `NODE_TREND_SQL`, the same body as `NODE_TRENDS_SQL` bound to host, container, reporter, instance and metric | Raw (`metrics_gauge`, `metrics_sum`) |
| `top_nodes_trend` | New function | New `TOP_NODES_TREND_SQL`, ranking in the store | Raw |
| `check_trend` | New `synthetics.service.check_trend` | New `CHECK_TREND_SQL`: `CHECK_TRENDS_SQL`'s body bound to one `check_id`, resolved in the pre-read. `check_trends` reads every check in the project up to 40,000 rows (`synthetics/service.py:47,67,113-132`, `queries.py:190-215`), so it would stop paying for one line only by being shared, and a project with many checks would pass the 2,000-row keep limit | Raw (`metrics_gauge`). T5.7 measures 30 days before that exception stays |
| `statement_trend` | New `dbstats.service.statement_trend` | New statement, the body of `STATEMENT_TRENDS_SQL` (`dbstats/queries.py:130`) bound to one hash and server | Raw (`metrics_gauge`) |
| `error_groups` | `errorgroups.service.list_error_groups` | | Raw (`logs`, `spans`) |
| `slow_statements` | `dbstats.service.slow_statements` | | Raw (`metrics_gauge`) |

`open_findings` and `note` have no reader: the client draws them from the panel context and the definition, so they survive any ClickHouse failure.

**`project_overview` needs splitting.** It runs two statements back to back (`services/service.py:117-120`), so reusing it whole
would run two scans whenever only one is wanted. It splits into `project_totals` and `project_trend`, and stays as a thin function composing the two, so its route and direct callers in `test_services.py` and `test_services_live.py` do not change.

**AC14 is checked per kind, and never assumed.** There is one statement and one row-to-model step for each kind with a matching route. New statements have no route, and the spec's AC14 lists how each is checked.

**`TOP_SERVICES_TREND_SQL`.** `service_trend` with no name returns **every** service and has no `LIMIT` (`services/queries.py:147`).
Trimming afterward is the mistake that module warns about (`:61-65`). So the statement ranks in the store: `TREND_SQL`'s body plus
`AND service_name IN (SELECT service_name … GROUP BY service_name ORDER BY … LIMIT {limit:UInt32})`. **The inner `SELECT` repeats the tenant and project predicates** in one literal, as the structure test reads literals (`metrics/queries.py:12-15`). Without the project, it would pick staging's noisy services as production's top 5 under a production header, and `binds_tenant` would still pass. `TOP_NODES_TREND_SQL` follows the same shape. F20's statements join a named list in `test_tenant_chokepoint_structure.py`, next to `PROJECT_BOUND_PACKAGE` (`:116`), and each is checked for the project predicate in each `SELECT` (AC85). Reused statements are read in T5.2 and join that list too.

**A node panel must not use the fleet read.** `NODE_TRENDS_SQL` ends `ORDER BY host, … LIMIT {max_series}` (`infrastructure/queries.py:204-218`), and that limit applies to one row per metric per bucket, not to series, despite the name `MAX_SERIES` (`infrastructure/service.py:63-68`). At 60 buckets, 50,000 rows is about 833 series (*arithmetic*), cut alphabetically by host. A panel for a host late in the alphabet would get no rows and read `empty`, a false reading against BR5. So a node panel gets its own statement, as `trend_sql` does for one service (`services/service.py:66-68`), and its scan key is per node and metric. This also stops each panel paying for the whole fleet. The existing Infrastructure screen's silent cut is a separate bug record, not absorbed here.

**The picker.** The value arrives as a query parameter, bounded like `MetricName`, and is bound in the statement, never spliced. An
unset picker gives `needs_service` and no scan.

**References to Postgres rows are re-read at read time.** A check is looked up by `org_id`, `project_id` and name. A miss is `not_in_project`, identical whether it exists elsewhere or nowhere. A panel's definition can say anything; only the session decides the
tenant (`services/router.py:3-9`).

**`not_in_project` and `empty`.** Step 2 pre-reads `service_last_seen` for all services the definition names, by `org_id`, `project_id` and `service_name = ANY`, bounded by 30 panels, written by the usage meter with `GREATEST` (`models.py:1831`, `usage/postgres.go:166-175`). After scanning, the state comes from that map: an empty result and no row is `not_in_project`; a row is `empty`, with `last_seen_at`. No lookup runs after a scan. Read from the code: the meter writes a row for every service in each flush, joined to its project. A just-started service with no rows yet, or any service while the meter is stopped (`worker/run.go:291,307-311`), reads `not_in_project`, so its words say only "No record of checkout in staging". A node has no such table, so an absent node is plain `empty`, and project-wide panels have nothing to look up.

**`series_changed`.** A chosen `series_hash` that returns no rows gets one further charged read: does this metric report under any other hash in this window? If yes, `series_changed`, since adding one attribute changes the hash (`000003_metrics_sum.up.sql:27`). So a chosen-series panel counts as 2 at save.

### 5. The batch read, step by step

`GET /api/v1/dashboard-panels/read?dashboard_id=&project_id=&range=&from=&to=&service=&panels=`
`POST /api/v1/dashboard-panels/preview` with `{project_id, range, from, to, service, panels, definition}`, gated by `MayEditDashboards`

`range` names a live window, and the server rounds its end up to the next minute (the client rounds to 10 s, `selection.ts:40,260`), so people on one dashboard ask for one window. `from` and `to` name a fixed one. `panels` limits the read to named panels, used by catch-up, a panel's own "Read again" and Apply. Over the wire, `/services/overview`-style `from` and `to` stay supported.

**The route names `TenantRepository`, as the coverage test requires, so with no ClickHouse client yet it answers 503 before the handler runs** (`deps.py:83-93`). That is accepted, because the panel context is a separate route and still answers (below). The console's client would retry that 503 twice and raise a critical toast (`query-client.ts`, `shouldRetry`, `handleApiFailure`), repeating on a wall display, so the batch query sets `retry: false` on 503 and a `meta` option that makes `handleApiFailure` skip the toast. The page-level state is then the only message (AC61).

1. **Who.** `CurrentIdentity`, so a token is refused (`auth/dependencies.py:192-200`). Support view passes the GET and is refused the
   POST, by its existing rule (`:145`).
2. **Read Postgres first, in sequence.** Dashboard by `org_id`, project through `require_project`, every kind's pre-reads (check rows by name, `service_last_seen` for the named services, F19's status) and
   `error_groups`' deploy history. **All of it happens before any scan starts, because an `AsyncSession` must not be shared between
   concurrent tasks**, which `asyncio.gather` over scans would otherwise risk. Scans receive the repository and no session.
3. **Stamp last opened.** If `last_viewed_at` is older than an hour and this is not a support view (`identity.impersonation is None`),
   set it. The row is already in hand, so no read is added. Such a read also records `panels_not_reading` in one short write after step 7, so at most two writes an hour per dashboard.
4. **Commit.** The request session is otherwise held open until the end (`deps.py:63-73`). A batch may take many seconds, and it must not
   pin a Postgres connection while it waits on ClickHouse.
5. **Compile.** Validate each panel, compute `ScanKey`s, refuse raw panels over their reader's window limit without scanning, and merge. Keys past `MAX_SCANS_PER_READ` give `over_limit`, with no scan.
6. **Run scans in layout order,** each taking a batch permit. `shared_reads.py` then, in this order: uses a kept answer if one exists; joins a read in flight; otherwise this request becomes the leader, taking the process's cap and its organization's share (a wait of at most 5 s, then `refused`), checking the store again after that wait, then `ScanCharge`, and only then creating the in-flight entry. So a refusal is never shared. The whole batch runs under a deadline counted from the request's arrival, `PANEL_READ_DEADLINE_SECONDS = 20` (*guess*). Before starting each scan, `request.is_disconnected()` is checked, so an abandoned read starts nothing more, and **with under 1 s left no scan starts, and it reads `timed_out`.** Each statement runs through
   `repository.bounded_by(SHARED_READ_STATEMENT_SECONDS)` (section 6), a fixed 15 s (*guess*), since one reader's remaining time must not limit what others share. A panel whose scan is running or not started at its reader's deadline is `timed_out`. Refused keeps its meaning: a real quota or cap refusal, with a real wait.
7. **Assemble.** Each panel gets its state, values, `read_at`, `data_starts_at`, `data_ends_at`, and the response carries `correlation_id` (`scope["state"]`, `middleware/correlation.py:43`), so a 200 can show one. Its time budget is below.

### The panel context

`GET /api/v1/dashboards/{id}/context?project_id=&range=&from=&to=`: Postgres only, on the default tier, gated by `CurrentIdentity`. It answers the project's name, time zone and plan retention (null when self-hosted, `nexovue_platform/deployment.py`), the window, detection's state, whether the project has ever reported and its last-seen time (`service_last_seen`), markers (`markers_in_window`, cap 500), each service's coverage (`coverage_of`), findings with severity, alert incidents, and open-finding counts. **It does not name `TenantRepository`, so it answers whatever ClickHouse is doing.** It refreshes on the dashboard's interval. So the header, notes, open findings and strip draw before scans finish, and through an outage, including the cold case where the batch answers 503. The console treats that 503 as every scan panel failed, and shows the page-level state.

### The time budget

The console aborts every request at 20 s unless told otherwise (`api-client.ts:17,159`). An abort throws away the partial answer, and does not stop the server. **Adding up constants does not work.** The middleware waits up to 5 s in **each** bucket (`middleware/ratelimit.py:96`, a fresh deadline per call, and `:195-205` loops over both), and the Postgres pool may wait 10 s (`settings.py:201`). So the first design's 5 + 15 + 5 could really be 10 + 10 + 15, with nothing left. Instead, one deadline is counted from arrival, and every wait shortens what scans get:

| Part | Seconds |
|---|---|
| From the request's arrival (recorded by the middleware in `scope["state"]`, before any wait): middleware slot waits, Postgres pre-reads, process-cap waits and scans, all inside `PANEL_READ_DEADLINE_SECONDS` | 20 (*guess*) |
| Margin for assembling and the network | 5 (*guess*) |
| **`timeoutMs` on the batch call** | **25** |

A test asserts the two add up to `PANEL_READ_CLIENT_TIMEOUT_SECONDS`, and a web test asserts the batch call passes that value. Neither is the proof. AC84 is, in two cases. Each slot frees just before its 5 s wait ends: the middleware waits about 10 s, and the answer arrives with partial panels before the client's timeout. A bucket stays full: the middleware answers 429 when that bucket's wait ends and the handler never runs (`middleware/ratelimit.py:96-102,199-209`), so the page shows FR45's sentence. If limits are off, the middleware still records arrival. If it ever gives up anyway, the page has its own state.

### 6. Charging what really runs

**`ratelimit/charge.py` (new).** `Counted` moves here from `middleware/ratelimit.py`. The middleware stores the `counted` list it already built (`:148-156`) and the arrival time in `scope["state"]`, beside the correlation id, and `ScanCharge` reads them. So route and middleware charge the same keys. A handler holding only `Identity` has no `Session` and no plan multiplier, and recomputing them could produce different buckets. `counted` absent means limits are off, which also implements AC60 with no second check. `_wait_for_slot` and `_per_person` stay where they are, and `test_ratelimit_callers.py` stays unchanged.

```python
class ScanCharge:
    def __init__(self, limiter, counted: list[Counted] | None): ...
    async def claim(self) -> Refused | None: ...  # rider first, else one quota place in each bucket
```

| Rule | Why |
|---|---|
| The first scan rides on the request's own quota place | The middleware already paid for one |
| Every later scan spends one quota place in **each** bucket, person first | `_limit` does the same (`middleware/ratelimit.py:168-177`). This is what BR6 requires |
| No later scan claims a slot (D43) | At one process, the 4 permits and the cap of 5 both bind before the person's 8, and the organization's 32 never does (`limiter.py`, `max_concurrent // instances`). Slots would add the rider rule, moving `_wait_for_slot`, a second refusal path and a third wait, for one case: three processes, which is unsupported. They come back only if T5.7 finds what the process cap misses. The request keeps the middleware's own slot |
| A refusal stops only that scan | Trap 2: never fail as one |
| `counted` absent means no charge | The off switch is the middleware's `enabled` (`middleware/stack.py:27`), and the lifespan builds a limiter regardless (`lifespan.py:258`). Without this, limits off would leave reads unlimited and panels `refused` |
| A scan is charged when it starts, whether it succeeds or not | It cost the store either way |
| A kept answer, or a joined one, charges nothing | It cost the store nothing (BR6) |

**Why 4 at once, not 8.** The whole process has **8 connections to ClickHouse** (`settings.py:209`), shared by every tenant. *4 is a guess*, and FR33's measurement sets it.


**Every limit here is per process, and an install may run several** (`deploy/compose/nexovue.yaml:299,326`; `.env.replica` sets 2). The limiter divides by `API_INSTANCES` (`limiter.py:132,146`). So a batch sizes itself from the share, and this design is sized for **`LARGEST_SUPPORTED_API_INSTANCES = 2`**, which a test reads.

| Limit | At 1 process | At 2 | At 3 | One 30-panel dashboard at 1 minute |
|---|---|---|---|---|
| Person's quota share | 180/min | 90 | 60 | At most 40 scans (*arithmetic*), since the cap is refused at save. Merging and kept answers lower it |
| Organization's quota share | 600/min | 300 | 200 | 15 such screens with nothing shared. Kept answers change that (below) |
| Person's requests in flight (the middleware's slot) | 8 | 4 | 2 | One batch takes one |
| Scans a batch runs at once | 4 | 4 | 4 | Each process's cap holds the total |
| Organization's requests in flight | 32 | 16 | 10 | Does not bind on scans |
| Dashboard scans per process | 5 | 5 | 5 | 3 for one organization, where more than one can exist |

`MAX_SCANS_PER_READ = 40` (*guess*). **It does not hold 30 comparing numbers (60 scans), and FR7 refuses that dashboard at save.** Above it, a stored one gets `over_limit`. A new test asserts
`MAX_SCANS_PER_READ * 60 / REFRESH_FLOOR_SECONDS <= TELEMETRY_TIER.quota.limit // LARGEST_SUPPORTED_API_INSTANCES`, which replaces `LARGEST_SCREEN_FANOUT`'s logic for this
screen: at the floor of 30 s, 80 ≤ 90. `LARGEST_SCREEN_FANOUT` stays as it is: a dashboard's request count is 1. **At 3 or more processes that test fails, on purpose,** and the caps must be reconsidered.

### Protecting ClickHouse across batches

`PANEL_SCANS_AT_ONCE` holds one batch. Two wall displays under one login reach all 8 connections. So one `asyncio.Semaphore` per API process covers all dashboard scans, `DASHBOARD_SCANS_PER_PROCESS = 5` of 8 (*guess*), and a smaller one for raw readers, `RAW_DASHBOARD_SCANS_PER_PROCESS = 2` (*guess*). A wait is bounded by 5 s, then `refused`, with a wait time. A leader's task holds its place until its statement ends, however its requester fares, so no load goes uncounted.

**A share per organization.** Without one, one organization's displays can hold all 2 raw places and all 5 scan places for up to 15 s each, and another's panels are refused, wrongly, during its incident. An organization's in-flight limit of 32 is far above these caps, so it never helps. Inside each semaphore, a counter holds one organization to 3 of 5 and 1 of 2 (*guesses*). It applies where more than one organization can exist: `NEXOVUE_DEPLOYMENT=hosted` (`deployment.py`) or `SIGNUP_ENABLED`. Elsewhere, the share is the whole cap, so a self-hosted install loses nothing.

**A bound outside the API.** These caps protect the API's connections. They do not bound what the design's own arithmetic says is dangerous. About 5 reads at 1 GiB fill the server, and 1 GiB applies to every read (`tenant_statements.py:44`), so at two processes dashboards alone may run 10, plus ordinary screens with no cap. The API and the scheduler share one ClickHouse account (`nexovue.yaml:291,360`), and `reader.xml` sets neither of the limits below. So:

- New `deploy/clickhouse/users.d/api-reader.xml`: account `nexovue_api_reader`, a profile with the reader's constraints plus `max_memory_usage_for_user` of about 2.4 GiB (*guess*, half the 4.8 GB cap) and `max_concurrent_queries_for_user` of 12 (*guess*, below the 16 connections at two processes).
- The API's `CLICKHOUSE_DSN` moves to it. The scheduler stays on `nexovue_reader`, so its alert and detection reads sit outside this bound. **All API screens share the bound, not only dashboards:** that is the point, and a screen's read may fail at the limit, which T5.7 looks for.
- Both setting names were read from ClickHouse's `src/Core/Settings.cpp` on 2026-11-02, on the master branch, not the pinned 26.3 tag, and neither has been run here. T5.7 tests the bound, with inserts and the detection sweep alongside.
- The errors, from `ErrorCodes.cpp` on that date: `TOO_MANY_SIMULTANEOUS_QUERIES` (202) maps to `refused`, `TIMEOUT_EXCEEDED` (159) to `timed_out`, and `MEMORY_LIMIT_EXCEEDED` (241) to `failed`, ahead of the broad catch. How `clickhouse_connect` surfaces the code is read in T5.6b.
- A lower `priority` for the API was asked for and is not adopted yet (D45). `Settings.cpp`: "1 - the highest, higher value - lower priority; 0 - do not use priorities", so it needs the scheduler's reads to carry one too, and what pausing does to held memory is untested.

**An off switch.** `dashboard_scans_enabled` (default true), a setting: false means no scan runs, every scan panel reads `paused_by_operator`, and the context keeps answering. So an operator who sees dashboards hurting alerts can stop them without touching the alerts.

**A cancelled scan does not free ClickHouse unless told to.** A search of `apps`, `packages` and `deploy` finds no setting that cancels a read on disconnect, so a read abandoned at the deadline keeps running to its 30 s limit (`tenant_statements.py:47-53`), while its slot is free and the next refresh starts more. Two changes:

1. `TenantScopedRepository.bounded_by(seconds)` returns a repository for the same tenant, whose reads send `lowered_read_settings(max_execution_time=seconds)` (`tenant_statements.py`), which needs a settings argument on the internal read (`data/clickhouse/base.py:77-85`). No new construction site: it copies itself. This is the guarantee.
2. `bounded_by`'s repository, and only it, also sends `cancel_http_readonly_queries_on_client_close=1`, merged in `base.py` beside its settings, so an abandoned read also stops early. **`READ_SETTINGS` does not change:** the scheduler's alert and detection reads derive from it (`alerts/telemetry.py`, `detection/store.py:53`), and `lowered_read_settings` refuses any name that is not a bound (`tenant_statements.py:75-85`). The setting's name and meaning were read from `Settings.cpp` (default false; "Cancels HTTP read-only queries (e.g. SELECT) when a client closes the connection"), and it has not been run. A live test cancels a slow read and checks `system.processes`. If it does not work, drop it; the fixed limit stands.

**Whether the server cancels a handler on disconnect was not verified.** So nothing relies on it: `is_disconnected` is checked between scans, and AC18 records what happens through a real disconnecting client.

**Under 1 s, and ClickHouse's own timeout.** `lowered_read_settings` refuses 0 (`:75-85`), and the reader profile's minimum is 1 (`reader.xml:29-32`), so a limit computed from a shrinking remainder could raise and read `failed`, and ClickHouse's timeout would often arrive with the deadline and also read `failed`, breaking AC19. Hence a fixed limit, no scan started with under 1 s left, and `TIMEOUT_EXCEEDED` mapped to `timed_out` before the broad catch. A statement can then outlive its reader by up to the fixed limit, if the cancel setting does not work (AC63).

### Sharing a read between people

`dashboardpanels/shared_reads.py` keeps each scan's answer in process memory for `SHARED_READ_SECONDS = 30` (*guess*), and lets concurrent misses join the read in flight. Without it, 30 people at 30 scans a minute is 900 against the organization's 600.

- **Key:** tenant, project, reader, its arguments, and the exact window. So it cannot cross a tenant or project, and it runs after the route has checked who may read.
- **Bounded by rows, not entries.** 2,000 entries of 5,000 rows would be 10 million Python dicts (`data/clickhouse/base.py:85`) inside an 832 MB container (`nexovue.yaml:329`): at a guessed 100 bytes a row, about 1 GB, and the kernel would kill the API process, taking every route with it. So at most `SHARED_READ_MAX_ROWS = 100_000` rows retained in total, 25,000 for one organization, and an answer over 2,000 rows is not kept (*guesses*; 100 bytes a row is not a measurement). Entries are evicted on expiry as well as by least recent use. Only `ok` answers are kept, never a failure. A lost entry costs one read.
- **Owned by a task, not a request.** One person's disconnect does not fail the others.
- **Honest:** a panel's `read_at` is when the store was really read, and the page's age shows that.
- **Per process.** At two processes, the store does at most twice the work. No Redis (ADR-020).

**The rules for a joined read, which AC86 to AC89 test.**

| Case | What happens |
|---|---|
| A miss | Only a request holding its permit, its process-cap place and its charge creates the in-flight entry. So a refusal comes before the entry exists, and cannot be shared |
| Refused on quota or the cap | That panel is `refused`. Nothing is created. A joiner never inherits it, since it never joined |
| Ten misses at once | One statement, one charge. Others re-check after waiting for the cap, and join |
| Statement limit | Fixed, `SHARED_READ_STATEMENT_SECONDS = 15` (*guess*), not one requester's remainder. So a leader at second 14 does not hand a joiner at second 2 a timeout |
| A reader's deadline passes | That reader's panel is `timed_out`. The task carries on for those still waiting |
| The leader disconnects | The task carries on, if anyone is waiting. With no reader left, it is cancelled, which closes its connection and, if the setting works, stops the statement |
| The read fails or times out | The joiners see it. Each runs its own single attempt, charged to it, if at least 1 s remains. Otherwise it reports its own state |
| Slots and places | The request's slot releases in `finally`. The task holds its process-cap place until its statement ends |

The limiter already keeps its state in process memory, so this is not a new kind of state. Fixed windows are kept too, since they do not change.

**Reads in an incident.** A picker change is debounced by 300 ms (*guess*). Catch-up and Apply read only their panels. A drag names a window nobody shares, so merging barely helps: a 40-scan board at 90 a minute (two API processes) spends 40 on refresh plus 40 per drag, so two drags a minute reach 120 (*arithmetic*). So **after a drag, only panels on screen are read,** through `panels=` and an `IntersectionObserver`, and the rest read as they scroll in; and a drag's window is rounded out, to whole minutes under an hour and whole 5 minutes above (*guess*), so colleagues zooming on one spike share scans. "Read now" goes through the shared read, so repeated presses cost nothing. `refetchOnWindowFocus` (`query-client.ts:125`) is off for this query unless past its interval. T5.7 runs an incident script, not just idle refresh.

**Failures inside a scan.** Today an unexpected ClickHouse error reaches `handle_unexpected_error` as a 500 (`errors/handlers.py:127`). Here
each scan catches `Exception` at the panel boundary, after the mapped errors above, logs it with its reader name, and marks its panels `failed`. The response carries no
detail. `CancelledError` is not an `Exception` and passes. That catch is broad on purpose, and only there: one panel's fault must not take
the page.

### 7. The console's read

`features/dashboards/api.ts`: one `useQuery` for the batch, passing `timeoutMs` (the time budget, above), and one for the panel context.

- **Key:** `["dashboard-panels", dashboardId, version, projectId, windowCacheKey(choice), service]`. `windowCacheKey` (`selection.ts`) keeps
  the key still while the window moves, so a refresh does not bring back the skeleton (AC29).
- **`refetchInterval`:** the dashboard's own value, `false` when paused or absolute (`refresh.ts:124`), and hidden tabs do not refetch, by TanStack
  Query's default of `refetchIntervalInBackground: false` (*not read in its source; AC29 tests it*).
- **Keeping the last reading:** the client merges per panel, under FR31's rule and its time limit.
- **Catch-up reads.** A `refused` result schedules one read of the refused panels at `retry_after_seconds`, up to 3 times (*guess*), whatever the refresh setting, including a fixed window, since that read never finished. While paused or hidden it waits, and the words change. A panel's "Read again" reads that panel. In an outage, the page-level state's one "Read again" re-reads everything. "Read now" and a per-tab interval (FR71) live only in this tab.
- **Numbers from the panel context** draw `open_findings` and the strip, and pass markers (`shared/deploy-markers.ts:37`) and `findingSpans` to `TimePanel`, filtered per FR18.
- **Address and top bar.** `use-dashboard-address.ts` reads the project from the address, and checks it against `useProjects()`. **Until that list has loaded, no read runs.** If the id is not in it, the page shows the refusal, keeps the address and leaves the store alone, since `useProjectSelection` would otherwise replace an unknown id with the first project (`projects.ts:70-72`), and the reader would be looking at the wrong project's numbers under the sender's link. If it is, a **screen override** applies: a new, unremembered field in `selection.ts`, whose comment already allows "unless a screen overrides it" (`:65`). `useSelection` returns the override while this screen is mounted, so the top bar agrees, and `remember()` never sees it. The panels query is `enabled: false` until store and address agree, so no read reaches the old project and nothing stamps last opened on it.
- **Changing the top bar on purpose** replaces the address and is remembered, as anywhere. **Following a panel's service link** applies the project and window through the ordinary setters, because the person asked. Otherwise the override ends on unmount, and `/overview` shows the reader's own choice (AC69).
- **Drag a range:** `onSelectRange` rounds the window out, sets it in the address and override, and pushes one history entry, moving every panel on screen through one key, with "Zoomed to 02:10 to 02:20. Back to 02:00 to 03:00". Browser Back restores the earlier window. It does not call `setAbsoluteWindow`, which calls `remember()` (`selection.ts:150-155`). It is not remembered, so one stray drag cannot freeze a wall display across reloads or pin the whole console.
- **Default range.** With no window in the address, `default_range` applies through the override. Null follows the reader's own. While mounted, the top bar's range list also offers 30 days, through the same declaration the refresh control uses, and `TIME_RANGES` itself does not change (`selection.ts:21-27`).
- **Newest bucket.** `incomplete_from` in a time panel's result marks the bucket still filling: a live window's end is rounded up, so its newest bucket has only part of its count, and `rollup.per_minute` divides by the whole bucket (`services/service.py:73-80`, `rollup.py:45-53`). At 24 hours, a bucket is 24 minutes. Overview has the same fault today, read and not run, and that is a separate bug record (T4.9).
- **Opening from a finding.** An open finding opens the shortest live range covering it, from before `started_at`, and refreshes, since fixed windows never do and an engineer is watching for recovery. A closed one opens a fixed window with a margin each side.
- **Project clock.** The context's time zone goes to `TrendChart`'s `zone` (`:116`), the readout, tables and sentences, as `FindingScreen` does. The header names it. While a dashboard is mounted, the screen declares the zone to the top bar, so the window phrase, the paused chip (`RefreshControl.tsx:70` uses `toLocaleTimeString()` today) and `WindowControl`'s picker (`:100-103`, "Times are yours"; `:81-82`, `toLocalInput`) use it, and the picker's fields say "Times in IST". Otherwise, a phone's only way to change the window would read a chart's 02:14 and enter it on another clock, with no warning.

### 8. Metric series (Step 8)

The hard read, in its own step, after every other panel works.

- **One real series** is `(service_name, host, container, instance, series_hash)`, because `series_hash` hashes `attrs` alone
  (`000003_metrics_sum.up.sql:27`). The paths are constants, as `infrastructure/queries.py:67-71` does.
- **Choosing a series.** `series` is optional in settings. A new `GET /metrics/{metric_name}/series` lists a metric's attribute combinations with their `series_hash`, and the panel stores only the hash, bound as a parameter, so no SQL is built at runtime, the tenant test still passes, and a file carries no attribute values. Attributes become the line's label at read time, shown as text through FR61. This meets "orders_processed in eu" without group-by (D16, D33).
- **That route's gate.** It first exposes raw attribute values to an API client. Today's metric reads return none (`metrics/queries.py:95-99`). They are customer-defined labels, which may hold user ids or emails, or attacker text, since masking covers only credential-shaped data (`mask/rules.go:26-38`). So it is `CurrentIdentity`, off the read surface, though its router is mostly `CurrentReader` (`metrics/router.py:48,73`), with a token test and the structural rule naming it. It binds one service and a window of at most 7 days, returns at most 200 combinations (*guess*), and runs under the raw-read limits. It renders attributes once per combination, and the 2.29 s cost of `toJSONString` per row (`:97-99`) is measured in T5.7's rerun. Labels may hold personal data, are shown to every role, and the docs say so.
- **Gauge:** mean per bucket per series, then the chosen aggregation across series, into one line. "All" means one combined line, never many: 20 lines cannot be told apart with 6 colours and 5 dashes, and hiding some would make the sum false.
- **Cumulative, monotonic sum:** the last value per bucket per series; subtract the previous bucket with `lagInFrame`, reading one bucket before
  the window and dropping it after (`HEATMAP_SQL`, `metrics/queries.py:322-378`). **A falling value is a restart, and the value after it is the
  contribution.** Delta temporality: plain sum. Non-monotonic cumulative: treated as a gauge. `aggregation_temporality` and `is_monotonic` exist on
  the table (`000003`), and are read per row.
- **Uniqueness:** a metric row is not unique, because a redelivered batch is kept (`metrics/queries.py:90-93`), so `argMax` per bucket per series
  is used, never `count` or a plain `sum` of stored points.
- **Shape:** the builder lists gauge and sum names through `GET /metrics?shape=`. The panel stores the chosen shape.
- **Units:** `uniqExact(metric_unit)` per name; above 1, the panel says so and draws no sum across them.
- **Cap:** at most 1,000 series combined (*guess*); the answer says how many it combined. Over it, `too_many_series`, and nothing drawn.
- **Keys:** the sort key begins `(tenant_id, project_id, service_name, metric_name, …)` (`000003`), so one service is cheap. "All services" scans more, and
  is grouped by service only. Measured before release.
- **Testing.** A live test writes a known fixture itself to `metrics_sum` and `metrics_gauge`: a restart, two equal-attribute instances, two units, a changed attribute set, and more series than the cap, with expected results computed in the test. `tools/gateseed` emits cumulative histograms only (`payload.go:124`), and CI does not start the demo, which stays for the founder's judging.

**A separate suspected bug, not F20's to absorb.** `HISTOGRAM_SQL` groups by `series_hash` and only takes `any(service_name)`
(`metrics/queries.py:225-262`), and `HEATMAP_SQL` partitions by `series_hash` within one service (`:357-360`). If two instances report equal attributes,
each query may subtract one instance's total from another's. **Read from the code; no query was run.** Step 8 starts with a live test. If it is
real, it becomes a bug record of its own (research trap 5 flagged the first; the heatmap is added here).

### 9. Markers and spans

- **Markers.** F28's `markers_in_window` (`deployment-release-tracking/design.md`, *Data flow and interfaces*, D30, in `releases/service.py`), called in the panel context, with F28's cap of 500 and `truncated`, which the screen states in F28's words. It replaces the console's newest-50 read (`overview/api.ts:219`) for dashboards. F20 calls the function rather than `GET /releases/markers`: one request, on the default tier, answering through a ClickHouse outage. `deploys_in_window` is not used, because it is signed deploys only and would lose seen releases (F28 D29), so one install would draw different marks on two screens. **Treat `version` as attacker-controllable,** since a seen version can be planted by a stolen ingest key (same section): it goes through FR61, and `basis` travels to the client, so `seen` is drawn with a dotted rule and reads "Version seen in telemetry", and `reported` keeps the dashed rule and may say deploy. A test holds this (AC122), so a false "deploy" beside a finding on an on-call screen cannot look like one CI reported.
- **Does this service report deploys?** F28's `coverage_of`, which needs no ClickHouse, since `releases` stores the times (same section). The words are F28's: coverage `none` says "We receive no deploy information for this service", linking to setup.

- **Findings.** A new statement, `project_findings_in_window`, in `detection/queries.py`, sharing its overlap predicate with `service_findings` (`:105`). It reads project and service scope, with an optional service, **newest first, keeping 200**, because a busy window must lose the oldest, and re-sorted for the chart. It sets `findings_truncated`, which the screen states. It does not change `service_findings`, which keeps the oldest first, hard-codes `scope = 'service'`, and whose caller slices to `MARKER_PAGE` (`detection/service.py:211-239`), so F6's service screen and its truncation stay as they are, and a live test holds both reads at over 200 findings. Served by `ix_findings_org_id_project_id_state_started_at` through its org and project prefix (*not measured*; T5.7 adds it, since a flapping service opens 5 findings an hour, foundation section 4). A project-scope finding (`scope = 'project'`, no service, `models.py:1529`) shades project panels and service panels, and is named in the strip. `findingSpans` moves from
  `features/servicedetail/service-detail.ts:137` to `shared/`, keeping each finding's id, because a feature may not import another feature (`docs/engineering/frontend.md`).
- **Alert incidents.** Listed in the strip, not shaded (D52): `incidents` (`models.py:1344`) in the window whose rule names a service (`AlertRule.condition`, `:1272`), at most 50 (*guess*), with the cap stated. T5.4 reads which condition kinds name one. `IncidentScreen` offers "Dashboards showing this service" for those.
- **Which panels:** per FR18. Node, check, statement and metric panels draw all the project's, and say "All findings and releases in this project", because they have no service setting.

## Data flow and interfaces

### Routes

All under `/api/v1`, all `CurrentIdentity` or `MayEditDashboards`. None on the read API, by D9.

| Method and path | Gate | Does | Tier |
|---|---|---|---|
| `GET /dashboards` | Identity | List, from columns only, with no definitions, up to 1,000. `?service=` limits to dashboards naming that service or using the picker, naming ones first, then by last opened, at most 5, and says if more exist; `?deleted=true` lists Recently deleted | Default |
| `POST /dashboards` | `MayEditDashboards` | Create, from a document | Default |
| `GET /dashboards/{id}` | Identity | One, with definition, version and owner's name | Default |
| `PUT /dashboards/{id}` | `MayEditDashboards` | Replace, with `version`. 409 on mismatch. Keeps the replaced version unless nothing changed | Default |
| `DELETE /dashboards/{id}` | `MayEditDashboards` | Soft delete | Default |
| `POST /dashboards/{id}/restore` | `MayEditDashboards` | Undo a delete. 409 if the title is in use. Refused at the live cap | Default |
| `POST /dashboards/{id}/purge` | `MayPurgeDashboards` (owner, admin) | Hard-delete the dashboard, or one version named in the body, at once, and rewrite `title` and `previous_title` to `[removed]` in its audit entries. One audit entry, holding no removed text | Default |
| `POST /dashboards/validate` | `MayEditDashboards` | Runs the validator on a document. No scans, no write. Declared before `/{id}` | Default |
| `GET /dashboards/{id}/versions`, `GET …/versions/{version}` | Identity | History. Restoring is a draft, then `PUT` | Default |
| `GET /dashboards/{id}/context` | Identity | The panel context | Default |
| `GET /dashboards/templates` | Identity | The templates, as documents | Default. Declared before `/{id}` |
| `GET /dashboard-panels/read` | Identity | Saved read | **Telemetry** |
| `POST /dashboard-panels/preview` | `MayEditDashboards` | Draft read | **Telemetry** |
| `GET /metrics/{metric_name}/series` | Identity | One service's attribute combinations, for the builder, with its limits (section 8) | **Telemetry**, by `/metrics`'s prefix |

Duplicate is a client-side read, then `POST`. Export is client-side. Import is the browser reading a file, then `POST /dashboards/validate`, and only then a draft (D22, D53), and import over an existing dashboard is that draft, then a version-checked `PUT`. All new write handlers go in `POLICY_OF`
(`test_authorization_coverage.py:161`), and no entry goes in `EXEMPT`: preview and validate are gated, unlike `symbolicate`.

### Response of a batch read

**Batch:**

```json
{
  "correlation_id": "…",
  "window_start": "…", "window_end": "…",
  "panels": {
    "p_01": {"state": "ok", "kind": "service_trend", "read_at": "…", "bucket_seconds": 60, "data_starts_at": null,
             "data_ends_at": "…", "series_combined": 0, "incomplete_from": "…", "series": [{"label": "p95", "points": [["…", 88.0]]}]},
    "p_02": {"state": "refused", "retry_after_seconds": 20},
    "p_03": {"state": "failed"},
    "p_04": {"state": "timed_out"}
  }
}
```

A Pydantic union per state, so `failed` cannot carry values. A compared number adds `previous` and `previous_coverage`.

**Panel context:**

```json
{
  "project": {"id": "…", "name": "production", "timezone": "Asia/Kolkata", "retention": {"raw_days": 30, "derived_days": 395}},
  "window_start": "…", "window_end": "…",
  "detection": {"state": "learning", "earliest_judging_date": "…"},
  "ever_reported": true, "last_seen_at": "…",
  "markers": [{"release_id": "…", "period_id": "…", "deploy_id": "…", "service_name": "checkout", "version": "v2.1", "at": "…", "basis": "reported", "state": "…"}],
  "markers_truncated": false,
  "coverage": {"checkout": "…"},
  "findings": [{"id": "…", "scope": "service", "service_name": "checkout", "severity": "critical", "title": "…", "started_at": "…", "closed_at": null}],
  "findings_truncated": false,
  "incidents": [{"id": "…", "service_name": "checkout", "rule_name": "…", "opened_at": "…", "resolved_at": null}],
  "incidents_truncated": false,
  "open_findings": {"total": 1, "by_service": {"checkout": 1}}
}
```

`retention` is null on a self-hosted install. The finding's `title` is the same words the feed shows, drawn as React text.

## Data and migration impact

**Postgres:** two tables, in the next free migration (Alembic is at 0050 on this branch, and F28, F3, F22, F4, F5 and F19 come first).

**`dashboards`:**

| Column | Type | Note |
|---|---|---|
| `id` | UUID | Primary key |
| `org_id` | UUID, FK organizations, CASCADE, not null | Tenancy |
| `title` | String(120) | |
| `description` | String(500), default '' | |
| `definition` | JSONB, not null | Checked by the validator. Holds no title or description |
| `version` | Integer, default 1 | Optimistic lock |
| `created_by`, `updated_by` | UUID, FK users, SET NULL | As `alert_rules.muted_by` (`models.py:1277`). **Removing a member does not null these:** removal keeps the row and scrubs it (`members/service.py:210-216`), so the owner shows as "Former member" by joining `users`. SET NULL only fires when the row goes with organization erasure |
| `last_viewed_at` | Nullable timestamp | Written at most hourly. Shown as "last opened" |
| `panel_count` | Integer | Written at save from the compiled definition. List routes read this, not the JSON |
| `service_names` | `text[]` | Literal services named, written at save. A GIN index serves `?service=`, which otherwise would parse up to 1,000 rows of up to 64 KiB on each finding and service screen |
| `uses_picker` | Boolean | Written at save |
| `panels_not_reading` | Integer, default 0 | From the last stamped read. Non-transient states only |
| `deleted_at` | Nullable timestamp | Soft delete. Reads and lists exclude it, by one shared predicate |
| `created_at`, `updated_at` | Timestamps | |

Indexes: partial unique `(org_id, lower(title)) WHERE deleted_at IS NULL`; `(org_id, last_viewed_at)`; GIN on `service_names`. CHECK: `jsonb_typeof(definition) = 'object'`.

**`dashboard_versions`:** `dashboard_id` (FK, CASCADE), `org_id`, `version`, `title`, `description`, `definition`, `saved_by` (FK users, SET NULL), `saved_at`, with primary key `(dashboard_id, version)`. A save whose definition hash and title are unchanged makes no version. A version is trimmed in the save's transaction, only when past the newest 20 **and** older than 30 days (*guesses*), with a hard ceiling of 200 per dashboard (*guess*). Without the time rule, 20 saves by a compromised account would erase the last good version, and the audit log holds only a hash, so it could not be recovered.
At worst, 200 × 64 KiB × 1,000 dashboards is about 12.8 GB (*arithmetic*), far above any likely use, reachable only by abuse, and T5.7 reports real size. A stated limit: 200 such saves can still flush a dashboard's history. New tables, so no lock on existing writes, and no backfill. Downgrade drops both.

**Expiry.** `dashboards` joins `_EXPIRED` in the scheduler's sweep (`jobs/expiry.py:32-42`), `deleted_at < now() - interval '30 days'`, and versions cascade. So the scheduler changes by one line, and the plan says so.

**Caps.** Live dashboards at 1,000 and all rows at 2,000 (*guesses*), counted in `POST`, duplicate and restore, in the save's transaction.

**Tenancy path.** Session → `Identity.org_id` → `WHERE org_id = :org`, for every statement in `dashboards/queries.py`. `organizations/tables.py` gets `dashboards` under
`DELETED_DIRECTLY`, because it hangs off the organization alone, and the organization's row is kept (`organizations/tables.py:27-31`), and `dashboard_versions` with its parent `dashboards`. A project's deletion leaves
dashboards alone, and a link to it gets a 404 page state.

**Personal data, decided.** Definitions are the organization's data: removed by an editor, or by organization erasure, and **not** by a person's erasure or a project's deletion. Masking runs on telemetry only (`apps/workers/internal/pipeline/mask/rules.go`), so nothing masks what a person types, and people paste logs into notes (foundation section 3).

| Field | Can hold | Who reads it | What removes it |
|---|---|---|---|
| Title, description | A name, free text | Everyone in the organization, support while an owner has granted access | An edit, then past 20 saves and 30 days, or 30 days after delete; **purge at once**; organization erasure |
| Note text, panel title, link | Free text, possibly a secret. A link may have held one before the validator refused it | Same, and every export | Same |
| Literal service and node names | Host and service names, surviving project deletion | Same | Same |
| `created_by`, `updated_by`, `saved_by` | An id; the name is scrubbed on removal | Same | Scrub by `members.service`; SET NULL on erasure |
| Audit detail's title | A name, outliving the dashboard | Admins | **Purge rewrites it to `[removed]`** for that dashboard. Otherwise organization erasure. `_without_person` replaces only an exact match (`members/service.py:225-232`) |

**Under a note, the words depend on the install.** `self-hosted`: "Everyone in this organization can read this. Do not paste secrets or personal details." `hosted`: "... and support while an owner has granted access" (`auth/dependencies.py:130`). A self-hosted install has no vendor operator (`superadmin/seed.py:11-13`), and the first sentence would send a customer who chose self-hosting for this reason looking for a back door. Both name purge. **Purge is the prompt path:** people paste logs into notes, nothing masks them, an edit leaves the old text readable in versions, and delete keeps it for 30 days. It cannot reach backups, and the setup page says so. `profile`'s export is not extended: a dashboard is the organization's, not the person's.

## Performance and scale

**Nothing here has been measured.** Step 5's measurement replaces the numbers.

| Thing | Starting value | Basis | Measured by |
|---|---|---|---|
| Panels per dashboard | 30 | *Guess* (R12) | Step 5 |
| Scans per read | 40, refused at save | *Guess* | Step 5 |
| Scans a read runs at once | 4 | *Guess*, below the 8-connection pool | Step 5 |
| Dashboard scans per process | 5, 2 raw; 3 and 1 for one organization | *Guess* | Step 5 |
| API account's server-side limits | 2.4 GiB, 12 at once | *Guess* | Step 5 |
| Shared read | 30 s; 100,000 rows, 25,000 for one organization, 2,000 an answer; statement limit 15 s | *Guess* | Step 5 |
| Refresh default and floor | 60 s, 30 s | *Guess*; rollups change by the minute (`rollup.py:42`) | Step 5 |
| Read deadline | 20 s from arrival | *Guess*. Each statement may run 30 s otherwise (`tenant_statements.py`) | Step 5 |
| Raw window | `check_trend` and one `node_trend` 30 days, others 7, never over what the plan keeps | *Guess*, per reader. `resolve_window` caps nothing today (`window.py:30-38`). T5.7 tries 30 and 90 days on metric reads | Step 5 |
| Series combined on a metric panel | 1,000 | *Guess* | Step 8, T11.6 |
| Definition size | 64 KiB | *Arithmetic:* 30 panels × about 300 bytes ≈ 9 KiB, with room | Observed |
| Dashboards per organization | 1,000 | *Guess*, abuse guard only | |
| Refresh with chart count | 30 uPlot charts | Not tried, with a shared cursor especially | Step 4, and again after Step 5 |
| API memory with the store full | Under the 832 MB limit | **Reported, not assumed.** T5.7 | Step 5 |

**Self-hosted may tune five of these** through settings, with these values as defaults, as `clickhouse_pool_size` is: panel cap, scan cap, refresh floor, raw windows and scans at once (`settings.py`, documented in `sizing.md`). Startup refuses a set where scans times refreshes a minute would exceed the telemetry quota's share. The quota itself stays product policy (`policy.py:14`). The tests hold the defaults.

**Worst case by arithmetic.** 40 scans at 4 at once, each at the fixed 15 s limit, would take 2.5 minutes, which is why a whole-batch deadline exists. Refresh mostly cannot pile up: TanStack
Query does not start a refetch while one is in flight for that key (*documented behaviour, not read in its source*), and the console's timeout outlasts the server's deadline. If a read is ever abandoned (a window change, a closed tab), the server's lowered statement limit bounds what continues, and the process's cap holds the total.

**Response size (arithmetic).** 30 panels × 60 points (`rollup.py:38`) × up to 5 lines × about 40 bytes ≈ 360 KB at worst, before compression; typical is far smaller.

**Capacity on the 16 GB install.** No new container or role. One new ClickHouse account. Postgres: under 64 KiB a row, with versions as above. API memory: shared reads are capped by rows, and the node panel no longer reads the fleet.
**ClickHouse:** it caps itself at about 80% of its 6 GB ceiling (`sizing.md:13,22`), about 4.8 GB, and each read may use 1 GiB (`tenant_statements.py:39-45`), so about 5 at full size would fill it. The repository's comment says that at this ceiling every query fails, the workers' inserts included. So raw readers are capped at 2 per process, and API reads together at about 2.4 GiB by `nexovue_api_reader`. T5.7 tests this. `sizing.md` gains the new account and settings, and otherwise changes only if the measurement says so.

**AI cost: none.** No model is called. It adds no tokens and no entry to `ai_runs`.

**Alerts and ingest come first** (NFR11). Caps in the API protect its own connections. `nexovue_api_reader` bounds its total on the server, and leaves the scheduler outside. That is new, and untested, so T5.7 records `nexovue_scheduler_detection_settled_minute_age_seconds`, `nexovue_scheduler_oldest_alert_waiting_seconds`, `nexovue_scheduler_oldest_ready_job_seconds` per queue, `nexovue_worker_consumer_lag_records` and a failed-insert measure against a no-load baseline, and F20 does not ship if they do not hold. An operator is already alerted when the settled minute is over 10 minutes old (`deploy/monitoring/rules/nexovue.yml:102`), and `dashboard_scans_enabled` turns scans off.

## Failure handling

| Failure | Behaviour | Retry |
|---|---|---|
| ClickHouse goes away after the API has connected | Each scan `failed`; request still 200. Panel context unaffected | Read again, or the next refresh |
| ClickHouse down and the API has never connected | Batch answers 503 before the handler runs (`deps.py:83-93`). Panel context still 200. The console shows the page-level state, with no toast | No retry on that 503; the next refresh |
| Postgres unreachable | Both requests fail; central 5xx toast; last readings stay, under FR31's limit | Next refresh |
| One slow scan | Others finish; `timed_out` at its reader's 20 s deadline. Its statement stops at its fixed limit, or sooner if the cancel setting works | Next refresh |
| Quota runs out mid-batch | Remaining `refused`, with `retry_after_seconds`. Top of the page read first | Catch-up read, up to 3 times |
| The process's cap wait expires, or the server's limit answers `TOO_MANY_SIMULTANEOUS_QUERIES` | That scan `refused` | Catch-up read |
| Whole request 429, including a slot bucket still full after its 5 s wait | The panel's sentence, at page level, honouring `Retry-After`. No toast on the batch read | FR45's catch-up read, timed from `Retry-After` (`context.retryAfterSeconds`). The client's own policy never retries a 4xx (`query-client.ts`, `shouldRetry`) |
| Client disconnects | Unstarted scans do not start (`is_disconnected`). The slot releases in `finally`. A task with no reader left is cancelled, and its fixed limit bounds it. AC63 checks the store | N/A |
| Browser timeout at 25 s | Its own page state. The server's work is bounded as above | Read again |
| Operator turned scans off | Every scan panel `paused_by_operator`. Context unaffected | When turned on |
| Save conflict | 409 naming who and when. Draft kept | Reload, or save as a copy |
| Unique title violation | 409 naming the existing dashboard, with its id for a link | Rename or open |
| Unknown stored kind | That panel `unknown_kind` | Removable in the editor |
| Corrupt stored panel | Same as unknown: the page draws, and an error is logged | Removable in the editor |
| API restarted mid-batch | Next refresh works; slots are in process memory, and vanish with it (`limiter.py`) | Automatic |
| AI off, or `scheduler-insight` stopped | No effect | N/A |

Saves are one transaction: the row, its version and its audit entry commit together, through the request's session (`deps.py:63-73`).

## Security, tenancy, and privacy

| Threat | Stopped by | Proved by |
|---|---|---|
| Another customer's dashboard by id (IDOR) | `org_id` on every statement; same 404 | AC6, two-tenant live test |
| Another customer's check or service through a panel | Tenant comes from the session (`auth/dependencies.py:246-257`). Checks are resolved by `org_id`, `project_id` and name | AC22 |
| Another project's data under this project's header | Every F20 statement binds `project_id`, subqueries too. Today's structure test holds this only for `detection` (`test_tenant_chokepoint_structure.py:116,540-555`) | AC26, AC85 |
| A kept answer served to the wrong reader | Key includes tenant and project; the route's checks run first, for every reader | AC62 |
| A project-scoped token reading other projects' names through definitions | **Tokens reach no dashboard route** (D9), by `_refuse_a_token_here` (`:192-200`). `test_read_surface_structure.py` only looks for a `CurrentReader` annotation, and `TenantRepository` itself depends on `CurrentReader`, so it cannot see a panels handler that drops its gate | AC9, through the real app with real tokens, and a new structural rule: every handler in `dashboards/` and `dashboardpanels/`, and `metrics/router.py`'s series route, annotates `CurrentIdentity`, `MayEditDashboards` or `MayPurgeDashboards` |
| A shared link pointing a reader at the wrong project | The address's project is checked against the reader's list before anything is stored or read (section 7). `require_project` still answers 404 | AC69 |
| SQL through a definition | Definition can only name a kind and checked settings. Values reach SQL as bound parameters. `test_tenant_chokepoint_structure.py:325` refuses runtime SQL | AC4, AC5, structure test |
| Stored script through titles, notes or service names | React text only, with no `dangerouslySetInnerHTML` (`DocsScreen.tsx:89` remains the only use). No text renderer. uPlot's built file holds no `innerHTML`, from a text search | AC35, AC36, browser test |
| Malicious import file | `POST /dashboards/validate`: the same validator, 64 KiB, `extra="forbid"`, before anything opens. File is parsed, never run. Imported text shows through FR61 | AC4, AC5, AC41, AC128 |
| Bidirectional, zero-width and tag characters in names, to deceive | `dir="auto"` and truncation do not stop a name looking like `checkout`. One shared helper shows them as visible escapes such as `[U+202E]`, for display only, in picker, legends, tables, tooltips and sentences, for every telemetry value and all stored free text. The raw value stays for binding, since the name must match what the service sends. Titles, labels and panel titles refuse them outright | AC74, AC123 |
| Overload through a huge window or many panels | Panel and scan caps, charging, deadline, existing statement limits of 30 s, 1 GiB and 500,000 rows (`tenant_statements.py`) | AC16 to AC19, AC23 |
| Hiding load inside a batch | Each scan charged | AC16 |
| Dashboard loaded repeatedly to run heavy reads | Paid by the reader, not the author. Saved definitions hold no credentials | AC16 |
| Viewer writes through a preview POST | `MayEditDashboards` | AC7, `test_authorization_coverage.py` |
| Support staff browsing | Each request already recorded for the owner (`auth/dependencies.py:133-140`); writes refused; last viewed not moved | AC8 |
| False story: a "Production" title over staging | Header shows the project's name from the data | AC27 |
| Prompt injection through dashboards | **No AI link here.** A later AI link must treat titles, descriptions and notes as free text (foundation section 3, U1 to U7) | AC53 |
| Open redirect or external fetch | A note renders no links or images. The `links` field accepts `https` only, with no user name, password or credential-shaped query key, and the console draws it with `rel="noopener noreferrer"` and its host beside the label. Panel links are built by the console from ids | AC36, AC77, AC124 |
| Data leaving the install | No dashboard module opens an outbound connection. A copied link's address holds the project id and service name, so an incident channel may keep them, and the copy menu says so | AC142: a structural test on direct imports, and a browser check of the requests made |
| A planted version shown as a deploy | `basis` reaches the client. `seen` is drawn and worded differently, and `version` goes through FR61 | AC122 |
| Attribute values through the series route | `CurrentIdentity`, off the read surface, bounded; only the hash is stored | AC9, AC121 |
| A secret pasted into a note | Warning under the note, purge at once, audit rewritten | AC119, AC120 |
| A compromised member erasing history | Time-based trimming, no version for a no-op, a ceiling | AC125 |
| One tenant starving another | A share per organization in each process cap | AC94 |
| One tenant filling the shared-read store | A share of rows per organization | AC90 |

**AI safety, stated plainly.** Telemetry is attacker-controlled text, and it reaches this feature as service names and error titles. No AI reads them here, no AI holds any
permission here, and AI stays off until the customer turns it on. So there is **no AI link and no injection suite to add**. A test keeps it so: no module in `dashboards` or
`dashboardpanels` may reach any item on the deny-list, through the same import walk.

**That test could not pass as written, and the fix is at the cause.** The shared auth path reaches the SDK: `auth/dependencies.py:51` and `deps.py:45` import `lifespan`, and `lifespan.py:21` has `from mcp.server import MCPServer`, used only to type one argument (`:137`), and only its `session_manager` is called (`:166`). `mcp` is on the walk's deny-list (`test_detection_has_no_ai.py:37-40`). So `lifespan.py` types that argument as a small Protocol and drops the import, and no exemption is needed. The walker and deny-list move to one place both tests import, `nexovue_platform/import_walk.py`, because a second copy would silently miss what foundation section 1 requires be added (`nexovue_platform.ai`, `nexovue_scheduler.insight`). A control proves the walk finds an AI import from `nexovue_api.assistant.server`. Its known limit carries over: it cannot see a provider reached by plain HTTP (`:15-16`). So a test also asserts that no handler from `dashboards` or `dashboardpanels` is in F4's `TICKET_HANDLERS` (`ai-chatbot/design.md`, *The ticket*), once F4 has merged. F4 allows by handler, not by path.

**Audit.** `dashboard.created`, `.updated`, `.deleted`, `.restored`, `.purged`, following `<entity>.<past tense>` (`audit/service.py`), with `title`, `previous_title` on a rename, `version`, `panel_count` and a SHA-256 of the canonical definition, so an admin can match an entry against an exported file and see that content changed even when the count did not. Version history holds the content, and the audit log never does. A title is free text in an admin-only screen, shown as React text.

## Observability and operations

- **Logs:** each batch logs dashboard id, panel count, scans run, merged, kept answers used, joined, refused, timed out, failed, total time and its correlation id, with no titles or service names. A failed scan logs its reader's name. No log line carries a definition.
- **Metrics:** a histogram of scans per read and a counter by panel state, labelled by state and reader only. No label names a tenant, the rule the scheduler's metrics follow (`nexovue_scheduler/metrics.py:15`).
- **Alert for the operator:** none new at first. F6's rule already pages when the settled minute is over 10 minutes old (`nexovue.yml:102`), which is the harm that matters. `refused` panels are reported through the existing rate-limit warning (`middleware/ratelimit.py:244`), extended by a log line.
- **Runbook:** a new section in `docs/operations/monitoring.md` for "dashboards are slow": which caps to change, where, and `dashboard_scans_enabled`. Hosted caps are code defaults, as limits are "product policy" (`policy.py:14`); self-hosted may set five.
- **Docs:** `docs/product/setup/` needs a page: what a dashboard is, what limits exist and why, that panels come from a fixed list, that latency is an estimate, how to set up a wall display on a viewer account with one screenful, what a copied link contains, that dashboards send nothing out of the install, that attribute labels may hold personal data, what purge does and that backups are outside it, and a mapping from common request-rate, error and machine panels in other tools to panel kinds, naming no rival. `docs/self-hosting/sizing.md` gains the new account and the five settings, and changes otherwise only if the measurement requires it.

## Alternatives and tradeoffs

| Choice | Rejected | Why rejected | Cost of the choice |
|---|---|---|---|
| Fixed kinds | Query builder compiling to SQL | Fails the structure test; needs its own safety proof; cost unbounded | Fewer questions answerable |
| Fixed kinds | Free-form SQL or PromQL | Breaks the tenant rule (`alerts/telemetry.py:1-13`) | Grafana stays ahead |
| Batch, merged, charged | Each panel its own request | 30 panels at 10 s spends the quota; 30 response shapes for the console | More backend code |
| Batch, merged, charged | Batch counted as one | Hides real load (research trap 1) | |
| Batch, merged, charged | Raising the telemetry quota | The limit exists to protect the store; raising it removes the protection for everyone | |
| GET for saved | POST with panels in the body | Support view would blank (`auth/dependencies.py:145`); the door tests would need an exemption | Two routes |
| `/dashboard-panels/read` | `/dashboards/{id}/panels` | Prefix match would need the whole `/dashboards` on the costly tier (`policy.py:286`) | A second prefix |
| `/dashboard-panels/read` | `/dashboards/panels:read` | First colon route, and a router-order trap with a 422 on every read | A rename of the foundation's name, stated |
| Quota only for later scans | Slots for each later scan | Adds much for a case the process caps already hold (D43) | Reviewed if T5.7 finds otherwise |
| A server-side bound | API caps alone, or a test before release | Neither holds on customer data, nor across processes | One more account and its secret |
| Native select plus typed field | `<datalist>` | Cannot show an escape and hold the raw value | One more field |
| Second package | Both routers in one `router.py` | `test_ratelimit_coverage.py:56-60` judges by file | One more package |
| Ordered list, width of 12 | `react-grid-layout`, `gridstack` | Dependencies, and weak keyboard support reported; free placement isn't needed. Details in [research](research.md) section 5 | Less free than rivals |
| Ordered list, buttons | Drag only | Fails WCAG 2.5.7 | Drag arrives later |
| JSON document | Child table per panel | Layout change touches many rows; export reassembles | A validator must enforce what Postgres can't |
| Organization | Per project | The same dashboard for staging and production means a copy | `not_in_project` state |
| One service picker | General variables | Moves towards a query language; no evidence | Likely the first request from buyers |
| One service picker | None (R10) | "Start from this service" then makes a copy per service | One more setting |
| Tokens read nothing | Tokens read definitions (R14) | A one-project token would learn other projects' names | No scripted backup |
| Short shared read in the process | No sharing | 30 viewers exceed the organization's quota, so NFR12 cannot be met | Up to 30 s old, stated by its read time; one more module |
| Short shared read in the process | Redis | Gone (ADR-020), and each process's cache is enough | Per-process work is at most multiplied by the process count |
| Separate panel context route | Everything in the batch | A cold ClickHouse connection 503s the whole request, and findings wait for scans | Two requests per refresh |
| Separate panel context route | Lazy ClickHouse provider on the batch route | A second path around the one construction site `test_tenant_chokepoint_structure.py` holds; a fiddly test exception | A 503 the console must read as "all failed" |
| Names for checks and SLOs | Ids | Breaks across projects and in export | A rename breaks a panel, visibly; `ambiguous` state |
| One batch | Two batches, first screenful first (UX's preference) | Runs shared scans twice | One stuck scan holds scan panels up to the deadline; revisited if T5.7 says so |
| Versions and soft delete | None, export as backup | Any member can wreck an on-call board for good | One table, one column, one sweep line, four columns for lists |
| Screen-level override | Remember what a link says | One link pins the whole console, across reloads | A field in `selection.ts` and a notice |
| No materialized view per panel | Materialized views | Migration per customer choice | Rollups only where they exist |
| Stamp on batch read | Stamp on `GET /dashboards/{id}` | A wall display opened once a month would look abandoned | A write on a GET route, at most hourly; "last opened" claims no more |
| Replace `/findings` | Keep both `/` and `/findings` | Two addresses for one screen; pre-deployment (AGENTS rule 14) | |
| Plain text note | Markdown | A renderer to defend, and a way out for a later AI | Plain notes |

## Proposed ADRs

Written here and not in `docs/decisions/`. Each becomes a numbered file if approved.

### Proposed ADR: A dashboard is a checked document of fixed panel kinds

- **Status:** Proposed.
- **Context.** The tenant guarantee rests on every ClickHouse statement being a module constant (`test_tenant_chokepoint_structure.py`). Rivals with a query language accept what that costs. Nexovue has one developer.
- **Decision.** A dashboard is one JSON document on one row, holding panels of fixed kinds with checked settings. It belongs to an organization and is displayed against a project. No document, file, AI or setting can introduce SQL.
- **Consequences.** Good: isolation stays provable, cost stays bounded, numbers match their screens. Bad: unforeseen questions need a new kind, which is code. Harder to reverse after the first customer: the document's format, hence `schema_version`.

### Proposed ADR: A batch read is charged by the scan

- **Status:** Proposed.
- **Context.** The limiter counts requests, which was accurate only while one request meant about one scan (`middleware/ratelimit.py`, `policy.py:8-11`). The first route that breaks this sets the pattern for any later one.
- **Decision.** A route that runs more than one scan per request must merge equal scans, then charge each one the store really runs to the same buckets through `ratelimit/charge.py`, and report refusal per item. What it runs at once is held by its own permits, a process cap and a server-side account.
- **Consequences.** Good: the limits still mean what they say. Bad: some of the limiter's knowledge is now used inside a route. The middleware can't see scan counts, and that is accepted.

### Proposed ADR: The API reads ClickHouse through its own bounded account

- **Status:** Proposed.
- **Context.** The API and scheduler share `nexovue_reader`, and nothing on the server limits what either uses in total. Caps in the API hold neither across processes nor for other screens, so a load test is the only safeguard for alerts, and it cannot hold on customer data.
- **Decision.** The API reads through `nexovue_api_reader`, with per-user memory and concurrency limits. The scheduler stays on its own account, outside that bound.
- **Consequences.** Good: API reads at their worst cannot take alerts with them. Bad: one more secret, and any screen can now get a limit error under load, which must read as such.

## Specification impact

- **`feature-catalog.md`:** F20's entry and its "still to specify" line, once approved. Its amendment that the composed overview "holds `/`" must also change.
- **`route-manifest.ts` comment** on Overview, and `router.tsx:236-243`'s comment.
- **`docs/features/team-and-api-access/spec.md`, FR1:** gains `EDIT_DASHBOARDS` and `PURGE_DASHBOARDS`, as the permission table demands.
- **`console-redesign/wireframes-dashboard.md`:** says "no builder"; true of that feature, so add a pointer.
- **`phase-2-foundation.md`,** applied 2026-11-02, apart from section 12's claim wording and the shared deny-list note: section 8 replaces `/dashboards/panels:read` with `/dashboard-panels/read` (the reason: section 2 above), and gains `/preview`, `context`, `validate`, `purge` and `dashboard_versions`, and section 8's checklist names the one shared deny-list. Section 5's action table gains `PURGE_DASHBOARDS`. Section 9's F20 row takes `markers_in_window`. Section 12 gains F20's final claim wording, and says deploys on charts are parity. Section 10's "Honeycomb's limit of 2" is an SLO limit, by the reading recorded in research, section 4.
- **F28's documents:** F28's `design.md`, *Specification impact*, and D30 already say F20 calls `markers_in_window` and uses `coverage_of`. This design now agrees, so nothing is left to change there except F28's checklist line that F20 must follow, which is met.
- **`slo-tracking/design.md`, *API, under `/api/v1`*:** agreed. F19's budget function reads Postgres only, its burn line is separate, and `slo_budget` stores the SLO by name, not `{slo_id}`.
- **`anomaly-detection` feed:** F6's `headline.ts`, `findings/api.ts` and FindingScreen change in Steps 1, 5 and 7.
- **`docs/features/personal-data/spec.md`:** gains the dashboards statement from *Data and migration impact*, including purge.
- **`docs/self-hosting/sizing.md`, `docs/operations/monitoring.md`,** `deploy/clickhouse/users.d/`, compose and `deploy/nexovue`: the new account (T5.6b).
- **Overview's newest bucket:** a separate bug record (T4.9). **The Infrastructure screen's silent cut:** a separate bug record, as section 4 says. **Both staged-read comments:** corrected in T1.8, with removing the waits a separate change.
- **`phase-2-plan.md`:** Stage 6 notes that F20's Step 1, the front door, ships as soon as F6 merges, ahead of the stage order (spec D58). Applied 2026-11-02, Proposed.
- **`research.md`:** the original text stays as a record, with corrections marked, including section 4's facts added in review; `spec.md`'s table at the top lists where it was overruled.
