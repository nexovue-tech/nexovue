# Status: anomaly-detection

## Lifecycle status

Approved: specification (amended through D21) and design (revision 2, 2026-10-31). The plan is next.

## Current task

The plan gate.

## Completed

**The specification**

- Read the code the Phase 2 plan names, and decision 0013 in full from git history (`e0f11a0^`).
  Researched current methods (`spec.md`, *Research*).
- Spec revision 1 went to four independent reviews, which found 5 blockers and about 15 major findings.
  Revision 2 answers them, and the founder approved it on 2026-10-30, with D1 to D10.
- The founder later added D11 to D20, recorded in `spec.md`, *Decisions taken*. D15 was superseded by D17.

**The design**

- Design revision 1 went to five independent reviews: statistics, architecture, operations, security and
  tenancy, and product and QA. They found it not ready. Every claim they made about the code was checked
  against the source.
- The security review found a separate injection bug in Slack, Teams and Discord messages. It was fixed
  and merged in [nexovue-tech/nexovue#20](https://github.com/nexovue-tech/nexovue/pull/20).
- Design revision 2 answers every review finding. Each section names the finding it answers. It proposes
  ADR-022 and ADR-023.

**The evidence**

- `tools/detection-sim/` is a standalone uv project, numpy and scipy, with its own strict mypy. The root
  mypy excludes it.
- The D17 run is in `design.md`, *Simulation*, and in `tools/detection-sim/RESULTS.md`.
- `tools/detection-measure/` (`just detection-measure`) is for the plan's demo run (D14, D17).

## Next action

Write `plan.md` with `/write-plan`: ordered steps, files and tests read from the code. Step 1 is the
demo run and the cost measurement (D14, AC29), as the design says.

## Open decisions

None.

## Blocked

AC37, the two-week no-fault run, waits on the test server's price (`phase-2-plan.md`, Open 2).

## Risks

- **Real failure bursts are unknown.** Every burst rate, length and height is a guess until the plan's demo
  run (D14, D17). The simulation covers a range of guesses, not the real value.
- **The traffic is synthetic.** It has no deploys, daylight-saving changes or late data, and no user
  actions.
- **Per-pass cost and storage** are estimated, not measured. Plan Step 1 measures them.
- **Nothing is pushed.** Commits on `feature/anomaly-detection` exist only in this container until the
  feature's final push, by the founder's decision.

## Validation

- Simulation runs are recorded with their seeds in `tools/detection-sim/RESULTS.md`.
- `ruff` and the tool's own strict mypy pass. The root mypy passes (650 files).
- No product code exists yet.

## Specification impact

- New specification.
- `design.md`, *Specification impact*, lists the amendments it would make to FR28, FR29, F21, F1 and
  `phase-2-plan.md`.

## Documentation impact

- `phase-2-plan.md` and `feature-catalog.md` record the plan's approval and link this spec.
- The ADR index lists ADR-022 and ADR-023 as proposed.

## Last updated

2026-10-31
