# Status: anomaly-detection

## Lifecycle status

Approved (specification, amended with D11 to D16). Design paused at the measurement D15 requires.

## Current task

Measuring the demo's real failure bursts and variability (spec D15), before the design is revised.

## Completed

- Read the code the Phase 2 plan names. It is summarised in `spec.md`, *Problem* and *Dependencies and
  constraints*.
- Read decision 0013 in full from git history (`e0f11a0^`).
- Researched current methods. See `spec.md`, *Research*.
- Wrote revision 1.
- Reviewed revision 1 with four independent read-only agents: on-call, statistics, architecture, and
  product and QA. Each finding they cited about the code was checked against the source before use. Their
  verdict was not ready: 5 blockers, about 15 major findings.
- The founder decided D1 to D4 (`spec.md`, *Decisions taken*). The agent proposed D5 to D10.
- Wrote revision 2, which answers the review.
- The founder approved revision 2, 2026-10-30.
- Simulated the detection method (FR9): 200 services over 4 judged weeks. False findings per service per week:
  0.06 at normal, 0.29 at high, 0.001 at low, and 0 in daily mode. One-minute spikes opened 0 findings in 120.
  Faults beyond critical were caught in 6 to 7 minutes. The full results are in `design.md`, *Simulation*.
- Wrote `design.md`, and proposed ADR-022 and ADR-023.
- Reviewed the design with five independent read-only agents: statistics, architecture, operations, security
  and tenancy, and product and QA. Their verdict was not ready, including blockers on:
  - when a minute is settled;
  - the outbox, which is bound to incidents;
  - the scheduler's read-only ClickHouse account;
  - the feed's healthy state.

  The code claims were checked against the source. A Slack, Teams and Discord injection bug found there
  was fixed separately in [nexovue-tech/nexovue#20](https://github.com/nexovue-tech/nexovue/pull/20).
- The founder decided D11 to D16. The spec was amended and committed (`b4f4ceb`).
- The harsher simulation (`sim2`): 1.65 false findings per service per week at normal sensitivity, after its
  one fix. The cause is short failure bursts at a guessed rate, hence D15.
- Wrote `tools/detection-measure/` (`just detection-measure`), which reads the dev stack's rollup and
  reports real burst rates and variability. It has 6 offline tests.

## Next action

The founder runs `just dev` for at least 3 days on a machine that stays awake, then runs
`just detection-measure` and sends back `detection-measure.json`. From it, the founder decides whether short
bursts are reported or treated as noise (D15). Then the design is revised against all five reviews, the
simulation is rerun with the measured rates, and the design returns for approval.

## Open decisions

- Short failure bursts: report them, or treat them as noise. This waits on the measurement (D15).
- Where the simulations live (`design.md`, *Open questions*). They are now only in the session's scratch
  space.

## Blocked

AC37, the two-week no-fault run, waits on the test server's price (`phase-2-plan.md`, Open 2).

## Risks

- *Product values* are judgement from published practice, not measurement. AC36 and AC37 tune them.
- The demo's traffic is not customer traffic (`phase-2-plan.md`, *Risks*).
- BR5 needs new work on the workers: a per-partition "written through" mark. Nothing like it exists today.
- Grafana's framework post was read only through a search summary.
- The simulation is synthetic traffic. It has no endpoints, deploys or daylight-saving changes.
- The read cost and storage per pass are estimated, not measured. Plan Step 1 measures them.
- Both simulation scripts, and their results, exist only in the session's scratch space. They are lost if
  the container is reclaimed. Their results are recorded here and in the founder's chat.
- `design.md`, ADR-022 and ADR-023 are the first draft, which the reviews rejected. Revision 2 waits on D15.

## Validation

None. No code exists. The review findings were checked against source, but no tests were run.

## Specification impact

- New specification.
- Amends F21's BR10 and its use of incidents, and F1's AC1.8 (`spec.md`, *Dependencies and
  constraints*). Both are recorded in their own specs when the design is approved.

## Documentation impact

- `phase-2-plan.md`'s status line now records the founder's approval of the plan.
- `feature-catalog.md` now says the same, and links this specification from F6.

## Last updated

2026-10-30
