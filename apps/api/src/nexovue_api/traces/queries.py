"""The ClickHouse reads behind the trace endpoints.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

Both list and detail are written to touch as few parts as possible. `spans` is
ordered by `(tenant_id, project_id, service_name, toStartOfHour(timestamp),
trace_id)` and partitioned by day, so a bounded time range is the difference
between reading an hour and reading a month.
"""

from __future__ import annotations

from typing import Final

from nexovue_api.data.clickhouse import values

# `parent_span_id` is FixedString(16), so it is never zero-length: an absent
# parent arrives either NUL-padded or as sixteen ASCII zeros depending on how
# the writer encodes it. Both mean "root".
_PARENT_CLEANED: Final = "replaceAll(parent_span_id, '\\0', '')"
ABSENT_PARENT_SENTINEL: Final = values.ABSENT_SPAN_ID

# Bound to a module constant because the statements below interpolate it, and
# only a name of this module may be spliced into SQL here.
NANOSECONDS_PER_MILLISECOND: Final = values.NANOSECONDS_PER_MILLISECOND

# Worst-status ordering: an error dominates everything, an explicit OK beats a
# status nobody set. Mapped back to its name in `service.py`.
SEVERITY_UNSET: Final = 0
SEVERITY_OK: Final = 1
SEVERITY_ERROR: Final = 2

# The vocabulary `spans.status_code` actually holds. The worker strips OTLP's
# `STATUS_CODE_` prefix before writing, so these are the stored spellings —
# comparing against the prefixed enum names matches nothing and silently reports
# every trace as healthy. Defined once here and imported, never re-typed.
STATUS_UNSET: Final = "UNSET"
STATUS_OK: Final = "OK"
STATUS_ERROR: Final = "ERROR"

_SPAN_FACTS: Final = f"""
    SELECT
        trace_id,
        service_name,
        span_name,
        status_code,
        toUnixTimestamp64Nano(timestamp) AS start_nanos,
        toUnixTimestamp64Nano(timestamp)
            + toInt64(duration_ms * {NANOSECONDS_PER_MILLISECOND}) AS end_nanos,
        {_PARENT_CLEANED} NOT IN ('', '{ABSENT_PARENT_SENTINEL}') AS has_parent,
        multiIf(
            status_code = '{STATUS_ERROR}', {SEVERITY_ERROR},
            status_code = '{STATUS_OK}', {SEVERITY_OK},
            {SEVERITY_UNSET}
        ) AS severity
    FROM spans
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND timestamp >= {{from_time:DateTime64(9)}}
      AND timestamp < {{to_time:DateTime64(9)}}
"""

# ClickHouse's own parameter syntax is braces, so the cursor predicate is
# spliced on a comment marker rather than with str.format.
_CURSOR_PLACEHOLDER: Final = "/* cursor */"

# `argMin` over the tuple `(has_parent, start_nanos)` names the trace after its
# root span, falling back to the earliest span when the root was never received
# — a partial trace still has to render.
_LIST_TRACES_TEMPLATE: Final = f"""
SELECT
    trace_id,
    argMin(service_name, (has_parent, start_nanos)) AS root_service,
    argMin(span_name, (has_parent, start_nanos)) AS root_name,
    min(start_nanos) AS start_time_nanos,
    (max(end_nanos) - min(start_nanos)) / {NANOSECONDS_PER_MILLISECOND} AS duration_ms,
    count() AS span_count,
    countIf(severity = {SEVERITY_ERROR}) AS error_count,
    max(severity) AS worst_severity
FROM ({_SPAN_FACTS})
GROUP BY trace_id
{_CURSOR_PLACEHOLDER}
ORDER BY start_time_nanos DESC, trace_id DESC
LIMIT {{limit:UInt32}}
"""

# Applied only when the caller sent a cursor. Tuple comparison gives the strict
# "everything after this exact trace" the keyset needs, with no row repeated or
# skipped when two traces share a start time.
CURSOR_CLAUSE: Final = (
    "HAVING (start_time_nanos, trace_id) < ({cursor_nanos:Int64}, {cursor_trace_id:String})"
)


def list_traces_sql(*, with_cursor: bool) -> str:
    """The list statement, with the keyset predicate only when there is one."""
    return _LIST_TRACES_TEMPLATE.replace(_CURSOR_PLACEHOLDER, CURSOR_CLAUSE if with_cursor else "")


# Step one of the detail read. `spans` is ordered by service and hour, so
# opening a trace by id alone would scan every part in the retention window;
# this companion table turns that into a bounded range.
# The materialized view behind it writes one row per insert block, so a trace
# whose spans arrived separately has several rows here; the range is aggregated
# rather than read from a single row.
TRACE_TIME_BOUNDS_SQL: Final = """
SELECT
    count() AS matches,
    toUnixTimestamp64Nano(min(span_start)) AS min_nanos,
    toUnixTimestamp64Nano(max(span_end)) AS max_nanos,
    -- Our own clock, which is what `spans` is partitioned by (ADR 0067). The
    -- span timestamps above cannot prune a partition, because the client
    -- supplies them and ClickHouse cannot relate the two.
    toUnixTimestamp64Nano(min(received_at)) AS received_from_nanos,
    toUnixTimestamp64Nano(max(received_at)) AS received_to_nanos
FROM span_trace_lookup
WHERE tenant_id = {tenant_id:String}
  AND trace_id = {trace_id:String}
"""

# Step two: the spans themselves, inside the window step one returned.
TRACE_SPANS_SQL: Final = f"""
SELECT
    span_id,
    if({_PARENT_CLEANED} = '{ABSENT_PARENT_SENTINEL}', '', {_PARENT_CLEANED})
        AS parent_span_id,
    service_name,
    span_name,
    span_kind,
    toUnixTimestamp64Nano(timestamp) AS start_time_nanos,
    duration_ms,
    status_code,
    status_message,
    http_method,
    http_route,
    http_status,
    toJSONString(span_attrs) AS attributes_json
FROM spans
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND trace_id = {{trace_id:String}}
  AND timestamp >= {{window_start:DateTime64(9)}}
  AND timestamp <= {{window_end:DateTime64(9)}}
  -- Prunes every partition older than this trace's first arrival. Exact rather
  -- than a guess at clock skew: no span of a trace can have reached us before
  -- the earliest arrival the lookup recorded for it.
  AND received_at >= {{received_from:DateTime64(9)}}
ORDER BY timestamp, span_id
LIMIT {{limit:UInt32}}
"""
