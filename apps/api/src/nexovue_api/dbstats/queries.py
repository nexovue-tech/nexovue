"""The ClickHouse reads behind the database-statistics endpoints.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**The metric names come from the writer.** They are
`nexovue_scheduler.pollers.envelope.METRIC_OPERATION_DURATION` and
`METRIC_CONNECTION_COUNT`, and the attribute keys are that module's `ATTR_*`
constants. Restating them here rather than importing is deliberate — the API
does not depend on the scheduler package — but they are a contract, and a change
on either side must change both.

**There is no statement text, and there never will be.** The poller sends
`db.query.hash` (`pg_stat_statements.queryid`) and not `query`, because a
statement built by string concatenation carries its parameters — customer data —
into that column. A slow statement is therefore identified by its hash plus the
database it ran in, and a person matches it back by querying their own
`pg_stat_statements`.

The table is partitioned on `received_at`, our own receive clock, not on the
`timestamp` a caller asks about, so both are bounded.
"""

from __future__ import annotations

from typing import Final

METRIC_OPERATION_DURATION: Final = "db.client.operation.duration"
METRIC_CONNECTION_COUNT: Final = "db.client.connection.count"

# JSON paths. ClickHouse's JSON type reads a dot as a path separator, so the
# OTLP key `db.query.hash` is stored nested; the subcolumn is `Dynamic`, so
# every read of one is cast before it is compared or grouped.
QUERY_HASH: Final = "toString(attrs.db.query.hash)"
DB_NAMESPACE: Final = "toString(attrs.db.namespace)"
DB_SYSTEM: Final = "toString(attrs.db.system.name)"
CONNECTION_STATE: Final = "toString(attrs.db.client.connection.state)"
CALLS: Final = "toInt64OrZero(toString(attrs.db.client.operation.calls))"
SERVER_ADDRESS: Final = "toString(resource_attrs.server.address)"
SERVER_PORT: Final = "toInt32OrZero(toString(resource_attrs.server.port))"

_WINDOW_PREDICATE: Final = """
  AND timestamp >= {from_time:DateTime64(3)}
  AND timestamp < {to_time:DateTime64(3)}
  AND received_at >= {received_from:DateTime64(3)}
  AND received_at < {received_to:DateTime64(3)}
"""

# The slowest normalized statements, one row per statement per database server.
#
# `calls` is cumulative since the last `pg_stat_statements_reset()`, so the
# latest reading is a running total and the spread across the window is what
# happened during it. Both are reported: the total says how hot the statement is
# overall, the spread says whether it was hot just now.
# `readings` counts distinct instants rather than rows, for the reason
# `infrastructure/queries.py` gives at `NODE_READINGS_SQL`: a metric row is not
# unique, and both statements below already group by the whole series, so one
# instant is one poll.
# The optional predicates, spliced on comment markers: ClickHouse's own
# parameter syntax is braces, so str.format cannot be used here.
_SERVER_PLACEHOLDER: Final = "/* server */"
_MIN_MEAN_PLACEHOLDER: Final = "/* min mean */"

# "Only this server". The address is a resource attribute, so this narrows the
# rows read rather than the statements returned.
SERVER_CLAUSE: Final = f"AND {SERVER_ADDRESS} = {{server:String}}"

# "Slower than N", tested against the worst reading rather than the latest,
# because that is what the list is ranked by: a statement that spiked to two
# seconds and has since settled is the one worth finding.
MIN_MEAN_CLAUSE: Final = "HAVING worst_mean_seconds >= {min_mean_seconds:Float64}"

SLOW_STATEMENTS_SQL: Final = f"""
SELECT
    {QUERY_HASH} AS query_hash,
    {DB_NAMESPACE} AS namespace,
    {SERVER_ADDRESS} AS server_address,
    {SERVER_PORT} AS server_port,
    argMax({DB_SYSTEM}, timestamp) AS db_system,
    argMax(value, timestamp) AS latest_mean_seconds,
    max(value) AS worst_mean_seconds,
    argMax({CALLS}, timestamp) AS calls_total,
    max({CALLS}) - min({CALLS}) AS calls_in_window,
    uniqExact(timestamp) AS readings,
    max(toUnixTimestamp64Nano(timestamp)) AS latest_nanos
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = '{METRIC_OPERATION_DURATION}'
  {_SERVER_PLACEHOLDER}
{_WINDOW_PREDICATE}
GROUP BY query_hash, namespace, server_address, server_port
{_MIN_MEAN_PLACEHOLDER}
ORDER BY worst_mean_seconds DESC, query_hash
LIMIT {{limit:UInt32}}
"""


def slow_statements_sql(*, server: bool, min_mean: bool) -> str:
    """The statement list, narrowed to what the caller asked for.

    Inside the statement, never over its result: `LIMIT` caps the list at five
    hundred, so filtering afterwards would answer "the slow ones among the five
    hundred worst" and have no way to say so.
    """
    return SLOW_STATEMENTS_SQL.replace(
        _SERVER_PLACEHOLDER, SERVER_CLAUSE if server else ""
    ).replace(_MIN_MEAN_PLACEHOLDER, MIN_MEAN_CLAUSE if min_mean else "")


# Each statement's cost over time — the sparkline in its row.
#
# The mean duration within the bucket, not the latest: a statement sampled several times
# a minute would otherwise show whichever reading arrived last rather than the minute.
_BUCKET_NANOS: Final = (
    "intDiv(toUnixTimestamp(timestamp), {bucket_seconds:UInt32}) * {bucket_seconds:UInt32}"
    " * 1000000000"
)

STATEMENT_TRENDS_SQL: Final = f"""
SELECT
    {QUERY_HASH} AS query_hash,
    {SERVER_ADDRESS} AS server_address,
    {SERVER_PORT} AS server_port,
    {_BUCKET_NANOS} AS bucket_nanos,
    avg(value) AS mean_seconds
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = '{METRIC_OPERATION_DURATION}'
{_WINDOW_PREDICATE}
GROUP BY query_hash, server_address, server_port, bucket_nanos
ORDER BY query_hash, server_address, server_port, bucket_nanos
LIMIT {{limit:UInt32}}
"""

# Backends by state: how close a database is to refusing connections.
# The latest reading is the level right now; the peak is the one that would
# have exhausted `max_connections` if it had gone a little further.
CONNECTION_HEALTH_SQL: Final = f"""
SELECT
    {CONNECTION_STATE} AS state,
    {SERVER_ADDRESS} AS server_address,
    {SERVER_PORT} AS server_port,
    argMax(value, timestamp) AS latest_backends,
    max(value) AS peak_backends,
    uniqExact(timestamp) AS readings,
    max(toUnixTimestamp64Nano(timestamp)) AS latest_nanos
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = '{METRIC_CONNECTION_COUNT}'
{_WINDOW_PREDICATE}
GROUP BY state, server_address, server_port
ORDER BY latest_backends DESC, state
LIMIT {{limit:UInt32}}
"""
