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
while the caller asks about `timestamp`, the customer's (ADR 0067). ClickHouse
cannot relate the two, so both bounds are present and the service widens the
requested window by `window.RECEIVE_CLOCK_SLACK` to get the `received_at` range.

**Node identity.** A host is `resource_attrs.host.name` and a container is
`resource_attrs.container.name`, both OTLP resource attributes. ClickHouse's
JSON type reads a dot as a path separator, so the flat key an SDK sends is a
two-level path here. A resource carrying neither collapses to the one unnamed
node, which is visible rather than silently dropped — an unnamed host is a
misconfigured collector, and the screen should say so.
"""

from __future__ import annotations

from typing import Final

# Every reading, from both instrument tables, narrowed to the metric names the
# infrastructure screen knows and to the caller's window.
_READINGS: Final = """
    SELECT
        toString(resource_attrs.host.name) AS host,
        toString(resource_attrs.container.name) AS container,
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
NODE_READINGS_SQL: Final = f"""
SELECT
    host,
    container,
    metric_name,
    metric_unit,
    attributes_json,
    argMax(value, timestamp) AS latest_value,
    argMin(value, timestamp) AS earliest_value,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos,
    toUnixTimestamp64Nano(min(timestamp)) AS earliest_nanos,
    count() AS points
FROM ({_READINGS})
GROUP BY host, container, metric_name, metric_unit, attributes_json
ORDER BY host ASC, container ASC, metric_name ASC, attributes_json ASC
LIMIT {{max_series:UInt32}}
"""

# What has arrived at all, which is what the screen's empty state needs: a
# project with no rows here has no collector sending host metrics, and that is a
# different problem from a host that has gone quiet.
SIGNALS_SEEN_SQL: Final = f"""
SELECT
    metric_name,
    any(metric_unit) AS metric_unit,
    count() AS points,
    uniqExact(attributes_json) AS series,
    uniqExact((host, container)) AS nodes,
    toUnixTimestamp64Nano(max(timestamp)) AS latest_nanos
FROM ({_READINGS})
GROUP BY metric_name
ORDER BY metric_name ASC
"""
