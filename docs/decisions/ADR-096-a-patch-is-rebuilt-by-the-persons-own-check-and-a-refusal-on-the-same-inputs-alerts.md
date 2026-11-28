# ADR-096: A patch is rebuilt by the person's own check run, and a refusal on the same inputs alerts

## Status

Accepted 2026-11-21, with F5 Step 5 (plan T5.1, T5.2). Builds the F5 design's *The check run*
for `patch`, and settles F5 status open decision 52 (the tamper signal's alert). **Awaiting the
founder's review with Step 5's diff.**

## Context

A written suggestion's stored `edits` came from the AI room, which may have been broken into
(D18), and the repository may have moved since. The design has a person's own check run, on
`scheduler-connections` with a read-only token, re-read the files and rebuild the patch, and
names one tamper signal: the same rules, owner's list and files giving a different result. Two
questions were open: how the page shows the rebuilt diff without a second copy that could
differ from the download, and how the alert reads a signal that is decided in a worker's child
process, whose metrics no scrape sees (`metrics.py`).

## Decision

1. **The check run decides with pure code** (`nexovue_platform/fixes/recheck.py`): the stored
   row checked again as a read does; each changed file at the default branch's head; the
   display facts recomputed from the file as it was read (its base blob) and compared; K1 to
   K10 on the head. The outcome is unchanged, moved, out of date, or refused again. Out-of-date
   comparisons that need no file (rules, the owner's list, the code location, the likely cause)
   run before any file is read.
2. **The patch is stored on the person's own check row** by a statement taken under the
   suggestion's row lock, conditional on the suggestion still `proposed` with its code and a
   consent still covering its folder, so an owner's withdrawal can never be overtaken.
3. **The page shows the rebuilt diff by reading back the stored patch** (`diff.patch_hunks`),
   so what a person reads and what they download are one text. Flags are worked out again from
   its lines; new letters from what the hunks show, which can only add flags.
4. **Refused again** ends the suggestion `failed`, `checks_refused_at_click`, deletes its code
   (BR9's first row), audits `fix.checks_refused` with `at` and the rule, and logs a warning.
   Beat's leader reads the last hour's count of that reason from Postgres
   (`nexovue_scheduler_fix_checks_refused_recent`), and `NexovueSuggestionRefusedAgain` opens a
   ticket when it is above zero. New rules or a moved file read "Out of date" and never count.
5. **No write to any repository**: the job's client has no write route (ADR-082), and a test
   reads the fake GitHub's log for any write.

## Alternatives rejected

- **Store rebuilt hunks beside the patch.** A second copy that could disagree with the file a
  person applies.
- **Count the signal in the worker.** A child process's counters are never scraped.
- **Alert on any check that fails a rule.** An upgrade or a moved file would make it noise, and
  an operator would learn to ignore it.

## Consequences

- `finding_fix_checks.reason` gains the out-of-date codes and `checks_refused_at_click` (0082).
- The alert has a runbook entry in `docs/operations/ai.md`.
- Step 7's draft reuses this job for `draft`, adding only its own refusals.
