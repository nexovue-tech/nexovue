"""The statements behind the Profiles screen, over `profile_samples`.

Every statement names `{tenant_id:String}` and `{project_id:String}`; the repository
refuses one that does not. Optional narrowing is spliced in from this module's own
constants by `narrowed`, never from a runtime value, so the tenant guarantee stays
readable from the source (`tests/test_tenant_chokepoint_structure.py`).

Every statement also bounds `received_at` — the partition key, which `timestamp`
cannot prune (`window.RECEIVE_CLOCK_SLACK`) — and refuses rows past their own
retention: the table's TTL removes them only when a merge next runs, and a profile
older than its retention must not be read in the meantime (spec AC14).
"""

from __future__ import annotations

from typing import Final

# How many distinct stacks one graph reads. Past it, the smallest stacks are counted
# into a remainder rather than returned, so a service with a vast profile cannot
# ship a vast response; the remainder keeps the shares summing to the whole.
MAX_STACKS: Final = 20_000

# How many (host, version, environment) combinations the filters read.
MAX_FILTER_ROWS: Final = 1_000

_HOST_PLACEHOLDER: Final = "/*host*/"
_VERSION_PLACEHOLDER: Final = "/*version*/"
_ENVIRONMENT_PLACEHOLDER: Final = "/*environment*/"
_SPAN_PLACEHOLDER: Final = "/*span*/"
_SERVICE_PLACEHOLDER: Final = "/*service*/"

HOST_CLAUSE: Final = "AND host_name = {host:String}"
VERSION_CLAUSE: Final = "AND service_version = {version:String}"
ENVIRONMENT_CLAUSE: Final = "AND environment = {environment:String}"
SPAN_CLAUSE: Final = (
    "AND trace_id = {trace_id:FixedString(32)} AND span_id = {span_id:FixedString(16)}"
)
SERVICE_CLAUSE: Final = "AND service_name = {service:String}"

# The conjuncts every read shares after its own tenant binding — project, window,
# partitions, retention. The binding itself is written into each statement, where
# the chokepoint test can read it.
#
# The window is stated twice, the second time on the sort key's own expression:
# ClickHouse does not derive `toStartOfHour(timestamp)` bounds from a DateTime64
# range, and without them an hour's read scanned every granule of the service —
# 1,092 of 1,189 in the measurement recorded in the feature's status.
_SCOPE: Final = """project_id = {project_id:String}
  AND timestamp >= {from_time:DateTime64(9)}
  AND timestamp < {to_time:DateTime64(9)}
  AND toStartOfHour(timestamp) >= toStartOfHour({from_time:DateTime64(9)})
  AND toStartOfHour(timestamp) <= toStartOfHour({to_time:DateTime64(9)})
  AND received_at >= {received_from:DateTime64(3)}
  AND received_at < {received_to:DateTime64(3)}
  AND received_at >= now64(3) - toIntervalDay(retention_days)"""

# One service and profile type, narrowed as the caller asked.
_SELECTION: Final = f"""{_SCOPE}
  AND service_name = {{service:String}}
  AND sample_type = {{sample_type:String}}
  {_HOST_PLACEHOLDER}
  {_VERSION_PLACEHOLDER}
  {_ENVIRONMENT_PLACEHOLDER}
  {_SPAN_PLACEHOLDER}"""

# Profiled services in the window, busiest first: a whole-host profiler reports every
# process, and the services worth opening are the ones with the most samples.
SERVICES_SQL: Final = f"""
SELECT
    service_name,
    groupUniqArray((sample_type, sample_unit)) AS types,
    max(timestamp) AS last_received
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SCOPE}
GROUP BY service_name
ORDER BY count() DESC, service_name
LIMIT 500
"""

# The newest sample the project (or one service) holds, however far back: what the
# screen offers when the window it was asked for is empty.
LATEST_SQL: Final = f"""
SELECT max(timestamp) AS last_received, count() AS samples
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SCOPE}
  {_SERVICE_PLACEHOLDER}
"""

# The hosts, versions and environments the selection's rows carry, with how many
# samples each combination holds and when it was last seen — the filter menus,
# busiest first, and the two latest versions a comparison starts from.
FILTERS_SQL: Final = f"""
SELECT host_name, service_version, environment, count() AS samples,
    max(timestamp) AS last_seen
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SCOPE}
  AND service_name = {{service:String}}
  AND sample_type = {{sample_type:String}}
GROUP BY host_name, service_version, environment
ORDER BY samples DESC
LIMIT {MAX_FILTER_ROWS}
"""

# The selection's value over time. The width is a bound parameter, which
# `INTERVAL {n} SECOND` cannot take — the reason `metrics/queries.py` gives too.
TIMELINE_SQL: Final = f"""
SELECT
    intDiv(toUnixTimestamp(timestamp), {{bucket_seconds:UInt32}}) * {{bucket_seconds:UInt32}}
        AS bucket_epoch,
    sum(value) AS value,
    count() AS samples,
    any(sample_unit) AS unit
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SELECTION}
GROUP BY bucket_epoch
ORDER BY bucket_epoch
"""

# The heaviest stacks of one selection, by value — hash and value only. Frames are
# read afterwards for just these (`STACK_FRAMES_SQL`): grouping a busy day's stacks
# with their frame arrays held 950 MiB for one read, because every aggregation
# thread keeps its own copy of every group's arrays. This holds sixteen bytes a stack.
RANKED_STACKS_SQL: Final = f"""
SELECT stack_hash, sum(value) AS value
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SELECTION}
GROUP BY stack_hash
ORDER BY value DESC, stack_hash
LIMIT {MAX_STACKS}
"""

# One row of frames for each ranked stack, the ranking repeated with the same order —
# ties broken by hash — so both reads keep the same stacks. `LIMIT 1 BY` streams: it remembers which
# hashes it has seen, never their arrays. Every row of a stack carries the same frames,
# so any one of them is the stack. The ranking is repeated here rather than sent back
# as twenty thousand hashes, which would make the request itself the large thing.
STACK_FRAMES_SQL: Final = f"""
SELECT
    stack_hash,
    frame_functions AS functions,
    frame_files AS files,
    frame_lines AS lines,
    frame_binaries AS binaries
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SELECTION}
  AND stack_hash IN (
    SELECT stack_hash
    FROM profile_samples
    WHERE tenant_id = {{tenant_id:String}}
      AND {_SELECTION}
    GROUP BY stack_hash
    ORDER BY sum(value) DESC, stack_hash
    LIMIT {MAX_STACKS}
  )
LIMIT 1 BY stack_hash
"""

# The same selection's whole: what the returned stacks are a share of.
TOTAL_SQL: Final = f"""
SELECT sum(value) AS total, any(sample_unit) AS unit, count() AS samples
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SELECTION}
"""

# Around one span: how many samples the service sent at all, and how many of them the
# profiler linked to this span — the three answers a span's Profile section gives.
SPAN_SQL: Final = f"""
SELECT
    count() AS samples,
    countIf(trace_id = {{trace_id:FixedString(32)}} AND span_id = {{span_id:FixedString(16)}})
        AS linked,
    groupUniqArrayIf(
        sample_type,
        trace_id = {{trace_id:FixedString(32)}} AND span_id = {{span_id:FixedString(16)}}
    ) AS linked_types
FROM profile_samples
WHERE tenant_id = {{tenant_id:String}}
  AND {_SCOPE}
  AND service_name = {{service:String}}
"""


def narrowed(
    statement: str,
    *,
    host: bool = False,
    version: bool = False,
    environment: bool = False,
    span: bool = False,
    service: bool = False,
) -> str:
    """A statement narrowed to what the caller asked for."""
    return (
        statement.replace(_HOST_PLACEHOLDER, HOST_CLAUSE if host else "")
        .replace(_VERSION_PLACEHOLDER, VERSION_CLAUSE if version else "")
        .replace(_ENVIRONMENT_PLACEHOLDER, ENVIRONMENT_CLAUSE if environment else "")
        .replace(_SPAN_PLACEHOLDER, SPAN_CLAUSE if span else "")
        .replace(_SERVICE_PLACEHOLDER, SERVICE_CLAUSE if service else "")
    )
