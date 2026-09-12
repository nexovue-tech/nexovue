# Status: template-gaps

## Lifecycle status

Verified

## Current phase

None — a single-phase change.

## Current task

None.

## Completed

- `spec.md` gains Problem, Goal, Target users, User requirements, Business rules and **Error
  cases** — fourteen sections, with a header instructing the author to delete what does not apply
  rather than answer it "N/A".
- `design.md` gains User experience and UI structure — the two the backend-shaped form lacked, and
  the reason console-redesign's UX work lives in unreferenced sibling files.
- `plan.md` gains Phases, carrying ADR-0019's sequencing rule, and a Design QA gate.
- `status.md` gains Current phase, Open decisions, Risks, Specification impact and Documentation
  impact — every one of which was invented by hand while writing console-redesign.
- Each new section carries a one-line prompt, so an empty one reads as empty rather than as
  considered.

## Next action

None for this change.

Raised by it and not done here: **there is no Design QA step anywhere.** `plan.md` now names the gate
and nothing performs it. `review-diff` checks invariants — tenancy, ordering, tokens, states — not
whether a built screen matches its approved design. That is a separate record, and console-redesign
phase 1 is the first work that will need it.

## Open decisions

None.

## Blocked

Nothing.

## Risks

**Fourteen sections on `spec.md` invites box-filling.** A section answered "N/A" is worse than one never asked,
because it reads as considered. Whether the prompts prevent that is an observation to make over the
next two or three features, not something this change can prove.

Existing workspaces were deliberately not retrofitted. If that turns out to make the older ones hard
to read beside the newer, retrofitting is cheap and can be done then.

## Validation

`just check` on 2026-09-13, exit code 0, output read: 2277 Python tests, 120 browser-SDK across 12
files, 613 web across 47 files, plus `buf lint` and `buf breaking`. No code was touched.

`docs/templates/README.md` re-read and still describes each form correctly; the additions are
sections within the existing four, not new documents.

## Specification impact

None.

## Documentation impact

`docs/templates/` only. The templates are copied into workspaces rather than referenced, so existing
features are unaffected until their next revision.

## Last updated

2026-09-13
