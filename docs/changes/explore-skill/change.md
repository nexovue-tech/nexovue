# Change: explore-skill — the step before the first gate

## Classification

Operations (engineering system)

## Current behavior and problem

Both existing workflow skills assume the requirement is already known. `feature-work` opens with
"understand before proposing" and reaches a specification gate immediately; `change-work` assumes a
defect or a defined change. Neither covers the case that actually precedes most real work: an idea
that is not yet a requirement.

That left two holes.

**Exploration had no discipline.** Thinking an idea through in conversation drifts into
implementation — once code exists the idea stops being negotiable, because arguing with a file is
harder than arguing with a sentence. It also blurs which decisions are the founder's: a product
question settled in passing has been decided by the agent.

**Exploration produced nothing durable.** `AGENTS.md` states that chat history is never the source
of truth, but an unstructured brainstorm leaves its conclusions exactly there. The same ground then
gets re-covered months later, which is what happened to the 112 deleted decision records.

`AGENTS.md` rule 8 already says to stop rather than guess about material uncertainty. It does not say
what to do next, or where the answer is written down.

## Intended behavior

An `explore` skill covering the step before `Proposed`, and a section in
`docs/engineering/workflow.md` so the step exists for agents that cannot read skills.

It is mostly negative discipline, deliberately: no code, no specification, no workspace, no invented
requirement, and no single option presented as the only one. What it adds positively is grounding —
reading the catalog, the decision log and the code before inventing — and an explicit split between
what the founder decides and what the agent recommends.

It ends in one of four outcomes, each with an existing home: an ADR, a catalog or specification
entry, a recorded decision not to build, or the naming of something missing that must exist first.

**No new document type.** Exploration output goes where such output already belongs. Creating an
`exploration.md` template would violate the rule about not inventing a home for something that has
one, and would add a document whose only reader is the next step.

## Affected specifications and architecture

None. No product behavior, API, schema, or service changes.

## Risks and rollback

**A fifth skill is real cost.** The set was deliberately small and each addition dilutes the
description matching that decides which one fires. This one earns it because the gap it fills sits
directly in front of the next piece of work: F1 Findings has a complete specification but depends on
an insight engine that has no specification, no catalog entry, and no code.

**Auto-invocation may misfire** in both directions — firing on a clear request that should have gone
to `feature-work`, or not firing on a vague one. The description names the distinction explicitly.
Whether it works is an observation to make over the next few sessions, not something this change can
prove.

Rollback is deleting one skill and one documentation section.

## Plan and validation

1. Write the skill.
2. Add the exploration section to `docs/engineering/workflow.md` for agent-agnostic readers.
3. Wire the skills table in `CLAUDE.md`.
4. Verify links resolve and `just check` is unaffected.

## Status

See [status.md](status.md).
