# Change: console-review-fixes — what the whole-product review found, corrected

## Classification

Bug (several), with layout and copy corrections in the same pass.

## Current behavior and problem

[`docs/quality/console-review-2026-09-20.md`](../../quality/console-review-2026-09-20.md) is a
review of every screen in the console — 46 routes at two widths, light and dark, with a scripted
pass measuring boxes, labels and accessible names. It found twelve defects (D1–D12) and a set of
layout and copy inconsistencies. Founder: **fix them all** (2026-09-20).

The two that matter are places where the console states something untrue about the customer's own
system:

- **D1** — Findings prints "All systems normal" while four of the project's checks are down and
  four critical incidents are open, because its feed is empty by construction.
- **D2** — a channel says "Last delivered 21 hr. ago" directly above a delivery log that promises
  *every* attempt of the last 30 days and shows none, because a test send never records one.

The rest are: a filter that runs off the screen on a phone, timings charted for runs that never
connected, a download button that names the wrong number, clipped chart labels, misaligned table
headers, a settings rail that buries the screen on mobile, four different page-column rules, and a
list of customer-visible copy defects including internal jargon in the product and the docs.

## Intended behavior

Each finding is corrected at its cause, and the screens keep the voice the review praised. Grouped:

1. **Truth** — D1, D2, D4, D5: no screen claims more than it knows.
2. **Responsive** — D3 and the phone-width findings: nothing overflows, nothing is buried, and the
   controls can be hit with a finger.
3. **Layout** — one column rule for every screen, and the alignment, gutter and grid corrections.
4. **Copy and labels** — D9, D10, D11, and the operator's sidebar (D8).

**Not fixed, and why:** D12, the React Flow attribution, is a licensing decision rather than a
defect — removing it without a Pro licence would breach their terms. Signup's missing terms link
needs a terms page to link to. Both stay in the review as decisions for the founder.

## Affected specifications and architecture

None. No behaviour a specification names changes; the Findings headline (D1) is the one place where
a screen's *claim* changes, and it moves toward what F1 AC1.7 and AC18 already require.

## Risks and rollback

- **D2 writes a row where none was written before.** `notification_attempts` is pruned after 30
  days and already carries the outbox's rows; a test send adds one of the same shape.
- **D1 changes what the healthy state says.** The risk is the opposite of the defect — a screen
  that cries wolf. The headline is derived from readings the screen already has, and says nothing
  when it has none.
- Rollback is `git revert`; nothing here has a schema or a store change.

## Plan and validation

[plan.md](plan.md). Validation and residual risk in [status.md](status.md).

## Status

In development.
