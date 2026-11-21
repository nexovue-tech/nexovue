# ADR-094: A suggestion's stored code is taken back at every end, through one list

## Status

Accepted 2026-11-21, with F5 Step 5 (plan T5.3; migration 0082). Extends the F5 design's BR9
table (*The sweep*) with two ends it did not name: a deleted project and a removed member.
**Awaiting the founder's review with Step 5's diff** (status, open decisions 59 and 60).

## Context

F5 stores a customer's code in three places: a suggestion's written change and listing
(`finding_fixes.edits`, `files_read` and their kin), the files sent to the provider while a
suggestion is written (`finding_fix_source_packs`), and each person's rebuilt patch
(`finding_fix_checks.patch`). BR9 promises it all goes at the earliest of a set of ends, and
that the fact a suggestion existed stays. Before Step 5, three ends were unbuilt (the Fix App's
disconnect, GitHub access gone for 72 hours, the restore), and two ends of the product itself
were not in the table at all:

- **A project deleted** is only marked (`projects.service.delete`); its findings stay. A
  suggestion of that project still `proposing` would go on to send its files to the provider
  for a project that no longer exists, and its written code would wait 30 days for nothing.
  F3 already stops its own queued wording when a project goes.
- **A member removed** keeps their user row (the audit log points at it), so their check runs,
  each holding a patch and their GitHub access answer, would stay until they expired.

## Decision

1. **One list, one statement.** Every end runs `clear_stored_code`
   (`nexovue_platform/fixes/statements.py`) after ending any work in flight, so no path can
   forget a column; the restore runs the same list over every row (ADR-095).
2. **The API's ends** go through `findings/fix_takeback.py`: an owner's withdrawal; F22's
   disconnect, through the hook F22 built for F5, which also withdraws every consent on the
   App's repositories; a deleted project, whose suggestions of either kind still being prepared
   stop as `failed`, `project_deleted`, and whose stored code goes; and a removed member, whose
   GitHub link, link attempts and own check runs go.
3. **The sweep's ends**: AI switched off by a person or removed, 30 days, and GitHub access gone
   for 72 hours, counted from `connection_repositories.unreachable_since` (set by F22's refresh
   when it first misses a repository, cleared when it answers) or from an App GitHub says is not
   installed (`needs_attention`, `app_not_installed`, `failing_since`). That last end withdraws
   consent too (`access_gone`), as an owner would.
4. **Not ends**, as BR9 says: a stale suggestion (until its 30 days), a consent that ended by
   itself (its owners are emailed once), and F3's automatic AI-off.
5. **The record stays**: the row, its state and reason, its verdict and the audit entries; a
   person's verdict is exported with their personal data.

## Alternatives rejected

- **Leave a deleted project's code to the 30-day sweep.** It would let a `proposing` row send a
  customer's files after the project was gone, and keep code nobody can see.
- **Delete a removed member's verdicts.** A verdict is the organization's record of whether
  suggestions help (UR12), as a case note is; it holds no code and names the person only by id.
- **Count 72 hours from the App's `failing_since` alone.** A single repository can drop out of
  an installation while the App stays healthy.

## Consequences

- `finding_fixes.reason` and `code_deleted_reason` gain `project_deleted` (0082).
- F22's refresh writes one more column, and its disconnect calls F5's hook.
- Each end is proved by a test that plants a marker in a file and finds it in no column
  afterwards (`test_fix_takeback_live.py`, `test_fix_access_gone_live.py`).
