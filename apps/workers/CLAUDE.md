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

**Neither covers a partition moving between two live workers**, and that was worse than a crash: the
losing worker was never told, so it wrote its whole sampling buffer while the gaining worker was
already reading the same uncommitted records — 12.75% of spans stored twice with nothing killed. A
revoke now **drops** what the stages hold for those partitions (ADR 0090), and that drop covers the
counters too (ADR 0094).

**Everything that survives one message keeps the partition it came from**, so a revoke can take that
partition's whole contribution back out: `sample.Buffer` per span, `derive.Counters` and
`serviceedges.Accumulator` per minute-bucket share, `usage.Meter` per tenant-day share. The shares
are summed back together at drain time — in **ascending partition order**, because a floating-point
sum that changes order changes the block bytes ClickHouse deduplicates on — so no table knows the
difference. Dropping is safe because a batch's offsets, and its durable watermark, move only once
every bucket it fed has drained; anything still in an open bucket is a record the broker has not been
told about. Adding a new accumulator without provenance re-opens the defect **silently**: a
duplicated span row can be collapsed, a doubled counter cannot.

All of it is released in one critical section — buffered spans, minute buckets, uncommitted offsets
and the in-process watermark — or the next handover is a duplicate again; and if the watermark alone
survives, a hole. For `usage` a hole is an under-bill and a duplicate is an over-bill, so its four
pieces (meter, `reached`, `receivedThrough`, `dedupe.Offsets`) go together or none of them do.

`log-writing` and `metric-ingest` have the same root and a much narrower window: they insert inside
`Absorb`, so a batch is exposed from its insert to its commit rather than for the ~70s spans wait.
Narrower is not closed — measured over 60 abrupt restarts under load, 49,580 of 905,020 distinct log
rows had two copies. Both now keep a written-through watermark in one shared KeeperMap table
(migration `000029`), keyed by topic and partition so the two signals cannot collide.

**A batch bound in messages is not a bound on memory.** One OTLP message carries as many log records
or data points as the sender's exporter batched, so `WORKERS_*_BATCH_MAX_ROWS` — which counted
messages — let 5,000 messages hold 50,000 rows and OOM-kill the worker at its 1 GiB limit. The
message bound is now `WORKERS_*_BATCH_MAX_MESSAGES`, `WORKERS_*_BATCH_MAX_ROWS` bounds one insert,
and `consume` bounds a batch's bytes as well.

Because of that, **a bucket must always be able to settle**, so `derive` counts on the client's own
span timestamp only while it is within a minute ahead of, or the broker's retention behind, the
envelope's `received_at_unix_nano`; outside that it counts at receive time and says so. A client
clock legitimately ahead of ours therefore adds its skew to the commit lag, up to that minute.

A message that cannot be decoded is parked on `spans-dlq` with its reason and coordinates before
its offset moves. **Nothing drains the dead-letter topic automatically**: a poison message that
retries on a timer is an infinite loop with extra steps.

Writes use `ch-go` (not `clickhouse-go`), which is what ClickHouse recommends for insert-heavy paths.
