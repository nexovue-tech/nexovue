# Status: agent-skills

## Lifecycle status

Implemented

## Current task

None.

## Completed

- `docs/engineering/workflow.md` created: the starting/resuming steps, the proportionate workflow,
  the lifecycle states, the founder-workflow table, and the handoff form — one agent-agnostic copy,
  readable by Codex and human contributors, not only Claude Code.
- `AGENTS.md` reduced from 12,667 to 10,251 bytes. It keeps the fourteen permanent working rules,
  source of truth, the standards routing table, commands, invariants, service rules, validation, and
  approval boundaries, and now links to the workflow instead of restating it. Two stale
  cross-references corrected: "the workflow below" and the standards count.
- Four skills added under `.claude/skills/`: `feature-work`, `change-work`, `review-diff`,
  `readiness-check`. Each carries the procedure, the approval gates, and the report shape, and names
  the document that owns each rule rather than copying it.
- `review-diff` and `readiness-check` are named to avoid colliding with the built-in `/code-review`
  command, whose name a project skill directory would otherwise claim.
- Indexes updated: `docs/engineering/README.md`, `docs/changes/README.md`, and `CLAUDE.md`, which now
  records the verb/noun boundary that keeps a skill from forking a standard.

## Next action

None for this change. The skills load on the next Claude Code session, or on `/reload-plugins` in a
running one.

Unrelated and still open, from `docs/changes/sdd-adoption/status.md`: select the first feature from
`docs/product/feature-catalog.md`, and decide the CI provider and release policy.

## Blocked

Nothing.

## Validation

`just check` on 2026-09-12, exit code 0, output read rather than inferred: 2277 Python tests passed
in 123s, 120 browser-SDK across 12 files, 613 web across 47 files, plus `buf lint` and `buf
breaking`. Identical counts to the run recorded in `../sdd-adoption/status.md`, which is the expected
result for a change that touches no code.

Link and path checking was scripted, not eyeballed: all 31 markdown links across the changed and
added documents resolve, and all 47 repository paths cited inside the four skills exist. Two paths in
`feature-work` were written relative to a service directory and are now fully qualified so the same
check covers them.

Not run, and not required by this change: `just e2e` and `just chaos`, which need a running stack and
exercise no path this change touches.

## Last updated

2026-09-12
