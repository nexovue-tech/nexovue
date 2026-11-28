# ADR-103: Code from a model reaches a person or a repository only through rules in a room with no model

## Status

Accepted with F5's design, approved by the founder on 2026-11-02 (design, *Proposed ADRs*,
first). Filed 2026-11-22 with F5 Step 9 (plan T9.9), as built through Steps 2 to 8. **Awaiting
the founder's review with Step 9's diff.**

## Context

F5's model must read source, which an attacker may have written: a planted comment, a forged
stack naming a file. Fences around the input and filters on it are not a defence (foundation,
section 3). The model's output is the one thing in Phase 2 that can become something that runs,
in the customer's repository and in their CI.

## Decision

1. **A model's output is data.** The AI room (`scheduler-insight`, as `nexovue_insight`) may
   store nothing else a person reads, and it can change a `finding_fixes` row only while that
   row is `proposing` (row security, migration 0077 onward).
2. **A process that runs a model holds no repository key; a process with a repository key runs
   no model.** `scheduler-connections` reads GitHub and writes to it; it re-derives what is
   shown, downloaded and committed from fresh files and the stored edits.
3. **One set of rules, with no I/O,** in `nexovue_platform/fixes/checks.py` (K1 to K10) and
   `policy.py` (the lists), run by the AI room, again by the sweep when it first records a
   result, again by the API on every read, and again at the person's check run before a patch
   or a draft (ADR-096). The same rules on the same inputs refusing is the tamper signal.
4. **Every write is a job in `scheduler-connections`,** binding the digest a person confirmed
   (ADR-100), and only behind the founder's go (ADR-097, ADR-101).
5. **A person's click comes between, is required, and is not counted as the boundary.**

## Consequences

A broken AI process cannot write, and cannot make the screen differ from the commit. It can
still read the in-flight packs it is given and send them out (the outbound limit, decision 43,
is planned before the first deployment). Honest changes are sometimes refused. Logic bugs and
comments still get through the rules, which every page says ("Not compiled and not run by
Nexovue. A subtle bug passes every rule."). Changing a rule is a security change and needs a
hostile fixture, and bumps `CHECKS_VERSION`, so older suggestions read "Out of date". This
amends the foundation's "API, on the click", as F22's ADR does.

## Evidence

`test_fix_hostile_answers.py`, `test_fix_source_never_leaks_live.py`,
`test_fix_write_hostile_live.py` (a compromised AI room's change stops before any repository
write), `test_fix_rooms_import.py` and `test_fix_facts_hold_no_ai.py` (AC33's walk).
