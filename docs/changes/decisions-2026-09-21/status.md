# Status: decisions-2026-09-21

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- **Eight decisions recorded** in `docs/product/vision.md` §6 — two the founder made, six on
  recommendation.
- **React Flow's attribution hidden**, on the MIT text read from the installed package rather than
  on the review's claim about it.
- **The audit log opens on "Changes only"** — a filter defined by subtraction, proved by a test that
  asserts an unclassified action still appears.
- **The stale allowance docstring corrected** in `usage/billing.py`, and the contradictory comment
  on the billing screen: it promised the bill would "later move back to services actually
  monitored", directly above copy reading *"We never bill past it and never charge overage."*
- **The plan-change copy says the order it happens in.** It read "cancel above, then choose the new
  plan here" beside plans that cannot be pressed until the cancellation lands — a sentence naming a
  control the customer could see and not use.
- **Documentation caught up with history.** Nine status files claimed work was uncommitted or
  awaiting review that had already shipped, including the one that sent this session chasing an
  already-fixed race.

## Next action

Founder: review the diff and say **push**. Then decisions 7 and 8 are the next two changes, in
either order.

## Open decisions

- **Decision 7 — merge General into Account, and give the organization its own screen.** Decided,
  not built. It moves routes that `route-manifest.test.ts` and `unsaved-changes-coverage.test.ts`
  hard-code.
- **Decision 8 — publish the browser SDK to npm.** Decided, not built. Needs `"private": true`
  removed, the permissive licence file ADR-004 requires, a published build, and the setup docs
  rewritten.

## Blocked

Nothing.

## Risks

- **The audit log now opens on a subset.** It hides only sign-ins and says so, but a reader who does
  not notice the control could believe they see everything. The filter subtracts rather than lists
  precisely because the other design's failure mode — a new action kind quietly missing from an
  audit log — is unbounded and silent.
- **Hiding a vendor's badge is a courtesy declined.** Legally clean on the MIT text; whether it is
  the founder's preference is his call, and it is one word.

## Validation

- `just check`, exit 0: **4130 Python passed, 2 skipped**; **1335 web** (123 files); **120** browser
  SDK; Go, format, lint, `mypy` (572 files) and the contract checks clean.
- **Seen red before green** — removing the `changes` branch: *"a sign-in survived the changes
  filter: [… 'session.created', 'session.created' …]"*.
- **The licence was read, not recalled**: `@xyflow/react` 12.11.5, `license: MIT`, and the LICENSE
  file is the unmodified MIT text with no display clause.
- **The overage question was checked in both languages** before being called closed:
  `usage/billing.py:allowance_service_count` and
  `apps/receiver/internal/allowance/postgres.go:allowanceServiceCount` are the same rule, and
  `usageFrom` sets `Enforced: true` whenever a count sizes the allowance.

## Specification impact

None. `docs/product/vision.md` §6 gains the decision row; §5's overage paragraph was already
corrected by `docs/changes/commitment-is-the-bill-ceiling/`.

## Documentation impact

`docs/product/vision.md`, `docs/quality/console-review-2026-09-20.md` (the React Flow claim),
`docs/changes/alert-links-to-incident/status.md` (the fixed race), `docs/features/{alerts,
profiling,team-and-api-access}/status.md` and seven change statuses now say what is true.

## Last updated

2026-09-21
