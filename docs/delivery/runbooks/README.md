# Runbooks

> **Type:** Delivery reference · One file per procedure, loaded on its own

An operator procedure that is dangerous, rare, or easy to get wrong belongs here rather than in an
ADR. An ADR records *why a decision was taken*; a runbook records *what to type when the incident is
live*, and the two age differently — the decision is frozen once accepted, the procedure gets
corrected every time someone runs it.

One file per procedure, kebab-case, named for the action: `collapse-duplicate-span-rows.md`, not
`clickhouse.md`. Same reasoning as `docs/architecture/decisions/` — at 3 a.m. you want to open one
short file, not scroll a long one.

Each entry carries these headings, in this order:

`When to run this` · `When NOT to run this` · `Before you start` · `The procedure` · `Verify it
worked` · `What it costs`

**"When NOT to run this" is not padding.** Every procedure in here is one somebody will reach for in
the wrong situation, and naming the wrong situation first is most of the value.

| Runbook | Covers |
|---|---|
| [collapse-duplicate-span-rows.md](collapse-duplicate-span-rows.md) | Removing duplicate rows from `spans` after a crash-loop duplicated data a customer queries |
| [restore-from-backup.md](restore-from-backup.md) | Putting ClickHouse and Postgres back from a `just backup` snapshot, and checking that they came back |

[ADR 0053](../../architecture/decisions/0053-nexovue-monitors-nexovue.md) names a start-up ordering
that belongs here too, once it has been run at least once.
