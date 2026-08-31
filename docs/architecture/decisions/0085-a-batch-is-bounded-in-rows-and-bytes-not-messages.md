# 0085 — A batch is bounded in rows and bytes, not only in messages

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

`nexovue-worker-1` was being OOM-killed against its 1 GiB limit. Caught in the log immediately
before `Killed / Process Exit with Code: 137`, with `docker inspect` reporting `OOMKilled=true`:

```
batch absorbed  pipeline=log-writing  messages=5000  logs=49662
batch absorbed  pipeline=log-writing  messages=5000  logs=49875
batch absorbed  pipeline=log-writing  messages=5000  logs=49893
```

**`WORKERS_LOGS_BATCH_MAX_ROWS` counted Kafka messages, not rows**, and so did
`WORKERS_METRICS_BATCH_MAX_ROWS` and `consume.Settings.MaxRows` behind them. `LogIngest`'s own
docstring said *"MaxRows keeps one insert to a size ClickHouse merges cheaply"*, which is what the
name promises and not what the code did. One OTLP message carries as many log records or data points
as the sender's exporter batched, so a bound reading "5,000 rows" held fifty thousand. At a hundred
records per export — an ordinary exporter setting — the same configuration holds half a million.

Two things were wrong and the second is the serious one. The name did not describe the behaviour,
which is why nobody noticed the bound was ten times looser than it read. And **memory was unbounded
in records-per-message**: nothing in the pipeline noticed how much a batch was holding. That is not a
dev-stack artifact. It is a customer with a well-configured exporter taking down ingest for every
tenant, and it interacts with idempotency — a batch that OOMs never commits its offsets, so it
replays, into the same OOM.

## Decision

**Three bounds, each naming what it counts.**

- `WORKERS_{LOGS,METRICS}_BATCH_MAX_MESSAGES` (5000) — how many Kafka messages one batch holds. This
  is what the old `*_BATCH_MAX_ROWS` actually did, under its right name.
- `WORKERS_{LOGS,METRICS}_BATCH_MAX_ROWS` (5000) — how many **rows** accumulate before they are
  inserted. `Absorb` flushes at a message boundary when the block reaches it and goes on filling, so
  one insert's size is a property of the configuration rather than of the sender's batching.
- `consume.Settings.MaxBytes` (16 MiB default, applied when a pipeline names none) — how many bytes
  of raw messages one batch holds, plus `kgo.FetchMaxBytes` set to the same value so a single poll
  cannot hand back more than the budget before the bound is consulted.

The byte bound lives in `consume` and defaults rather than being opt-in, so it covers
`trace-assembly` and the three usage meters too — every pipeline has this defect, and a default is
the only way to fix it everywhere without editing packages other agents hold.

The env var `WORKERS_*_BATCH_MAX_ROWS` keeps its name and changes meaning. Correcting the meaning in
place was rejected: a deployment that set the old name was setting a message bound, and would now be
setting a row bound, which is the safer of the two things to be surprised by.

## Alternatives considered

**Raise the container's memory limit.** Rejected: it moves the cliff rather than removing it. The
term that overflows is per-message record count, which the customer sets and no limit bounds.

**The row bound alone.** This was the first change and it is measurably not sufficient. It fixed the
crash-loop on a backlog of ordinary messages, and then the same 1 GiB was exceeded again by a load of
200 records per message — because the rows in the block were never the only thing held. The raw
`[]*kgo.Record` the loop accumulates is bounded only in count, and franz-go buffers a fetch behind
it. Recorded because the reasoning is seductive and wrong: bounding the insert does not bound the
batch.

**A byte bound only, with no row bound.** Rejected: they bound different things. Bytes on the wire
are compressed protobuf; rows in the block are decoded columns with JSON strings, and the ratio
between them is the customer's payload shape. Bounding one leaves the other free.

## Consequences

**Measured, and the second number is the honest one.** A backlog of 209,344 log messages that
crash-looped the worker drained after the row bound with **zero** `Exit with Code: 137` in the log,
and both consumer groups reached lag 0. Then, on a load of 200 records per message: before the byte
bound the worker reached **1016 MiB and was OOM-killed**; after it, the same load peaked at **964
MiB and survived**. Across the subsequent nine-minute run with sixty abrupt restarts there were
**zero** OOM kills. 964 of 1024 MiB is not a margin, and this ADR does not claim one.

**What is left is not batching, and it is not fixed.** After a heavy drain the worker sits at ~739
MiB while idle, having idled at 36 MiB after a fresh start. That is retained Go heap, not live data.
Go does not read the cgroup limit, so its GC has no reason to stop growing before the OOM killer
does. `GOMEMLIMIT` is the standard remedy and is a deployment change with its own trade-off (a GC
that works harder near the limit rather than dying). **It was not profiled**, so no cause is claimed
beyond what those two idle numbers show, and no fix is recorded here.

**One coupling survives, deliberately, and it is fragile.** `usage/usage.go` reads
`cfg.Logs.MaxRows` and `cfg.MetricIngest.MaxRows` as *message* bounds. After the rename those fields
are the insert row bound, and the usage meters are correct today only because both defaults are
5000. Whoever changes either default must point those two lines at `MaxMessages` first. They were
left alone because that package was held by another agent at the time.

**`consume.Settings.MaxRows` still counts messages** and still carries the wrong name, for the same
reason: renaming the field means editing `traceassembly` and `usage` in the same change. Its doc
comment now says outright what it counts.

**The startup line gained `insert_max_rows`**, because `batch_max_rows` in that same line means
messages and the two are otherwise indistinguishable.
