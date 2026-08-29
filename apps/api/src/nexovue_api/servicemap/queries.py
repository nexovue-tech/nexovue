"""The ClickHouse read behind the service map.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

`service_edges_1m` holds one row per (caller, callee, edge kind, minute), written
by the trace pipeline before the sampler — so the call rate is the real one, not
a tenth of it. It is shaped exactly like `endpoint_stats_1m`: summed counters and
one `quantiles(…)` state, partitioned on `minute`, so a bound on `minute` prunes
directly and the latency has to be merged rather than read.

The latency on an edge is the *caller's* view — measured on the client span, so
it includes the network. The callee's own view of the same work is in
`endpoint_stats_1m`, and the gap between them is where a network problem shows.
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

SERVICE_EDGES_SQL: Final = f"""
SELECT
    client_service,
    server_service,
    edge_kind,
    sum(call_count) AS call_count,
    sum(error_count) AS error_count,
    {_LATENCY_MS}
{_SERVICE_EDGES_IN_WINDOW}
GROUP BY client_service, server_service, edge_kind
ORDER BY {BUSIEST_FIRST}, client_service, server_service, edge_kind
LIMIT {{limit:UInt32}}
"""
