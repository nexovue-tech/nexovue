# 0069 — Severity before time in the log sort key

- **Status:** Accepted
- **Date:** 2026-08-28


> **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named
> here no longer exist as separate files; each table's final shape is now one file, and the
> links point there. See [0073](0073-migrations-squashed-before-m3.md).

## Context

[0057](0057-project-id-in-every-telemetry-sort-key.md) established that a sort key is free while a table is
empty and a full rebuild once it is not, because ClickHouse cannot alter one in place. `logs` still
holds zero rows, and P1-28 fills it next.

an earlier migration, now folded into [000002_logs](../../../deploy/clickhouse/migrations/000002_logs.up.sql), added
`severity_number` as a column — SDKs emit `WARN`, `WARNING`, `W` and `warn` for one level, so "warn
and above" is not expressible against the text — but left the sort key as
`(tenant_id, project_id, service_name, timestamp)`. Having the column and not ordering by it means
the commonest log query in the product still scans a whole time window and filters row by row.

## Decision

`ORDER BY (tenant_id, project_id, service_name, severity_number, timestamp)`.

Severity goes **before** the timestamp. After it the column would be decoration: the key is already
fully ordered by the time any severity comparison is reached, so it could not narrow a scan. The
OpenTelemetry ClickHouse exporter orders its log table the same way, severity ahead of time.

## The trade

This is not free, and the cost falls on time-range queries.

- **"Errors in this service, this window"** reads one contiguous range instead of scanning the window
  and discarding most of it. This is the question the product exists to answer first.
- **"Everything in this window"** now reads one range per severity present rather than one overall.
  In practice that is a handful of ranges — OTLP defines 24 severity numbers but real traffic
  clusters into a few — and each remains time-ordered within itself, so the granules read are much
  the same. The planner handles multi-range reads on a sorted key natively.

The asymmetry is what decides it: the severity-filtered query improves by a large factor, the
unfiltered one degrades by a small one, and an observability product's first log question is "what
is broken", not "show me everything".

## Consequences

`severity_number` is `0` when a record carried no severity. Those rows sort first, ahead of every
real level, which is the right place for them — a query for "warn and above" excludes them by the
same comparison that excludes DEBUG, rather than needing a special case.

P1-28's writer must populate `severity_number` from the text on every record. A writer that leaves
it at `0` does not merely lose a filter, it puts every row it writes into one sort bucket.

Done as a rebuild ([000002_logs](../../../deploy/clickhouse/migrations/000002_logs.up.sql))
while the table is empty, which costs nothing. The same change after M3 has been running is a rebuild
of the busiest table in the system.
