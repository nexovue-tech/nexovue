# Status: three defects F21's final gates found outside F21

## Lifecycle status

**Implemented** — all three fixed and verified; defect 2's root cause found by the founder's choice A
(2026-09-19). Uncommitted.

## Current task

None. Waiting on the founder to read the diff before the commit.

## Completed

- **Defect 1 — the worker now exits when a pipeline fails, and development restarts it.**
  `consume.Loop.Run` defers `AllowRebalance()`, so no way out of `Run` leaves the group's rebalance
  blocked and `Close` can leave the group. `deploy/air/{worker,receiver}.toml` set `rerun = true` with a
  5 s `rerun_delay`, as the production `restart: unless-stopped` does.
- **Defect 3 — the synthetics suite judges a healthy check.** Its subject is the check with the most
  successful timed runs among checks with no failed run in the window, and the phase-count test counts
  that check alone. The QA organization's five hand-made checks were deleted through
  `checks.service.remove` as its owner (audited, incidents closed); they had called httpbin.org every
  minute for two days. The 27 zero-duration readings the pre-fix publisher wrote on 2026-09-17 were
  deleted from the development store (`DELETE FROM metrics_gauge` for that tenant, metric, value and
  23-minute window — 27 before, 0 after).
- **Defect 2 — the live suites' per-test cleanup.** Six suites deleted their test tenant with a
  mutation after every test; each mutation re-wrote every part of its table and ClickHouse held every
  superseded part's metadata for 480 s — 12,900 parts, 3.3 GiB. Found with the allocation profiler
  (`MutateTask::prepare` → `loadColumnsChecksumsIndexes`) and proved by its decay to the minute of
  `old_parts_lifetime`; see `bug.md`. The six now register their tenants with
  `tests/helpers/clickhouse_cleanup.py`, a pytest plugin that deletes them once per table at the end
  of the run. `memory.xml`'s note now states the cause.

## Next action

Founder: read the diff; then the commit.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Production sizing: a purge holds memory for eight minutes.** Deleting an organization runs
  `ALTER TABLE … DELETE` on 14 tables by design (`purge/queries.py`; deleting a project purges
  nothing — its telemetry expires with retention), and each re-writes every
  part, whose superseded copies stay in memory for `old_parts_lifetime`. On a production store with far
  more parts than a laptop's, one purge is a transient spike and several close together are this bug
  again. Decided: erasures run as a nightly batch — ADR-014.
- The product-purge suites (`test_deletion_live`, `test_purge_live`) still issue ~427 mutations a
  gate, because running the real purge is what they prove. The gate peaked at 3.42 GiB of 4.
- Defect 1's fix is proved in development (the worker exited 30 ms after the failure and air started it
  again 5 s later) and by the probe against a real broker; the production path — PID 1 exiting under
  `restart: unless-stopped` — is not exercised by any running deployment, because none exists.
- **Found after commit, fixed (uncommitted, with F17):** air's `rerun` restarted the binary with a
  loop that a rebuild during its 5 s wait does not stop, so the rebuild started a second loop. The
  development worker ran two copies; each new one joined the consumer groups, then failed to bind
  `:8081` and exited, re-balancing the healthy copy every 5 s (108 exits in 3 minutes). Fixed twice
  over:
  - `deploy/air/*.toml` restart with a shell loop in `full_bin`, which air starts and kills as one
    process group.
  - `worker/run.go` binds the health port before any pipeline starts, so a second copy fails before
    it touches Kafka.

  Proved live: a second copy exits on the bind with no pipeline started; a killed binary is back in
  5 s; after a rebuild there is one loop and one binary.
- The probe runs only with `PROBE_KAFKA_BROKERS` set; `just check` compiles it and does not run it, as
  with the existing `storeprobe` probes.

## Validation

- `closeprobe_test.go`: failed before the fix (`Close did not return within 20s`, 20.20 s), passes
  after (0.72 s).
- Live, development stack: `logs` made to refuse inserts; `07:07:08.156 pipeline ended` →
  `worker: pipeline: insert logs failed for 2m0s …` → `Process Exit with Code: 1` →
  `07:07:13.210 worker starting`; container healthy; the table setting reset (`SHOW CREATE TABLE`
  back to its two original settings).
- `test_synthetics_live.py`: 13 passed.
- Defect 2, before: one gate — 63 failures and errors, all `MEMORY_LIMIT_EXCEEDED`. Idle decay after
  the suites: superseded parts 12,941 → 2,178, `jemalloc.allocated` 3.35 → 1.47 GiB, beginning
  exactly `old_parts_lifetime` after the last mutation.
- Defect 2, after: the six suites issue one mutation per table (five tables) and run in 8.7 s.
- **`just check`, 2026-09-19, exit 0**, with ClickHouse at its normal configuration: Python **3,462
  passed**, 3 skipped, no failures (3 min 37 s, from 4 min 49 s); Go every package `ok` (with `vet
  -tags storeprobe`); web 1,214; browser SDK 120; lint, `tsc`, `buf` clean. Sampled every 20 s through
  it: peak 10,049 superseded parts, 2.95 GiB allocated, **3.42 GiB resident** of the 4 GiB cap.
- A stale `.mypy_cache` reported three false errors once; `mypy --no-incremental` was clean and the
  cache was cleared.

## Specification impact

None.

## Documentation impact

- `deploy/clickhouse/config.d/memory.xml` — the cause and the new peak recorded.
- `pyproject.toml` — `addopts = "-p clickhouse_cleanup"`.
- `docs/features/alerts/status.md` — *Found outside F21* points here.
- `docs/changes/README.md` — the record listed.

## Last updated

2026-09-19
