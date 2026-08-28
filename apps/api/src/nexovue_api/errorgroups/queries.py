"""The ClickHouse reads behind error grouping.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**Two sources, one group.** OpenTelemetry is deprecating the Span Events API in
favour of log-based events, so an exception arrives either as a span event —
promoted to `spans.exception_type` / `spans.exception_message` — or as a log
record, promoted to the same two columns on `logs`. `OTEL_SEMCONV_EXCEPTION_
SIGNAL_OPT_IN=logs/dup` emits *both* during a migration. The occurrence rows are
therefore a `UNION ALL` of the two tables, and the count de-duplicates; see
`occurrence_key` below.

**Partition pruning.** Both tables are partitioned on `received_at`, our clock,
while the caller asks about `timestamp`, the customer's (ADR 0067). ClickHouse
cannot relate the two, so every statement here bounds both — the service widens
the requested window by `window.RECEIVE_CLOCK_SLACK` to get the `received_at`
range. Without that a one-hour question reads every partition in the retention
period.
"""

from __future__ import annotations

from typing import Final

from nexovue_api.data.clickhouse import values

# Bound to module constants because the statements below interpolate them, and
# only a name of this module may be spliced into SQL here.
ABSENT_TRACE_ID: Final = values.ABSENT_TRACE_ID
ABSENT_SPAN_ID: Final = values.ABSENT_SPAN_ID

# The attribute an SDK sets to choose its own grouping. Present and non-empty,
# it replaces everything derived below. ClickHouse's JSON type reads a dot as a
# path separator, so the flat key arrives as a two-level path.
SDK_FINGERPRINT_PATH: Final = "grouping.fingerprint"

SOURCE_SPAN: Final = "span"
SOURCE_LOG: Final = "log"

# ---------------------------------------------------------- message templating
#
# Grouping on the raw message would make one group per occurrence, because a
# message routinely embeds an id, a path, a duration or an address. Each of
# these collapses one class of embedded value to a fixed token, so that
# "User 4f3a…6666 not found" and "User 9c8d…ffff not found" are one error.
#
# Order matters: a UUID contains digits and an email contains a hostname, so the
# specific patterns run before the general ones.

UUID_PLACEHOLDER: Final = "<uuid>"
EMAIL_PLACEHOLDER: Final = "<email>"
HEX_PLACEHOLDER: Final = "<hex>"
NUMBER_PLACEHOLDER: Final = "<n>"

# Doubled backslashes: ClickHouse unescapes the string literal once before the
# regular expression engine sees it.
_UUID_PATTERN: Final = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_EMAIL_PATTERN: Final = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"
# Eight or more hex digits is an id, a hash or a token, never a word.
_HEX_PATTERN: Final = r"\\b[0-9a-fA-F]{8,}"
# No trailing word boundary: `45.12ms` must template as `<n>ms`, not `<n>.12ms`.
_NUMBER_PATTERN: Final = r"\\b[0-9]+(\\.[0-9]+)?"
_WHITESPACE_PATTERN: Final = r"\\s+"

# A template longer than this is a payload dump, not a message. Truncating keeps
# the group key bounded and the list readable.
MESSAGE_TEMPLATE_MAX_CHARS: Final = 512

_MESSAGE_TEMPLATE: Final = f"""
        substring(
            trim(replaceRegexpAll(replaceRegexpAll(replaceRegexpAll(replaceRegexpAll(
                replaceRegexpAll(exception_message, '{_UUID_PATTERN}', '{UUID_PLACEHOLDER}'),
                '{_EMAIL_PATTERN}', '{EMAIL_PLACEHOLDER}'),
                '{_HEX_PATTERN}', '{HEX_PLACEHOLDER}'),
                '{_NUMBER_PATTERN}', '{NUMBER_PLACEHOLDER}'),
                '{_WHITESPACE_PATTERN}', ' ')),
            1, {MESSAGE_TEMPLATE_MAX_CHARS})
"""

# The two ways a group id is derived, kept apart inside the hash so an SDK
# fingerprint can never collide with a derived one.
_SDK_NAMESPACE: Final = "sdk\\n"
_DERIVED_NAMESPACE: Final = "derived\\n"

# ------------------------------------------------------------ occurrence rows

_SPAN_COLUMNS: Final = f"""
    '{SOURCE_SPAN}' AS source,
    exception_type,
    exception_message,
    ifNull(span_attrs.{SDK_FINGERPRINT_PATH}.:String, '') AS sdk_fingerprint,
    service_name,
    toUnixTimestamp64Nano(timestamp) AS occurred_nanos,
    replaceAll(trace_id, '\\0', '') AS trace_ref,
    replaceAll(span_id, '\\0', '') AS span_ref
"""

_SPAN_ERRORS: Final = """
FROM spans
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND exception_type != ''
  AND timestamp >= {from_time:DateTime64(9)}
  AND timestamp < {to_time:DateTime64(9)}
  AND received_at >= {received_from:DateTime64(9)}
  AND received_at < {received_to:DateTime64(9)}
"""

_LOG_COLUMNS: Final = f"""
    '{SOURCE_LOG}' AS source,
    exception_type,
    exception_message,
    ifNull(log_attrs.{SDK_FINGERPRINT_PATH}.:String, '') AS sdk_fingerprint,
    service_name,
    toUnixTimestamp64Nano(timestamp) AS occurred_nanos,
    replaceAll(trace_id, '\\0', '') AS trace_ref,
    replaceAll(span_id, '\\0', '') AS span_ref
"""

_LOG_ERRORS: Final = """
FROM logs
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND exception_type != ''
  AND timestamp >= {from_time:DateTime64(9)}
  AND timestamp < {to_time:DateTime64(9)}
  AND received_at >= {received_from:DateTime64(9)}
  AND received_at < {received_to:DateTime64(9)}
"""

# The stacktrace is not a column: on a span it stays inside the `events` JSON
# and on a log record it stays in the attributes, both because it is large and
# never filtered on. It is read only for the handful of rows a detail view
# shows, which is why the list statement below does not select it.
_SPAN_DETAIL: Final = ", events AS detail_json"
_LOG_DETAIL: Final = ", toJSONString(log_attrs) AS detail_json"

_OCCURRENCES: Final = f"""
    SELECT {_SPAN_COLUMNS} {_SPAN_ERRORS}
    UNION ALL
    SELECT {_LOG_COLUMNS} {_LOG_ERRORS}
"""

_OCCURRENCES_WITH_DETAIL: Final = f"""
    SELECT {_SPAN_COLUMNS} {_SPAN_DETAIL} {_SPAN_ERRORS}
    UNION ALL
    SELECT {_LOG_COLUMNS} {_LOG_DETAIL} {_LOG_ERRORS}
"""

# ------------------------------------------------------------- the group key
#
# `occurrence_key` is what stops `logs/dup` counting every exception twice.
# Under that opt-in the SDK emits one span event and one log record for the same
# throw, both carrying the same trace and span context, so the pair is one
# occurrence. Counting distinct (trace, span) within a group collapses them.
#
# A record with no trace context — an uninstrumented logger — gets an empty key
# and is counted individually instead, because there is nothing to de-duplicate
# it against and collapsing them would report thousands of errors as one.

_GROUPING: Final = f"""
    source,
    exception_type,
    exception_message,
    service_name,
    occurred_nanos,
    trace_ref,
    span_ref,
    {_MESSAGE_TEMPLATE} AS message_template,
    sdk_fingerprint != '' AS grouped_by_sdk,
    lower(hex(sipHash64(if(
        sdk_fingerprint != '',
        concat('{_SDK_NAMESPACE}', sdk_fingerprint),
        concat('{_DERIVED_NAMESPACE}', exception_type, '\\n', message_template)
    )))) AS group_id,
    if(
        trace_ref NOT IN ('', '{ABSENT_TRACE_ID}') AND span_ref NOT IN ('', '{ABSENT_SPAN_ID}'),
        concat(trace_ref, ':', span_ref),
        ''
    ) AS occurrence_key
"""

_GROUPED: Final = f"SELECT {_GROUPING} FROM ({_OCCURRENCES})"
_GROUPED_WITH_DETAIL: Final = f"SELECT {_GROUPING}, detail_json FROM ({_OCCURRENCES_WITH_DETAIL})"

# ClickHouse's own parameter syntax is braces, so the single-group predicate is
# spliced on a comment marker rather than with str.format.
_GROUP_PLACEHOLDER: Final = "/* one group */"

ONE_GROUP_CLAUSE: Final = "HAVING group_id = {group_id:String}"

_ERROR_GROUPS_TEMPLATE: Final = f"""
SELECT
    group_id,
    max(grouped_by_sdk) AS grouped_by_sdk,
    argMax(exception_type, occurred_nanos) AS exception_type,
    argMax(message_template, occurred_nanos) AS message_template,
    argMax(exception_message, occurred_nanos) AS latest_message,
    min(occurred_nanos) AS first_seen_nanos,
    max(occurred_nanos) AS last_seen_nanos,
    uniqExactIf(occurrence_key, occurrence_key != '')
        + countIf(occurrence_key = '') AS occurrences,
    arraySort(groupUniqArray(service_name)) AS services,
    argMax(trace_ref, occurred_nanos) AS sample_trace_id,
    argMax(span_ref, occurred_nanos) AS sample_span_id,
    argMax(source, occurred_nanos) AS sample_source
FROM ({_GROUPED})
GROUP BY group_id
{_GROUP_PLACEHOLDER}
ORDER BY occurrences DESC, last_seen_nanos DESC, group_id
LIMIT {{limit:UInt32}}
"""


def error_groups_sql(*, one_group: bool) -> str:
    """The grouping statement, narrowed to a single group when one is asked for."""
    return _ERROR_GROUPS_TEMPLATE.replace(_GROUP_PLACEHOLDER, ONE_GROUP_CLAUSE if one_group else "")


# The instances behind one group, newest first, carrying what a detail view
# needs to render a stacktrace and open the trace it came from.
GROUP_OCCURRENCES_SQL: Final = f"""
SELECT
    source,
    service_name,
    occurred_nanos,
    trace_ref,
    span_ref,
    exception_type,
    exception_message,
    detail_json
FROM ({_GROUPED_WITH_DETAIL})
WHERE group_id = {{group_id:String}}
ORDER BY occurred_nanos DESC
LIMIT {{limit:UInt32}}
"""
