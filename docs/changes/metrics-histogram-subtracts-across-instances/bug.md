# Bug: the metrics screen's histogram reads subtract one instance's total from another's

## Classification

Bug — wrong numbers on the metrics screen's histogram and on a service's latency heatmap. Found
by F20's plan T8.1 (design section 8, *A separate suspected bug*); **not fixed there, by the
plan**, and recorded here.

## Reported behavior

When two instances of one service report a histogram with equal attributes (any service with
more than one replica, since an instance's identity is a resource attribute, not a data point
attribute), the histogram's count and buckets for a window, and each heatmap column, are the
difference between **two different instances'** running totals, not the rise of either. The
answer can be ten times too high or a third of the truth, depending on which instance reported
last.

## Reproduction

Run, 2026-11-12, against ClickHouse 26.3.20.7, through `metrics/service.read_histogram` and
`read_duration_heatmap` with a fixture written by
`apps/api/tests/test_metric_series_live.py` (`test_t8_1_*`): one service, two instances
(`service.instance.id` `a` and `b`), equal attributes, cumulative counts. Instance `a` rises
from 100 to 160 inside a 10-minute window and `b` from 1,000 to 1,030, so the true rise is 90.

| Read | Read | True |
|---|---|---|
| `HISTOGRAM_SQL`, the window's count | 930 | 90 |
| `HEATMAP_SQL`, the 10:00 column | 910 | 20 |
| `HEATMAP_SQL`, the 10:05 column | 30 | 90 |

Both cases are strict `xfail`s in that suite, so they pass while the fault stands and fail the
day it is fixed.

## Root cause

`series_hash` hashes the data point's attributes alone (`000003_metrics_sum.up.sql`, the same
column in `000005_metrics_histogram.up.sql`), so it does not tell two instances apart.
`HISTOGRAM_SQL` groups by `series_hash` and `explicit_bounds` and takes `any(service_name)`
(`metrics/queries.py`); `argMax(count, timestamp) - argMin(count, timestamp)` then spans both
instances. `HEATMAP_SQL` partitions its `lagInFrame` by `series_hash` within one service, so a
column's "previous" can be the other instance's state.

## Specification impact

None: the metrics screen's numbers should be each instance's rise, added. F20's `metric_series`
panel does not share this fault: it keys a series by service, host, container, instance and
`series_hash` (F20 design section 8, AC50).

## Regression test

The two `test_t8_1_*` cases, turned from strict `xfail` into plain tests when fixed.

## Fix plan and validation

Key a series by `(service_name, host, container, instance, series_hash)` in both statements, as
F20's `metric_series` statements do, with the paths as constants (`infrastructure/queries.py`).
Measure the cost against the 2.29 s `toJSONString` note in `metrics/queries.py` before choosing
whether to read the resource paths per row or to add a materialized instance column (a ClickHouse
migration). Not decided here.

## Status

Open. Recorded 2026-11-12 by F20 Step 8 (T8.1); not scheduled.
