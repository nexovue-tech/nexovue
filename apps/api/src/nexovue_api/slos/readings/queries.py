"""The suggestion's reads: the last 28 days of one source, split into four 7-day weeks
(design, *`/slo-readings/suggestion`*).

Every statement binds the tenant, which the repository supplies, and the project, which
the caller proved is theirs. Each groups by week, `0` the newest, so one statement reads
the four windows FR7 compares. The check-run rule is the Synthetics screen's own
(`nexovue_platform.readings`), bound here as module constants, the only values a
statement may embed (`test_tenant_chokepoint_structure.py`).
"""

from __future__ import annotations

from typing import Final

from nexovue_platform import readings

_WEEK_SECONDS: Final = 7 * 24 * 60 * 60
_METRIC_STATUS: Final = readings.METRIC_STATUS
_CHECK_ID: Final = readings.CHECK_ID
_COUNTED_RUN: Final = readings.COUNTED_RUN
_READING_ATTRIBUTES: Final = readings.READING_ATTRIBUTES
_ONE_ROW_PER_READING: Final = readings.ONE_ROW_PER_READING
_UP_VALUE: Final = readings.UP_VALUE

# A service's requests and failures by week: count columns only.
SERVICE_WEEKS_SQL: Final = f"""
SELECT intDiv(toUInt32(dateDiff('second', minute, {{now:DateTime}})), {_WEEK_SECONDS}) AS week,
       sum(request_count) AS total,
       sum(error_count) AS errors,
       min(minute) AS first_minute
FROM service_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = {{service:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{now:DateTime}}
GROUP BY week
"""

# A service's latency buckets by week, merged (design, *Reading latency from buckets*).
LATENCY_WEEKS_SQL: Final = f"""
SELECT intDiv(toUInt32(dateDiff('second', minute, {{now:DateTime}})), {_WEEK_SECONDS}) AS week,
       sumForEachMerge(latency_buckets) AS buckets,
       min(minute) AS first_minute
FROM service_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = {{service:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{now:DateTime}}
GROUP BY week
"""

# One endpoint's requests and failures by week. Its counts are exact; only its latency
# is sampled (spec, *Where this differs*), and that is never read here.
ENDPOINT_WEEKS_SQL: Final = f"""
SELECT intDiv(toUInt32(dateDiff('second', minute, {{now:DateTime}})), {_WEEK_SECONDS}) AS week,
       sum(request_count) AS total,
       sum(error_count) AS errors,
       min(minute) AS first_minute
FROM endpoint_stats_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND service_name = {{service:String}}
  AND span_kind IN ('SERVER', 'CONSUMER')
  AND span_name = {{endpoint:String}}
  AND minute >= {{from_minute:DateTime}}
  AND minute < {{now:DateTime}}
GROUP BY week
"""

# A service's endpoints over the same days, busiest first: what the form offers to leave
# out, with health checks pre-ticked (FR34), and to pick for an endpoint target.
ENDPOINT_NAMES_SQL: Final = """
SELECT span_name, sum(request_count) AS requests
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND span_kind IN ('SERVER', 'CONSUMER')
  AND minute >= {from_minute:DateTime}
  AND minute < {now:DateTime}
GROUP BY span_name
ORDER BY requests DESC, span_name
LIMIT {limit:UInt32}
"""

# A check's counted runs by week, by the uptime rule, each reading once. `received_at`
# is bounded too, for partition pruning, a day wider each side (`window.py`).
CHECK_WEEKS_SQL: Final = f"""
SELECT intDiv(toUInt32(dateDiff('second', timestamp, {{now:DateTime64(3)}})), {_WEEK_SECONDS})
           AS week,
       countIf({_COUNTED_RUN}) AS total,
       countIf({_COUNTED_RUN} AND value = {_UP_VALUE}) AS good,
       min(timestamp) AS first_run
FROM (
    SELECT timestamp,
           {_CHECK_ID} AS check_id,
           metric_name,
           any(value) AS value,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name = '{_METRIC_STATUS}'
      AND {_CHECK_ID} = {{check_id:String}}
      AND timestamp >= {{from_minute:DateTime64(3)}}
      AND timestamp < {{now:DateTime64(3)}}
      AND received_at >= {{received_from:DateTime64(3)}}
      AND received_at < {{received_to:DateTime64(3)}}
    {_ONE_ROW_PER_READING}
)
WHERE final_flag != 'false'
GROUP BY week
"""


# ------------------------------------------------------------ the burn line (AC109)
#
# Each source's minutes over [from, to): the same counts the scheduler's judging reads
# (`nexovue_scheduler.slos.store`), so the chart's rate equals the stored one.

SERVICE_MINUTES_SQL: Final = """
SELECT toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY minute
"""

LATENCY_MINUTES_SQL: Final = """
SELECT toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sumForEachMerge(latency_buckets) AS buckets
FROM service_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY minute
"""

ENDPOINT_MINUTES_SQL: Final = """
SELECT span_name,
       toUnixTimestamp(minute) AS minute_epoch,
       sum(request_count) AS requests,
       sum(error_count) AS errors
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND service_name = {service:String}
  AND span_kind IN ('SERVER', 'CONSUMER')
  AND span_name IN {endpoints:Array(String)}
  AND minute >= {from_minute:DateTime}
  AND minute < {to_minute:DateTime}
GROUP BY span_name, minute
"""

CHECK_MINUTES_SQL: Final = f"""
SELECT toUnixTimestamp(toStartOfMinute(timestamp)) AS minute_epoch,
       countIf({_COUNTED_RUN}) AS counted,
       countIf({_COUNTED_RUN} AND value = {_UP_VALUE}) AS up
FROM (
    SELECT timestamp,
           {_CHECK_ID} AS check_id,
           metric_name,
           any(value) AS value,{_READING_ATTRIBUTES}
    FROM metrics_gauge
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND metric_name = '{_METRIC_STATUS}'
      AND {_CHECK_ID} = {{check_id:String}}
      AND timestamp >= {{from_minute:DateTime64(3)}}
      AND timestamp < {{to_minute:DateTime64(3)}}
      AND received_at >= {{received_from:DateTime64(3)}}
      AND received_at < {{received_to:DateTime64(3)}}
    {_ONE_ROW_PER_READING}
)
WHERE final_flag != 'false'
GROUP BY minute_epoch
"""
