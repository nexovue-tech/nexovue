# Bug: the whole browser suite had never run on Phase 2

## Reported behavior

`docs/quality/quality-gates.md` requires `just e2e` for any change to `apps/web/src`. No CI job runs
Playwright, and during Phase 2 only single specs were run, feature by feature. On 2026-11-27 the
whole suite ran for the first time on `feature/phase-2`, on the founder's Mac against the dev stack:
69 passed, 12 failed, 4 skipped.

## Reproduction

`just e2e` with the dev stack up (`just stack`, seeded) and no traffic.

## Root cause

None of the 12 is a product defect:

- `dashboards-drag.spec.ts`: the keyboard move straight after a drop was ignored. `@dnd-kit/core`'s
  pointer sensor stops every click for 50 ms after a drag ends (`PointerSensor.detach`, a
  `setTimeout(removeAll, 50)` on its document click listener), so the drop's own release is never a
  click. The test pressed Enter inside that window; with the window passed, the move works.
- `alerts.spec.ts`: Phase 2 added two default rules (*Critical problems*, *Reliability targets*), so
  "Default" matched three rows.
- `chat-broken.spec.ts` and `layout-widths.spec.ts` (3): the Ask button is not offered on a hosted
  install (`useOffersAssistant`, foundation FD19), and the dev stack runs `NEXOVUE_DEPLOYMENT=hosted`.
  F4 Step 8 ran them on a self-hosted stack, where they passed.
- `fix-section.spec.ts` (5): controls under 44 px at 375 px, known and approved as a separate change
  after Phase 2 (F5 decision 120).
- `sign-in.spec.ts`: no traces in the last hour; the spec says so and names `just load`.

## Specification impact

None.

## Regression test

The specs themselves. `dashboards-drag.spec.ts` failed every time (two runs alone) and passes with
the wait; a probe confirmed the click reached the button and was dropped by the library's guard.

## Fix plan and validation

- The drag spec waits 150 ms (the library's 50 ms plus margin) before the keyboard move.
- The alerts spec reads the *Any check down* rule's own row.
- The two chat walks skip on a hosted install with a stated reason (`support/deployment.ts`), as
  `assistant.spec.ts` already skips without its fake provider. They run in the self-hosted live test.
- The 375 px cases that fall short of 44 px are `test.fail` naming decision 120, so the suite turns
  red when they are fixed and the mark must go.
- Validated: `just load`, then `just e2e`: 77 passed, 8 skipped, 0 failed; `just lint-web` passes.

## Status

Fixed; the chat walks are owed on the self-hosted live test.
