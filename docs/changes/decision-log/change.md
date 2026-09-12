# Change: decision-log — an index for 112 deleted decision records

## Classification

Operations (engineering system)

## Current behavior and problem

Commit `e0f11a0 "delate the docmnets unwanted"` removed 112 decision records — 10,206 lines written
between 2026-08 and 2026-09-10 — and the current `docs/decisions/` series holds three records of 17
lines each.

The text survives in git, but nothing pointed at it. A decision that cannot be found has the same
practical effect as one that was never made: it gets re-proposed. Ten of the 112 record a
supersession or reversal, including Kafka → Redpanda → Kafka reversed within a single day, and a UI
primitive layer that was decided and never installed. Those are precisely the decisions someone
re-opens.

Restoring all 112 was rejected: 10,206 lines of which 92 records still stand unchanged is a large
reading surface for a question most people will never ask, and git already stores them.

## Intended behavior

`docs/decisions/decision-log.md` indexes all 112 — number, decision, date, whether it still stands,
and what changed when it did not — grouped into ten areas, with the supersession chains called out
separately because they carry the recurrence risk.

The index is explicitly **not** a replacement. It records *what* was decided; the full records hold
*why*, which is the part no reading of the code can recover. The document says so, and gives the
`git show` command that retrieves any record.

Original four-digit numbering is kept rather than renumbered into the `ADR-NNN` series, because the
records cross-reference each other by number and renumbering would break that graph.

## What the index found

- **92 stand · 12 amended · 5 superseded · 1 claim withdrawn · 1 deferred · 1 never decided.**
- **Ten chains** where a decision changed, now listed in one table.
- **Twelve records carry measurement-grade evidence** — falsified premises, benchmark tables,
  reproduction counts — and are named as worth restoring in full, about 2,500 lines.
- **One live contradiction.** The 0060 → 0061 → 0075 chain measured a reproducible +512 span
  overcount across a ClickHouse restart, falsified the cause 0061 had assigned it, and left it open.
  `docs/architecture/current-system-reference.md` states the durable watermarks let workers survive a
  restart "without losing or double-counting data." That overclaims.

## Affected specifications and architecture

No product behavior, API, schema, or service changes. `docs/decisions/README.md` now points at the
index.

The overclaim in `current-system-reference.md` is **named here and not fixed here**. Correcting it
means deciding what the current state actually is, which needs the restored records and a founder
decision on whether the overcount is worth chasing. Recording it in this change and leaving the
document wrong would be the worse outcome, so it is written into `status.md` as the next action.

## Risks and rollback

The index reports what each record says **about itself**, not a re-verification against today's code.
Some "stands" entries may have been quietly overtaken by code — the document states this limitation
rather than implying freshness. Code remains current-state truth.

Rollback is deleting one document and one README section.

## Plan and validation

1. Extract all 112 records from `e0f11a0^` and parse title, status, date, and relationship fields.
2. Classify into ten areas; derive a verdict from each record's own status line.
3. Identify the chains and the records carrying irreplaceable evidence.
4. Write the index; wire `docs/decisions/README.md`.
5. Verify every link resolves and that a cited `git show` command actually retrieves a record.

## Status

See [status.md](status.md).
