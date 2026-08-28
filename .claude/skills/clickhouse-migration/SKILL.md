---
name: clickhouse-migration
description: Write, apply and prove a ClickHouse schema migration in deploy/clickhouse/migrations. Use when adding or changing a table, sort key, partition key, TTL, index or codec.
---

# ClickHouse migration

## Before writing anything

1. `ls deploy/clickhouse/migrations/` — the next number is the highest + 1.
2. Read `000001_spans.up.sql` for house style, and read the migration that
   last touched the table you are changing. Match them.
3. **Research the syntax.** CLAUDE.md rule 10 is not optional here. We pin
   `clickhouse:26.3.20.7`; syntax and recommended engines change between
   versions, and blog posts reproduce old DDL. Verify against the current
   docs, then test the statement on the server before writing the file.

## Rules that are not negotiable

**One statement per file.** The runner uses `x-multi-statement=false`. A
migration that needs three statements is three numbered pairs.

**Every up has a working down.** Prove it: up → down → up. `just migrate`,
then `migrate ... down N`, then `just migrate` again.

**A rollback that empties a table is not a rollback.** If the up moves data,
the down moves it back. Only skip that when the table is empty.

**A partition key cannot be altered.** Changing one is a rebuild:
- Table is **empty** → `CREATE OR REPLACE TABLE`.
- Table has **data** → create a new table, `INSERT … SELECT`, `EXCHANGE
  TABLES`, drop the old. See `000028`–`000031`.

**A materialized view blocks an exchange.** Drop the MV first, swap the
table, recreate the MV, then backfill — in that order. Backfilling *after*
the MV exists is safe when readers aggregate `min`/`max` (an overlap is
idempotent); it is not safe for a summing rollup, where an overlap double
counts.

## Backfilling a large table

The defaults will exhaust the server. They squash inserts up to 256 MiB
before writing a part and fan the read across threads holding a block each:

```sql
SETTINGS max_threads = 1,
         max_insert_threads = 1,
         max_block_size = 8192,
         max_insert_block_size = 8192,
         min_insert_block_size_rows = 8192,
         min_insert_block_size_bytes = 33554432
```

## Adding a column that a writer must fill

Give it a `DEFAULT` that degrades to the old behaviour. Deploying schema
ahead of the binary is normal; a column defaulting to zero can partition
live traffic into 1970 and expire it on arrival.

## Codecs

Choose by measurement, never by reputation. Build a probe table, load real
rows, compare. Today `Delta` beat `DoubleDelta` on our timestamps, and a
blanket `ZSTD(1)` made one column 3.5× worse.

## Verify — do not assume the DDL did what you meant

```sql
SELECT name, type, default_expression, compression_codec FROM system.columns
  WHERE database='nexovue' AND table='<table>';
SELECT partition_key, sorting_key FROM system.tables
  WHERE database='nexovue' AND name='<table>';
SELECT name, type_full FROM system.data_skipping_indices
  WHERE database='nexovue' AND table='<table>';
```

Then check the writers still work: the worker keeps inserting, and
`apps/api/tests/test_tenant_chokepoint_structure.py` derives its table list
from this directory, so a new table must be tenant-guarded.

## Finally

If the migration embodies a real decision — a sort key, a partition key, a
representation choice — write an ADR. Use the `write-adr` skill.
