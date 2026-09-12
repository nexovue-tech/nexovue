# Status: task-plan-gate

## Lifecycle status

Implemented

## Current phase

None — a single-phase change.

## Current task

None. The first real use is console-redesign phase 1.

## Completed

- `docs/templates/task-plan.md` — ten sections, Outcome filled in after the phase rather than before.
- `.claude/skills/task-plan/SKILL.md` — reads the documents, then the code, then checks what is
  already built, then narrowly checks versions.
- Wired into `docs/engineering/workflow.md` (agent-agnostic), `feature-work` as a gate before
  implementation, and named in the plan template, the templates index and `CLAUDE.md`.
- `feature-work`'s steps renumbered; its closing step now fills in Outcome and ticks the phase.

## Next action

Run `/task-plan` on console-redesign phase 1 and put it in front of the founder.

## Open decisions

None.

## Blocked

Nothing.

## Risks

**A fourth gate.** Phase 1 is one day across six files; the gate may cost more than it saves there.
It earns its cost on phases 4 and 5, at five screens each.

**The format is unproven.** Ten sections written from two observed failures is a hypothesis. Whether
it produces a reviewable artifact is answered by using it once, not by writing it well.

## Validation

`just check` on 2026-09-13, exit code 0, output read: 2277 Python tests, 120 browser-SDK across 12
files, 613 web across 47 files, plus `buf lint` and `buf breaking`. No code was touched.

Every markdown link in the added and changed documents resolves, checked by script.

**Not verified:** whether the `task-plan` skill fires when it should, and whether its output is
actually reviewable. Both are observations from first use.

## Specification impact

None.

## Documentation impact

`docs/engineering/workflow.md`, `docs/templates/README.md`, `docs/templates/plan.md`, `CLAUDE.md`
and the `feature-work` skill all name the new gate. No document now describes a workflow without it.

## Last updated

2026-09-13
