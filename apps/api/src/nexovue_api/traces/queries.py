"""The ClickHouse reads behind the trace endpoints.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**The trace list reads `trace_summaries`, not `spans`.** The worker writes one
row per stored trace (migration `000028`), ordered `(tenant_id, project_id,
start_time, trace_id)` — so "newest first" is a reverse read of the sort key
with no aggregate at all, and the cost is the page rather than the window.
Grouping `spans` for the same answer held one hash-table entry per trace in the
window whatever the page size: measured over 12.49M spans / 2.73M traces, a
7-day window took 292 MiB and 393 ms and read 607 MiB, and before the strings
were taken out of the group it needed 1.8 GiB and the server killed it.

**Why the list is still two statements.** A trace can hold more than one summary
row — a span arriving after the sampler forgot its trace is summarised on its
own, and two rows sharing a key stay two rows until a background merge collapses
them. So the first statement picks the page in sort-key order and the second
merges every row of those fifty ids into one answer, which the `trace_id` bloom
filter makes a granule-skipping read. The columns merge with `max`, `min` and
set union, so a stale row folded with a complete one is the complete one.

**One filter still reads `spans`: the arbitrary attribute.** Attribute paths are
unbounded — 125 distinct ones from 15 services in a single hour of the
OpenTelemetry Demo — so no per-trace row can carry them, and a request naming
`attr` falls back to the grouping statements below. Nothing is dropped and
nothing else pays for it. The service, operation, status and duration filters
are all answered from the summary.

**Why a span filter is a flag and not a `WHERE`** — in the fallback. Filtering
the spans that feed the group-by would compute the trace's duration, span count
and error count from the matching spans alone, which is a different and wrong
number. So each span is marked as matching or not, and `HAVING max(matched)`
keeps the traces that contain one — while the aggregate still runs over every
span of those traces.

The obvious alternative, `trace_id IN (SELECT trace_id … WHERE <filters>)`, was
measured and rejected. It is far cheaper when the filter is selective — one
service of a 2.73M-trace week came to 27 MiB against 620 MiB — but ClickHouse
materializes the set, and a filter matching most of the window needed 1.68 GiB
for the set alone and was killed in `CreatingSetsTransform`. A filter that makes
the screen fail where no filter succeeds is worse than a slow one, and nothing
in the request says which kind a filter will be. The flag form costs what the
unfiltered window costs, whatever is asked of it: the same week filtered on an
attribute carrying 9.8M spans came to 736 MiB.

**What the summary means for a window.** `trace_summaries` is keyed on when a
trace *started*, so the list now answers "traces that started in this window"
where grouping `spans` answered "traces with a span in this window". They differ
only at the two boundaries, and the first is what "newest first" was always
trying to say. The trace detail is unaffected: it reads `spans` through
`span_trace_lookup` as before.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class TraceFilters:
    """What narrows the trace list beyond the window. Every field is optional.

    It lives beside the SQL because it says which predicates a statement
    carries, which is a property of the statement. Deciding *what* goes in one
    — validating a path, choosing a column over the JSON — is `service.py`.

    The first three are span filters: a trace matches when **one** of its spans
    satisfies all of them together, which is what makes "a failing POST on
    checkout" mean one span rather than three unrelated ones. The last two are
    trace filters, tested against the assembled trace.
    """

    service_name: str | None = None
    operation: str | None = None
    attribute_path: str | None = None
    attribute_value: str | None = None
    status: str | None = None
    min_duration_ms: float | None = None
    max_duration_ms: float | None = None

    @property
    def has_span_filter(self) -> bool:
        return bool(self.service_name or self.operation or self.attribute_path)

    @property
    def answerable_from_summary(self) -> bool:
        """Whether `trace_summaries` alone can decide this request.

        Everything but the attribute filter can. Attribute paths are unbounded,
        so a per-trace row cannot carry them and the request falls back to
        grouping `spans` — which is what the statements below are still for.
        """
        return self.attribute_path is None


# ClickHouse's own parameter syntax is braces, so an optional predicate is
# spliced on a comment marker rather than with str.format.
_SPAN_MATCH_MARKER: Final = "/* span match */"
_TRACE_MATCH_MARKER: Final = "/* trace match */"

# --------------------------------------------------------------- span filters

# What one span must satisfy for its trace to be a match. Conjoined into the
# `matched` flag the aggregate below tests, never into a `WHERE`.
SERVICE_CLAUSE: Final = "service_name = {service_name:String}"
OPERATION_CLAUSE: Final = "span_name = {operation:String}"

# The escape hatch, for the attributes that were never promoted. `getSubcolumn`
# takes the path as a bound parameter, so an arbitrary customer attribute is a
# value and never SQL.
#
# `toString` puts the comparison in one type rather than leaving it to the
# store. A bare path is a `Dynamic`, and ClickHouse 26.3 does coerce one against
# a String on `=` — measured, an Int64 `200` and a Bool `true` both matched
# without the cast — so this is not what makes the filter work today. It is
# kept because that coercion is a property of the Dynamic type rather than a
# stated contract, the same type ClickHouse already refuses outright as a
# GROUP BY key, and a customer-facing filter should not rest on it. Removing
# the cast therefore breaks no test: it is a deliberate belt, not a brace.
ATTRIBUTE_JSON_CLAUSE: Final = (
    "toString(getSubcolumn(span_attrs, {attr_path:String})) = {attr_value:String}"
)

# Attribute paths the worker copies into a typed column *unchanged*. Filtering
# the column instead of the JSON is cheaper — measured over a 7-day, 12.45M-span
# window, 496 MiB read against 3.57 GiB, 68 ms against 223 ms — and it is also
# more correct: the column carries both semantic-convention spellings, so
# `http.method` from an older SDK and `http.request.method` from a newer one
# answer as one fleet. Asking the JSON for either spelling finds only half.
#
# The RPC attributes are deliberately absent even though `rpc_system`,
# `rpc_method` and `rpc_status` are promoted columns too. The worker rewrites
# those values on the way in (`semconv.go`): `connect_rpc` is stored as
# `connectrpc`, `rpc.method` is qualified with `rpc.service`, and the integer
# `rpc.grpc.status_code` 14 is stored as `UNAVAILABLE`. Routing them here would
# compare what the customer typed against something else and answer nothing.
# They still filter correctly through the JSON, which holds them as they came.
PROMOTED_ATTRIBUTE_CLAUSES: Final[dict[str, str]] = {
    "http.request.method": "http_method = {attr_value:String}",
    "http.method": "http_method = {attr_value:String}",
    "http.route": "http_route = {attr_value:String}",
    "http.response.status_code": "toString(http_status) = {attr_value:String}",
    "http.status_code": "toString(http_status) = {attr_value:String}",
}


def attribute_clause(path: str) -> str:
    """The cheapest predicate that answers for this attribute path."""
    return PROMOTED_ATTRIBUTE_CLAUSES.get(path, ATTRIBUTE_JSON_CLAUSE)


# -------------------------------------------------------------- trace filters

# `worst_severity` is `max(severity)` over the trace: an error anywhere makes
# the trace an error, and "ok" means nothing in it failed rather than
# "everything in it reported OK" — an unset status is not a failure.
STATUS_ERROR_CLAUSE: Final = f"worst_severity = {SEVERITY_ERROR}"
STATUS_OK_CLAUSE: Final = f"worst_severity < {SEVERITY_ERROR}"

MIN_DURATION_CLAUSE: Final = "duration_ms >= {min_duration_ms:Float64}"
MAX_DURATION_CLAUSE: Final = "duration_ms <= {max_duration_ms:Float64}"

# The span filters, once the trace is assembled: at least one of its spans
# satisfied all of them together.
SPAN_MATCH_CLAUSE: Final = "max(matched)"

# Tuple comparison gives the strict "everything after this exact trace" the
# keyset needs, with no row repeated or skipped when two traces share a start.
CURSOR_CLAUSE: Final = (
    "(start_time_nanos, trace_id) < ({cursor_nanos:Int64}, {cursor_trace_id:String})"
)

# ------------------------------------------------- the summary read (the list)

# What one row of `trace_summaries` must satisfy. All five are tested against a
# row rather than an aggregate, which is the whole point: no group-by, so the
# state does not grow with the window.
SUMMARY_SERVICE_CLAUSE: Final = "has(span_services, {service_name:String})"

# How the worker joins a service to an operation inside `span_endpoints`
# (`traceassembly/summary.go`). A unit separator cannot occur in either half, so
# the join is reversible; it is never spliced into SQL, only bound as a value.
ENDPOINT_SEPARATOR: Final = "\x1f"

# The service and the operation are tested as one value, because a span filter
# matches when ONE span satisfies all of it together. `span_endpoints` holds the
# trace's distinct `service<US>operation` pairs, so this cannot match a trace
# whose service and whose operation are on different spans. The separator never
# reaches the SQL: the caller binds the joined value.
SUMMARY_ENDPOINT_CLAUSE: Final = "has(span_endpoints, {endpoint:String})"

# An operation named without a service. `endsWith` over the pairs is the same
# test with the service half left open.
SUMMARY_OPERATION_CLAUSE: Final = (
    "arrayExists(pair -> endsWith(pair, {operation_suffix:String}), span_endpoints)"
)

SUMMARY_STATUS_ERROR_CLAUSE: Final = f"worst_severity = {SEVERITY_ERROR}"
SUMMARY_STATUS_OK_CLAUSE: Final = f"worst_severity < {SEVERITY_ERROR}"

# The keyset, in the sort key's own terms so it prunes rather than filters.
SUMMARY_CURSOR_CLAUSE: Final = (
    "(toUnixTimestamp64Nano(start_time), trace_id)"
    " < ({cursor_nanos:Int64}, {cursor_trace_id:String})"
)

_SUMMARY_MATCH_MARKER: Final = "/* summary match */"

# Step one of the list: which fifty traces this page holds, and where the next
# page starts.
#
# `ORDER BY start_time DESC` over the sort key with tenant and project fixed is
# a reverse read that stops at the page: measured over a 7-day window of the
# busiest dev project, 30 ms and 17.3 MiB reading 120k rows, against 1100 ms and
# 290 MiB reading 12.48M for the group-by it replaces.
#
# **No `LIMIT 1 BY trace_id` here, and it is measured rather than assumed.** A
# trace can hold more than one row — one that gained a span after the sampler
# forgot it, and two rows sharing a key until a background merge collapses them
# — so the page has to be collapsed somewhere. Doing it in the statement stops
# ClickHouse reading in order: the same window went to 19 ms and 63.5 MiB over
# 1.02M rows, and 113 ms on one thread. It is 962 rows of 2.73M in this store,
# so the page is collapsed where it is free, over the fifty rows returned.
_TRACE_PAGE_TEMPLATE: Final = f"""
SELECT
    trace_id,
    toUnixTimestamp64Nano(start_time) AS start_time_nanos
FROM trace_summaries
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND start_time >= {{from_time:DateTime64(9)}}
  AND start_time < {{to_time:DateTime64(9)}}{_SUMMARY_MATCH_MARKER}
ORDER BY start_time DESC, trace_id DESC
LIMIT {{limit:UInt32}}
"""

# Step two: the whole trace behind each id on the page.
#
# Bounded by the page's own span of start times, not by the request's window.
# The fifty rows a page returns are adjacent in the sort key, so that bound is a
# primary-key range: measured, 7 ms reading 27k rows, where the same statement
# leaning on the `trace_id` bloom filter alone read 1.91M — fifty ids scattered
# across a week hit most of the index's granules, which is what a bloom filter
# at GRANULARITY 4 is for and not what it can do. A row of one of these traces
# outside that range is not merged; it is older or newer than the whole page,
# and there are 962 such rows in 2.73M.
#
# The aggregate is over at most a handful of rows per trace and is what makes a
# stale row harmless: `max` of a complete snapshot and a partial one is the
# complete one, and `argMax(…, span_count)` names the trace after whichever row
# saw most of it. `span_count` is qualified inside `argMax` because the alias
# below shadows the column, and ClickHouse then reads it as an aggregate inside
# an aggregate and refuses the statement.
TRACE_PAGE_VALUES_SQL: Final = """
SELECT
    trace_id,
    toUnixTimestamp64Nano(min(start_time)) AS start_time_nanos,
    max(duration_ms) AS duration_ms,
    max(span_count) AS span_count,
    max(error_count) AS error_count,
    max(worst_severity) AS worst_severity,
    argMax(root_service, trace_summaries.span_count) AS root_service,
    argMax(root_name, trace_summaries.span_count) AS root_name
FROM trace_summaries
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND start_time >= fromUnixTimestamp64Nano({page_from_nanos:Int64})
  AND start_time <= fromUnixTimestamp64Nano({page_to_nanos:Int64})
  AND trace_id IN {trace_ids:Array(String)}
GROUP BY trace_id
"""


def trace_page_sql(filters: TraceFilters, *, with_cursor: bool) -> str:
    """The page statement carrying only the predicates this call needs."""
    return _TRACE_PAGE_TEMPLATE.replace(
        _SUMMARY_MATCH_MARKER, _summary_match_clause(filters, with_cursor=with_cursor)
    )


def _summary_match_clause(filters: TraceFilters, *, with_cursor: bool) -> str:
    """The rest of the `WHERE`, or nothing beyond the window."""
    predicates = []
    if filters.service_name and filters.operation:
        predicates.append(SUMMARY_ENDPOINT_CLAUSE)
    elif filters.service_name:
        predicates.append(SUMMARY_SERVICE_CLAUSE)
    elif filters.operation:
        predicates.append(SUMMARY_OPERATION_CLAUSE)
    if filters.status == STATUS_ERROR:
        predicates.append(SUMMARY_STATUS_ERROR_CLAUSE)
    elif filters.status == STATUS_OK:
        predicates.append(SUMMARY_STATUS_OK_CLAUSE)
    if filters.min_duration_ms is not None:
        predicates.append(MIN_DURATION_CLAUSE)
    if filters.max_duration_ms is not None:
        predicates.append(MAX_DURATION_CLAUSE)
    if with_cursor:
        predicates.append(SUMMARY_CURSOR_CLAUSE)
    if not predicates:
        return ""
    return "\n  AND " + "\n  AND ".join(predicates)


# --------------------------------------------- statements over `spans` (detail,
# facets, and the attribute-filtered list)

# Where the group-by spills to disk instead of growing.
#
# `GROUP BY trace_id` is the one aggregate in this service whose key count is
# unbounded — every other one groups by a service, an endpoint, a host or a time
# bucket, all of which a customer has few of. Without a bound it is limited only
# by the server: a week of the busiest dev project reached 640 MiB, and whenever
# the 2.5 GiB dev server was already near its cap that query was killed — the
# reader got a 500 rather than a slow page. Watched, not reasoned about.
#
# 256 MiB caps a wide read at roughly 300 MiB of state rather than 640 MiB, so
# several readers fit where two did not. Measured on that window: peak 600 MiB
# and 356 ms becomes peak 288 MiB and 770 ms, writing 127 MB of temporary parts.
# An hour never reaches the threshold and is unchanged, so this is paid only by
# the windows that would otherwise be the ones to fail.
#
# ClickHouse's own default is `max_bytes_ratio_before_external_group_by = 0.5`,
# a share of memory *free when the query starts*. On a server already near its
# cap that share is nothing, which is exactly when the bound is needed — hence
# an absolute one. It is a constant rather than configuration because it is
# derived from the deployment's memory, which nothing here reads yet.
AGGREGATION_BOUND: Final = 256 * 1024 * 1024

# Only fixed-width values leave this subquery. Every string a trace row needs
# is fetched by `TRACE_ROOTS_SQL` for the page alone; see the module docstring.
_TRACE_SPAN_FACTS: Final = f"""
    SELECT
        trace_id,
        toUnixTimestamp64Nano(timestamp) AS start_nanos,
        toUnixTimestamp64Nano(timestamp)
            + toInt64(duration_ms * {NANOSECONDS_PER_MILLISECOND}) AS end_nanos,
        multiIf(
            status_code = '{STATUS_ERROR}', {SEVERITY_ERROR},
            status_code = '{STATUS_OK}', {SEVERITY_OK},
            {SEVERITY_UNSET}
        ) AS severity{_SPAN_MATCH_MARKER}
    FROM spans
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND timestamp >= {{from_time:DateTime64(9)}}
      AND timestamp < {{to_time:DateTime64(9)}}
"""

_LIST_TRACES_TEMPLATE: Final = f"""
SELECT
    trace_id,
    min(start_nanos) AS start_time_nanos,
    (max(end_nanos) - min(start_nanos)) / {NANOSECONDS_PER_MILLISECOND} AS duration_ms,
    count() AS span_count,
    countIf(severity = {SEVERITY_ERROR}) AS error_count,
    max(severity) AS worst_severity
FROM ({_TRACE_SPAN_FACTS})
GROUP BY trace_id
{_TRACE_MATCH_MARKER}
ORDER BY start_time_nanos DESC, trace_id DESC
LIMIT {{limit:UInt32}}
SETTINGS max_bytes_before_external_group_by = {AGGREGATION_BOUND}
"""

# `argMin` over the tuple `(has_parent, start_nanos)` names the trace after its
# root span, falling back to the earliest span when the root was never received
# — a partial trace still has to render. Run for one page of ids, never for the
# window: `spans` carries a bloom filter on `trace_id`, which turns this into a
# granule-skipping read even across a week.
TRACE_ROOTS_SQL: Final = f"""
SELECT
    trace_id,
    argMin(service_name, (has_parent, start_nanos)) AS root_service,
    argMin(span_name, (has_parent, start_nanos)) AS root_name
FROM (
    SELECT
        trace_id,
        service_name,
        span_name,
        toUnixTimestamp64Nano(timestamp) AS start_nanos,
        {_PARENT_CLEANED} NOT IN ('', '{ABSENT_PARENT_SENTINEL}') AS has_parent
    FROM spans
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND timestamp >= {{from_time:DateTime64(9)}}
      AND timestamp < {{to_time:DateTime64(9)}}
      AND trace_id IN {{trace_ids:Array(String)}}
)
GROUP BY trace_id
"""

# What the filter controls may offer, counted over the same window the results
# come from — a service that sent nothing this hour is not a filter, and a
# screen that offers it is a screen that returns nothing without saying why.
#
# Both columns are LowCardinality and the group is services by operations, so
# this stays a small aggregate however many spans it reads. There is
# deliberately no attribute facet: attribute paths are unbounded — 125 distinct
# ones from 15 services in a single hour of the OpenTelemetry Demo — so the
# attribute filter is a typed path, not a list to choose from.
TRACE_FACETS_SQL: Final = """
SELECT
    service_name,
    span_name,
    count() AS spans,
    uniq(trace_id) AS traces
FROM spans
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND timestamp >= {from_time:DateTime64(9)}
  AND timestamp < {to_time:DateTime64(9)}
GROUP BY service_name, span_name
ORDER BY spans DESC
LIMIT {limit:UInt32}
"""


def list_traces_sql(filters: TraceFilters, *, with_cursor: bool) -> str:
    """The list statement carrying only the predicates this call needs."""
    return _LIST_TRACES_TEMPLATE.replace(_SPAN_MATCH_MARKER, _matched_flag(filters)).replace(
        _TRACE_MATCH_MARKER, _trace_match_clause(filters, with_cursor=with_cursor)
    )


def _matched_flag(filters: TraceFilters) -> str:
    """Whether this span satisfies the span filters, or nothing when there are none.

    The outer parentheses are load-bearing: ClickHouse binds `AS` tighter than
    `AND`, so `a AND b AS matched` names `b` and silently drops `a`.
    """
    predicates = []
    if filters.service_name:
        predicates.append(SERVICE_CLAUSE)
    if filters.operation:
        predicates.append(OPERATION_CLAUSE)
    if filters.attribute_path:
        predicates.append(attribute_clause(filters.attribute_path))
    if not predicates:
        return ""
    conjunction = " AND ".join(f"({predicate})" for predicate in predicates)
    return f",\n        ({conjunction}) AS matched"


def _trace_match_clause(filters: TraceFilters, *, with_cursor: bool) -> str:
    """The `HAVING` the assembled trace must satisfy, or nothing at all."""
    predicates = []
    if filters.has_span_filter:
        predicates.append(SPAN_MATCH_CLAUSE)
    if filters.status == STATUS_ERROR:
        predicates.append(STATUS_ERROR_CLAUSE)
    elif filters.status == STATUS_OK:
        predicates.append(STATUS_OK_CLAUSE)
    if filters.min_duration_ms is not None:
        predicates.append(MIN_DURATION_CLAUSE)
    if filters.max_duration_ms is not None:
        predicates.append(MAX_DURATION_CLAUSE)
    if with_cursor:
        predicates.append(CURSOR_CLAUSE)
    return f"HAVING {' AND '.join(predicates)}" if predicates else ""


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
    -- Our own clock, which is what `spans` is partitioned by. The
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
    -- A gRPC span carries none of the three above: its identity is the RPC
    -- method and its outcome the RPC status (migration 000023).
    rpc_system,
    rpc_method,
    rpc_status,
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
