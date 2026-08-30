# workers — the processing pipeline (Go)

**One binary.** `WORKER_PIPELINES` selects which pipelines a process runs — `trace-assembly`,
`log-writing`, `metric-ingest` (ADR 0050). An unknown name fails at boot rather than silently not
running. The names are load-bearing: splitting a deployment is a config change, not a rewrite.

Counter derivation and the service-edge rollup are stages **inside** `trace-assembly`, not names of
their own. They had names once and started nothing.

Pipeline order is **decode → mask → derive → sample → sink**, and it carries two invariants:
- **`derive` runs before `sample`** — metrics are computed from all traces, so counts stay exact
- **`mask` runs before everything downstream** — an unmasked value never reaches a counter, a
  buffer or the store, because the stage rewrites the decoded payload in place

**`trace-assembly` buffers by trace for 10s** (`WORKERS_SAMPLING_WINDOW`) so tail sampling can see a
whole trace before deciding. ADR 0009 proposed ~30s; 10s is what the §4 p99 < 15s budget leaves.
Spans arriving after the window follow their trace's remembered verdict, and are written
individually once that is forgotten — ClickHouse joins them at read time.

Offsets commit only after everything a batch produced has been stored: its spans out of the
sampling buffer *and* its counters out of their minute bucket. That is at-least-once, and it puts
commits up to about 70s behind ingest by design (measured: p50 43s, p90 72s). A replay must
therefore be recognised on **both** counts — re-inserting an identical span row does *not* change
nothing, because `spans` is a plain MergeTree and nothing deduplicates it on read.
`internal/pipeline/dedupe` catches a redelivery inside one process; `internal/pipeline/ingeststate`
catches one across a restart, keeping both watermarks in Keeper — counted-through (ADR 0061) and
written-through (migration `000026`).

Because of that, **a bucket must always be able to settle**, so `derive` counts on the client's own
span timestamp only while it is within a minute ahead of, or the broker's retention behind, the
envelope's `received_at_unix_nano`; outside that it counts at receive time and says so. A client
clock legitimately ahead of ours therefore adds its skew to the commit lag, up to that minute.

A message that cannot be decoded is parked on `spans-dlq` with its reason and coordinates before
its offset moves. **Nothing drains the dead-letter topic automatically**: a poison message that
retries on a timer is an infinite loop with extra steps.

Writes use `ch-go` (not `clickhouse-go`), which is what ClickHouse recommends for insert-heavy paths.
