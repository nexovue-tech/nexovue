# nexovue — working rules

Insight-first observability platform. Phase 1 (core system) is planned in `docs/delivery/phase-1-plan.md`; product and technical context live under `docs/` — see `docs/README.md`.

## Rules

**1. Never push without permission.** Committing is fine. Pushing happens only when Raj says "push" — every time, not once per session.

**2. Commits carry Raj's identity only.** `~/.gitconfig` has an `includeIf` for `~/Personal/` that resolves this automatically. Never pass `-c user.name` / `-c user.email`. Never add a `Co-Authored-By: Claude` trailer.

**3. Never use the `gh` CLI here.** It is authenticated as Raj's office account, which has no access to the `nexovue-tech` org. Use git over SSH; hand anything else to Raj.

**4. The code is the source of truth.** Read the source to understand behaviour. Documents describe intent when written and drift; where they disagree with the code, the code wins — say so, so the doc gets fixed.

**5. Root fixes only.** No temporary patches or workarounds. Trace every symptom to its cause. If the root fix is large, say so and let Raj scope it — never silently substitute a patch.

**6. Code Complete construction standards.** Intention-revealing names, small focused routines, low coupling, no magic values, invalid input handled deliberately. The structure should make the intent obvious.

**7. Short comments.** A line or two explaining *why*, where it is not obvious. Never paragraphs. If a block needs a long explanation, rewrite the block.

**8. Stop and ask rather than guess.** Any unclear requirement, missing decision, or unknown: finish what does not depend on it, then halt and ask open questions. Never invent a plausible answer.

**9. Analyze instructions before implementing them.** When told to build or fix something, evaluate it against the actual code and the recorded decisions first. Raise problems and missing decisions before writing code — Raj welcomes being questioned. If he reaffirms after hearing the concern, build it in full.

## Invariants (Phase 1)

Tenant isolation enforced at the repository layer · no data loss between receiver and storage · metrics derived from all traces **before** sampling · every door normalizes to one internal envelope before the broker · performance budgets in `docs/delivery/phase-1-plan.md` §4 measured, not estimated.
