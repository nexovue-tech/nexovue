"""The ClickHouse reads behind the infrastructure screen.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**Why both metric tables.** The semantic conventions put host signals in two
instrument kinds — `system.cpu.utilization` is a Gauge, `system.memory.usage` an
UpDownCounter — and the worker writes those to `metrics_gauge` and `metrics_sum`
respectively. Which table a name lands in is a property of the exporter, not of
the question being asked, so the read covers both tables and lets the name
filter decide.

**Why the two branches repeat their WHERE clause.** Factoring it into a constant
would leave `FROM metrics_gauge` in one string literal and
`tenant_id = {tenant_id:String}` in another, and
`tests/test_tenant_chokepoint_structure.py` reads literals — it would see a
statement against a telemetry table with no tenant predicate and fail, correctly,
because from the source alone the two are no longer provably joined. The
repetition is what keeps the guarantee readable by a parser.

**Why the reads collapse to one row per series.** `metrics_gauge` and
`metrics_sum` are ordered by
`(tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp),
series_hash, timestamp)`, so a metric-name filter narrows well but a one-hour
window still holds every scrape. A host reporting every ten seconds is 360
points per series; the screen wants one. Collapsing in ClickHouse rather than in
Python is the difference between returning hundreds of rows and hundreds of
thousands.

Each row carries its *earliest* point as well as its latest, because four of
these metrics are monotonic counters whose only readable form is a rate, and a
rate needs two points.

**Partition pruning.** Both tables are partitioned on `received_at`, our clock,
while the caller asks about `timestamp`, the customer's. ClickHouse
cannot relate the two, so both bounds are present and the service widens the
requested window by `window.RECEIVE_CLOCK_SLACK` to get the `received_at` range.

**Node identity, and why it has three forms.** A host is
`resource_attrs.host.name` and a container is `resource_attrs.container.name`,
both OTLP resource attributes. ClickHouse's JSON type reads a dot as a path
separator, so the flat key an SDK sends is a two-level path here.

A resource carrying neither used to collapse into one node, and that was worse
than a bad label: CPU from one machine and memory from another were added into
one row, so the readings themselves were meaningless. Such a resource is now
identified by the *process that reported it* — `service_name`, promoted to a
column by the worker, and `resource_attrs.service.instance.id`. The cost is an
over-counted inventory: three instrumented services on one machine are three
rows. That is the lesser evil, because each row's numbers stay coherent, and
presentation can aggregate upward where it can never recover discarded detail.

**The reporter is blanked whenever the resource named itself**, in the inner
read rather than in Python, so the SQL grain and the node grain are the same
expression. Grouping more finely than a node and merging afterwards would sum
two collectors' readings of one named host into twice its memory.
"""

from __future__ import annotations

from typing import Final

# Every reading, from both instrument tables, narrowed to the metric names the
# infrastructure screen knows and to the caller's window.
_READINGS: Final = """
    SELECT
        toString(resource_attrs.host.name) AS host,
        toString(resource_attrs.container.name) AS container,
        (host != '' OR container != '') AS names_itself,
        if(names_itself, '', service_name) AS reporter,
        if(names_itself, '', toString(resource_attrs.service.instance.id)) AS reporter_instance,
        metric_name,
        metric_unit,
        toJSONString(attrs) AS attributes_json,
        timestamp,
        value
    FROM metrics_gauge
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND metric_name IN {metric_names:Array(String)}
      AND timestamp >= {from_time:DateTime64(3)}
      AND timestamp < {to_time:DateTime64(3)}
      AND received_at >= {received_from:DateTime64(3)}
      AND received_at < {received_to:DateTime64(3)}
    UNION ALL
    SELECT
        toString(resource_attrs.host.name) AS host,
        toString(resource_attrs.container.name) AS container,
        (host != '' OR container != '') AS names_itself,
        if(names_itself, '', service_name) AS reporter,
        if(names_itself, '', toString(resource_attrs.service.instance.id)) AS reporter_instance,
        metric_name,
        metric_unit,
        toJSONString(attrs) AS attributes_json,
        timestamp,
        value
    FROM metrics_sum
    WHERE tenant_id = {tenant_id:String}
      AND project_id = {project_id:String}
      AND metric_name IN {metric_names:Array(String)}
      AND timestamp >= {from_time:DateTime64(3)}
      AND timestamp < {to_time:DateTime64(3)}
      AND received_at >= {received_from:DateTime64(3)}
      AND received_at < {received_to:DateTime64(3)}"""

# One row per series: the node, the metric, the attribute set that distinguishes
# it, and the first and last point inside the window.
#
# The bound is called `max_series` rather than `limit` because over ClickHouse's
# native protocol a query parameter named `limit` collides with the server
# setting of the same name and the statement fails to parse. The API speaks
# HTTP, where it does not, but a name that works over only one transport is a
# trap for whoever ports this next.
# `points` counts distinct instants, not rows. A metric row is not unique —
# `metricwriting.Absorb` takes a duplicate over a lost point — and each statement
# here already groups by the whole series, so one instant is one reading. It is
# what `reporting_interval_seconds` divides by, and counting rows would report a
# host as sampling twice as often as it does. Measured at 7% over `count()`,
# against 22% for materialising the deduplicated rows in a subquery.
# The optional predicates, spliced on comment markers: ClickHouse's own
# parameter syntax is braces, so str.format cannot be used here. Both narrow the
# series read rather than the nodes returned, which also makes `truncated` less
# likely to fire on a filtered read than on an unfiltered one.
_KIND_PLACEHOLDER: Final = "/* kind */"
_HOST_PLACEHOLDER: Final = "/* host */"

# `kind` is not stored — it is what a node turns out to be once you know whether
# it named a container, a host, or neither. `NodeKey.kind` decides it the same
# way in Python, and the two must agree.
KIND_CLAUSES: Final = {
    "container": "WHERE container != ''",
    "host": "WHERE container = '' AND host != ''",
    "reporter": "WHERE container = '' AND host = ''",
}

# "This host's containers" — a container names the host it runs on, so one
# predicate answers both "this machine" and "what is on it".
HOST_CLAUSE: Final = "host = {host:String}"

NODE_READINGS_SQL: Final = f"""
SELECT
    host,
    container,
    reporter,
    reporter_instance,
    metric_name,
    metric_unit,
    attributes_json,
    argMax(value, timestamp) AS latest_value,
    argMin(value, timestamp) AS earliest_value,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos,
    toUnixTimestamp64Nano(min(timestamp)) AS earliest_nanos,
    uniqExact(timestamp) AS points
FROM ({_READINGS})
{_KIND_PLACEHOLDER}
{_HOST_PLACEHOLDER}
GROUP BY host, container, reporter, reporter_instance, metric_name, metric_unit, attributes_json
ORDER BY host ASC, container ASC, reporter ASC, reporter_instance ASC,
         metric_name ASC, attributes_json ASC
LIMIT {{max_series:UInt32}}
"""

# Each node's utilization over time — the sparkline in its row.
#
# Bucketed by integer division for the reason `synthetics/queries.py` gives: the width is
# a bound parameter, which `INTERVAL {n} SECOND` cannot take.
#
# Averaged within the bucket rather than taken at its edge. A host reporting every 15
# seconds writes four points to a minute, and `argMax` would show whichever arrived last
# rather than what the minute was like.
_BUCKET_NANOS: Final = (
    "intDiv(toUnixTimestamp(timestamp), {bucket_seconds:UInt32}) * {bucket_seconds:UInt32}"
    " * 1000000000"
)

NODE_TRENDS_SQL: Final = f"""
SELECT
    host,
    container,
    reporter,
    reporter_instance,
    metric_name,
    {_BUCKET_NANOS} AS bucket_nanos,
    avg(value) AS mean_value
FROM ({_READINGS})
GROUP BY host, container, reporter, reporter_instance, metric_name, bucket_nanos
ORDER BY host, container, reporter, reporter_instance, metric_name, bucket_nanos
LIMIT {{max_series:UInt32}}
"""

# What has arrived at all, which is what the screen's empty state needs: a
# project with no rows here has no collector sending host metrics, and that is a
# different problem from a host that has gone quiet.
SIGNALS_SEEN_SQL: Final = f"""
SELECT
    metric_name,
    any(metric_unit) AS metric_unit,
    uniqExact((host, container, reporter, reporter_instance, attributes_json, timestamp)) AS points,
    uniqExact(attributes_json) AS series,
    uniqExact((host, container, reporter, reporter_instance)) AS nodes,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos
FROM ({_READINGS})
GROUP BY metric_name
ORDER BY metric_name ASC
"""


def node_readings_sql(*, kind: str | None, host: str | None) -> str:
    """The node statement, narrowed to what the caller asked for.

    The two predicates compose into one `WHERE`, so whichever arrives first
    carries the keyword and the other is appended with `AND`.
    """
    clauses = [KIND_CLAUSES[kind]] if kind is not None else []
    if host is not None:
        clauses.append(f"AND {HOST_CLAUSE}" if clauses else f"WHERE {HOST_CLAUSE}")
    return NODE_READINGS_SQL.replace(_KIND_PLACEHOLDER, clauses[0] if clauses else "").replace(
        _HOST_PLACEHOLDER, clauses[1] if len(clauses) > 1 else ""
    )
