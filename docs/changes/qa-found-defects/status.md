# Status: seven defects a hands-on QA pass found outside the feature it was testing

## Lifecycle status

**Implemented.** Committed after F18, which the QA pass that found these was testing.

## Current task

None. The seven are fixed, each with a regression test observed red before the fix.

## Completed

| Defect | Fix | Test |
|---|---|---|
| SeaweedFS health check probed the master port | `deploy/compose/compose.infra.yaml` probes `:8333/status`, the S3 gateway the API calls | `apps/api/tests/test_compose_dependencies.py` — and it lists which services a dependant gates on, so the next one is not forgotten |
| `RefreshControl` set state during another component's render | `app/layout/RefreshControl.tsx` reads the cache in a microtask, outside the render that caused the event | `app/layout/RefreshControl.test.ts` — asserts on `console.error`, where React reports it |
| The unsaved-changes dialog was unreachable by keyboard | `shared/components/UnsavedChanges.tsx` takes focus on the safe action, traps Tab, answers Escape by keeping the work, and gives focus back | `shared/components/UnsavedChanges.test.ts` — four behaviours, all four red first |
| Two `<main>` landmarks on settings screens | `shared/settings/SettingsLayout.tsx` renders a column, not a second landmark | `app/landmarks.test.ts` — reads the source, so tomorrow's component cannot reintroduce it |
| Series colours 20° apart read as one | New hues (200, 105, 300, 265, 335) with `--series-6` separated by lightness; `packages/ui/tokens.css` and `docs/design/design-system.md` both updated | `packages/ui/scripts/verify-contrast.js` measures every series pair and every series against the reserved hues |
| A cancelled request logged as "Request timed out" | `shared/api-client.ts` tells the caller's abort from our timeout, and logs nothing for a cancellation | `shared/api-client.test.ts` — three endings, three messages |
| `test_metrics_live.py` compared a delta with a cumulative total | The comparison is now delta against delta: the largest rise any one series made inside the window | the test itself |

## Next action

The seventeen undeclared dependencies frozen in `UNDECLARED_TODAY` need a decision each — see
*Risks*. Nothing else here is outstanding.

## Open decisions

None. One judgement worth naming: the palette needed six distinct colours and the reserved bands
(red 15–40°, amber 55–85°, brand green 130–175°) leave roughly 230° of hue, so six cannot sit 60°
apart. Five carry hue separation and the sixth is a lightness step on the first — the technique the
design system already uses for critical ↔ warning. If you would rather have six hues and a narrower
reserved band, that is a design decision and this is the place to overturn it.

## Blocked

Nothing.

## Risks

- **The palette change is visible in every chart**, not only the one that exposed the defect: the
  service map, the metrics histogram, the duration heatmap and the trend charts all draw from these
  tokens. The contrast gate passes in both themes and the old values are recorded in the diff.
- **The `useNothingLive` gap is wider than this change.** F18's two form screens now declare
  themselves non-live; the settings screens still show a refresh interval that governs nothing.
  Recorded rather than fixed, because it is a screen-by-screen judgement.
- **`_NO_SINGLE_PORT` in the new compose test is a list, not a rule.** A service whose health check
  genuinely cannot be reduced to one port has to be added to it by hand, with a reason.
- **Seventeen other services name a service they do not wait for**, found by the new rule and frozen
  in `UNDECLARED_TODAY` rather than fixed. They are not all the same defect: `web` naming `api` is
  deliberate — the console loads and explains itself while the API is down — while `worker` naming
  `postgres` looks like the real thing. The list may only shrink, and the test fails if one is
  fixed without the record being updated. **This is the decision this change deliberately did not
  take on your behalf.**
- **`shared/api-client.ts` is touched by both this change and F18** (F18 adds a `file?: Blob` option
  for uploads; this change rewrites the failure classification). The two are independent, but the
  file appears in both diffs.

## Validation

- **`just check`: exit 0** — the first fully green run, on 2026-09-18. Python **3147 passed, 3
  skipped**; web **1167 passed** (95 files); browser SDK **120 passed**; Go every package `ok`;
  ruff, mypy, biome, `tsc`, `buf lint` and `buf breaking` clean. The metrics test that was red
  before this change is now green because it compares comparable numbers, not because the data
  changed.

### What the review of this change found, and what it changed

`/review-diff` on these fixes found three HIGH defects **in the fixes themselves**. All are now
corrected, and the counts above are from after that:

- **The health-check fix left the defect live where it mattered most.** `docker-compose.app.yaml` —
  the production replica — never declared a `seaweedfs` dependency at all, so a correct health check
  gated nothing there. Seven service/file pairs named `seaweedfs:8333`; exactly one waited for it.
  All seven now do, and the test enforces the rule that catches it: **a service that names another
  waits for it**. The first version of that test asked the question the other way round and passed.
- **I destroyed six existing tests.** `UnsavedChanges.test.ts` was a tracked file with six AC19
  navigation tests in it, and I wrote my new ones over the top — which the record then called a new
  file. The six are restored and mine sit beside them; the file now has twelve.
- **The focus trap was one click from useless.** Escape and Tab were handled on the dialog element,
  so a click on the scrim moved focus to `<body>` and the dialog went inert again — the reported
  defect, re-entered by the back door. They are on the document now, with a `focusin` guard that
  pulls focus back, and two new tests cover exactly that.

Also corrected from the same review: the contrast table in `docs/design/design-system.md` still
carried the old palette's measured numbers (**0 drift** now); a cancellation that landed while the
response body was still streaming escaped as a raw `DOMException` rather than an `ApiError`; the
classifier now tests that the error *is* an abort rather than that one happened to be in flight; the
`RefreshControl` test reproduced the defect only because of a random query key, and now reproduces
the navigation sequence that causes it in the product; `phase-series.ts` kept a palette-reordering
workaround whose reason the new palette removed; and the record named a test file that does not
exist.
- **The cold start, which is the whole point of defect 1**: `docker compose down` then
  `up -d --wait` returned **exit 0** with `nexovue-api-1 healthy, restarts=0`, and the API log holds
  no "could not prepare the bucket" and no connection refusal. Before the fix the same command
  ended with `container nexovue-api-1 is unhealthy`. Run again after the schedulers were gated on
  `seaweedfs` as well: **exit 0**, every service up, no bucket error.
- **Each regression test was observed failing first**, with the failure recorded:
  - compose: `seaweedfs's health check probes something other than port 8333`
  - refresh control: `Cannot update a component … RefreshControl ScreenWithANewQuery`
  - dialog: all four assertions red
  - landmarks: `["shared/settings/SettingsLayout.tsx"]`
  - palette: `FAIL series 1 <-> 2  Δh 20°` and `FAIL series-2 vs brand green  Δh 23°`
  - api client: `calls a caller's cancellation a cancellation, and logs nothing`
- **The metrics assertion's arithmetic, measured** rather than argued. Over a ten-minute window the
  largest single cumulative point is 1,778 while the largest rise any series actually made inside
  that window is 531: the old assertion demanded a correct read return at least 1,778, which is the
  `assert 543 >= 2423` it failed with.
- **In the browser after the change**: the five phase colours read as five colours (cyan, violet,
  magenta, olive, indigo) in dark and light; the unsaved-changes prompt takes focus and closes on
  Escape.
- **End to end (`just e2e`), twice**: the four synthetics specs passed every run. Three specs failed
  on the first run and one on the second — a different one each time (`errors`, `services`,
  `service-map`, then `first-span`), each waiting on exported telemetry to become readable, and each
  **passing on its own** immediately afterwards (13s, 17s). The stack had just been cold-started for
  the health-check proof, so the workers were rebalancing while sixteen specs and the demo's own
  traffic competed for the pipeline. Recorded rather than dismissed: this suite is sensitive to how
  loaded the stack is, which is a property of the suite worth knowing before it is trusted in CI.

## Specification impact

`docs/design/design-system.md` gains the series-separation rule it never stated and the new token
values. Nothing else changes a requirement.

## Documentation impact

- `docs/design/design-system.md` — palette values, the reasoning for the sixth series, and the
  separation table.
- `docs/features/synthetic-checks/status.md` — its *Validation* section listed these seven as found
  and left; it now points here.
- `docs/changes/README.md` — this record.

## Last updated

2026-09-18
