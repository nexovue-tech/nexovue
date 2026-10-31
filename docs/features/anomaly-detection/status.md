# Status: anomaly-detection

## Lifecycle status

In development. Specification (through D32), design (revision 2.1) and plan (revision 2) approved.

## Current task

Building the plan, step by step, without stopping (the founder, 2026-11-01).

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

**The persona reviews, 2026-11-01**

- Six read-only reviews read the spec, design and plan: a solo developer, an on-call lead, the implementing
  engineer, security, QA and a startup advisor. They found 8 defects, including a blocker that let outsiders
  switch detection off, plus about 20 smaller fixes.
- The founder decided D22 to D32. Stage 1 now ships on its own (`phase-2-plan.md`).
- The spec, design (revision 2.1) and plan (revision 2) are updated to match.

**The build**

- **Step 2a**, the worker's per-service rollup, progress record and three ClickHouse migrations, was built on
  2026-11-01. Its checks:
  - 27 worker packages pass, and `just lint-go` reports 0 issues;
  - the erasure and tenancy structure tests pass (23);
  - `test_erasure_live.py` passes (6), against the dev stores;
  - the progress store-probe and the migrations probe pass against the dev ClickHouse;
  - `ruff` passes, and mypy reports no issues in 651 files.
- **Step 2b**, the settled minute, the project-bound store and the writer account, was built on 2026-11-01.
  Its checks:
  - 163 new or changed tests pass, the live ones included;
  - red-then-green for the lowered-limits override, three planted structure violations, and the upgrade
    secret;
  - across the repo, ruff passes, mypy reports no issues in 662 files, and the offline Python suites give
    3,869 passed and 1 skipped.

  The plan's 2b demo check, "the scheduler's log names the settled minute", needs T2c.3's coordinator. It is
  proved there.
- **The dev stores run in this environment**, through a local compose override for the sandbox's open-file
  limit. The application images cannot be built here, because the network blocks `ghcr.io` blobs. So an
  end-to-end check on the stack needs the worker run from source, or your Mac.

**The evidence**

- `tools/detection-sim/` is a standalone uv project, numpy and scipy, with its own strict mypy. The root
  mypy excludes it.
- The D17 run is in `design.md`, *Simulation*, and in `tools/detection-sim/RESULTS.md`.
- `tools/detection-measure/` (`just detection-measure`) is for the plan's demo run (D14, D17).

## Next action

The founder approves `plan.md` revision 2, including corrections C1 to C26, or changes it. Then Step 1 (the
demo run on the founder's Mac) and Step 2a start.

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
