# Status: console-review-fixes

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None — every planned task is done and the gate is green.

## Completed

- **Step 1 — no screen claims more than it knows.**
  - **D1** `features/findings/headline.ts` (new): the headline is derived from the readings the
    screen holds. A check is called down only when a probe said so; one that has not run is a gap in
    what we can see. Proved by `headline.test.ts` and a case in `FindingsView.test.ts`, both seen
    red first.
  - **D2** migration `0036` adds `notification_attempts.kind`; `alerts/send_test.py` records its
    attempt; the API and the channel screen carry and name it. Proved by two scheduler live tests
    and one API live test, seen red first.
  - **D4** `phase-series.ts` — the sentence over the phase chart says what it measured.
  - **D5** the logs buffer download names what it holds, and its file matches.
- **Step 2 — the console works on a phone.**
  - **D3** the trace attribute filter wraps; APPLY was 22px past the edge of a page that cannot
    scroll sideways.
  - The settings rail collapses to one row below `md`.
  - Errors drops its trend column below `sm` so the exception keeps the width.
  - The drawer button is 44px; the drawer's own rows are 44px.
- **Step 3 — one column rule.** Settings and the platform screens are left-aligned at `px-4` with
  their measure capped; the Alerts header is capped to the width of the lists under it. **D6** the
  chart reserves its last label's overhang. **D7** Traces' header gained the duration bar's column
  and Errors' padding moved into the shared constant. Overview's worst-services table gained its
  headings; the RUM breakdown stopped wrapping a reading away from its unit; the new-check picker
  seats five cards 3 + 2; the waterfall axis uses one unit.
- **Step 4 — say the right thing.** **D8** the operator sees their own section and nothing else, and
  no project selector or range picker. **D9** the seed verifies the operator. **D10** all six copy
  defects. **D11** both pairs of duplicate labels. Plus: API tokens list live ones first,
  Infrastructure's empty state is two sentences and a link, and Metrics explains its last column
  once instead of repeating an unexplained phrase on every row.

- **Step 5 — the width** (founder, after reading the first round): Alerts and every settings screen
  left a fifth to a quarter of a wide window empty. The measure moved off the column and onto the
  sentence — `max-w-3xl` on prose, full width for lists, tables, forms and panels — Alerts runs the
  width like every other list, the settings column runs to `max-w-7xl`, and `SettingGroup` is two
  columns from `lg`. 27 paragraphs across eight settings screens carry the measure explicitly; an
  arbitrary-variant selector would have said it once, but this codebase uses none and one new idiom
  is worse than 27 explicit classes.

- **Step 6 — two defects found by walking the support flow in a browser** (2026-09-21, with the
  founder watching). Both were invisible to the suites because the suites asked the wrong question.
  - **The step-up locked the operator out of the platform.** `app/query-client.ts` throws every 403
    to the route boundary; a lapsed step-up is the one 403 the screen answers, and it never reached
    the component. The screen became "This session may not perform that action" with no way to
    confirm — the control meant to protect the platform area made it unreachable. The two platform
    reads now keep the global rule and carve out that refusal (`throwUnlessTheStepUpLapsed`).
    `PlatformScreens.test.ts` had passed for a day against a bare `QueryClient` that did not carry
    the app's rule; it now carries it, and fails without the fix.
  - **A refused write said nothing at all.** `problem-fields.ts` left 403 to the query client, the
    query client suppressed its toast because "the boundary renders it", and the boundary only ever
    sees a *query* — `throwOnError` is a query option. Pressing Save during a support view left the
    name in the box, no message, and a `PATCH /profile 403 "Support view is read-only."` visible
    only in the API log. The form now says what the API said. Proved by `problem-fields.test.ts`.
  - `features/admin/api.ts` is now the fourth file allowed to read an HTTP status, which
    `status-interpretation.test.ts` deliberately makes a visible decision; the reason is recorded
    there.

- **Step 8 — the width, again, and the reason it kept coming back** (founder, 2026-09-21: *"you do
  lot of space you give empty not utilizing always you do this mistake"*).
  - **The rail stays; the setting scrolls.** `SettingsLayout` had one scroll container holding both,
    so reading to the bottom of the audit log took the list of settings off screen with it. At `md`
    and up the row is the height of the pane and each column scrolls on its own. Below `md` the rail
    is a collapsed disclosure, so the page stays one scroller — two nested ones on a phone is how a
    list stops scrolling under a thumb.
  - **Three containers were capping things that are not sentences.** `SettingsLayout` at `7xl`, the
    two platform screens at `4xl`, and the channel *list* at `3xl` — which stopped two thirds across
    a 1440 window and put "Last delivered" in the middle of the page. All uncapped; the measure was
    already on the sentences.
  - **The alignment defect had a cause, and removing the shims had not fixed it.** `TextField` and
    `NumberField` put the label *above* a 34px box; `SelectField` put it *beside* a 26px box. In one
    row they cannot line up, which is what the `pb-1`/`pb-2` shims were guessing at — removed twice,
    because removing them treats the symptom. `SelectField` now has a **`stacked`** shape matching
    the text fields, applied to all ten selects that sit inside a form. Held by
    `shared/components/field-shape.test.ts`, which reads `src/` and names the next one; **seen red**
    by unsetting `stacked` on the invite form.
  - **The step-up box is centred.** `ConfirmItIsYou` sat at the top-left of an otherwise empty
    window, reading as a widget on a page that had failed to load the rest of itself.
  - **The customer detail screen was rebuilt.** House frame (header holds, body scrolls, full
    width); Ingest and Plan side by side from `xl`; Members and Projects are columns rather than
    run-on sentences; "View as" opens under its row instead of inside a 176px cell it could not fit.
    Channel detail is two columns from `xl` for the same reason.

## Next action

Founder: review the diff and say **push**. The platform screen's own record is
`docs/changes/platform-list-first/`.

## Open decisions

- **D12 — React Flow's attribution** on the service map. Removing it without a Pro licence would
  breach their terms, so it stays until that is a decision you have made.
- ~~**billing's committed-vs-monitored line**~~ — **it was a defect after all**, not a product
  decision: the screen charged for every service monitored while the mandate charged the committed
  count. Fixed in `docs/changes/commitment-is-the-bill-ceiling/`.
- **The audit log's sign-in noise**, the **General / Account split**, **signup's terms link** and
  **publishing the browser SDK** are each a product decision rather than a defect. They are listed with their reasoning in the review.
- ~~**Live suites that age out.**~~ **Decided 2026-09-21: seed the window.** See
  `docs/changes/gate-seeds-its-own-window/` — `just check` now puts what the suites need into the
  stack through the ingest path and waits for it, for 12.4 seconds.
- ~~**A channel that is gone offers "Try again"**~~, which can only ever fail again. Found by typing
  `/settings/channels/new` by hand — nothing links there — but the same dead end meets a customer
  whose bookmarked channel somebody removed. **Fixed**: `isNotFound` → "No such channel" with a way
  back, the same answer a deleted check's page has given since F18. Seen red first.

## Blocked

Nothing.

## Risks

- **D1 changes what a healthy project is told.** The risk is the opposite of the defect — a screen
  that cries wolf. It names only failing synthetic checks, which a probe confirmed, and says nothing
  about error rates, which are normal for most services and already on the screen as a reading.
- **D2 writes a row where none was written before.** Same table, same shape, same 30-day pruning as
  the outbox's rows; the column has a server default so nothing else had to change.
- **The settings rail now has a control.** One suite asserted "a viewer sees no buttons on this
  screen" and counted the new disclosure; it is scoped to the screen's own region now, which is what
  it meant.

## Validation

- `just check` re-run 2026-09-21 after step 8: **1334 web** (123 files), `mypy` clean (572 files),
  format, lint, Go, browser SDK and the contract checks clean. Four Python failures, all the same
  environmental family and none of them this change's — the diff for step 8 is `.tsx` only:
  - `test_alert_telemetry_live.py::test_error_groups_…` needs a span carrying an exception *event*.
  - Three in `test_metrics_live.py` need `k6.http_req_duration` histogram points inside a two-day
    lookback. **Measured**: the newest such point is `2026-09-19 06:38:56`, and the run was at
    `2026-09-21 06:44` — the data crossed the window *six minutes* after the green run earlier the
    same session. Nothing changed but the clock.
  - **This is worth a decision** (below): several live suites rest on demo data with a rolling
    window, so the gate turns red with the passage of time rather than with a defect.
- `just check` after step 7: **4118 Python passed, 2 skipped**; **1333 web**
  (122 files); **120 browser SDK**; Go, format, lint, `mypy` (572 files) and the contract checks
  clean.
  - An earlier run the same day reported **51 errors** and 4037 passed. That was the laptop, not
    the code: `recording_site.private_address()` opens a UDP socket to learn this machine's LAN
    address and got `[Errno 51] Network is unreachable`, and the connect guard's rebinding test
    needs `127.0.0.1.nip.io` to resolve. Both are suites that refuse to skip, on purpose — a skip
    there would be indistinguishable from a pass. With the network back the same command reports
    zero errors.
  - As before, two tenant-isolation tests want traffic in the default window; `just load`, then
    green. Not a defect — the suite says so itself.
  - **One live suite cannot pass on this machine today**, and it is not this change's doing:
    `test_alert_telemetry_live.py::test_error_groups_come_back_with_the_errors_screens_identity`
    needs a span carrying an exception *event* in the last 24 hours. Only the OpenTelemetry demo
    sends those here — `just load` cannot, and the newest such span is from 19 Sep 16:48, which
    aged out of the window mid-session. Proved rather than assumed: the same test fails on a clean
    worktree of `HEAD`, with the same diff absent. It passes again after `just otel-demo up`, which
    is 21 containers and ~2 GB and is deliberately not part of any gate.
- **Seen red before green**: the Findings headline (3 cases), the test-send delivery log (2 cases),
  and the platform-organization search exclusion from the previous change.
- **The width round was judged the same way**: Alerts, Account, General, Billing, Ingest keys,
  Members, Audit log and Usage re-shot at 1440 and read against the originals. Usage gained a column
  that had been cut off; the Ingest keys prose still wraps at ninety characters while its forms and
  key rows reach the right edge.
- **Looked at, not just tested.** The fixed screens were re-shot at 1440×900 and 390×844 and read
  side by side with the originals: APPLY is on screen, the settings rail is one row, the exception
  column is legible, the worst-services numbers have headings, the axis reads `9:22pm` rather than
  `9:22p`, Traces' headings sit over their values, and the operator's sidebar is one section.

## Specification impact

None. No specification names the behaviour any of these screens changed.

## Documentation impact

- `docs/quality/console-review-2026-09-20.md` carries each finding's outcome, including the two
  **withdrawn** after measurement (RUM's card grid and the sparkline gutter were not defects).
- `docs/product/setup/{index,api,browser}.md` — three callouts rewritten from our words into the
  customer's; none of them now names a repository path or an open decision.

- **Step 7 — the five things walking the flow turned up** (2026-09-21, founder's list). All fixed.
  - **The operator landed on whatever route the browser remembered** — `/traces` in the
    walkthrough, which answers "No such project" because their organization has none. `LoginScreen`
    now sends a `super_admin` to `OPERATORS_DESTINATION` (`/admin`), and the sidebar's wordmark
    takes them there rather than to a customer overview they do not have.
  - **`/admin` looked open before it was.** The step-up fired on the third keystroke of a search.
    The screen now reads on open (`platform-list-first`), so the confirmation belongs to entering
    the platform area — which is what it is for.
  - **A support view was told its own role could not do things.** `Refused.tsx` reads the
    `support_view` claim and says "A support view cannot open this." The rule was always right —
    a support view is denied `grant_support` so it cannot widen its own access — but the sentence
    blamed the owner's role.
  - **The seeded operator read "nexovue at nexovue"** in the banner and in the customer's audit log.
    `service.operator_label` collapses it: a name that is empty or is the company's own becomes
    "nexovue support", and a human name still reads "Raj at nexovue". Three cases asserted.
  - **Two stale messages corrected where somebody reads them.** `tools/e2e/tests/errors.spec.ts`
    said nothing in the worker writes `spans.exception_type` (P1-30, 2026-08-30);
    `traceassembly/block.go:109` has written it since. And the scheduler suite told a reader to run
    `just load`, which cannot produce the span event it needs — it names `just otel-demo up` now.

## Last updated

2026-09-21
