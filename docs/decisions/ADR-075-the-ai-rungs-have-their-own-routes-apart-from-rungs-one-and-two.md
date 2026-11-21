# ADR-075: The AI rungs have their own routes, apart from rungs one and two

## Status

Accepted 2026-11-21: the founder's decision that F5's fix routes live in `findings/fixes/router.py`
and that `fix_read.py` reads signals from `findings/signals.py`, so the finding page's rungs one and
two reach no AI code (F5 AC33, BR12); extended the same day with plan Step 2b, whose general
suggestion has its own router, `findings/fixes/general_router.py`.

## Context

The deploy line and the brief must work with AI off, stopped or failing (BR12). F3 Step 5 made
`findings/explanations.py` import AI code, and the router serving both F3's wording and F5's read then
reached it, failing AC33's import walk 45 times. Rung 2b needs the AI connection, the consent and
F3's limits to decide whether it is offered, all of which are AI code.

## Decision

1. **Rungs one and two are one router and read** (`findings/fixes/router.py`, `fix_read.py`), walked
   by `test_fix_facts_hold_no_ai.py` together with the whole `nexovue_platform.fixes` package; none
   reaches a denied module.
2. **Every AI rung has its own router and service.** Rung 2b is `GET` and `POST
   /findings/{id}/fixes/general` and the four refusals on code (`general_router.py`, `fix_general.py`);
   rungs 3 to 5 join that side. The *Suggested fix* section's line comes from that read, not from
   `GET /findings/{id}/fixes`, which no longer carries a `section` field.
3. **The page reads both, separately,** so a failing AI read shows its own failure in place and the
   brief still renders.
4. **Shared, AI-free pieces move beside what they share with**: the signal parser to
   `findings/signals.py`, and the alias restorer both F3 and F5 read through to
   `nexovue_platform/evidence/aliases.py` (`restored_segments`), rather than importing F3's AI module.

## Alternatives rejected

- **Narrow the walk to skip the explanation service.** Leaves rungs one and two one import away from
  provider code.
- **One read carrying every rung.** Couples the deploy line to the AI connection's state.

## Consequences

- Two requests on the finding page instead of one.
- A future rung-one-or-two change that reaches for AI code fails the gate the day it is written.
