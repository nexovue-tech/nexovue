# Bug: three defects F21's final gates found outside F21

Found on 2026-09-19 while F21's `just check` runs failed in suites F21 does not touch. Recorded in
`docs/features/alerts/status.md`, *Found outside F21*, and opened here on the founder's "fix all".

## 1. A worker whose pipeline fails never exits

### Reported behavior

ClickHouse refused an insert (`MEMORY_LIMIT_EXCEEDED`). The worker logged `pipeline ended; the worker
cannot continue` for three pipelines at 06:23:07 and then nothing: no `worker stopped`, no exit
message. The container stayed up, `unhealthy`, with `restarts=0`, and ingest stopped until it was
restarted by hand.

### Reproduction

1. Make ClickHouse refuse inserts into one table, reversibly:
   `ALTER TABLE logs MODIFY SETTING parts_to_throw_insert = 1` (each partition held 5–6 parts).
2. The log pipeline retries for two minutes and ends: `07:00:18.018 ERR pipeline ended; the worker
   cannot continue … pipeline=log-writing`.
3. 15 s later the worker binary is still running (`docker top`: PID 7732
   `/cache/air/worker/worker`), and its goroutine dump (`/debug/pprof/goroutine?debug=2`) shows the
   main goroutine at:

   ```
   kgo.(*Client).LeaveGroupContext        consumer_group.go:317
   kgo.(*Client).Close                     client.go:1308
   consume.(*Loop[...]).Close              consume.go:410
   logwriting.(*Pipeline).Close            pipeline.go:189
   worker.startPipelines.func1             run.go:154
   worker.run                              run.go:135   (the deferred stopPipelines)
   ```

4. `ALTER TABLE logs RESET SETTING parts_to_throw_insert` undoes step 1.

### Root cause

`apps/workers/internal/pipeline/consume/consume.go`, `Loop.Run`. The consumer is built with
`kgo.BlockRebalanceOnPoll()`: a poll that returns records blocks every rebalance until
`AllowRebalance()`. `Run` called `AllowRebalance()` only at the bottom of a successful iteration; when
`turn` returned an error — the store refused the batch — `Run` returned with the rebalance still
blocked. Leaving the group is a rebalance, so the `Close` that `run`'s deferred cleanup makes waits in
`LeaveGroupContext` for ever. franz-go's own documentation on the option: "You must always
AllowRebalances when you are done processing the records you received."

A second, development-only cause kept the container up even had the binary exited:
`deploy/air/{worker,receiver}.toml` leave air's `rerun` at its default `false`, and air returns after
its child exits (`runner/engine.go`: `if !e.config.Build.Rerun { return }`) — so the container lives
on with nothing running.

### Customer impact

Pre-deployment, so none today. In production the worker is PID 1 with `restart: unless-stopped`
(`docker-compose.app.yaml`), so the only thing standing between a failed write and a restart was this
hang: every signal's ingest would stop, the container would show `unhealthy`, and nothing would bring
it back. Telemetry is retained in Kafka, so it would be written late rather than lost — until Kafka's
retention passed.

## 2. Development ClickHouse refuses queries with little in use

### Reported behavior

63 failures and errors in one gate across eight ClickHouse-backed suites, every one
`MEMORY_LIMIT_EXCEEDED … current RSS: 4.0x GiB, maximum: 4.00 GiB`, with the queries themselves small.

### Reproduction

Restart ClickHouse (`MemoryResident` 1.03 GiB), run the six ClickHouse-heavy API suites twice:
`MemoryResident` 3.58 GiB, `MemoryResidentMax` 3.75 GiB, `jemalloc.allocated` 3.40 GiB — against 70 MiB
in `MemoryTrackingUncorrected`. With the allocation profiler on, one run reached the limit.

### What was ruled out, by measurement

- **Dirty pages.** `jemalloc.arenas.all.pdirty` near zero. ClickHouse 26.3's memory worker already
  purges them past 20 % of the cap (`memory_worker_purge_dirty_pages_threshold_ratio`, default 0.2 —
  [ClickHouse#93500](https://github.com/ClickHouse/ClickHouse/pull/93500)).
- **Caches.** `SYSTEM DROP` of every cache and `SYSTEM JEMALLOC PURGE`: `jemalloc.allocated` 3.37 GiB
  before and after.
- **Parts metadata.** 419 active parts, 2.9 MiB of primary keys.
- **Keeper.** `KeeperLatestLogsCacheSize` 58 MB.
- **Merges.** None running.

### Root cause

**The live suites' cleanup.** Six suites emptied their test tenant after every test with
`ALTER TABLE <table> DELETE WHERE tenant_id = …` (`test_dbstats_live`, `test_errorgroups_live`,
`test_filters_reach_past_the_cap`, `test_infrastructure_live`, `test_rum_live`,
`test_synthetics_live`). That statement is a mutation, and a tenant is not a partition, so each one
writes a new generation of every part in the table. ClickHouse keeps each superseded part in memory —
its per-file checksums and column substreams, large for the JSON attribute columns — until
`old_parts_lifetime` (480 s) has passed. Dozens of mutations per table inside that window held
**12,900 superseded parts** — `metrics_sum` 55 active and 2,464 outdated, `metrics_gauge` 40 and
2,057, `logs` 51 and 1,657. None of it is counted by ClickHouse's memory tracker, and the server's
guard counts resident memory, so ordinary queries were refused.

**Found with the allocation profiler**, reading live allocations the way ClickHouse documents it
(`system.trace_log`, `JemallocSample`, allocations paired with their frees by pointer), each sample
standing for ~512 KiB: the two largest live allocation sites, ~1.5 GiB between them, were
`ColumnsSubstreams::readText` and `MergeTreeDataPartChecksums::read`, both under
`IMergeTreeDataPart::loadColumnsChecksumsIndexes` ← `MutateTask::prepare`. `system.mutations` then
showed 34–80 per-tenant deletes per table in three minutes of suites.

**Proved by its decay.** With nothing running, sampled every 30 s: superseded parts 12,941 → 2,178
and `jemalloc.allocated` 3.35 → 1.47 GiB, starting at 07:41:34, eight minutes after the last
mutation — `old_parts_lifetime` to the minute. Resident memory stayed ~3.7 GiB: the allocator keeps
what it once held, which is why an idle ClickHouse *looked* full in every earlier investigation, and
why restarting it was the only thing that ever helped.

**Why the product is not the cause.** `purge/queries.py` uses the same heavy mutation deliberately —
a lightweight delete only masks rows, and a purge must remove the bytes — but it runs once per
deleted organization or project. Its own suites (`test_purge_live`, `test_deletion_live`) keep
running it, because it is what they prove.

## 3. A synthetics suite picked a check that fails, and old data it could not pass

### Reported behavior

`test_synthetics_live.py`: `assert 7475 == (5 * 3496)` and `assert 0.0 > 0.0` for a bucket of
2026-09-17 17:36.

### Root cause

Two things, neither in the product:

- The suite's subject was "the check with the most successful duration readings", after which a test
  asserted five phase readings per status reading **across the whole tenant**. The winner was a
  hands-on QA organization's (`end-to-end-8ddbde8e`, 2026-09-17), whose checks called httpbin.org every
  minute and failed some runs — and a run that never connects publishes fewer than five phases.
- 27 zero-duration readings, written 17:51–18:13 on 2026-09-17 by heartbeat and misconfigured runs
  before the publisher stopped emitting a duration for untimed runs (`publish.py`: "No point at all
  when nothing was timed"). Nothing has written one since.

The E2E specs were not the source: `synthetics.spec.ts` and `alerts.spec.ts` delete what they create
in `finally`. The five checks were made by hand in a QA pass.

## Specification impact

None.

## Regression test

- **1:** `apps/workers/internal/pipeline/consume/closeprobe_test.go` (`storeprobe` tag, live Kafka) —
  a store that refuses the first batch; `Run` must return the refusal and `Close` must return within
  20 s. Observed failing before the fix:
  `Close did not return within 20s: the consumer is stuck leaving its group …` (20.20 s), passing
  after it (0.72 s). `just check` compiles it through `go vet -tags storeprobe`.
- **2:** `tests/helpers/clickhouse_cleanup.py` — a pytest plugin (`-p clickhouse_cleanup`) the six
  suites register their tenants with; the session's end issues one mutation per table for all of
  them, unawaited. Observed: the six suites issued one mutation per table (five tables) where they
  had issued 34–80 each, and ran in 8.7 s. The full-gate measurement is in `status.md`.
- **3:** the suite's subject is now a check with no failed run in the window, and the phase count is
  that check's. Observed: before, test 1 failed on the QA check; after, it passed while the QA checks
  still existed.

## Fix plan and validation

See `status.md`.

## Status

See `status.md`.
