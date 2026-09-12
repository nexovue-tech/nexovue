# Status: decision-log

## Lifecycle status

Verified

## Current task

None.

## Completed

- `docs/decisions/decision-log.md` written: all 112 deleted decision records indexed by number,
  decision, date, verdict and what changed, grouped into ten areas.
- Ten supersession and reversal chains extracted into their own table, including Kafka → Redpanda →
  Kafka reversed the same day, and Base UI/shadcn decided but never installed.
- Twelve records carrying measurement-grade evidence identified and named as worth restoring in full.
- `docs/decisions/README.md` points at the index and names the contradiction it exposes.

## Next action

**Correct `docs/architecture/current-system-reference.md`.** It states the durable watermarks let
workers survive a restart "without losing or double-counting data." Records 0061 and 0075 measured a
reproducible +512 span overcount, bounded by KeeperMap rather than eliminated, with 0061's assigned
cause falsified by 0075.

That is its own change record, and it needs two founder decisions: what the document should say, and
whether the overcount is worth chasing now. The recommendation is to restore 0061 and 0075 in full,
correct the sentence to what was measured, and record the overcount as known and bounded — an
undercount is the worse failure for a monitoring product, which `ingeststate/state.go:221` already
says in the code.

## Blocked

Nothing technical.

## Validation

`just check` on 2026-09-12, exit code 0, output read: 2277 Python tests, 120 browser-SDK across 12
files, 613 web across 47 files, plus `buf lint` and `buf breaking`. No code was touched; the run
confirms no documentation gate regressed.

All 7 markdown links in the added and changed documents resolve, checked by script. Two residual
links that had leaked into table cells during extraction were found by that check and flattened.

The `git show` retrieval command printed in the document was executed and returned record 0075's
first line, so the instruction the index gives a reader is known to work rather than assumed to.

Extraction was parsed from the records' own `Status:`, `Date:` and relationship fields, not
summarised by reading — 112 of 112 parsed, with the one unclassified record (0008) placed by hand.

**Not verified:** whether each record's self-reported verdict still matches the code. The index says
so in its own closing section. Establishing that for 92 "stands" entries is a much larger piece of
work and was not attempted.

## Last updated

2026-09-12
