"""The ClickHouse reads behind the synthetic check endpoints.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**What makes these cheap.** `metrics_gauge` is ordered by
`(tenant_id, project_id, service_name, metric_name, toStartOfHour(timestamp),
series_hash, timestamp)`, so naming the metrics explicitly is what narrows the
scan. A `startsWith(metric_name, 'synthetic')` reads the same rows in the end
but cannot use the sort key to get there, which is why the metric names are
listed rather than matched by prefix.

The table is partitioned on `received_at`, our own receive clock, not on the
customer `timestamp` a caller asks questions about. A bound on
`timestamp` therefore prunes no partitions, and every statement below carries a
`received_at` range as well — widened by `window.RECEIVE_CLOCK_SLACK`.

**Where the identifiers live.** ClickHouse's JSON type reads a dot as a path
separator, so the OTLP key `synthetic.check.id` is stored nested and is read as
`resource_attrs.synthetic.check.id`. The subcolumn is `Dynamic`, so every read
of one is cast before it is compared or grouped.
"""

from __future__ import annotations

from typing import Final

# The four metrics the prober publishes. Named here once; the service and the
# tests import these rather than re-typing a string that must match the writer.
METRIC_STATUS: Final = "synthetic.check.status"
METRIC_DURATION: Final = "synthetic.check.duration"
METRIC_PHASE_DURATION: Final = "synthetic.check.phase.duration"
METRIC_TLS_REMAINING: Final = "synthetic.tls.certificate.remaining"

# JSON paths, cast out of `Dynamic` at the point of use.
CHECK_ID: Final = "toString(resource_attrs.synthetic.check.id)"
CHECK_TYPE: Final = "toString(resource_attrs.synthetic.check.type)"
PHASE: Final = "toString(attrs.synthetic.check.step)"
HTTP_STATUS: Final = "toInt32OrZero(toString(attrs.http.response.status_code))"
TLS_ISSUER: Final = "toString(attrs.tls.server.issuer)"
SERVER_ADDRESS: Final = "toString(attrs.server.address)"
OUTCOME: Final = "toString(attrs.synthetic.check.outcome)"
FINAL: Final = "toString(attrs.synthetic.check.final)"
MAINTENANCE: Final = "toString(attrs.synthetic.check.maintenance)"
RUN_ID: Final = "toString(attrs.synthetic.check.run.id)"
ATTEMPT: Final = "toInt32OrZero(toString(attrs.synthetic.check.attempt))"
LOCATION: Final = "toString(attrs.synthetic.check.location)"

# Outcomes that say nothing about the customer's service: the author's mistake,
# or ours (BR7). Held out of uptime, and still listed as runs.
UNMEASURED_OUTCOMES: Final = ("misconfigured", "prober_error")

# **A run counts toward uptime only when it is the confirmed one.** An attempt a
# retry overruled is visible in the run list and nowhere else (FR25), and a run
# taken during a maintenance window changes nothing (AC23). Readings published
# before these attributes existed carry none of them and count, as they did.
_COUNTED: Final = (
    "(final_flag != 'false' AND maintenance_flag != 'true' "
    f"AND outcome NOT IN ({', '.join(repr(outcome) for outcome in UNMEASURED_OUTCOMES)}))"
)

# The per-reading attributes every statement below carries up from the inner
# collapse. Taken with `any()`: they are part of `attrs`, so part of the series.
_READING_ATTRIBUTES: Final = f"""
        any({OUTCOME}) AS outcome,
        any({FINAL}) AS final_flag,
        any({MAINTENANCE}) AS maintenance_flag"""

# `synthetic.check.status` is 1 for up and 0 for down.
UP_VALUE: Final = 1

# The bucket a phase point falls in. Integer division rather than
# `INTERVAL {n} SECOND`, because the width is a bound parameter and the
# arithmetic form takes one where the interval syntax does not.
_BUCKET_NANOS: Final = (
    "toUnixTimestamp64Nano(toDateTime64("
    "intDiv(toUnixTimestamp(timestamp), {bucket_seconds:UInt32}) * {bucket_seconds:UInt32}, 3))"
)

# The window every statement shares. Split out so the two clocks are bounded
# the same way everywhere, rather than remembered four times.
_WINDOW_PREDICATE: Final = """
  AND timestamp >= {from_time:DateTime64(3)}
  AND timestamp < {to_time:DateTime64(3)}
  AND received_at >= {received_from:DateTime64(3)}
  AND received_at < {received_to:DateTime64(3)}
"""

# **One stored row per reading.** A metric row is not unique: `metricwriting.Absorb`
# takes a duplicate over a lost point — no data loss is a phase-1 invariant, no
# duplication is not — and leaves the reader to "group by series and timestamp
# first". Every statement below collapses to this key before it counts, averages
# or sums, because all four read rows a redelivery can double.
#
# The key names more than the series because `series_hash` is
# `cityHash64(toJSONString(attrs))` and that is narrower than it looks: it tells
# two phases apart, but not two checks (the check id lives in `resource_attrs`)
# and not two metrics whose attributes match (a duration point carries
# `attrs = {{}}`). `any()` over the group is exact rather than arbitrary — every
# column it takes is a property of the series, so its candidates are equal.
_ONE_ROW_PER_READING: Final = "GROUP BY check_id, metric_name, series_hash, timestamp"

# The optional predicates, spliced on comment markers: ClickHouse's own
# parameter syntax is braces, so str.format cannot be used here.
_CHECK_TYPE_PLACEHOLDER: Final = "/* check type */"
_DOWN_ONLY_PLACEHOLDER: Final = "/* down only */"

# "Only DNS checks". The type is a resource attribute, so this narrows the rows
# read rather than the summaries returned.
CHECK_TYPE_CLAUSE: Final = f"AND {CHECK_TYPE} = {{check_type:String}}"

# "Only what's down", and `runs > 0` is load-bearing: `argMaxIf` over no matching
# row returns zero, so a check with a certificate reading and no probe would
# otherwise be reported as down. `_to_summary` says the same thing about `up`.
DOWN_ONLY_CLAUSE: Final = "HAVING runs > 0 AND latest_up != 1"


# One row per check. Status, duration and certificate are read in a single pass
# with conditional aggregates rather than in three queries joined afterwards.
#
# `runs` counts status points, not rows: one probe writes one status point, one
# duration point, five phase points and a certificate point, so counting rows
# would report a check that ran once as having run eight times.
CHECK_SUMMARIES_SQL: Final = f"""
SELECT
    check_id,
    argMax(probe_type, timestamp) AS check_type,
    countIf(metric_name = '{METRIC_STATUS}' AND {_COUNTED}) AS runs,
    countIf(metric_name = '{METRIC_STATUS}' AND {_COUNTED} AND value = {UP_VALUE})
        AS successful_runs,
    argMaxIf(value, timestamp, metric_name = '{METRIC_STATUS}' AND {_COUNTED}) AS latest_up,
    argMaxIf(http_status, timestamp, metric_name = '{METRIC_STATUS}' AND {_COUNTED})
        AS latest_status_code,
    argMaxIf(outcome, timestamp, metric_name = '{METRIC_STATUS}' AND final_flag != 'false')
        AS latest_outcome,
    maxIf(toUnixTimestamp64Nano(timestamp), metric_name = '{METRIC_STATUS}') AS latest_run_nanos,
    countIf(metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS duration_points,
    avgIf(value, metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS mean_seconds,
    quantileIf(0.95)(value, metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS p95_seconds,
    maxIf(value, metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS max_seconds,
    countIf(metric_name = '{METRIC_TLS_REMAINING}') AS tls_readings,
    argMaxIf(value, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_days_remaining,
    argMaxIf(issuer, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_issuer,
    argMaxIf(server_address, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_server
FROM (
    SELECT
        timestamp,
        {CHECK_ID} AS check_id,
        metric_name,
        any(value) AS value,
        any({CHECK_TYPE}) AS probe_type,
        any({HTTP_STATUS}) AS http_status,
        any({TLS_ISSUER}) AS issuer,
        any({SERVER_ADDRESS}) AS server_address,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}', '{METRIC_TLS_REMAINING}')
      {_CHECK_TYPE_PLACEHOLDER}
    {_WINDOW_PREDICATE}
    {_ONE_ROW_PER_READING}
)
GROUP BY check_id
{_DOWN_ONLY_PLACEHOLDER}
ORDER BY check_id
LIMIT {{limit:UInt32}}
"""


def check_summaries_sql(*, check_type: bool, down_only: bool) -> str:
    """The summary statement, narrowed to what the caller asked for.

    Both predicates live inside the statement. `LIMIT` caps the list, so asking
    "what is down" of the rows that came back would answer "the down ones among
    the first hundred checks" with nothing able to say so.
    """
    return CHECK_SUMMARIES_SQL.replace(
        _CHECK_TYPE_PLACEHOLDER, CHECK_TYPE_CLAUSE if check_type else ""
    ).replace(_DOWN_ONLY_PLACEHOLDER, DOWN_ONLY_CLAUSE if down_only else "")


# Each check's history, bucket by bucket — the sparkline in its row.
#
# The duration is `avgIf` over the bucket, and **a bucket may hold no duration at all**:
# no run in it succeeded, or the runs in it were never timed — a heartbeat is judged
# every period and probed never. `duration_points` counts what the average was taken
# over, because `avg` over no rows returns NaN rather than nothing, and the service turns
# that count into a gap rather than a zero: a check with no response time drawn at zero
# reads as the site getting instantly fast.
CHECK_TRENDS_SQL: Final = f"""
SELECT
    check_id,
    {_BUCKET_NANOS} AS bucket_nanos,
    countIf(metric_name = '{METRIC_STATUS}' AND {_COUNTED}) AS runs,
    countIf(metric_name = '{METRIC_STATUS}' AND {_COUNTED} AND value = {UP_VALUE})
        AS successful_runs,
    countIf(metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS duration_points,
    avgIf(value, metric_name = '{METRIC_DURATION}' AND {_COUNTED}) AS mean_seconds
FROM (
    SELECT
        timestamp,
        {CHECK_ID} AS check_id,
        metric_name,
        any(value) AS value,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}')
    {_WINDOW_PREDICATE}
    {_ONE_ROW_PER_READING}
)
GROUP BY check_id, bucket_nanos
ORDER BY check_id, bucket_nanos
LIMIT {{limit:UInt32}}
"""

# The phase breakdown: which part of the request was slow, over time.
# One row per bucket per phase, which the service pivots into one point per
# bucket carrying every phase — the shape a stacked chart plots without
# reshaping it first.
PHASE_BREAKDOWN_SQL: Final = f"""
SELECT
    {_BUCKET_NANOS} AS bucket_nanos,
    phase,
    avg(value) AS mean_seconds,
    count() AS samples
FROM (
    SELECT
        timestamp,
        {CHECK_ID} AS check_id,
        metric_name,
        {PHASE} AS phase,
        any(value) AS value
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name = '{METRIC_PHASE_DURATION}'
      AND {CHECK_ID} = {{check_id:String}}
      AND {FINAL} != 'false'
    {_WINDOW_PREDICATE}
    {_ONE_ROW_PER_READING}, phase
)
GROUP BY bucket_nanos, phase
ORDER BY bucket_nanos, phase
LIMIT {{limit:UInt32}}
"""

# Individual runs, newest first — every attempt, including the ones a retry
# overruled, because a flaky check is found by its retries (FR25).
#
# Grouped by timestamp and run id: one run writes its status, its duration and
# its phases at the same instant under one id. Readings from before run ids
# existed share an empty id and fall back to the timestamp alone.
#
# This is the statement that sums, so it is where a duplicate hurt most: before
# the inner group a redelivered probe reported twice the time its request took.
CHECK_RUNS_SQL: Final = f"""
SELECT
    toUnixTimestamp64Nano(timestamp) AS observed_nanos,
    run_id,
    countIf(metric_name = '{METRIC_STATUS}') AS status_points,
    anyIf(value, metric_name = '{METRIC_STATUS}') AS up_value,
    anyIf(http_status, metric_name = '{METRIC_STATUS}') AS status_code,
    countIf(metric_name = '{METRIC_DURATION}') AS duration_points,
    anyIf(value, metric_name = '{METRIC_DURATION}') AS duration_seconds,
    countIf(metric_name = '{METRIC_PHASE_DURATION}') AS phase_points,
    sumIf(value, metric_name = '{METRIC_PHASE_DURATION}') AS phase_total_seconds,
    anyIf(outcome, metric_name = '{METRIC_STATUS}') AS outcome,
    anyIf(final_flag, metric_name = '{METRIC_STATUS}') AS final_flag,
    anyIf(maintenance_flag, metric_name = '{METRIC_STATUS}') AS maintenance_flag,
    anyIf(attempt, metric_name = '{METRIC_STATUS}') AS attempt,
    anyIf(location, metric_name = '{METRIC_STATUS}') AS location
FROM (
    SELECT
        timestamp,
        {CHECK_ID} AS check_id,
        metric_name,
        any(value) AS value,
        any({HTTP_STATUS}) AS http_status,
        any({RUN_ID}) AS run_id,
        any({ATTEMPT}) AS attempt,
        any({LOCATION}) AS location,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}', '{METRIC_PHASE_DURATION}')
      AND {CHECK_ID} = {{check_id:String}}
    {_WINDOW_PREDICATE}
    {_ONE_ROW_PER_READING}
)
GROUP BY observed_nanos, run_id
ORDER BY observed_nanos DESC
LIMIT {{limit:UInt32}}
"""

# How many runs a check has recorded since it was created: what deleting it
# loses (FR37). Status points collapsed to one per reading, attempts included.
CHECK_RUN_COUNT_SQL: Final = f"""
SELECT count() AS runs
FROM (
    SELECT timestamp
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name = '{METRIC_STATUS}'
      AND {CHECK_ID} = {{check_id:String}}
    {_WINDOW_PREDICATE}
    GROUP BY series_hash, timestamp
)
"""

# A run's evidence: the request sent, the response received, the failing
# assertion (FR31). One log record per attempt, from the prober's own service,
# which the sort key reaches before it reaches the body. Delivered twice it is
# the same record twice, so the newest is taken and the rest ignored.
PROBER_SERVICE_NAME: Final = "nexovue-prober"
EVIDENCE_RUN_ID: Final = "toString(log_attrs.synthetic.check.run.id)"
EVIDENCE_CHECK_ID: Final = "toString(resource_attrs.synthetic.check.id)"

CHECK_RUN_EVIDENCE_SQL: Final = f"""
SELECT body
FROM logs
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = '{PROBER_SERVICE_NAME}'
  AND {EVIDENCE_CHECK_ID} = {{check_id:String}}
  AND {EVIDENCE_RUN_ID} = {{run_id:String}}
{_WINDOW_PREDICATE}
ORDER BY timestamp DESC
LIMIT 1
"""
