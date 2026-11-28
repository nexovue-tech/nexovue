# F20 measurement report, 2026-11-27

Plan T5.7 and T11.6, rerun after [ADR-102](../decisions/ADR-102-a-request-gives-back-its-postgres-connection-once-its-caller-is-known.md)
(the caller's lookup gives its Postgres connection back; a full pool answers 503 `service-busy`). The run it repeats:
[`f20-measurement-2026-11-22.md`](f20-measurement-2026-11-22.md). Marks: [`f20-pass-marks-2026-11.md`](f20-pass-marks-2026-11.md),
unchanged. Tool: `tools/dashboard-load/load.py`, at commit 4bbcafa6. Raw results: `f20-measurement-2026-11-27-1-api-process.json`.

**This is the demo data, on the founder's Mac (Apple M4 Pro, 12 cores, 24 GB, macOS 26.6.2; Docker Desktop with 12 CPUs
and 11.7 GiB), shared with nothing heavy, over rollups written directly (no raw spans, logs or metrics). It does not prove
customer volume, and it does not replace the rerun on the deployed server before any cap is published.**

## How it ran

- `just down-hard`, then `just stack` (no OpenTelemetry Demo), then `tools/dashboard-demo/demo.py --run`: 15 services,
  7 days, 144,215 `service_stats_1m` rows, 372,955 endpoint rows, 144,349 version rows, 87 releases, and the demo's
  fault step (20 slow minutes for `checkout`, 12:51 to 13:10 UTC), which the 2026-11-22 run did not have.
- One API process from source, not in a container: `uvicorn nexovue_api.main:app` on 127.0.0.1:18100, no `--reload`,
  `ENV=development`, `EMAIL_PROVIDER=console` (its log is the tool's `--mail-log`), `API_INSTANCES=1`,
  `TRUSTED_PROXY_COUNT=1`, default pool (10 + 5, 10 s timeout), the dev compose file's other values on localhost ports.
  Started under `/usr/bin/time -l` for its peak memory (below).
- `load.py --people 20 --levels 0 1 5 20 0 --rounds 3`, receiver `localhost:14318`, the dev ingest key: as on 2026-11-22.
- **Two differences from 2026-11-22, forced by macOS:**
  - The worker's `/metrics` (port 8081) is not published by the dev stack, so a small forwarder outside the repository served
    it on 127.0.0.1:18081 by `docker exec ... wget`. The same counters, read the same way.
  - The tool reads API memory from `/proc/<pid>/status`, which macOS has no equivalent of, so `--api-pid` was not passed and
    the JSON's `api_peak_rss_mib` is empty. Mark 7 is `/usr/bin/time -l`'s *maximum resident set size* at exit: **one figure
    for the whole run, not one per level** (the 2026-11-22 figure, `VmHWM`, was also the process's lifetime peak).
- The dev stack's scheduler containers were running (the 2026-11-22 run had none). Nothing here reads their measures.
- `pg_stat_activity` was sampled about once a second during the run (by `docker exec psql`, so coarse).

## Result against the marks

| # | Mark | 1 API process |
|---|---|---|
| 1 | NFR4: one worst-case dashboard, p95 at most 3,000 ms, max at most 5,000 ms | **Pass**: 95.8 ms p95, 95.8 ms max (3 reads) |
| 2 | One person, one dashboard: 0 refused/failed/timed out | **Pass**: 84 of 84 panels `ok` |
| 3 | Worker lag peak within baseline + 2,000 | **Inconclusive.** Peaks 14.4k, 18.4k, 22.4k at 1, 5, 20 dashboards; baseline 12.4k before and **30.8k after**, so the gauge again rose by itself over the run, as on 2026-11-22 |
| 4 | Dead letters +0, every export 2xx | **Pass**: +0 at every level; 187 of 187 exports 200 |
| 5 | API account: no 241, peak query memory at most 1,024 MiB | **Pass**: peak 8.5 MiB during reads, no failed query, no 202 either |
| 6 | ClickHouse resident at most 4,000 MiB | **Pass**: at most 1,624.7 MiB |
| 7 | API peak resident at most 832 MiB | **Pass**: 328.5 MiB (344,473,600 bytes), lifetime peak, one figure for the run |
| 8 | Scheduler's three measures | **Not run**: the tool does not read them |
| 9 | NFR12: 30 people on one dashboard; 10 zooming | **Not run** (20 viewers invited, no team case) |

## What the load did (20 viewers, each their own dashboard window, 3 rounds)

| Dashboards at once | batch p50 / p95 / max | panels | HTTP errors | 2026-11-22, 1 process: p50 / p95, errors |
|---|---|---|---|---|
| 1 | 76 / 96 / 96 ms | 84 ok | none | 199 / 229 ms, none |
| 5 | 240 / 624 / 624 ms | 420 ok | none | 829 / 1,048 ms, none |
| 20 | 608 / 717 / 718 ms | 919 ok, 201 refused | **20 x 429**; no 500, no 503 | 886 / 11,662 ms, **7 x 500**, 20 x 429 |

At 20 dashboards: 60 reads, 40 answered 200 and 20 answered 429. No panel was `empty` this time (the seed ended
minutes before the run, so every window had data).

## Findings

1. **The HTTP 500 at 20 readers on one API process is gone.** The API log has no `QueuePool`, no `MissingGreenlet`, no
   traceback and no 500 or 503: 101 x 200, 40 x 201, 20 x 204, 20 x 429 in total. `pg_stat_activity` never showed
   more than **1** connection `idle in transaction` (2026-11-22: all 15). The 20-dashboard p95 fell from 11,662 ms to 717 ms.
   No 503 `service-busy` was needed: the pool never filled.
2. **The 20 x 429 are the organization's telemetry quota, working as designed.** All 20 were logged within one instant,
   `rate limit rejected a request ... policy=telemetry-organization` (600 a minute per organization,
   `apps/api/src/nexovue_api/ratelimit/policy.py:98`): one of the three rounds at 20 was refused whole. The 201 `refused`
   panels are the per-process scan limits. Both are the same refusals as 2026-11-22 (20 x 429 there too), and the marks
   record them at levels 5 and 20 rather than fail them.
3. The API account's reads stayed small (8.5 MiB at most; 20.0 MiB in the closing baseline, which had no dashboard reads:
   20 queries by the same account that this tool did not send, source not identified). The server-side bounds
   (2.4 GiB, 12 at once) were never approached: **they remain guesses, untested at their edge.**
4. Batch times are 2 to 3 times faster than 2026-11-22 at 1 and 5 dashboards: a faster, unshared machine, so not a
   product change and not comparable as a capacity figure.

## Not run, and why

Two API processes (one process passed, so not needed for this question); the templates case (AC129, `--templates`); the
incident script (needs ClickHouse stopped under load); NFR11's scheduler measures (the tool does not read them); NFR12's
30 people and 10 zooming; raw readers at 7, 30 and 90 days (the seed writes rollups only); memory with the shared-read
store full; the findings read's plan; AC91 and AC63 under load; metric series and catch-up reads (no raw data); per-level
API memory (no `/proc` on macOS). **Marks 1, 2, 4, 5, 6 and 7 pass; mark 3 is inconclusive and 8 and 9 are not run, so
the measurement gate is still not passed.** The failed margin that stopped release on 2026-11-22 is not reproduced.
