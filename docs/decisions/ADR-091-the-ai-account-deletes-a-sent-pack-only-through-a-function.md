# ADR-091: The AI account deletes a sent pack only through a function, never a table grant

## Status

Accepted 2026-11-21, with F5 Step 4 (plan T4.1, T4.2; migration 0081). Refines the F5 design's
*Grants and the checklist*, which listed `SELECT, DELETE` on `finding_fix_source_packs` for
`nexovue_insight`. **Awaiting the founder's review with Step 4's diff.**

## Context

The proposal job must delete the files it was sent **before** it moves its suggestion out of
`proposing` (design, *The flow*, step 5): the pack's row policy admits the AI account only while the
suggestion is `proposing`, so a delete after the move matches nothing and leaves whole files behind.
The design gave the account a table-level `DELETE` on the pack table for this.

F3 keeps a rule for this account that its grants test enforces (`test_insight_grants_live.py`,
`test_the_account_holds_exactly_its_list`): **no table-level `UPDATE`, `DELETE` or `TRUNCATE` anywhere**,
because Postgres lets any holder of one of those `LOCK` the table (`insight_account.py`'s header). A
broken-into AI process could then lock the pack table and stall every pack job, or with F3's tables,
the alert queue. The design's grant would have been the first exception.

## Decision

1. **`fix_drop_pack(fix_id, org_id)`** (migration 0081), `SECURITY DEFINER`, deletes the pack only
   when its suggestion is `kind = 'source'` and `state = 'proposing'` in that organization, and returns
   how many rows went. `EXECUTE` is taken from `PUBLIC` in the migration and given to
   `nexovue_insight` by `setup` (`INSIGHT_FUNCTIONS`), never in a migration.
2. The account keeps a **column `SELECT`** on the pack (`fix_id, org_id, files, frames, hint`), under
   the row policy, and **no `DELETE`**. The job asserts the count is 1 before it writes the end
   (`source_fix_store._end`); 0 means the row moved, and it writes nothing.
3. The move back to `gathering` stays as D75 set it: only `fix_request_more_files`.

## Consequences

- F3's "no table-level write" rule holds for every table the account touches, and its grants test
  needs no exception.
- The pack's `DELETE` row policy from migration 0079 is no longer reached by this account; it stays,
  harmless, as a second fence.
- Rejected: the design's `SELECT, DELETE` grant (lockable table), and a delete inside the end
  statement's transaction by the install account (the AI room holds no install credentials).
