"""The ClickHouse read behind the service map.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

`service_edges_1m` holds one row per (caller, callee, edge kind, minute), written
by the trace pipeline before the sampler — so the call rate is the real one, not
a tenth of it. It is shaped exactly like `endpoint_stats_1m`: summed counters and
one `quantiles(…)` state, partitioned on `minute`, so a bound on `minute` prunes
directly and the latency has to be merged rather than read.

The latency on an edge is the *caller's* view — measured on the caller's own
span. On a `call` edge that is the request, network included; the callee's own
view of the same work is in `endpoint_stats_1m`, and the gap between them is
where a network problem shows. On a `queue` edge it is the publish, not the
delivery lag: the producer and the consumer run on different machines, and the
gap between their clocks is not something this system trusts.
"""

from __future__ import annotations

from typing import Final

from nexovue_api import rollup

# Bound to a module constant because the statement below interpolates it, and
# only a name of this module may be spliced into SQL here.
QUANTILE_ARGUMENTS: Final = rollup.QUANTILE_ARGUMENTS

_LATENCY_MS: Final = f"quantilesMerge({QUANTILE_ARGUMENTS})(latency_quantiles) AS latency_ms"

# Busiest first, unlike the endpoint ranking, because a graph truncated by the
# limit must keep its backbone: an edge carrying a thousand calls an hour is the
# dependency, and one carrying two is noise even when both of the two failed.
# Every edge carries its own error rate, so a client can re-rank without asking
# again.
BUSIEST_FIRST: Final = "call_count DESC, error_count DESC"

# The source and its window, kept in one literal: the tenant predicate has to
# sit in the same string as the table it guards, or the structure test cannot
# see that the table is guarded.
_SERVICE_EDGES_IN_WINDOW: Final = """
FROM service_edges_1m
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND minute >= {from_time:DateTime}
  AND minute < {to_time:DateTime}
"""

# `server_peer_kind` is the technology a callee that never reported for itself
# was reached over, and it is empty on every edge whose callee did. It is not
# grouped on: one callee is reached more than one way — `cart` calls `flagd`
# over both plain HTTP and gRPC — and grouping would split one dependency into
# two edges the graph would have to merge back. `max` picks one, which puts a
# named technology ahead of the empty string and gives the same answer twice.
_PEER_KIND: Final = "max(server_peer_kind) AS server_peer_kind"

# ClickHouse's parameter syntax is braces, so the optional predicates are
# spliced on a comment marker rather than with str.format. Both are `HAVING`:
# they test sums, which do not exist until the group does.
_EDGE_FILTERS_PLACEHOLDER: Final = "/* edge filters */"

# "Hide low-volume links". The caller asks in calls per minute because that is
# what the edge is labelled with; the stored column is a count, so the service
# multiplies by the window it is reading and binds the floor as a count. Asking
# the reader to think in "calls per window" instead would move the arithmetic
# from here to their head.
MIN_CALL_COUNT_CLAUSE: Final = "sum(call_count) >= {min_call_count:UInt64}"

# "Only failing edges" — one failure in the window is enough to be worth seeing.
FAILING_ONLY_CLAUSE: Final = "sum(error_count) > 0"

_EDGE_FILTER_CLAUSES: Final = {
    "min_call_count": MIN_CALL_COUNT_CLAUSE,
    "failing_only": FAILING_ONLY_CLAUSE,
}

SERVICE_EDGES_SQL: Final = f"""
SELECT
    client_service,
    server_service,
    edge_kind,
    {_PEER_KIND},
    sum(call_count) AS call_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_SERVICE_EDGES_IN_WINDOW}
GROUP BY client_service, server_service, edge_kind
{_EDGE_FILTERS_PLACEHOLDER}
ORDER BY {BUSIEST_FIRST}, client_service, server_service, edge_kind
LIMIT {{limit:UInt32}}
"""


def service_edges_sql(*, min_call_count: bool, failing_only: bool) -> str:
    """The edge statement, narrowed to what the caller asked for.

    The predicates go inside the statement rather than over what it returns:
    `LIMIT` caps the graph at a thousand edges, so a filter applied afterwards
    would answer "the failing edges among the busiest thousand" with no way to
    say so.
    """
    wanted = {"min_call_count": min_call_count, "failing_only": failing_only}
    applied = [clause for name, clause in _EDGE_FILTER_CLAUSES.items() if wanted[name]]
    having = f"HAVING {' AND '.join(applied)}" if applied else ""
    return SERVICE_EDGES_SQL.replace(_EDGE_FILTERS_PLACEHOLDER, having)
