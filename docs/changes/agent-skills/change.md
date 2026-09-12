# Change: agent-skills — workflow document and Claude Code skills

## Classification

Operations (engineering system)

## Current behavior and problem

`AGENTS.md` carried both the permanent working rules and the full SDD lifecycle: the five starting
steps, the proportionate-workflow list, the lifecycle states, and the founder-workflow table. It is
loaded in full at the start of every session, so a CSS fix paid the same cost as a migration.

More importantly, the lifecycle had no other home. Any attempt to give an agent a repeatable
workflow — a Claude Code skill, a checklist, a second agent's instructions — would have had to copy
it, producing a second definition that drifts from the first. The same trap applied to
`docs/quality/code-review.md` and `docs/quality/production-readiness.md`, whose criteria a review or
readiness skill would naturally have restated.

## Intended behavior

One boundary, applied everywhere: **a skill is a verb and a standard is a noun.** A standard says
what is correct. A skill says what to do, in what order, and what to hand back — and points at the
standard rather than restating it.

- The lifecycle moves to `docs/engineering/workflow.md`, which is agent-agnostic and therefore
  readable by Codex, other agents, and humans, not only Claude Code.
- `AGENTS.md` keeps the permanent rules and the trigger to follow the workflow, and links to it.
- Four Claude Code skills under `.claude/skills/` supply the procedure, the approval gates, and the
  report shape. Each names the documents that own the rules; none restates them.

## Affected specifications and architecture

None. No product behavior, API, schema, or service is affected. `docs/changes/sdd-adoption/`
established the document structure this change reorganizes.

## Risks and rollback

- **Drift between a skill and the standard it points at.** Mitigated by the verb/noun boundary: a
  skill that starts restating a rule is the defect, and the fix is to delete the restatement.
- **Skills are Claude Code only.** This is why the lifecycle itself did not move into
  `.claude/skills/`. An agent that cannot read skills still gets the whole workflow from
  `docs/engineering/workflow.md`.
- **Name collision.** The skills are deliberately named `review-diff` and `readiness-check` rather
  than `code-review` and `production-readiness`: a project skill's command name comes from its
  directory name, and `/code-review` is already a built-in multi-agent reviewer worth keeping.

Rollback is deleting `.claude/skills/` and reverting two documents. Nothing depends on it at runtime.

## Plan and validation

1. Create `docs/engineering/workflow.md` from the content removed from `AGENTS.md`.
2. Reduce `AGENTS.md` to the permanent rules plus the trigger and link.
3. Add the four skills.
4. Update the engineering, changes, and Claude Code entry-point indexes.
5. Verify every markdown link and cited path resolves, and that `just check` is unaffected.

## Status

See [status.md](status.md).
