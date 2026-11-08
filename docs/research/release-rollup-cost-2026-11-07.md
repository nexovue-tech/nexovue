# Release rollup cost, Step 1a gate (2026-11-07)

> **Demo data, development machine, not a server figure.** Every number below comes from `tools/release-demo` traffic
> on a development machine. None of it is a measurement of a real customer's traffic or of a 16 GB server, and none
> of it may be published as one (Phase 2 foundation, *What A and B change*).
>
> **Type:** Measurement report, the committed cost report F28's spec asks for in AC21 and the plan in T1.7. Written
> 2026-11-07 on branch `wt/f28` from the runs listed below. Nothing here approves anything: it gives the founder the
> numbers behind the gate.
>
> **Status:** Gate passed on demo data. Every design row that Step 1a can measure passed. The sixth row, the alert
> evaluation start delay, was measured with Step 1b's sync job on 2026-11-08 and passed (*Addendum, Step 1b*).

## The result in plain words

Counting requests by version adds about 10% more rows and about 10% more disk than the per-service table already
holds when every service rolls out once an hour. It made no difference to the worker's memory that the measurement
can tell apart from noise. Under deliberate churn (70 new version names a minute on each of 40 services), the worker
stayed far under its memory ceiling, and every request was still counted: the version caps put most of the churn
into the combined `__other__` row, as designed, and no request was lost.

If this had failed, the product would have lost the comparison by version, and with it the "releases appear with no
webhook" route that buyers expect (*What disappears if the gate fails*, below).

## How it was measured

*Demo data, development machine, not a server figure.*

- **Machine.** A Linux development container, 4 CPUs and 16 GB of memory. ClickHouse, Postgres and Kafka ran as the
  local stack on non-default ports. The receiver and the worker ran from source as host processes, not in Docker, so
  no container memory ceiling held them. Each worker was started fresh for each run with `GOMEMLIMIT=1088MiB`, the
  value `deploy/compose/nexovue.yaml:249` ships. The plan said Step 1a would run on the founder's Mac's Docker; it ran
  here instead, so the worker's 1.25 GB container ceiling was not exercised (the Go limit was).
- **Builds.** The baseline worker was built from commit `5988d88` (the branch's last commit, before T1.2's version
  rollup), and logs `revision 5988d88…`. The versions worker was built from the same commit with Step 1a's
  uncommitted work, and logs `5988d88…+dirty`. Baseline and versions runs were interleaved.
- **Traffic.** `release_measure.py run`, which sends `send.py` traffic through the real receiver, samples the worker's
  resident memory (RSS) every 10 seconds, waits for every request to drain into `service_stats_1m`, then reads the
  rollups and runs `OPTIMIZE TABLE … FINAL` before reading disk sizes.
  - **Demo (`many-services`):** 50 services at 60 requests a minute each (3,000 a minute), one 5-minute rollout an hour
    on every service, staggered across the hour. 30 minutes a run, 3 repeats each for baseline and versions.
  - **Churn (`churn`):** 40 services at 840 requests a minute each (33,600 a minute), 70 version names a minute on each
    service, new names every minute (2,800 a minute in all). 15 minutes, one run each for baseline and versions.
- **The report.** `just release-measure report --results <folder>` turned the eight JSON results into the table below.
  Its full output is at the end of this file.
- **A discarded run.** An earlier baseline repeat 3 was thrown away and rerun, because a second copy of the run driver
  overlapped it and sent duplicate traffic. This was the coordinator's error, not the tool's. None of its numbers are
  used here; the baseline repeat 3 below is the clean rerun (14:29 to 14:59 UTC).

**Which rows apply to which traffic.** The design's gate table (design, *The gate (D27)*) has four ratio rows for
ordinary traffic with rollouts, and its own row for churn: "Many services with many versions on every partition, at
the arrival caps: worker stays under `GOMEMLIMIT`, totals exact". The founder's advance decision 24 puts the same split
in words: rows, memory share and disk share are cost checks; the worker over `GOMEMLIMIT` and totals not exact are
correctness checks. So the four ratios are judged on the demo, and churn is judged on its own row. The churn run's
ratios are still shown below, for information.

**What "totals exact" means here.** For each versioned run: every request sent drained into `service_stats_1m`; each
service's version rows sum to its service row (FR16); for each service, the sum over its versions, `__other__`
included, equals what was sent to it, for requests and for errors; and no named version holds more than was sent to
it. Folding only moves counts into `__other__`, so a named version can come out short, which FR17 and AC18 allow. A run
like that is exact but has *inexact versions*: the screen says "Versions were combined" for it.

## The gate

*Demo data, development machine, not a server figure. Thresholds are the design's guesses.*

| Design row | Traffic | Measured | Bound | Result |
|---|---|---|---|---|
| Rows in steady traffic | Demo | 1.000× `service_stats_1m` (by construction: 1,375 to 1,402 service-minutes per run served one version, one row each) | At most 1.5× | Pass |
| Rows with one rollout an hour on every service | Demo | 1.096× at most (runs: 1.083, 1.096, 1.095) | At most 3× | Pass |
| Worker peak memory | Demo | −0.3%: largest peak 53.7 MiB with versions against 53.9 MiB baseline | At most +15% | Pass |
| Bytes on disk per request | Demo | 1.101× at most (runs: 1.078, 1.096, 1.101) | At most 2× | Pass |
| Many services with many versions on every partition, at the arrival caps | Churn | Worker peak 151.4 MiB; totals exact in 1 of 1 runs, all 40 services folded into `__other__` | Under `GOMEMLIMIT`, 1,088 MiB; totals exact | Pass |
| Alert evaluation start delay | — | Measured in Step 1b | Within 10 seconds of baseline | Passed: 0.049 s median against 0.071 s (*Addendum, Step 1b*) |

**On the memory row.** The tool compares the largest peak of each set, which is −0.3%. Compared by the median of each
set's peaks instead, it is +2.9% (53.1 against 51.6 MiB). Both are far under 15%, and the spread within each set (51.2
to 53.9 MiB baseline) is as wide as the difference, so the honest reading is "no difference this measurement can see".

**On the churn row.** The worker's RSS is compared with `GOMEMLIMIT`. `GOMEMLIMIT` bounds only the memory Go's runtime
manages, and RSS includes more than that, so this comparison errs towards failing. The worker peaked at 14% of the
limit.

**Totals, every run.** Demo: exact in 3 of 3 versioned runs, sent equal to drained (90,264, 90,380 and 90,510), no
mismatch at all, no service folded. Churn: 504,579 requests sent and 504,579 drained; version rows summed to service
rows for every service; for all 40 services the versions plus `__other__` held exactly the requests and errors sent.
424,341 requests (84%) landed in `__other__`; 29,430 named versions kept nothing and 12,562 kept part of their count;
no named version held more than was sent. The worker logged the folding itself ("counted requests under a combined
version"), about 28,000 to 29,000 requests a minute. Its log does not say which cap folded them. The 13,440 version
rows are exactly 21 per service-minute (640 service-minutes), which is the drain cap's 20 named versions plus
`__other__`. By arithmetic, 2,800 names a minute is well past the 512 entries per tenant, partition and minute that the
tenant arrival cap allows. Whether one service also reached 64 names on one partition depends on how a service's
requests spread over the 12 partitions, which was not measured.

## Absolute figures, for sizing

*Demo data, development machine, not a server figure. Demo data repeats itself and compresses better than real
traffic, so real bytes per row will be higher.*

**Disk per million requests, demo traffic** (60 requests a minute per service), from the last versioned run's tables
after a final merge:

| Table | Bytes on disk per million requests | Bytes per row | Index memory |
|---|---|---|---|
| `service_stats_1m` | 314.1 KiB | 18.9 | 671 bytes for 7,710 rows |
| `service_versions_1m` (new) | 345.7 KiB | 19.1 | 491 bytes for 5,038 rows |
| `endpoint_stats_1m` | 4,801.6 KiB | 288.7 | 209 bytes for 7,710 rows |

Disk per request depends on traffic per service: a busier service packs more requests into each minute's row. So
these per-million figures hold for about 60 requests a minute per service, not in general. Bytes per row is the more
portable figure.

**Disk under churn.** The report prints churn's per-million figures as 183.2 KiB, 378.3 KiB and 5,775.9 KiB, but they
are diluted: the tables still held the demo runs' rows. Taking the difference before and after the churn run instead
(arithmetic on two readings, each after a final merge, so approximate): `service_versions_1m` grew by 204,544 bytes
and 13,440 rows for 504,579 requests, about 396 KiB per million and 15.2 bytes a row; `service_stats_1m` grew by
20,738 bytes, about 40 KiB per million. So under churn the version table costs about 10× the service table per request,
which is what 21 rows in place of 1 means. The design sets no disk bound for churn. **By arithmetic,** one service
churning like this for the table's whole 35-day retention holds about 50,400 minutes × 21 rows × 15.2 bytes ≈ 16 MB,
more if late spans or a second worker write a minute twice.

**Worker memory.**

| Traffic | Baseline peak RSS | With versions peak RSS | Runs |
|---|---|---|---|
| Demo, 3,000 requests a minute | 51.2 / 51.6 / 53.9 MiB | 52.8 / 53.1 / 53.7 MiB | 3 each |
| Churn, 33,600 requests a minute, 2,800 names a minute | 130.7 MiB | 151.4 MiB (+15.8%) | 1 each |

The worker's memory grows with request rate and names in flight, not with stored volume, so a "per million requests"
memory figure would mislead; these are given with their rates instead. Under churn the versions worker held 20.7 MiB
more RSS than the baseline. That is more than the design's 4.9 MB arithmetic for one hostile tenant's version map,
because RSS also holds Go's garbage-collection headroom and the extra rows on their way to ClickHouse; this run does
not separate them. Only one tenant churned. The design's arithmetic that about 100 hostile tenants would hold about
490 MB is still arithmetic, not measured.

**`sizing.md`** is not changed: the demo shows no growth in worker memory and about 10% more disk on a table that is
small beside `endpoint_stats_1m` (design, *Capacity and AI cost*).

## How often `side_by_side` is reached

*Demo data, development machine, not a server figure.*

A comparison shows two versions "side by side" only when each side has at least 20 requests over the minutes both
served (FR23's floor). Otherwise it falls back to a weaker reading.

**At the demo's traffic** (60 requests a minute, 5-minute rollouts): reached on 81 of 84 rollouts. The 3 that missed
are the same service, `svc-047`, in each run: its rollout started before the run did, so only 1 or 2 of its overlap
minutes fall inside the window (5 to 8 requests on the old side). The median smaller side was 136 to 150 requests.

**At small-team traffic, by arithmetic** (`release_measure.expected_side_by_side`, not measured). In an even rollout
over L minutes at R requests a minute, each side gets about R × L / 2 requests. The floor is 20, so a rollout must last
at least 40 / R minutes:

| Requests a minute to the service | Shortest rollout that reaches `side_by_side` |
|---|---|
| 1 | 40 minutes: in practice never |
| 5 | 8 minutes |
| 10 | 4 minutes |
| 60 | 1 minute |
| Any, with a one-minute swap (no overlap) | Never: there are no minutes both versions serve |

So a small team with a quiet service will mostly see the before-and-after reading, not side by side.

## A per-endpoint table, by arithmetic only

*Not measured. Arithmetic on demo data, development machine, not a server figure.*

D18 left a per-endpoint version table out, with an estimate owed here. Its rows would be `endpoint_stats_1m`'s rows ×
the version factor, which measured 1.096 with one rollout an hour (and would reach 21 under churn, if the same caps
applied per endpoint). Assuming its rows cost what `endpoint_stats_1m`'s do:

- Demo traffic: 4,801.6 KiB × 1.096 ≈ **5,263 KiB per million requests**, about 15× the 345.7 KiB the per-service
  version table costs.
- The demo has one endpoint per service, so `endpoint_stats_1m` there has exactly as many rows as `service_stats_1m`.
  A real service with E endpoints has about E times as many endpoint rows, and the per-endpoint table grows with it.

## What was not measured, and why

- **Alert evaluation start delay** (the gate's sixth row). Not measurable before Step 1b's sync job existed; measured
  on 2026-11-08 with it (*Addendum, Step 1b*), as the founder accepted on 2026-11-08.
- **Whether the whole server fits in 16 GB.** No test server is rented; this waits for the Oracle deployment after
  Phase 2 (foundation, *What A and B change*, and spec, founder decision 24's *Where "fits" is checked*).
- **The worker inside its 1.25 GB container.** It ran as a host process here, held only by `GOMEMLIMIT`.
- **Churn repeats.** The design asks for 30 minutes a run and at least 3 repeats. The demo had that. Churn had one
  15-minute pair. Its margin (151 MiB against 1,088) is wide and its totals check is a count, not a spread, but this is
  short of the design's wording.
- **Many hostile tenants at once.** One tenant churned.
- **Real customer traffic,** real version habits and a production mix of pipelines. These numbers are from a demo.

## What disappears if the gate fails

From design, *If it fails*, kept here because the plan asks the report to carry it. The recorded fallback is time
comparison with F6's readings. It costs: UR2; FR15 to FR19's seen path; FR26; FR47; FR48; AC22; coverage
`versions_only` and `deploys_and_versions`; the rollback with no CI; and the "no webhook needed" route, which buyers
expect as table stakes (spec, *Table stakes*). All that would remain is signed deploys, markers, time comparison, and
errors by version.

The gate passed on demo data, so none of this is triggered now.

## The report's output

*Demo data, development machine, not a server figure.* `just release-measure report`, 2026-11-07:

```
DEMO DATA, development machine. Not a server figure.

## churn: many versions on every partition, at the arrival caps: worker under GOMEMLIMIT, totals exact
baseline  peak RSS MiB over 1 runs: least 130.7, median 130.7, most 130.7
versions  peak RSS MiB over 1 runs: least 151.4, median 151.4, most 151.4
pass         Worker peak memory, under GOMEMLIMIT: 151.430 MiB (at most 1088 MiB)
pass         Totals exact: exact in 1 of 1 versioned runs; services with versions folded into __other__ (totals conserved, versions inexact), per run: 40
For information only, the demo's bounds, which the design does not set here:
info         Rows in steady traffic, versions over services (floor only): - (at most 1.5)
info         Rows with one rollout an hour on every service: 21.000 (at most 3)
info         Worker peak memory, increase over the baseline: 0.158 (at most 0.15)
info         Bytes on disk per request, versions over services: 2.065 (at most 2)
bytes on disk per million requests, service_stats_1m: 183.2 KiB
bytes on disk per million requests, service_versions_1m: 378.3 KiB
bytes on disk per million requests, endpoint_stats_1m: 5775.9 KiB

## many-services: one rollout an hour on every service: the design's four ratio rows
baseline  peak RSS MiB over 3 runs: least 51.2, median 51.6, most 53.9
versions  peak RSS MiB over 3 runs: least 52.8, median 53.1, most 53.7
pass         Rows in steady traffic, versions over services (floor only): 1.000 (at most 1.5)
pass         Rows with one rollout an hour on every service: 1.096 (at most 3)
pass         Worker peak memory, increase over the baseline: -0.003 (at most 0.15)
pass         Bytes on disk per request, versions over services: 1.101 (at most 2)
info         Totals exact: exact in 3 of 3 versioned runs; services with versions folded into __other__ (totals conserved, versions inexact), per run: 0, 0, 0
bytes on disk per million requests, service_stats_1m: 314.1 KiB
bytes on disk per million requests, service_versions_1m: 345.7 KiB
bytes on disk per million requests, endpoint_stats_1m: 4801.6 KiB
side_by_side reached on 81 of 84 rollouts

Gate, over the design rows measured here: pass
Not measured here: Alert evaluation start delay: needs Step 1b's sync job, which does not exist yet
```

The run results (eight JSON files) and the driver's log were kept in the session's scratch space, not committed; the
churn result alone is 7 MB of per-version mismatch rows.

## Addendum, Step 1b (2026-11-08)

**Demo data, development machine, not a server figure.** Both measurements ran on this machine's own stores
(`f28-pg`, `f28-ch`, `f28-kafka` in Docker, on non-default ports), with the receiver, the worker, beat and a
`scheduler-light`-shaped worker (8 slots, an uncapped floor of 4) run from source on branch `wt/f28`.

### The alert evaluation start delay (the gate's sixth row)

**Passed.** 51 projects (10 organizations of 5, and one more of its own) each had six hours of version rows; one
held 500 services with 2 versions over 360 minutes, 360,000 rows, so its first read was long. Each sample cleared every
project's sync state, so each job was a first run, queued all 51 `releases.observe_project` jobs at once, slow project
first, then queued one alert threshold sweep exactly as beat does and timed enqueue to claim, polling every 20 ms.
The baseline queued the sweep with no release job.

| | Samples | Median | Max |
|---|---|---|---|
| Baseline, no release jobs | 8 | 0.071 s | 0.844 s (the first sample, a cold worker) |
| 51 release jobs queued, one on a 360,000-row first read | 8 | 0.049 s | 0.054 s |

While loaded, at most 2 release jobs ran at once (the `releases` cap), with 9 to 51 waiting. The slow project's
first run took 4.1 to 5.3 seconds, reading 360,000 rows and creating 1,000 releases. The bound is 10 seconds of
baseline; the loaded delay sat at the poll's resolution. What keeps it there is the queue's cap of 2 inside the floor
that leaves 4 of 8 slots to probes and alert sweeps (`jobs/worker.py`), not the job being fast.

The 1,000 new releases in one run are above the design's 200 a run: all 1,000 were first counted in one minute, and
the sync takes a whole minute even past the cap, or one busy minute would stop it for good (`sync.walk`; F28
`status.md`, *Open decisions*).

### First settled span to visible release (AC22, T1.16)

**Passed: about 60 seconds, against 3 minutes.** `tools/release-demo send rolling` (120 requests a minute, `v1`
handing to `v2` from minute 3 over 6 minutes, 14 minutes) went through the real receiver, with no webhook. A
watcher read the settled minute and `releases` every 2 seconds.

| Version | First counted minute | Settled through it at | Release row at | From settled to visible |
|---|---|---|---|---|
| `v1` | 17:23 | 17:27:57 | 17:28:59 | 62 s |
| `v2` | 17:26 | 17:30:56 | 17:31:56 | 60 s |

The release appears on the release sweep after the minute settles; the sweep runs each minute. The settled minute
itself trailed the spans by about 4 to 5 minutes on this stack, which is detection's settling (F6) and is not
counted in AC22, which measures from the first settled span.

`send.py verify` then printed 708 requests and 2 errors on `v1`, 979 and 40 on `v2`, equal to what the receiver
accepted, with the version rows summing to `service_stats_1m`. The API's comparison service, read against the same
stores, gave `v1` as already running, and `v2` against `v1` in two blocks: side by side from 17:26 to 17:33, 399
requests and 14 errors on `v2` against 441 and 0 on `v1`, failure share higher; then since the rollout finished,
17:33 to 17:40, 580 and 26 against `v1`'s equal stretch before, 267 and 2, not clearly different. The two blocks'
`v2` counts add to the 979 sent.

**Not measured:** the 2-second first-numbers target (AC67) waits for Step 4.
