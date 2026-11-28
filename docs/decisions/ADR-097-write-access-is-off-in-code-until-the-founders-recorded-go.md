# ADR-097: Write access is off in code until the founder's recorded go

## Status

Accepted 2026-11-22, with F5 Step 6 (code) (plan T6.5; foundation item 33). **Awaiting the
founder's review with Step 6's diff** (status, open decision 77).

## Context

Foundation item 33 (the founder, 2026-11-21) has F5 Steps 7 and 8, the draft pull request and
Run CI, built now against the simulated GitHub **with write access switched off in code**, and
turned on only by his written go at T6.5, after his own real-model evaluation (item 31). The
plan names T6.5 as "the gate", proved by "the recorded decision" in `status.md`, but names no
code that Steps 7 and 8 read. Without one, each write path would carry its own idea of "off",
and nothing would stop a later change from turning one on without the go.

Ways it could have been switched: an environment variable or setting (an operator, or a test
stack, could turn it on, which item 33 does not allow); a database row (the same, and a migration
for a flag); or a constant in code, changed only in review.

## Decision

1. **One constant, in the shared package:** `nexovue_platform/fixes/write_gate.py`,
   `WRITE_ACCESS_GO = None`, read through `write_access_on()`. The API's draft route and the
   scheduler's writer (Steps 7 and 8) both import the shared package, so both read the same
   answer. Every write path calls it first and does nothing while it is False.
2. **No setting, environment variable or row can turn it on.** Only a reviewed commit that sets
   the constant to the founder's recorded go.
3. **The go names what T6.5 requires** (`WriteGo`): the AI options whose AC21 safety gates passed,
   where the buyers' answer is recorded (spec D57), the no-warranty terms (spec, founder decision
   1), and his answers on a draft from a stack alone (D58) and on GitHub Enterprise Server before
   Step 7. It refuses to be built without the first three.
4. **Code and status move together.** `status.md` carries the line "T6.5, the founder's go on
   write access: not given."; `test_fix_write_gate.py` fails if the line and the constant
   disagree.

## Consequences

Steps 7 and 8 have one switch to read, and their tests can pass a go explicitly to exercise the
write path against the simulated GitHub. Turning write access on is a code change, so a customer
running an older release never gains it by configuration; a release carrying a go is what ships
it. The constant says nothing about whether a repository's owner allowed drafts: that stays
per repository (BR14) and per person (D6), on top of this switch.
