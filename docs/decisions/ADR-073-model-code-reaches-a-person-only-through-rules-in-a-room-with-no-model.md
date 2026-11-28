# ADR-073: Code from a model reaches a person only through rules in a room with no model

## Status

Accepted 2026-11-02 by the founder with the F5 design ([`design.md`](../features/ai-solution/design.md),
*Proposed ADRs*, the first); filed 2026-11-21 with F5 plan Step 2b, the first step where a model's
code reaches a person (rung 2b's general suggestion). Rungs 3 to 5 apply it to source, patches and
pull requests in later steps.

## Context

F5's model output is the one thing in Phase 2 that can become something that runs: a suggestion is
made to be copied, and later to be applied. Fences around untrusted input and the model's own
instructions are not a defence (foundation, section 3). The process that runs the model, the AI room
(`scheduler-insight`, ADR-040), can be broken into through what it reads, so anything it writes may
be an attacker's.

## Decision

1. **A model's answer is data.** The AI room stores only an answer that passed Nexovue's rules, or
   the rule that refused it; the refused text is never stored or shown.
2. **The rules live in one module with no I/O and no AI code** (`nexovue_platform.fixes`): rung
   2b's G1 to G9 (`general_rules.py`), rung 3's K1 to K10 (`checks.py`), and the prose checks. Two
   structural tests hold that the package reaches no AI module, network client, process or file.
3. **Every read checks again, in a room with no model.** The API re-runs the rules on the stored
   answer, against the input it was written from, on every read; a stored answer that no longer
   passes shows nothing and raises no alert (rung 2b's AC71). Later, the room holding a repository
   key re-derives what is confirmed, downloaded and committed from fresh files and stored edits.
4. **A person's click comes between, is required, and is not counted as the boundary.** Rung 2b
   adds no copy control, so the label is read first (D70).

## Alternatives rejected

- **Trust the AI room's checks alone.** A broken AI process would write whatever it liked to a
  person's screen.
- **Rely on instructions or an input fence.** They lower the rate, never to zero; the rules are what
  is tested against hostile fixtures.
- **Rung 3's flags for rung 2b.** An example has no file and no reviewer behind it, so the same
  patterns refuse there (D68).

## Consequences

- Honest answers are sometimes refused; the bed (T2b.10, Step 6) measures how often, by rule.
- Changing a rule is a security change: it needs a hostile fixture first.
- Logic bugs and wrong advice still pass every rule; the label and "not compiled, not run" say so.
- Amends the foundation's "API, on the click" for writes, as ADR-030 does for tracker calls.
