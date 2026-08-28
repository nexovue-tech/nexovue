# 0067 — Storage partitions and expires on our clock, not the client's

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

`spans` ([000001](../../../deploy/clickhouse/migrations/000001_spans.up.sql)) partitioned and expired
on `timestamp`, which is the span's start time as supplied by the customer's SDK:

```sql
PARTITION BY toDate(timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
```

Both halves of our retention policy were therefore controlled by a value we do not own.

- A span stamped a year ahead is not dropped until **a year and thirty days** from now. It is
  effectively immortal, and no operator action short of a manual partition drop removes it.
- A span stamped 1970 has already expired when it lands, and the next merge deletes it. That is
  silent data loss, on data we accepted and acknowledged.
- A client whose clock is scrambled rather than merely wrong creates **one partition per distinct
  bogus date**, without bound. Partition proliferation degrades every query against the table.

None of this requires malice. A VM without NTP does it, and door 3 (browser RUM, M5) will carry real
browser clocks, where skew is ordinary rather than exceptional.

`span_trace_lookup` had the same defect one table along: [0063](0063-daily-partitions-on-the-trace-id-lookup.md)
partitioned it on `span_start` and expired it on `span_end`, both derived from the same client value.

This is the storage half of a failure already fixed in the counters, where a future-dated span held
offset commits forever (`apps/workers/internal/pipeline/derive/counters.go`). Fixing the pipeline
left the sink exposed.

## Decision

Add `received_at` — the receiver's own clock, already carried on the envelope as
`received_at_unix_nano` — and move `PARTITION BY` and `TTL` onto it. Keep `timestamp` exactly as it
is, and keep it in the sort key.

```sql
PARTITION BY toDate(received_at)
ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)
TTL toDateTime(received_at) + INTERVAL 30 DAY
```

`received_at` is declared `DEFAULT timestamp`. A writer that does not yet send the column degrades to
the previous behaviour rather than writing epoch — without it, deploying the schema ahead of the
worker would partition live traffic into 1970 and TTL it on arrival.

## Alternatives considered

**Clamp the stored timestamp to a trusted window**, as the counters do. Rejected: the counters reduce
a span to one number in one minute bucket, where attributing it to receive time loses nothing. A
trace waterfall is drawn from the *relative* offsets between its spans, and clamping each span
independently against our clock collapses those offsets — a skewed client's trace would render as a
flat bar instead of a shape. Clamping per trace by a single offset would preserve the shape, but it
requires the whole trace before any span can be written, which is the buffering
[0054](0054-spans-written-through-until-tail-sampling.md) removed.

**Leave it and rely on the counters' trust window.** Rejected: the counter fix bounds what a bad
clock does to *offsets*, not what it does to *storage*. They are separate failures with separate
consequences.

**Store only `received_at` and drop `timestamp`.** Rejected: event time is the answer to "when did
this happen in your system", which is the question a trace exists to answer.

## Consequences

Retention is ours. A partition can be reasoned about from our own clock, and the number of partitions
is bounded by the retention window rather than by what customers send.

**Partition pruning now keys off `received_at`, while queries filter on `timestamp`.** For the
overwhelming majority of traffic the two agree within seconds, but ClickHouse cannot infer that, so a
query bounded only by `timestamp` scans every partition in the range. The traces API must bound on
`received_at` as well to keep pruning — recorded as a follow-up rather than done here, because it is
an API change and the correctness problem was the retention one.

A partition key cannot be altered, so this was a rebuild via `EXCHANGE TABLES`
([000028](../../../deploy/clickhouse/migrations/000028_spans_received_at_table.up.sql)–[000031](../../../deploy/clickhouse/migrations/000031_spans_received_at_cleanup.up.sql))
rather than an `ALTER`. It was done while `spans` held 12M rows on one laptop; the same operation
against a production table is long and needs planning. That it is cheap **now** and expensive later
is the whole reason it was done now.

The backfill sets `received_at := timestamp` for existing rows. That is the only reading available
for them, and it keeps them in the partitions they already occupied.

`logs` carried the same defect and followed in
[000036](../../../deploy/clickhouse/migrations/000036_logs_received_at.up.sql). It was free there in
a way it was not here: empty, and with no writer yet, so there was nothing to copy and nothing to
exchange. P1-28's writer owes the column.

The five metric tables carried it too and followed in
[000038](../../../deploy/clickhouse/migrations/000038_metrics_gauge_received_at.up.sql)–[000045](../../../deploy/clickhouse/migrations/000045_metrics_sum_received_at_cleanup.up.sql).
They were missed on the first pass: only `spans` and `span_trace_lookup` were checked, and the
metric tables had been created hours earlier the same day. Four were empty and replaced outright;
`metrics_sum` holds the derived counters and was exchanged, which the worker kept writing across.
For a customer-sent metric `timestamp` is the customer's clock, so the defect was identical.

The rollback path copies the rows back rather than discarding them
([000029 down](../../../deploy/clickhouse/migrations/000029_spans_received_at_backfill.down.sql)): a
rollback that empties the table is not a rollback.
