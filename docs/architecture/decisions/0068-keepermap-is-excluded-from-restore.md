# 0068 — KeeperMap tables are excluded from a restore

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

[0061](0061-counter-overcount-across-a-store-restart.md) put the span ingest offset guard in a `KeeperMap`
table, because ClickHouse's insert-deduplication log does not survive the restart that causes a
replay. That guard is load-bearing for the no-data-loss invariant.

The backup documentation says nothing about how `KeeperMap` tables or insert-deduplication block
hashes behave across a restore. ClickHouse *can* back up `KeeperMap` — support landed in 2023 — so
this was undocumented rather than broken, and the action was to rehearse it rather than design
around it.

There was no backup destination configured at all, so nothing could be rehearsed. That is now
[`config.d/backups.xml`](../../../deploy/clickhouse/config.d/backups.xml), local disk, enough to
prove the round trip. Where production backups land is P1-51's decision.

## What the rehearsal found

`span_ingest_offsets` is declared `ENGINE = KeeperMap('/span_ingest_offsets')`. **That path is a
literal, not derived from the database or table name.** The rows live in Keeper under it; the table
is a view onto that state.

Restoring the table under a different name, into the same server, therefore does not produce a copy:

```
RESTORE TABLE nexovue.span_ingest_offsets AS nexovue.offsets_restored ...
→ Cannot restore the table nexovue.offsets_restored because it already contains some data
```

The "already contains some data" is the *live* guard, seen through the restored name — the new table
read back the same 12 rows before any data was restored into it. Overriding the refusal with
`allow_non_empty_tables=true` would have written the backup's older offsets over the live ones,
**rewinding the guard and permitting exactly the double-counting 0061 exists to prevent.**

The safe half: `KeeperMap` reference-counts its path. Dropping the second table left the live rows
intact, so an accidental restore-then-drop does not destroy the guard.

## Decision

**A restore must not include `KeeperMap` tables into a server whose guard is live.** Back them up —
they carry real state and a cold-start rebuild is worth having — but restore them only into a server
with a different `keeper_map_path_prefix`, or when the guard is genuinely absent.

Isolation between environments comes from `keeper_map_path_prefix` in
[`config.d/keeper.xml`](../../../deploy/clickhouse/config.d/keeper.xml), not from the table
definition. Two servers sharing a Keeper ensemble would share these offsets whatever the tables are
called. Today each stack embeds its own Keeper, so they are separate by construction — that is a
property of the deployment, and it needs stating rather than assuming.

## Consequences

P1-51 owns the restore runbook and it now has a named constraint rather than an open question:
enumerate `KeeperMap` tables and exclude them, and verify the offset guard after any restore rather
than trusting that it came back.

Insert-deduplication block hashes were not separately rehearsed. They are a server-side log rather
than table data, so a restore cannot carry them — which is the same conclusion 0061 already reached
about restarts, and is why the guard exists.
