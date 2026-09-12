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
| `spec.md` | **User requirements**, distinct from functional | Every requirement was written in the system's voice; nothing stated what a *user* must be able to do |
| `spec.md` | **Error cases** | Nothing asked what can go wrong from the user's side. Console-redesign's stale-node rule — unknown, never zero — is a product decision that had no home and was settled four documents later, in a wireframe |
| `design.md` | **User experience**, **UI structure** | The template is backend-shaped: *Data flow*, *Performance*, *Failure handling*, and nothing for the user. So `user-flows.md` and five `wireframes-*.md` exist as ad-hoc siblings the design does not reference |
| `plan.md` | **A sequencing rule** | Its tasks were generic placeholders; nothing said a plan should ship something judgeable before the end |
| `status.md` | **Open decisions**, **Risks**, **Specification impact**, **Documentation impact** | All four were added by hand, differently each time |

## Intended behavior

Each template gains the sections the work kept needing, with a one-line prompt saying what belongs
there — the prompts are the part that stops the section being filled with the wrong thing.

Three additions carry more than a heading:

- **`spec.md` Target users** asks explicitly which persona a feature *does not* serve, so scope does
  not later widen to a user with nowhere to stand.
- **`spec.md` Error cases** asks what the product owes a user when something fails — what they see,
  whether they can recover, and whether we can distinguish the failure from a normal empty result.
  That last question is the one console-redesign kept answering ad hoc: a filter matching nothing
  and a project with no telemetry are different screens, and so are a healthy feed and a feed that
  could not be read.
- **`design.md` UI structure** requires naming separate wireframe documents rather than leaving them
  as unreferenced siblings, which is exactly what console-redesign did.
- **`plan.md`** records the repository's own sequencing rule from ADR-0019 — one thin end-to-end
  path, then widen — so the first steps ship something judgeable rather than the first result
  arriving at the end.

`plan.md` also gains a **Design QA** gate: comparing the built screen against the approved design.
Nothing in the repository does that today. `review-diff` checks invariants, not fidelity.

## Affected specifications and architecture

None. No product behavior, API, schema, or service changes.

Existing feature and change workspaces are **not** retrofitted. They were written against the older
forms and rewriting them would be churn for no reader; console-redesign already carries the missing
sections under its own headings.

## Risks and rollback

**A longer template invites box-filling.** `spec.md` now carries fourteen sections. A section
answered "N/A" is worse than one never asked, because it looks considered. Two guards: each section
carries a prompt saying what belongs in it, and the template's own header says to **delete** a
section that does not apply rather than answer it.

**User requirements and Functional requirements can duplicate each other.** They are separated
because one is the user's voice and the other the system's, and the prompt says to delete a line
that only restates the other. Whether that holds under time pressure is an observation for the next
few features.

Rollback is `git revert`; nothing depends on the templates at runtime.

## Plan and validation

1. Add the missing sections with prompts.
2. Verify `docs/templates/README.md` still describes each form correctly.
3. `just check`.

## Status

See [status.md](status.md).
