# Performance and scale standard

These are the bounds the code already enforces, extracted from it rather than imposed as new
targets. A change that relaxes one of them is a design decision, not a detail.

## Every request is bounded

- Intake caps the body twice: 4 MiB as it arrives and 16 MiB decompressed, because the second is
  what stops a compression bomb. One publish to Kafka is bounded at 5 seconds.
- Every list endpoint declares `DEFAULT_LIMIT` and `MAX_LIMIT`, enforces the maximum in the query
  annotation *and* clamps it in the service. Telemetry lists cap at 200; logs, which are read in
  bulk, cap at 1 000. A new list endpoint does the same rather than returning whatever matches.
- Tenant telemetry reads carry `max_execution_time` 30 s, `max_result_rows` 500 000, and
  `result_overflow_mode: throw`. These are set on the client and on the server, so defeating one is
  not enough. Never raise them for a single slow screen; make the query cheaper.
- The telemetry tier allows 60 requests a minute per caller and 4 concurrent. Rate limit and
  concurrency cap are separate controls: one bounds cost over time, the other bounds a burst.

## Storage is laid out for the read

- Every ClickHouse table orders by `(tenant_id, project_id, …)`. Tenant first is what makes a
  tenant-scoped read a range scan instead of a full scan; it is a performance property and an
  isolation property at once.
- Telemetry tables partition by `(toDate(received_at), retention_days)` and expire by TTL from
  `retention_days`. Retention is enforced by the table, not by a cleanup job.
- Expensive aggregates are precomputed into rollups at one-minute granularity
  (`endpoint_stats_1m`, `service_edges_1m`) through materialized views. A screen that needs a new
  aggregate gets a rollup rather than a heavier query at read time.
- Span-derived metrics are computed **before** tail sampling. Deriving after would make every rate
  and latency a function of the sampling rate rather than of traffic.
- Every tenant-scoped Postgres foreign key is indexed. A migration that adds one adds its index in
  the same change.

## Cost that is not the database

- Receiver caches for keys and allowance are bounded by TTL (30 s) and by entry count, and the
  count is a bound on tenants seen rather than on attacker-chosen strings. An unbounded cache keyed
  by request input is a memory exhaustion path.
- Pools are explicit and small: Postgres 10 with 5 overflow, ClickHouse 8 in the API and 4 per
  worker. Raising a pool to fix latency usually moves the queue rather than shortening it.
- The console virtualizes long lists rather than rendering them, caches reads for 30 seconds, and
  abandons a request after 20. A screen that renders thousands of rows uses the virtualizer the
  traces, logs, and database screens already use.

## What a design must answer

A design document's *Performance and scale* section states the volume the change is expected to
hold, which of the bounds above it operates within, and what it does when that volume is exceeded.
"It should be fine" is not an answer; neither is a benchmark nobody ran. `just load` and
`just chaos` exist for the claims worth measuring.

Nexovue has no production traffic, so none of these bounds has been validated against real load.
They are deliberate engineering limits, not measurements.
