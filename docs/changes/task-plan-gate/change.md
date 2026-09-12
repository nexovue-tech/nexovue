# Change: task-plan-gate — the step between an approved plan and the first edited file

## Classification

Operations (engineering system)

## Current behavior and problem

`feature-work` step 5 read "Implement" and then listed standards to read. Nothing sat between an
approved plan and the first edited file.

That gap is not theoretical. `plan.md` approves work at **phase** granularity: console-redesign's
phase 1 is six checkboxes, one of which — *"/services rebuilt on the ranked-entity row"* — is a day
of work across six files. A founder reviewing that checkbox cannot see which files change; they find
out when the diff arrives, which is the same objection already raised about wireframes, one level
down.

The gap has a second cost, and this feature demonstrated it twice:

- **Work already done was planned as work.** Two of phase 1's tasks — exposing latency quantiles and
  batching the series read — turned out to be implemented already. They were listed as work because
  nothing required reading the code before writing the plan.
- **A misreading propagated.** A grep anchored to indentation skipped every schema field declared
  after a comment line, and this codebase comments between fields constantly. A non-existent backend
  gap reached five documents and four commits before reading one file corrected it.

Both are the same failure: planning from documents about the code rather than from the code.

## Intended behavior

A gate, once per phase, producing `docs/features/<feature>/tasks/phase-N.md`.

Its sections are Current system, Change required, Impact, Reuse, Gaps, Tasks, New risks, Questions,
Validation and Outcome. Three rules carry most of the value:

- **Current system is read from the code with `file:line`**, never from the design documents. Where
  the two disagree, that disagreement is the finding.
- **A task names a file and the test that proves it.** "Rebuild the services screen" is a phase.
- **Questions route rather than accumulate.** A blocking question stops the phase and its answer
  goes to `spec.md` Open decisions or an ADR — never into the task plan, where a decision would be
  made by default.

One phase at a time. Expanding phase 5 before phase 1 is built contradicts ADR-0019, which this
repository already follows.

**Outcome is filled in after the phase**, and the difference between Tasks and Outcome is the only
honest measure anyone will have of whether this planning works.

## Affected specifications and architecture

None. No product behavior, API, schema, or service changes.

Wired into `docs/engineering/workflow.md` so Codex and human contributors get the step, not only
Claude Code; into `feature-work` as a gate before implementation; and named in
`docs/templates/plan.md`, `docs/templates/README.md` and `CLAUDE.md`.

## Risks and rollback

**A fourth gate on a feature that already has three.** For phase 1 — one day, six files — it may
cost more than it saves. It earns its cost on phases 4 and 5, which are five screens each. Phase 1
is nonetheless the cheapest place to discover whether the format is right.

**Web research was deliberately narrowed.** The skill checks that a library or pinned version has
not changed, and explicitly refuses to research *scope* here: "is this the right feature" was settled
at the specification gate, and reopening it at the task gate is scope creep with a process blessing.
If the code analysis shows the specification was wrong, the skill says to go back to the
specification rather than fix it in a task list.

Rollback is deleting one skill, one template, and five wiring edits.

## Plan and validation

1. Write `docs/templates/task-plan.md` and the `task-plan` skill.
2. Wire the gate into `workflow.md`, `feature-work`, the plan template, the templates index, and
   `CLAUDE.md`.
3. Run it on console-redesign phase 1 — the first real use, and the test of whether the format holds.
4. `just check`.

## Status

See [status.md](status.md).
