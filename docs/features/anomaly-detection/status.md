# Status: anomaly-detection

## Lifecycle status

Approved: specification revision 2, approved by the founder 2026-10-30, including D5 to D10.

## Current task

None. The specification gate is passed; the design has not started.

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

## Next action

On the founder's word, write `design.md` from `docs/templates/design.md`. Its first task is the
simulation FR9 requires.

## Open decisions

None.

## Blocked

AC37, the two-week no-fault run, waits on the test server's price (`phase-2-plan.md`, Open 2).

## Risks

- *Product values* are judgement from published practice, not measurement. AC36 and AC37 tune them.
- The demo's traffic is not customer traffic (`phase-2-plan.md`, *Risks*).
- BR5 needs new work on the workers: a per-partition "written through" mark. Nothing like it exists today.
- Grafana's framework post was read only through a search summary.

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
