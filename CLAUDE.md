# nexovue — working rules

Insight-first observability platform. Phase 1 (core system) is planned in `docs/delivery/phase-1-plan.md`; product and technical context live under `docs/` — see `docs/README.md`.

## Rules

**1. Never push without permission.** Committing is fine. Pushing happens only when Raj says "push" — every time, not once per session.

**2. Commits carry Raj's identity only.** `~/.gitconfig` has an `includeIf` for `~/Personal/` that resolves this automatically. Never pass `-c user.name` / `-c user.email`. Never add a `Co-Authored-By: Claude` trailer.

**3. Never use the `gh` CLI here.** It is authenticated as Raj's office account, which has no access to the `nexovue-tech` org. Use git over SSH; hand anything else to Raj.

**4. The code is the source of truth.** Read the source to understand behaviour. Documents describe intent when written and drift; where they disagree with the code, the code wins — say so, so the doc gets fixed.

**5. Root fixes only.** No temporary patches or workarounds. Trace every symptom to its cause. If the
root fix is large, say so and let Raj scope it — never silently substitute a patch. **A root fix is
applied everywhere the root exists** — the same defect in five sibling tables is one fix, not one
table's.

**6. Code Complete construction standards.** Intention-revealing names, small focused routines, low coupling, no magic values, invalid input handled deliberately. The structure should make the intent obvious.

**7. Short comments.** A line or two explaining *why*, where it is not obvious. Never paragraphs. If a block needs a long explanation, rewrite the block.

**8. Stop and ask rather than guess.** Any unclear requirement, missing decision, or unknown: finish what does not depend on it, then halt and ask open questions. Never invent a plausible answer.

**9. Analyze instructions before implementing them.** When told to build or fix something, evaluate it against the actual code and the recorded decisions first. Raise problems and missing decisions before writing code — Raj welcomes being questioned. If he reaffirms after hearing the concern, build it in full.

**10. Research before recommending.** Search the web before choosing a library, a component approach, a config syntax, or answering any "what's the current best way" question — including when confident. This covers *approach*, not just package names: the current way to build a toast might be a library, a native browser API, or something that did not exist at training time. Report what the search showed with its source, name the alternative rejected, and say plainly when something turns out to be outdated.

**11. Replacing means deleting.** When a change supersedes existing code, config, docs or a
database object, remove what it replaced in the same change — or say plainly why it must stay.
Report what was deleted. Measured across 623M real code changes, AI-assisted work raises
duplication 81% and cuts refactoring 70%: agents add and rarely delete, so this is the one that
does not happen by itself.

**12. A result is evidence only if you watched it happen.** Exit code 0 is not proof a test ran —
twice in one day a green result meant zero tests executed, once from a missing build tag and once
from a suite that skipped itself. Read the output and check the count. Never record a claim as fact
— in an ADR, a report or a commit message — without having run the thing that proves it. If it was
not run, say so.

**13. Explain at Raj's level.** Lead with what a change means and what breaks without it, then
the detail. Define production vocabulary the first time it appears in a reply — partition, consumer
group, p95, rollup, DNS rebinding, e2e — in one clause, then use it normally. This is not
simplification of the engineering: keep every measurement, and never soften a real problem to make
it easier to say. It lapses specifically in status reports, findings lists and "what's next", which
is where it has had to be corrected twice — so check those before sending.

**14. Nothing is deployed and nobody is a customer.** This is pre-deployment
development. There is no production, no staging, no customer, no data anyone
would miss, and no version anybody is running. **So propose the correct fix, not
the compatible one.** Rename the column, change the shape, delete the migration
and write it again, break the API, redo the schema — none of it costs what it
would cost later, and doing it later is what costs.

Reasoning that does not apply yet, and must not be used as an argument: a deploy
window, a backwards-compatible migration, a customer already part-way through
their allowance, an existing key or URL somebody would have to edit, a rollout
plan, a feature flag to protect a live path, a deprecation period. If a
recommendation leans on any of those, it is answering a question this project
does not have.

What *does* still apply: a decision that will be hard to reverse **after** the
first customer — a public URL shape, a stored credential scheme, a hostname
customers paste into their own config — is worth getting right now precisely
because it is cheap now. The rule is not "care less". It is **"the constraint
you are designing around does not exist yet, so design for the end state."**

Say so when a plan is being shaped by an absent constraint. It is the most
common way this project's work gets made worse than it needs to be.

## Invariants (Phase 1)

Tenant isolation enforced at the repository layer · no data loss between receiver and storage · metrics derived from all traces **before** sampling · every door normalizes to one internal envelope before the broker · performance budgets in `docs/delivery/phase-1-plan.md` §4 measured, not estimated.
