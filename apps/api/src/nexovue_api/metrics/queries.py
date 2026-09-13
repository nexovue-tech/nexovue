"""The ClickHouse reads behind the metrics catalogue.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**Five tables, one question.** OTLP has five point shapes and each gets its own
table, because their columns genuinely differ — a histogram carries bucket
bounds a gauge has no use for. "What is my application reporting" is one
question across all five, so this is a `UNION ALL` of five identically shaped
projections rather than five reads a client would have to merge.

The tenant predicate is repeated in every branch rather than factored out. That
is deliberate: `tests/test_tenant_chokepoint_structure.py` reads string
literals, and a statement whose `FROM` and whose tenant binding live in
different constants is no longer provably scoped from the source alone.

**Shape is a fact about the point, not about the metric.** Nothing stops a
customer sending `queue.depth` as a gauge on Monday and a sum on Tuesday. The
catalogue reports every shape a name arrived in rather than picking one, because
picking one would hide a misconfiguration that is worth seeing.
"""

from __future__ import annotations

from typing import Final

# What each table's points are, in the OTLP vocabulary a customer's SDK uses.
SHAPE_GAUGE: Final = "gauge"
SHAPE_SUM: Final = "sum"
SHAPE_HISTOGRAM: Final = "histogram"
SHAPE_EXPONENTIAL_HISTOGRAM: Final = "exponential histogram"
SHAPE_SUMMARY: Final = "summary"

# Only a histogram has a reader today. The others list with their counts and say
# so, rather than being hidden — a metric a customer is paying to store should
# appear even when we cannot yet draw it.
READABLE_SHAPES: Final = frozenset({SHAPE_HISTOGRAM})

# The window every branch shares, bounded on both clocks. The customer supplies
# `timestamp` and cannot prune a partition with it; `received_at` is ours and is
# what the tables are partitioned by.
_WINDOW: Final = """
      AND timestamp >= {from_time:DateTime64(3)}
      AND timestamp < {to_time:DateTime64(3)}
      AND received_at >= {received_from:DateTime64(3)}
      AND received_at < {received_to:DateTime64(3)}"""


def _branch(table: str, shape: str) -> str:
    """One table's contribution to the catalogue.

    `uniqExact` over the series and the instant, never `count()`: a metric row
    is not unique — `metricwriting.Absorb` takes a duplicate over a lost point —
    and a redelivered batch would otherwise inflate every count on this screen.
    The same rule `infrastructure/queries.py` follows, for the same reason.

    The series is `series_hash`, not the attributes rendered as JSON. It is the
    column the schema provides for this and it is part of the sort key, where
    `toJSONString(attrs)` is computed per row: measured over six hours of the
    development project, the two answer the same and the JSON form takes 2.29
    seconds against 0.09. It hashes attributes alone and so cannot tell two
    *metrics* apart — which costs nothing here, because the catalogue groups by
    name before it counts.
    """
    return f"""
    SELECT
        metric_name,
        '{shape}' AS shape,
        metric_unit,
        series_hash,
        timestamp,
        toUnixTimestamp64Nano(timestamp) AS observed_nanos
    FROM {table}
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}{_WINDOW}"""


_EVERY_SHAPE: Final = "\n    UNION ALL".join(
    (
        _branch("metrics_gauge", SHAPE_GAUGE),
        _branch("metrics_sum", SHAPE_SUM),
        _branch("metrics_histogram", SHAPE_HISTOGRAM),
        _branch("metrics_exp_histogram", SHAPE_EXPONENTIAL_HISTOGRAM),
        _branch("metrics_summary", SHAPE_SUMMARY),
    )
)

# One row per metric name. A name that arrived in two shapes is one row naming
# both, because it is one metric with a configuration problem rather than two
# metrics.
CATALOGUE_SQL: Final = f"""
SELECT
    metric_name,
    arraySort(groupUniqArray(shape)) AS shapes,
    any(metric_unit) AS metric_unit,
    uniqExact(series_hash) AS series,
    uniqExact((series_hash, observed_nanos)) AS points,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos
FROM ({_EVERY_SHAPE}
)
GROUP BY metric_name
ORDER BY points DESC, metric_name
LIMIT {{limit:UInt32}}
"""


# A histogram's distribution over the window, one row per bucket layout.
#
# **Cumulative, which is what makes this more than a `sum()`.** Every histogram
# in the store carries `CUMULATIVE` temporality: each point is the running total
# since `start_timestamp`, so the same `count` and the same `bucket_counts`
# repeat on every export. Summing points across an hour of ten-second exports
# reports 360 times the traffic that happened. The window's own distribution is
# the difference between the last point and the first, per series — the rule
# `infrastructure/service._summed_delta` already follows for counters.
#
# Two things make that subtraction wrong, and both are handled as one condition:
# an exporter restart, which resets the counters and shows up as a falling
# `count`; and a series whose accumulation *began inside the window*, which shows
# up as a `start_timestamp` at or after the window opened. In both cases the
# latest point is itself the window's contribution, and subtracting anything from
# it would discard real observations.
#
# **Grouped by `explicit_bounds`, never merged across them.** Two histograms with
# different bucket layouts are not one distribution. This is not theoretical:
# `db.client.operation.duration` arrives from one service with bounds
# `[0, 5, 10, ...]` and from another with `[0.001, 0.005, 0.01, ...]`, and adding
# those counts would add milliseconds to seconds and call the result a latency.
# Each layout is returned whole, with the services that reported it, and the
# caller decides what to show.
_WINDOW_IS_THE_WHOLE_SERIES: Final = """
                argMax(start_timestamp, timestamp) >= {from_time:DateTime64(3)}
                OR argMax(count, timestamp) < argMin(count, timestamp)"""

HISTOGRAM_SQL: Final = f"""
SELECT
    bounds,
    arraySort(services) AS services,
    series,
    bucket_counts,
    total_count,
    total_sum,
    low,
    high,
    toUnixTimestamp64Nano(latest) AS latest_nanos
FROM (
    SELECT
        explicit_bounds AS bounds,
        groupUniqArray(service_name) AS services,
        uniqExact(series_hash) AS series,
        sumForEach(window_buckets) AS bucket_counts,
        sum(window_count) AS total_count,
        sum(window_sum) AS total_sum,
        min(low) AS low,
        max(high) AS high,
        max(latest) AS latest
    FROM (
        SELECT
            series_hash,
            explicit_bounds,
            any(service_name) AS service_name,
            max(timestamp) AS latest,
            min(min) AS low,
            max(max) AS high,
            {_WINDOW_IS_THE_WHOLE_SERIES} AS whole,
            if(
                whole,
                CAST(argMax(bucket_counts, timestamp), 'Array(Int64)'),
                CAST(
                    arrayMap(
                        (later, earlier) -> toInt64(later) - toInt64(earlier),
                        argMax(bucket_counts, timestamp),
                        argMin(bucket_counts, timestamp)
                    ),
                    'Array(Int64)'
                )
            ) AS window_buckets,
            if(
                whole,
                toInt64(argMax(count, timestamp)),
                toInt64(argMax(count, timestamp)) - toInt64(argMin(count, timestamp))
            ) AS window_count,
            if(
                whole,
                argMax(sum, timestamp),
                argMax(sum, timestamp) - argMin(sum, timestamp)
            ) AS window_sum
        FROM metrics_histogram
        WHERE tenant_id = {{tenant_id:String}}
          AND project_id = {{project_id:String}}
          AND metric_name = {{metric_name:String}}{_WINDOW}
          -- A point whose buckets do not match its own bounds is malformed;
          -- `sumForEach` would refuse the group it landed in and take every
          -- well-formed series with it.
          AND length(bucket_counts) = length(explicit_bounds) + 1
        GROUP BY series_hash, explicit_bounds
    )
    GROUP BY bounds
)
ORDER BY total_count DESC, length(bounds) DESC
"""

# The unit and description belong to the name rather than to a layout, and are
# read even when no point carried a bucket — which is how a name that exists but
# reported nothing in the window is told apart from one that does not exist.
HISTOGRAM_IDENTITY_SQL: Final = """
SELECT
    any(metric_unit) AS metric_unit,
    any(metric_description) AS metric_description
FROM metrics_histogram
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND metric_name = {metric_name:String}
"""
