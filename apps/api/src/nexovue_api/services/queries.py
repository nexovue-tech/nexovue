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
after it (ADR 0010). Until 000024 a materialized view over `spans` filled it
instead, which put it *downstream* of tail sampling — so these statements
returned about a tenth of the real request rate at about ten times the real
error rate, because the sampler keeps every failed and every slow trace but
only one healthy trace in ten. Nothing here changed to fix that; the source
did.

**Partition pruning.** Unlike `spans`, this table is partitioned on `minute` —
the bucket itself — so a bound on `minute` prunes directly and there is no
receive-clock window to widen. ADR 0067 applies to the raw signals, which are
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

# ClickHouse's own parameter syntax is braces, so the single-service predicate is
# spliced on a comment marker rather than with str.format.
_ONE_SERVICE_PLACEHOLDER: Final = "/* one service */"

ONE_SERVICE_CLAUSE: Final = "AND service_name = {service_name:String}"

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
GROUP BY service_name
ORDER BY {WORST_FIRST}, service_name
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
GROUP BY service_name, span_kind, span_name
ORDER BY {WORST_FIRST}, service_name, span_kind, span_name
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


def _narrowed(template: str, *, one_service: bool) -> str:
    return template.replace(_ONE_SERVICE_PLACEHOLDER, ONE_SERVICE_CLAUSE if one_service else "")


def services_sql(*, one_service: bool) -> str:
    """The service statement, narrowed to one service when one is named."""
    return _narrowed(_SERVICES_TEMPLATE, one_service=one_service)


def trend_sql(*, one_service: bool) -> str:
    """The trend statement, narrowed to one service when one is named."""
    return _narrowed(TREND_SQL, one_service=one_service)


def endpoints_sql(*, one_service: bool) -> str:
    """The endpoint statement, narrowed to one service when one is named."""
    return _narrowed(_ENDPOINTS_TEMPLATE, one_service=one_service)
