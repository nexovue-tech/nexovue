# Collapse duplicate span rows

> **Type:** Delivery reference · **Background:** [ADR 0079](../../architecture/decisions/0079-span-rows-are-idempotent-across-a-restart.md)
> **Blast radius:** rewrites every part in one partition of `spans`. Not reversible.

`spans` is a plain `MergeTree`. Nothing deduplicates it on read, so a row written twice is simply
two rows, and a customer opening that trace sees each span twice. ADR 0079 fixed the cause — a
worker killed abruptly replayed records whose spans were already stored. This runbook is for the
data that was written *before* a fix, or by a defect not yet found.

## When to run this

All four, together:

1. **A customer queries the affected data.** Duplicated spans are visible in the trace waterfall and
   inflate every count derived from stored spans.
2. **The cause is fixed and the store is no longer producing duplicates.** Collapsing rows while the
   writer is still duplicating them buys hours, not a fix.
3. **You know which partition holds them,** and it is one or a few — not "the whole table".
4. **It is off-peak for that cluster.**

The originating case: a crash-loop — a rebuild, an OOM kill, a hard `kill` — replaying records whose
spans ClickHouse had already acknowledged. A graceful `SIGTERM` drains and commits, so `docker
restart` barely reproduces it; abrupt termination is the one that hurts.

## When NOT to run this

- **In development.** Dev duplicates cost nothing and nobody is querying them. `OPTIMIZE … FINAL` is
  real work against a shared store for no benefit.
- **On a timer, a cron, or "as maintenance".** This is an incident response. ClickHouse's own
  documentation is explicit that `OPTIMIZE … FINAL` should not be routine —
  <https://clickhouse.com/docs/optimize/avoidoptimizefinal>.
- **Across the whole table.** `OPTIMIZE TABLE spans FINAL` with no `PARTITION` rewrites everything
  ever stored. Always name the partition.
- **Before the cause is fixed.** See condition 2 above.
- **When the duplicates will expire before anyone queries them again.** Check the TTL first —
  waiting costs nothing and this does not. **Check it, do not assume it:** measured 2026-08-31, the
  dev store's `spans` TTL is the per-row
  `toDateTime(received_at) + toIntervalDay(retention_days)` with `retention_days` of 7 and 30 —
  *not* the 3 days `deploy/clickhouse/dev-retention.sql` would set, which has evidently never been
  applied here. Spans from 2026-08-27 were still queryable on 2026-08-31.

## Before you start

**Confirm the duplication is real, and find the partition.** Duplicates from this cause always share
`received_at` to the nanosecond, and the partition key is `(toDate(received_at), retention_days)`, so
every copy of a span sits in exactly one partition:

```sql
SELECT day, ret,
       count()             AS spans_with_extra_copies,
       sum(copies - 1)     AS surplus_rows,
       max(copies)         AS max_copies
FROM (
  SELECT toDate(received_at) AS day,
         retention_days      AS ret,
         count()             AS copies
  FROM spans
  WHERE toDate(received_at) >= toDate('YYYY-MM-DD')
  GROUP BY day, ret, tenant_id, project_id, trace_id, span_id, timestamp, received_at
)
WHERE copies > 1
GROUP BY day, ret
ORDER BY day, ret
```

`retention_days` is part of the partition key, so **one date can hold more than one partition** — a
project on a 7-day plan and one on 30 days are separate partitions of the same day. Read both
columns off this result; each row is one `OPTIMIZE`.

Then check the partition's size, because that is what you are about to rewrite:

```sql
SELECT partition, sum(rows), formatReadableSize(sum(bytes_on_disk)), count() AS parts
FROM system.parts
WHERE table = 'spans' AND active
GROUP BY partition ORDER BY partition
```

## The procedure

One partition. Once. Watched.

```sql
OPTIMIZE TABLE spans PARTITION (toDate('2026-08-30'), 30) FINAL
  DEDUPLICATE BY tenant_id, project_id, service_name, timestamp,
                 trace_id, span_id, received_at, retention_days
```

**The column list is not negotiable and not shortenable.** The
[OPTIMIZE reference](https://clickhouse.com/docs/sql-reference/statements/optimize) states it
directly: the column list "must include all columns specified in row ordering expression (both
primary and sorting keys) and partitioning expression (partitioning key)". A shorter list is
rejected outright, so the four-column identity you would reach for first —
`trace_id, span_id, tenant_id, project_id` — does not work. The eight above are exactly:

| Column | Why it must be listed |
|---|---|
| `tenant_id`, `project_id`, `service_name`, `trace_id` | sort key — `ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)` |
| `timestamp` | the sort key's fourth position is `toStartOfHour(timestamp)`; the underlying column is what gets named |
| `received_at`, `retention_days` | partition key — `PARTITION BY (toDate(received_at), retention_days)` |
| `span_id` | the span's own identity; without it two distinct spans of one trace would collapse into one |

If the schema's `ORDER BY` or `PARTITION BY` ever changes, this list changes with it. Re-read them
from `SHOW CREATE TABLE spans` before running, rather than trusting this file.

Repeat per partition found above. **Do not loop it in a script** — run one, verify, then decide about
the next.

## Verify it worked

Re-run the counting query from *Before you start*, scoped to the partition you just optimized. It
must return no row for that `(day, ret)` pair. Then confirm the trace a customer complained about:

```sql
SELECT span_id, count() FROM spans
WHERE trace_id = 'THE_TRACE' GROUP BY span_id HAVING count() > 1
```

Empty is the pass. Also check the writer is still healthy — `OPTIMIZE` competes with ingest merges,
and a backed-up merge queue shows up here:

```sql
SELECT count() FROM system.merges WHERE table = 'spans'
```

## What it costs

**It rewrites everything it touches.** ClickHouse's own guidance describes the work as
"decompressing all parts, merging the data, compressing it again, writing the final part to disk",
and calls these "resource intensive operations which may impact cluster performance"
([Avoid `OPTIMIZE FINAL`](https://clickhouse.com/docs/optimize/avoidoptimizefinal)). Naming the
partition is what keeps that bounded — the same page's advice is that in most cases "you're better
off letting ClickHouse manage merges on its own".

**It bypasses the size guardrail.** `OPTIMIZE … FINAL` ignores
`max_bytes_to_merge_at_max_space_in_pool` (~150 GB), which the background scheduler respects
precisely to avoid "long merge times, memory pressure, or even out-of-memory errors". This matters
here more than it would elsewhere: our ClickHouse runs under a hard memory cap
(`deploy/clickhouse/config.d/memory.xml`), and an OOM inside the server is an ingest outage, not a
failed command.

**It competes with ingest.** A merge is single-threaded, and this one occupies a background merge
pool slot for its whole duration, so ingest merges for the same table queue behind it. Check
`system.merges` afterwards, per *Verify it worked*.

**It is not reversible.** No undo, no tombstone — the collapsed copies are gone. The operation only
removes rows identical across all eight columns, so it is safe *given the right column list*; with a
wrong one it can collapse rows that were never duplicates. Read the list against
`SHOW CREATE TABLE spans` first, every time.

Together, that is why the four preconditions are conditions and not suggestions: the cost is
certain, so the benefit has to be a customer actually looking at the data.

---

*Written 2026-08-31. Nothing in this file has been run against stored data — the dev store's
duplicates were left in place deliberately (see ADR 0079), so the procedure is derived from the
schema and ClickHouse's documented behaviour, not from an execution. The first person to run it
should correct this file afterwards.*
