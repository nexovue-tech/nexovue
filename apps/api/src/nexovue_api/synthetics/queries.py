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

# One row per check. Status, duration and certificate are read in a single pass
# with conditional aggregates rather than in three queries joined afterwards.
#
# `runs` counts status points, not rows: one probe writes one status point, one
# duration point, five phase points and a certificate point, so counting rows
# would report a check that ran once as having run eight times.
CHECK_SUMMARIES_SQL: Final = f"""
SELECT
    {CHECK_ID} AS check_id,
    argMax({CHECK_TYPE}, timestamp) AS check_type,
    countIf(metric_name = '{METRIC_STATUS}') AS runs,
    countIf(metric_name = '{METRIC_STATUS}' AND value = {UP_VALUE}) AS successful_runs,
    argMaxIf(value, timestamp, metric_name = '{METRIC_STATUS}') AS latest_up,
    argMaxIf({HTTP_STATUS}, timestamp, metric_name = '{METRIC_STATUS}') AS latest_status_code,
    maxIf(toUnixTimestamp64Nano(timestamp), metric_name = '{METRIC_STATUS}') AS latest_run_nanos,
    avgIf(value, metric_name = '{METRIC_DURATION}') AS mean_seconds,
    quantileIf(0.95)(value, metric_name = '{METRIC_DURATION}') AS p95_seconds,
    maxIf(value, metric_name = '{METRIC_DURATION}') AS max_seconds,
    countIf(metric_name = '{METRIC_TLS_REMAINING}') AS tls_readings,
    argMaxIf(value, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_days_remaining,
    argMaxIf({TLS_ISSUER}, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_issuer,
    argMaxIf({SERVER_ADDRESS}, timestamp, metric_name = '{METRIC_TLS_REMAINING}') AS tls_server
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}', '{METRIC_TLS_REMAINING}')
{_WINDOW_PREDICATE}
GROUP BY check_id
ORDER BY check_id
LIMIT {{limit:UInt32}}
"""

# Each check's history, bucket by bucket — the sparkline in its row.
#
# The duration is `avgIf` over the bucket, and **a bucket with no successful run has no
# duration at all**: `avgIf` over nothing returns nothing, and `runs`/`successful_runs`
# beside it say why. The service turns that into a gap rather than a zero, because a
# check that failed has no response time and a line drawn at zero would read as the site
# getting instantly fast.
CHECK_TRENDS_SQL: Final = f"""
SELECT
    {CHECK_ID} AS check_id,
    {_BUCKET_NANOS} AS bucket_nanos,
    countIf(metric_name = '{METRIC_STATUS}') AS runs,
    countIf(metric_name = '{METRIC_STATUS}' AND value = {UP_VALUE}) AS successful_runs,
    avgIf(value, metric_name = '{METRIC_DURATION}') AS mean_seconds
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}')
{_WINDOW_PREDICATE}
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
    {PHASE} AS phase,
    avg(value) AS mean_seconds,
    count() AS samples
FROM metrics_gauge
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND metric_name = '{METRIC_PHASE_DURATION}'
  AND {CHECK_ID} = {{check_id:String}}
{_WINDOW_PREDICATE}
GROUP BY bucket_nanos, phase
ORDER BY bucket_nanos, phase
LIMIT {{limit:UInt32}}
"""

# Individual probes, newest first — the uptime strip rather than the average.
# Grouped by `timestamp` because one probe writes its status, its duration and
# its phases at the same instant; the group is one run.
#
# The inner group is correctness, not optimisation. A metric row is not unique —
# `metricwriting.Absorb` takes a duplicate over a lost point, leaving the reader
# to "group by series and timestamp first" — and this is the only statement here
# that sums, so a redelivered probe would report twice the time it measured.
# The series is `(metric_name, series_hash)`: `series_hash` hashes `attrs` alone,
# and a duration point carries `attrs = {{}}`.
CHECK_RUNS_SQL: Final = f"""
SELECT
    observed_nanos,
    countIf(metric_name = '{METRIC_STATUS}') AS status_points,
    anyIf(value, metric_name = '{METRIC_STATUS}') AS up_value,
    anyIf(http_status, metric_name = '{METRIC_STATUS}') AS status_code,
    anyIf(value, metric_name = '{METRIC_DURATION}') AS duration_seconds,
    sumIf(value, metric_name = '{METRIC_PHASE_DURATION}') AS phase_total_seconds
FROM (
    SELECT
        toUnixTimestamp64Nano(timestamp) AS observed_nanos,
        metric_name,
        any(value) AS value,
        any({HTTP_STATUS}) AS http_status
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name IN ('{METRIC_STATUS}', '{METRIC_DURATION}', '{METRIC_PHASE_DURATION}')
      AND {CHECK_ID} = {{check_id:String}}
    {_WINDOW_PREDICATE}
    GROUP BY metric_name, series_hash, observed_nanos
)
GROUP BY observed_nanos
ORDER BY observed_nanos DESC
LIMIT {{limit:UInt32}}
"""
