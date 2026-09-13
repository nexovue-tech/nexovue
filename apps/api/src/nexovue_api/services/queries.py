"""The ClickHouse reads behind the service overview.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**One table, read two ways.** `endpoint_stats_1m` holds one row per
(service, span kind, span name, minute), already aggregated. Grouping it by
service answers "how is each service doing"; grouping it by endpoint answers
"which endpoint is hurting". Both read the same counters, so the two can never
disagree.

**The rows are counted before sampling.** The trace pipeline's `derive` stage
fills this table itself, over every span of every trace, and the sampler runs
after it. Until 000024 a materialized view over `spans` filled it
instead, which put it *downstream* of tail sampling — so these statements
returned about a tenth of the real request rate at about ten times the real
error rate, because the sampler keeps every failed and every slow trace but
only one healthy trace in ten. Nothing here changed to fix that; the source
did.

**Partition pruning.** Unlike `spans`, this table is partitioned on `minute` —
the bucket itself — so a bound on `minute` prunes directly and there is no
receive-clock window to widen. The widening applies to the raw signals, which are
partitioned on our clock and queried on the customer's; a rollup keyed on the
bucket has only one clock.

**The counters are pre-aggregated.** `request_count` and `error_count` are
`SimpleAggregateFunction(sum, …)`, so they are summed. `latency_quantiles` is an
`AggregateFunction(quantiles(…), Float64)`: it is a state, not a number, and
reading it without `quantilesMerge` returns an unreadable blob rather than a
latency.
"""

from __future__ import annotations

from typing import Final

from nexovue_api import rollup

# Bound to module constants because the statements below interpolate them, and
# only a name of this module may be spliced into SQL here.
QUANTILE_ARGUMENTS: Final = rollup.QUANTILE_ARGUMENTS
P95_INDEX: Final = rollup.P95_INDEX

# The stored state, merged across every minute in the group and across the parts
# the AggregatingMergeTree has not merged for itself yet.
_LATENCY_MS: Final = f"quantilesMerge({QUANTILE_ARGUMENTS})(latency_quantiles) AS latency_ms"

# Worst first, and this ordering is the product: the overview screen is read
# top-down and nobody scrolls.
#
# Failed requests lead, in absolute count rather than as a rate, because harm is
# what a person acts on — an endpoint failing 5% of 200,000 calls has hurt more
# people than one failing 100% of two. The slow tail breaks ties, then volume,
# so a busy endpoint outranks a quiet one that is equally broken.
WORST_FIRST: Final = f"error_count DESC, latency_ms[{P95_INDEX}] DESC, request_count DESC"

# The other question this screen is asked: not "what is broken" but "what is
# slow". The same three measures, with the slow tail leading.
#
# **It is an ordering, so it has to be the store's.** `LIMIT` keeps the first
# hundred rows of whichever order the statement used, and re-sorting those in
# the browser answers "the slowest among the hundred with the most errors" — a
# service that is the slowest in the project and fails for nobody is exactly the
# row that ordering cannot reach.
SLOWEST_FIRST: Final = f"latency_ms[{P95_INDEX}] DESC, error_count DESC, request_count DESC"

_ORDERINGS: Final = {"worst": WORST_FIRST, "slowest": SLOWEST_FIRST}
_ORDER_PLACEHOLDER: Final = "/* ordering */"

# The source and its window, kept in one literal: the tenant predicate has to
# sit in the same string as the table it guards, or the structure test cannot
# see that the table is guarded.
_ENDPOINT_STATS_IN_WINDOW: Final = """
FROM endpoint_stats_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND minute >= {from_time:DateTime}
  AND minute < {to_time:DateTime}
"""

# ClickHouse's own parameter syntax is braces, so the optional predicates are
# spliced on comment markers rather than with str.format.
_ONE_SERVICE_PLACEHOLDER: Final = "/* one service */"
_SPAN_KIND_PLACEHOLDER: Final = "/* span kind */"
_WITH_ERRORS_PLACEHOLDER: Final = "/* with errors */"

ONE_SERVICE_CLAUSE: Final = "AND service_name = {service_name:String}"

# "HTTP, not queues" — the rollup keeps SERVER and CONSUMER rows apart, so this
# narrows which rows are aggregated rather than which totals are shown. A
# service that only consumes a queue disappears from a SERVER-only answer, which
# is the question being asked.
SPAN_KIND_CLAUSE: Final = "AND span_kind = {span_kind:String}"

# "Only the ones with errors", applied after the group because `error_count` is
# a sum across the window: a service that failed once in an hour has errors even
# though most of its minutes did not.
WITH_ERRORS_CLAUSE: Final = "HAVING error_count > 0"

# One service's row and the whole ranking are the same read: the detail screen shows
# exactly the numbers its row on the overview shows, so they cannot disagree.
_SERVICES_TEMPLATE: Final = f"""
SELECT
    service_name,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_ENDPOINT_STATS_IN_WINDOW}
  {_ONE_SERVICE_PLACEHOLDER}
  {_SPAN_KIND_PLACEHOLDER}
GROUP BY service_name
{_WITH_ERRORS_PLACEHOLDER}
ORDER BY {_ORDER_PLACEHOLDER}, service_name
LIMIT {{limit:UInt32}}
"""

# An endpoint is (service, kind, name). `span_kind` stays in the key because the
# rollup holds SERVER and CONSUMER rows: an HTTP route and a queue handler can
# share a name and are not the same endpoint.
_ENDPOINTS_TEMPLATE: Final = f"""
SELECT
    service_name,
    span_kind,
    span_name,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_ENDPOINT_STATS_IN_WINDOW}
  {_ONE_SERVICE_PLACEHOLDER}
  {_SPAN_KIND_PLACEHOLDER}
GROUP BY service_name, span_kind, span_name
{_WITH_ERRORS_PLACEHOLDER}
ORDER BY {_ORDER_PLACEHOLDER}, service_name, span_kind, span_name
LIMIT {{limit:UInt32}}
"""


# The trend behind each service's row. Same source and same window clause as the
# aggregate above — the difference is that the minutes are kept rather than
# collapsed, which is all a sparkline needs.
#
# The bucket is a parameter rather than a fixed minute: an hour of minutes is 60
# points, a week is over 10,000, and a sparkline drawn from 10,000 points is a
# smear that costs a large response to produce. The caller picks a bucket that
# bounds the count; `trend_bucket_seconds` below is that arithmetic.
TREND_SQL: Final = f"""
SELECT
    service_name,
    toStartOfInterval(minute, INTERVAL {{bucket_seconds:UInt32}} SECOND) AS bucket,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_ENDPOINT_STATS_IN_WINDOW}
  {_ONE_SERVICE_PLACEHOLDER}
GROUP BY service_name, bucket
ORDER BY service_name, bucket
"""


def _narrowed(
    template: str,
    *,
    one_service: bool,
    span_kind: bool = False,
    with_errors: bool = False,
    order: str = "worst",
) -> str:
    """The statement with each optional predicate either spliced in or removed.

    Every filter is a predicate inside the statement, never a list trimmed after
    it comes back. `LIMIT` caps the ranking, so a filter applied afterwards would
    answer "the failing ones among the first hundred" and have no way to say so.
    """
    return (
        template.replace(_ONE_SERVICE_PLACEHOLDER, ONE_SERVICE_CLAUSE if one_service else "")
        .replace(_SPAN_KIND_PLACEHOLDER, SPAN_KIND_CLAUSE if span_kind else "")
        .replace(_WITH_ERRORS_PLACEHOLDER, WITH_ERRORS_CLAUSE if with_errors else "")
        .replace(_ORDER_PLACEHOLDER, _ORDERINGS[order])
    )


def services_sql(
    *,
    one_service: bool,
    span_kind: bool = False,
    with_errors: bool = False,
    order: str = "worst",
) -> str:
    """The service statement, narrowed to what the caller asked for."""
    return _narrowed(
        _SERVICES_TEMPLATE,
        one_service=one_service,
        span_kind=span_kind,
        with_errors=with_errors,
        order=order,
    )


def trend_sql(*, one_service: bool) -> str:
    """The trend statement, narrowed to one service when one is named.

    Ordered by service and bucket, never by the ranking: it draws the history of
    rows the ranking already chose.
    """
    return _narrowed(TREND_SQL, one_service=one_service)


def endpoints_sql(
    *,
    one_service: bool,
    span_kind: bool = False,
    with_errors: bool = False,
    order: str = "worst",
) -> str:
    """The endpoint statement, narrowed to what the caller asked for."""
    return _narrowed(
        _ENDPOINTS_TEMPLATE,
        one_service=one_service,
        span_kind=span_kind,
        with_errors=with_errors,
        order=order,
    )


# The whole project over the window, and across it.
#
# **Grouped by nothing and by bucket alone.** A project's p95 is not the mean of
# its services' p95s and cannot be recovered from them — that is what storing
# `AggregateFunction(quantiles(…))` rather than three numbers is *for*, and
# `quantilesMerge` over every row in the window is the merge that answers it.
# A client summing the service list would get the rate roughly right, the error
# rate roughly right, and the latency wrong in a way nothing on the screen could
# reveal.
#
# It also cannot be summed from that list for a duller reason: the list is
# capped, so its total is the total of the first hundred services.
OVERVIEW_SQL: Final = f"""
SELECT
    uniqExact(service_name) AS service_count,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_ENDPOINT_STATS_IN_WINDOW}
"""

OVERVIEW_TREND_SQL: Final = f"""
SELECT
    toStartOfInterval(minute, INTERVAL {{bucket_seconds:UInt32}} SECOND) AS bucket,
    sum(request_count) AS request_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_ENDPOINT_STATS_IN_WINDOW}
GROUP BY bucket
ORDER BY bucket
"""
