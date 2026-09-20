# Bug: ClickHouse's own logs fill the memory the product's queries need

## Reported behavior

`just check` fails with `MEMORY_LIMIT_EXCEEDED` (ClickHouse code 241) across
suites that have nothing to do with the change being tested — `test_rum_live`,
`test_metrics_live`, `test_tenant_isolation_live`, the scheduler's telemetry
tests — and a gate only passes after ClickHouse is restarted by hand and its
mutation queue has drained. On 2026-09-20 this cost three gate runs: 140
failures in one, 17 in another, each one `code: 241`.

`deploy/clickhouse/config.d/memory.xml` already records the symptom and its
first-aid — *"When the live suites start failing MEMORY_LIMIT_EXCEEDED, restart
this container before touching the number"* — and
[`gate-found-defects`](../gate-found-defects/bug.md) removed one cause of it (test
cleanups mutating after every test). This record is about what remains, which
turns out not to be the product's data at all.

## Reproduction

Measured on the development stack, idle, shortly after a restart, with
`max_server_memory_usage` at its configured 4 GiB:

```
SELECT metric, formatReadableSize(value) FROM system.asynchronous_metrics
WHERE metric IN ('MemoryResident','jemalloc.allocated')

jemalloc.allocated   1.57 GiB
MemoryResident       3.04 GiB     ← 76% of the cap, with nothing running
```

So a gate that needs one more gigabyte for a scan does not have it. Where the
disk — and the churn — actually is:

```
SELECT table, formatReadableSize(sum(bytes_on_disk)), sum(rows), count()
FROM system.parts WHERE active GROUP BY table ORDER BY 2 DESC

system.text_log                  3.45 GiB    76,712,391 rows    15 parts
system.trace_log                 1.28 GiB    42,420,278 rows     9 parts
system.part_log                641.79 MiB     6,325,276 rows    11 parts
system.query_log               585.85 MiB     3,602,761 rows     4 parts
system.processors_profile_log  466.15 MiB    21,344,332 rows    13 parts
system.metric_log              163.82 MiB       361,717 rows     7 parts
system.asynchronous_metric_log 103.36 MiB   319,828,465 rows     9 parts

nexovue.logs                   183.76 MiB     2,153,809 rows    59 parts
nexovue.metrics_sum            167.19 MiB    27,836,390 rows    53 parts
nexovue.spans                   84.09 MiB     1,767,649 rows    43 parts
…every nexovue table together:  ~600 MiB
```

**6.7 GiB of the 7.3 GiB on disk is ClickHouse describing itself.** The product's
telemetry — every span, log and metric the demo stack has produced since the
project began — is under a tenth of it.

### What was ruled out, and by which reading

- **The product's data volume.** ~600 MiB across eight tables, 2.1 M log rows.
  Not enough to strain a 4 GiB budget.
- **A pending mutation backlog.** `SELECT countIf(NOT is_done) FROM
  system.mutations` → **0**, while resident memory was still 3.04 GiB.
- **Superseded parts from the purge suites** — the cause `gate-found-defects`
  found. Now 834 inactive parts holding 75 MiB, two orders of magnitude below
  the 12,900 parts and 3.3 GiB measured then. Still a contributor after a purge
  run, no longer the headline.
- **Cache configuration.** `MarkCacheBytes` 459 KiB, `UncompressedCacheBytes` 0,
  `PrimaryIndexCacheBytes` 0 — the caps in `memory.xml` are doing their job.
- **The allocator alone.** `jemalloc.allocated` 1.57 GiB against 3.04 GiB
  resident: ~1.5 GiB is allocator-retained, which a restart reclaims. That is
  why restarting "fixes" it, and why it comes back — it is the *reason the
  first-aid works*, not the reason the memory is needed.

## Root cause

**Every ClickHouse system log is on, unbounded, in a configuration that caps the
server at 4 GiB.** `deploy/clickhouse/config.d/` sets `max_server_memory_usage`,
the cache sizes and `ttl_only_drop_parts`, and says nothing about `text_log`,
`trace_log`, `part_log`, `query_log`, `processors_profile_log`, `metric_log` or
`asynchronous_metric_log` — so each runs at its default, which is *enabled with
no TTL*. They then behave like any other MergeTree: parts accumulate, merges run,
part metadata and mark ranges are held in memory, and the allocator retains what
those merges touched. Seven tables the product never reads generate more inserts,
more parts and more merges than the eight tables it does read, and they never
stop growing.

The 4 GiB cap is not wrong. What is wrong is that most of what fits inside it is
ClickHouse's own bookkeeping.

## Customer impact

Nexovue is pre-deployment, so no customer is affected today. In a deployment:

- **The disk fills with logs about logs.** At the rate measured here — 6.7 GiB
  from one developer's demo traffic — a production node's system tables would
  outgrow the customer telemetry beside them, and the first symptom would be
  ingestion failing on a full disk.
- **Memory pressure lands on customer queries.** ClickHouse does not ring-fence
  its own logs; they compete for the same budget as a customer's trace search,
  so the failure a customer sees is *their* screen returning an error while the
  store is busy merging a table nobody reads.
- **It hides the thing it is supposed to explain.** `part_log` and `query_log`
  are the two tables an operator actually wants during an incident. Unbounded,
  they are also the two that are hardest to query when one is happening.

Today it costs a gate run and a manual restart, repeatedly.

## Fix

`deploy/clickhouse/config.d/system-logs.xml`, mounted for every target that
mounts the rest of `config.d`:

- **Off:** `text_log` (76 M rows of the server's own stderr, duplicated into a
  table nobody queries), `trace_log` (a sampling profiler nothing here reads),
  `processors_profile_log` and `asynchronous_metric_log` (319 M rows describing
  metrics about metrics). Each is `remove="1"`, which is ClickHouse's own way of
  saying "do not create this table".
- **Kept, with a three-day TTL:** `query_log`, `part_log`, `metric_log`. These
  are what an operator reads when something is wrong — which query was slow,
  which merge ran, what memory did — and three days is longer than any debugging
  session and short enough to bound.
- `flush_interval_milliseconds` left alone: the defaults are not the problem.

### The second cause, found by measuring the fix

The configuration above was written believing `old_parts_lifetime` could stay at
its default. Two consecutive gate runs said otherwise:

| | resident | code 241 |
|---|---|---|
| before run 1 | 1.73 GiB | — |
| after run 1 | 3.41 GiB | 0 — the run passed |
| after run 2 | 4.11 GiB | **89 — the run failed** |

and at that point:

```
inactive parts        16,253 holding 24.77 GiB
jemalloc.allocated    3.17 GiB      ← live allocation, not allocator retention
pending mutations     0
```

So the system logs were most of the *baseline*, and the gate's own purges are
the *growth*. The live suites exercise the product's tenant purge (ADR-014),
which is an `ALTER DELETE`; a tenant is not a partition, so each purge rewrites
every part of every telemetry table, and every superseded part is kept —
metadata in memory — for `old_parts_lifetime`, 480 s. A second gate starts
before any of it has expired.

`deploy/clickhouse/config.d/dev-parts.xml` sets `old_parts_lifetime` to 30 s
**in development only**, mounted by `docker-compose.yaml` rather than
`compose.infra.yaml` so no deployment target inherits it. The setting exists to
survive an unclean shutdown; on a laptop whose ClickHouse holds a demo dataset
that `just load` regenerates, there is nothing to survive, and 480 seconds of
safety is bought with the memory the gate needs.

Production keeps ClickHouse's own answer. The right fix for a deployment is not
this — it is that a purge should not rewrite every part of every table, which is
ADR-014's own unresolved consequence and out of scope here.

## Regression test

`apps/api/tests/test_clickhouse_system_logs_live.py`, against the running store:

- every log this configuration disables is absent from `system.tables`
- every log it keeps declares a TTL, so none of them can grow without bound

Seen red before the fix — with the previous configuration all seven tables exist
and none has a TTL, 7 failed — and green after, 7 passed.

The second cause has no unit test, and could not usefully have one: what it
asserts is a property of a whole gate run. It is proved by the measurement
below instead, which is repeatable with `just check` twice in a row.

## Specification impact

None. This is store configuration, not product behaviour.

## Status

See [status.md](status.md).
