---
name: write-adr
description: Write an architecture decision record in docs/architecture/decisions. Use after making a decision with a real alternative — a schema shape, a library choice, a trade-off accepted.
---

# Writing an ADR

## Number and file

`ls docs/architecture/decisions/` — next number is the highest + 1. Name it
`00NN-a-sentence-about-the-decision.md`. The title says what was decided, not
what the topic was: *"Severity before time in the log sort key"*, never
*"Log sort key"*.

## Shape

```markdown
# 00NN — <the decision, as a statement>

- **Status:** Accepted
- **Date:** YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```

## What makes it worth writing

**Context is the problem, with evidence.** Numbers you measured, not
adjectives. *"It had reached 2.6M rows and 113 MiB"* beats *"it was growing"*.

**Alternatives considered is mandatory.** An ADR with no rejected option is
a note, not a decision. Say what you rejected **and why** — that is what stops
someone re-opening it in three months, and it is what tells a future reader
whether their new idea was already considered.

**Consequences includes what got worse.** Every real decision costs
something. Write the cost down: the query that got slower, the field that
cannot be recovered, the operation that is now long in production.

## Verify the links — every time

Relative links break silently and three were wrong on the first pass today.
Filenames rarely match what you remember: `0057` is
`0057-project-id-in-every-telemetry-sort-key.md`, not
`0057-project-id-in-every-sort-key.md`.

```bash
grep -o "([0-9]\{4\}-[a-z0-9-]*\.md)" <file> | tr -d '()' | \
  while read f; do [ -f "docs/architecture/decisions/$f" ] && echo "OK $f" || echo "MISS $f"; done
```

Do the same for `../../../deploy/...` paths.

## Honesty rules

- Record what you **measured**. If you did not run it, say you did not.
- A claim that turns out wrong is **marked withdrawn with the contradicting
  evidence quoted**, never quietly deleted. See the M2 follow-ups in
  `docs/delivery/phase-1-plan.md` for the form.
- If a source was checked, cite it. If it was remembered, do not cite it —
  go and check it.

## Afterwards

Add a row to the decisions log in `docs/architecture/system-architecture.md`
§6. An ADR nobody can find from the architecture doc is an ADR nobody reads.
