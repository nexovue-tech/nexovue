# Change: template-gaps — the forms had no home for work we kept doing

## Classification

Operations (engineering system)

## Current behavior and problem

The four templates were incomplete, and the console-redesign feature is the evidence. Writing it
required inventing six headings `status.md` does not have — *The scope problem*, *Open decisions*,
*Decision record*, *Specification impact*, *Documentation impact*, *Everything open is now decided* —
each one papering over a missing section. A template that forces every author to invent the same
headings is not a form; it is a starting point that silently costs consistency.

The specific gaps, and what each cost:

| Template | Missing | What happened without it |
|---|---|---|
| `spec.md` | **Problem**, distinct from Business objective | The objective was recorded; what was actually wrong was not |
| `spec.md` | **Users** | That P3 Owner has no console surface — F7 is unbuilt — was discovered during wireframing, four documents late. A Users section asks that question at spec time |
| `spec.md` | **Business rules** | Invariants that hold across every path had to live among functional requirements that describe one |
| `design.md` | **User experience**, **UI structure** | The template is backend-shaped: *Data flow*, *Performance*, *Failure handling*, and nothing for the user. So `user-flows.md` and five `wireframes-*.md` exist as ad-hoc siblings the design does not reference |
| `plan.md` | **Phases** | Its tasks were generic placeholders; nothing said a plan should ship something judgeable before the end |
| `status.md` | **Open decisions**, **Risks**, **Specification impact**, **Documentation impact** | All four were added by hand, differently each time |

## Intended behavior

Each template gains the sections the work kept needing, with a one-line prompt saying what belongs
there — the prompts are the part that stops the section being filled with the wrong thing.

Three additions carry more than a heading:

- **`spec.md` Users** asks explicitly which persona a feature *does not* serve, so scope does not
  later widen to a user with nowhere to stand.
- **`design.md` UI structure** requires naming separate wireframe documents rather than leaving them
  as unreferenced siblings, which is exactly what console-redesign did.
- **`plan.md` Phases** records the repository's own sequencing rule from ADR-0019 — one thin
  end-to-end path, then widen — and says to expand a phase into tasks when that phase starts rather
  than writing later phases as fiction with checkboxes.

`plan.md` also gains a **Design QA** gate: comparing the built screen against the approved design.
Nothing in the repository does that today. `review-diff` checks invariants, not fidelity.

## Affected specifications and architecture

None. No product behavior, API, schema, or service changes.

Existing feature and change workspaces are **not** retrofitted. They were written against the older
forms and rewriting them would be churn for no reader; console-redesign already carries the missing
sections under its own headings.

## Risks and rollback

**A longer template invites box-filling.** A section answered with "N/A" is worse than one that was
never asked, because it looks considered. The prompts are written to make an empty section obviously
empty rather than plausibly complete.

Rollback is `git revert`; nothing depends on the templates at runtime.

## Plan and validation

1. Add the missing sections with prompts.
2. Verify `docs/templates/README.md` still describes each form correctly.
3. `just check`.

## Status

See [status.md](status.md).
