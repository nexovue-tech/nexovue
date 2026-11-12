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

# Every shape the catalogue can report, which is what the shape filter may ask
# for. Spelled once here so the API's accepted values and the branches that
# produce them cannot drift apart.
ALL_SHAPES: Final = (
    SHAPE_GAUGE,
    SHAPE_SUM,
    SHAPE_HISTOGRAM,
    SHAPE_EXPONENTIAL_HISTOGRAM,
    SHAPE_SUMMARY,
)

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

# The optional predicates, spliced on comment markers: ClickHouse's own
# parameter syntax is braces, so str.format cannot be used here. The same shape
# `synthetics/queries.py` uses, for the same reason.
_NAME_MATCH_PLACEHOLDER: Final = "/* name match */"
_NARROWING_PLACEHOLDER: Final = "/* narrowing */"

# A typed fragment of the name, matched anywhere in it and case-insensitively: a
# reader who wants the queue metrics types "queue", not a prefix. In each
# branch's own WHERE rather than over the result, so the group-by never sees the
# rows that cannot match.
NAME_MATCH_CLAUSE: Final = "AND positionCaseInsensitive(metric_name, {name_match:String}) > 0"

# "Only names that arrived in this shape", asked of the grouped shapes rather
# than of the rows. Narrowing the rows instead would drop the other shapes the
# name arrived in, and a name reported as both a histogram and a summary would
# come back looking like a clean histogram — the conflict this screen exists to
# show, hidden by a filter.
SHAPE_CLAUSE: Final = "has(shapes, {shape:String})"

# "Only what this console can draw", which is `service._to_summary`'s rule and
# must stay it: every shape the name arrived in has a reader. The set is bound
# from `READABLE_SHAPES` rather than written out here, so the filter cannot say
# one thing while the `readable` flag beside it says another.
READABLE_ONLY_CLAUSE: Final = (
    "arrayAll(arrived -> has({readable_shapes:Array(String)}, arrived), shapes)"
)


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
      AND project_id = {{project_id:String}}
      {_NAME_MATCH_PLACEHOLDER}{_WINDOW}"""


_EVERY_BRANCH: Final = "\n    UNION ALL".join(
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
#
# **`uniqExact` rather than a `GROUP BY` that counts, and this was measured
# both ways.** The alternative deduplicates in a grouping that can spill to
# disk, which sounds like the safer shape for a read whose cost the customer
# sets. It is not: over the same nine million rows it took 2.0 GiB and 2.8
# seconds against 850 MiB and 1.3, because it materialises every distinct
# reading where the hash set stays compact. Twice the memory is a strange way
# to guard against running out of it.
CATALOGUE_SQL: Final = f"""
SELECT
    metric_name,
    arraySort(groupUniqArray(shape)) AS shapes,
    any(metric_unit) AS metric_unit,
    uniqExact(series_hash) AS series,
    uniqExact((series_hash, observed_nanos)) AS points,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos
FROM ({_EVERY_BRANCH}
)
GROUP BY metric_name
{_NARROWING_PLACEHOLDER}
ORDER BY points DESC, metric_name
LIMIT {{limit:UInt32}}
"""


def catalogue_sql(*, name_match: bool, shape: bool, readable_only: bool) -> str:
    """The catalogue statement, narrowed to what the caller asked for.

    Every predicate lives inside the statement. `LIMIT` cuts the catalogue at
    `service.MAX_METRICS`, so narrowing the rows that came back would answer
    "the gauges among the busiest five hundred" — and `truncated` beside it
    would still say the cut was about traffic.
    """
    grouped = [
        clause
        for clause, asked in ((SHAPE_CLAUSE, shape), (READABLE_ONLY_CLAUSE, readable_only))
        if asked
    ]
    return CATALOGUE_SQL.replace(
        _NAME_MATCH_PLACEHOLDER, NAME_MATCH_CLAUSE if name_match else ""
    ).replace(_NARROWING_PLACEHOLDER, f"HAVING {' AND '.join(grouped)}" if grouped else "")


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


# The duration histogram a service sends, under either spelling.
#
# OTel renamed this metric: `http.server.duration` is the older semantic
# convention and `http.server.request.duration` the current one, and both are in
# the wild because an SDK's version decides which it emits. The demo stack sends
# both — `frontend` uses the old name, `cart` and `shipping` the new one — so a
# reader that knew only one would show the panel to a third of the services that
# can have it.
DURATION_METRIC_NAMES: Final = (
    "http.server.request.duration",
    "http.server.duration",
)

# Bucketed by integer division rather than `INTERVAL`, for the reason
# `synthetics/queries.py` gives: the width is a bound parameter, and
# `INTERVAL {n} SECOND` cannot take one.
_BUCKET_NANOS: Final = (
    "intDiv(toUnixTimestamp(timestamp), {bucket_seconds:UInt32}) * {bucket_seconds:UInt32}"
    " * 1000000000"
)

# A latency heatmap: the distribution's shape over time, one column per bucket.
#
# **The per-bucket delta is what makes this a heatmap rather than a smear.**
# These points are cumulative, so plotting the stored state would draw every
# column as the total since the process started — monotonically growing, and
# identical in shape from one column to the next. A column's own contribution is
# its state minus the previous column's, per series, which `lagInFrame` reads
# without a self-join.
#
# **One bucket is read before the window and then discarded.** Without it the
# first column has no predecessor, so its delta is the whole history since
# start-up: measured on the development store the first column read 24,845
# against 500 to 1,300 for every column after it, which on a heatmap is one black
# stripe and fourteen empty ones. `{from_nanos:Int64}` drops it after the
# window function has used it.
#
# A falling total is an exporter restart, and the state after it is the
# contribution — the same rule `HISTOGRAM_SQL` applies across the whole window.
HEATMAP_SQL: Final = f"""
SELECT
    metric_name,
    metric_unit,
    bounds,
    bucket_nanos,
    counts,
    total
FROM (
    SELECT
        metric_name,
        any(metric_unit) AS metric_unit,
        explicit_bounds AS bounds,
        bucket_nanos,
        sumForEach(delta) AS counts,
        arraySum(sumForEach(delta)) AS total
    FROM (
        SELECT
            metric_name,
            metric_unit,
            explicit_bounds,
            bucket_nanos,
            if(
                length(previous) = 0 OR arraySum(state) < arraySum(previous),
                state,
                arrayMap((later, earlier) -> later - earlier, state, previous)
            ) AS delta
        FROM (
            SELECT
                metric_name,
                any(metric_unit) AS metric_unit,
                explicit_bounds,
                series_hash,
                {_BUCKET_NANOS} AS bucket_nanos,
                CAST(argMax(bucket_counts, timestamp), 'Array(Int64)') AS state,
                lagInFrame(CAST(argMax(bucket_counts, timestamp), 'Array(Int64)')) OVER (
                    PARTITION BY metric_name, explicit_bounds, series_hash
                    ORDER BY bucket_nanos
                ) AS previous
            FROM metrics_histogram
            WHERE tenant_id = {{tenant_id:String}}
              AND project_id = {{project_id:String}}
              AND service_name = {{service_name:String}}
              AND metric_name IN {{metric_names:Array(String)}}
              AND timestamp >= {{read_from:DateTime64(3)}}
              AND timestamp < {{to_time:DateTime64(3)}}
              AND received_at >= {{received_from:DateTime64(3)}}
              AND received_at < {{received_to:DateTime64(3)}}
              AND length(bucket_counts) = length(explicit_bounds) + 1
            GROUP BY metric_name, explicit_bounds, series_hash, bucket_nanos
        )
    )
    GROUP BY metric_name, bounds, bucket_nanos
)
WHERE bucket_nanos >= {{from_nanos:Int64}}
ORDER BY metric_name, bucket_nanos
"""


# ------------------------------------------------------------ metric series (F20, Step 8)
#
# A dashboard's `metric_series` panel: one gauge or sum over time, one chosen series or all
# of them combined into one line (F20 design section 8). Every statement here binds tenant,
# project and metric, and the dashboard structure test holds each `SELECT` to the project
# (AC85).
#
# **One real series is `(service_name, host, container, instance, series_hash)`.**
# `series_hash` hashes the data point's attributes alone, so two instances of one service
# reporting equal attributes share it, and must still be two series (AC50): their running
# totals are separate counters, and subtracting one from the other is the fault
# `HISTOGRAM_SQL` has (docs/changes/metrics-histogram-subtracts-across-instances). The
# resource paths are constants, read as `infrastructure/queries.py` reads them.
#
# **A row is not unique** (a redelivered batch is kept), so every statement first takes one
# reading per series and instant, and never counts or adds stored rows.

# The optional narrowing, spliced on comment markers like the catalogue's: the service
# (absent for "all services") and the chosen series. Both are bound, never spliced.
SERVICE_PLACEHOLDER: Final = "/* service */"
SERIES_PLACEHOLDER: Final = "/* series */"
SERVICE_CLAUSE: Final = "AND service_name = {service_name:String}"
SERIES_CLAUSE: Final = "AND series_hash = {series_hash:UInt64}"

# How the series of one bucket combine into the line's one value (spec, `aggregation`).
# Bound as a value and chosen inside the statement, so no SQL is built from it.
AGGREGATIONS: Final = ("mean", "min", "max", "sum")
_COMBINED: Final = """multiIf(
        {aggregation:String} = 'min', min(reading),
        {aggregation:String} = 'max', max(reading),
        {aggregation:String} = 'sum', sum(reading),
        avg(reading)
    )"""

# A gauge: the mean of each series' readings in each bucket, then the chosen aggregation
# across series. The series count and the units are the window's, over every bucket.
METRIC_GAUGE_SERIES_SQL: Final = f"""
SELECT
    bucket_nanos,
    {_COMBINED} AS value,
    any(series_total) AS series,
    any(unit_list) AS units,
    any(attributes_json) AS attributes_json
FROM (
    SELECT
        {_BUCKET_NANOS} AS bucket_nanos,
        avg(value) AS reading,
        uniqExact((service_name, host, container, instance, series_hash)) OVER () AS series_total,
        groupUniqArrayArray(groupUniqArray(metric_unit)) OVER () AS unit_list,
        toJSONString(any(attrs)) AS attributes_json
    FROM (
        SELECT
            service_name,
            toString(resource_attrs.host.name) AS host,
            toString(resource_attrs.container.name) AS container,
            toString(resource_attrs.service.instance.id) AS instance,
            series_hash,
            timestamp,
            any(value) AS value,
            any(metric_unit) AS metric_unit,
            any(attrs) AS attrs
        FROM metrics_gauge
        WHERE tenant_id = {{tenant_id:String}}
          AND project_id = {{project_id:String}}
          AND metric_name = {{metric_name:String}}
          {SERVICE_PLACEHOLDER} {SERIES_PLACEHOLDER}{_WINDOW}
        GROUP BY service_name, host, container, instance, series_hash, timestamp
    )
    GROUP BY service_name, host, container, instance, series_hash, bucket_nanos
)
GROUP BY bucket_nanos
ORDER BY bucket_nanos
"""

# A sum, by each series' own temporality, read per row (design section 8):
#
# - **Cumulative and monotonic, a counter:** a bucket's contribution is its rise. Each
#   series' last reading in the bucket less the last in the bucket before it, which
#   `lagInFrame` reads; one bucket is read before the window for that and dropped after,
#   as `HEATMAP_SQL` does. **A falling value is a restart, and the value after it is the
#   contribution**: the counter began again from zero. A fall inside one bucket (its last
#   reading below its highest) counts the rise up to the highest and then the value after
#   the restart, so the common restart, to near zero, is counted exactly.
# - A series with no bucket before it began inside what was read (its start time is at or
#   after the read's start), so its whole value is its rise; otherwise only what it rose
#   within its first bucket is known.
# - **Delta:** each reading is already the bucket's share, so they are added.
# - **Anything else** (cumulative but not monotonic, or unspecified): read as a gauge, the
#   mean in the bucket.
METRIC_SUM_SERIES_SQL: Final = f"""
SELECT
    bucket_nanos,
    {_COMBINED} AS value,
    any(series_total) AS series,
    any(unit_list) AS units,
    any(attributes_json) AS attributes_json
FROM (
    SELECT
        bucket_nanos,
        contribution AS reading,
        uniqExact((service_name, host, container, instance, series_hash)) OVER () AS series_total,
        groupUniqArrayArray(units) OVER () AS unit_list,
        attributes_json
    FROM (
        SELECT
            service_name,
            host,
            container,
            instance,
            series_hash,
            bucket_nanos,
            units,
            attributes_json,
            lagInFrame(toNullable(last)) OVER (
                PARTITION BY service_name, host, container, instance, series_hash
                ORDER BY bucket_nanos
            ) AS previous,
            if(
                previous IS NULL,
                if(began >= {{read_from:DateTime64(3)}}, 0, first),
                assumeNotNull(previous)
            ) AS baseline,
            multiIf(
                temporality = 'DELTA', total,
                temporality = 'CUMULATIVE' AND monotonic,
                    if(
                        last < peak,
                        greatest(peak - baseline, 0) + last,
                        if(last < baseline, last, last - baseline)
                    ),
                mean
            ) AS contribution
        FROM (
            SELECT
                service_name,
                host,
                container,
                instance,
                series_hash,
                {_BUCKET_NANOS} AS bucket_nanos,
                argMax(value, timestamp) AS last,
                argMin(value, timestamp) AS first,
                max(value) AS peak,
                sum(value) AS total,
                avg(value) AS mean,
                argMax(started, timestamp) AS began,
                any(temporality) AS temporality,
                any(monotonic) AS monotonic,
                groupUniqArray(metric_unit) AS units,
                toJSONString(any(attrs)) AS attributes_json
            FROM (
                SELECT
                    service_name,
                    toString(resource_attrs.host.name) AS host,
                    toString(resource_attrs.container.name) AS container,
                    toString(resource_attrs.service.instance.id) AS instance,
                    series_hash,
                    timestamp,
                    any(value) AS value,
                    any(start_timestamp) AS started,
                    any(aggregation_temporality) AS temporality,
                    any(is_monotonic) AS monotonic,
                    any(metric_unit) AS metric_unit,
                    any(attrs) AS attrs
                FROM metrics_sum
                WHERE tenant_id = {{tenant_id:String}}
                  AND project_id = {{project_id:String}}
                  AND metric_name = {{metric_name:String}}
                  {SERVICE_PLACEHOLDER} {SERIES_PLACEHOLDER}
                  AND timestamp >= {{read_from:DateTime64(3)}}
                  AND timestamp < {{to_time:DateTime64(3)}}
                  AND received_at >= {{received_from:DateTime64(3)}}
                  AND received_at < {{received_to:DateTime64(3)}}
                GROUP BY service_name, host, container, instance, series_hash, timestamp
            )
            GROUP BY service_name, host, container, instance, series_hash, bucket_nanos
        )
    )
    WHERE bucket_nanos >= {{from_nanos:Int64}}
)
GROUP BY bucket_nanos
ORDER BY bucket_nanos
"""

# "Does this metric still report, under any attributes?" — the one further read a chosen
# series that came back empty earns, so it reads `series_changed` rather than "has not
# reported" (FR16, AC115). One row is enough to answer.
METRIC_GAUGE_REPORTS_SQL: Final = f"""
SELECT series_hash
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = {{metric_name:String}}
  {SERVICE_PLACEHOLDER}{_WINDOW}
LIMIT 1
"""

METRIC_SUM_REPORTS_SQL: Final = f"""
SELECT series_hash
FROM metrics_sum
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = {{metric_name:String}}
  {SERVICE_PLACEHOLDER}{_WINDOW}
LIMIT 1
"""

# One service's attribute combinations of a metric, for the builder to choose one
# (`GET /metrics/{metric_name}/series`, AC83, AC121). The attributes are rendered once per
# combination, never per row (the 2.29 s note on `_branch`). Busiest first.
_SERIES_LIST_COLUMNS: Final = """
    toString(series_hash) AS series_hash,
    toJSONString(any(attrs)) AS attributes_json,
    uniqExact(timestamp) AS points,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos"""

METRIC_GAUGE_SERIES_LIST_SQL: Final = f"""
SELECT{_SERIES_LIST_COLUMNS}
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = {{service_name:String}}
  AND metric_name = {{metric_name:String}}{_WINDOW}
GROUP BY series_hash
ORDER BY points DESC, series_hash
LIMIT {{max_combinations:UInt32}}
"""

METRIC_SUM_SERIES_LIST_SQL: Final = f"""
SELECT{_SERIES_LIST_COLUMNS}
FROM metrics_sum
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = {{service_name:String}}
  AND metric_name = {{metric_name:String}}{_WINDOW}
GROUP BY series_hash
ORDER BY points DESC, series_hash
LIMIT {{max_combinations:UInt32}}
"""


def narrowed(sql: str, *, service: bool, series: bool = False) -> str:
    """A metric series statement narrowed to one service and one chosen series, or not.

    The caller binds `service_name` and `series_hash` exactly when it asks for them here,
    so a predicate and its binding cannot come apart.
    """
    return sql.replace(SERVICE_PLACEHOLDER, SERVICE_CLAUSE if service else "").replace(
        SERIES_PLACEHOLDER, SERIES_CLAUSE if series else ""
    )
