# F20 measurement report, 2026-11-22

Plan T5.7 (first run) and T11.6 (the rerun after Steps 8 and 9, which this is the first of: T5.7 had been deferred).
Marks: [`f20-pass-marks-2026-11.md`](f20-pass-marks-2026-11.md), committed before the run. Tool: `tools/dashboard-load/load.py`.
Raw results: `f20-measurement-2026-11-22-1-api-process.json`, `...-2-api-processes.json`.

**This is the demo, on a shared 4 vCPU / 15 GiB VM with other builders' load, with the API unconstrained by a container
memory limit, over rollups written directly (no raw spans, logs or metrics). It does not prove customer volume, and the
rerun on Oracle is still required before any cap is published.**

## Result against the marks

| # | Mark | 1 API process | 2 API processes |
|---|---|---|---|
| 1 | NFR4: one worst-case dashboard, p95 at most 3,000 ms | **Pass**: 229 ms p95 (3 reads) | **Pass**: 220 ms |
| 2 | One person, one dashboard: 0 refused/failed/timed out | **Pass**: 84 of 84 panels `ok` | **Pass**: 84 of 84 |
| 3 | Worker lag peak within baseline + 2,000 | **Inconclusive.** Peaks 18.6k, 21.8k, 14.4k at 1, 5, 20 dashboards; baseline 17.8k before and **31.0k after**, so the gauge rose by itself over the run; the ingest trickle (400 spans/s) is near what this worker keeps up with here | Peaks 9.2k, 12.2k, 16.2k; baseline 21.4k before, 28.8k after. Inconclusive for the same reason |
| 4 | Dead letters +0, every export 2xx | **Pass**: +0; 209 of 209 exports 200 | **Pass**: +0; 186 of 186 |
| 5 | API account: no 241, peak query memory at most 1,024 MiB | **Pass**: peak 10.7 MiB, no errors (the account saw no 202 either) | **Pass**: 10.7 MiB |
| 6 | ClickHouse resident at most 4,000 MiB | **Pass**: at most 1,127 MiB | **Pass**: at most 1,072 MiB |
| 7 | API peak resident at most 832 MiB | **Pass**: 324 MiB | **Pass**: 276 MiB each |
| 8 | Scheduler's three measures | **Not run**: no scheduler | **Not run** |
| 9 | NFR12: 30 people on one dashboard; 10 zooming | **Not run** (this tool invited 20 viewers, not 30, and ran no team case) | **Not run** |

## What the load did (20 viewers, each their own dashboard window, 3 rounds)

| Dashboards at once | 1 process: batch p50 / p95 | panels | HTTP errors | 2 processes: p50 / p95 | panels |
|---|---|---|---|---|---|
| 1 | 199 / 229 ms | 84 ok | none | 199 / 220 ms | 84 ok |
| 5 | 829 / 1,048 ms | 406 ok, 14 empty | none | 488 / 732 ms | 402 ok, 8 empty, 10 refused |
| 20 | 886 / **11,662 ms** | 798 ok, 46 empty, 80 refused | **7 x 500**, 20 x 429 | 937 / 1,610 ms | 651 ok, 30 empty, **383 refused** |

(Panels `empty` are windows that end before the data does; a read with no window overlap.)

## Findings

1. **A failed margin: twenty people reading at once on one API process produced HTTP 500.** The logs say
   `QueuePool limit of size 10 overflow 5 reached, connection timed out` (and a `MissingGreenlet` after it). Sampled
   during the run, `pg_stat_activity` showed all 15 pooled connections `idle in transaction` whose last statement was
   the identity lookup (`SELECT users...`): each request keeps its Postgres connection from the identity check until
   its planning reaches the next statement, on a single event loop that is busy planning 20 thirty-panel documents.
   The handler does commit before scanning (`dashboardpanels/service.py`, "let go of the connection before ClickHouse"),
   so the hold is before that line. **Not fixed here**; it needs a decision (open decision 40 in `status.md`).
   Under the plan, a failed margin stops release at these caps.
2. With two processes the 500s went away and the per-process limits (halved by `API_INSTANCES=2`) refused 383 panels
   instead: working as designed, and it says the caps are low for 20 simultaneous worst-case readers on two processes.
3. The API account's reads were small (10.7 MiB at most), so the two server-side bounds (2.4 GiB, 12 at once) were never
   approached: **they remain guesses, untested at their edge.**
4. All three starting points open for one person with no refused panel (AC129, 1 hour, one service picked on the
   demo): *Start from Overview* 8 ok in 90 ms; *Service health* 5 ok, 1 `not_in_project`, 114 ms; *Infrastructure* 3
   `empty` (the demo has no host metrics), 213 ms.

## Not run, and why

The incident script (needs ClickHouse stopped under load); NFR11's scheduler measures (no scheduler); NFR12's 30 people and
10 zooming; raw readers at 7, 30 and 90 days (the seed writes rollups only); memory with the shared-read store full;
the findings read's plan; AC91 and AC63 under load; metric series and catch-up reads (no raw data). **The measurement
gate is not passed.**
