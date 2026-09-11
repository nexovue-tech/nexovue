"""The ClickHouse reads behind log↔trace correlation.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

**What makes this cheap.** `logs` is ordered by
`(tenant_id, project_id, service_name, severity_number, timestamp)`, so a trace
id is not in the sort key and cannot narrow the scan by itself. Two things do:
the bloom filter on `trace_id` skips granules, and the `received_at` bounds skip
whole partitions — `logs` is partitioned on our receive clock, not the
customer's `timestamp`, so a bound on `timestamp` prunes nothing.

The service derives those bounds from `span_trace_lookup`, which answers "when
did trace X reach us" without touching `spans`, and widens them by
`window.RECEIVE_CLOCK_SLACK`. The slack is not decoration: spans and logs travel
independent pipelines — tail sampling holds spans, a log exporter retries — so
neither signal's arrival time bounds the other's.

There is deliberately no predicate on `timestamp`. It would prune nothing here,
and a customer clock far enough out to fall outside a derived window would
silently drop the very logs the caller asked for.
"""

from __future__ import annotations

from typing import Final

LOGS_FOR_TRACE_SQL: Final = """
SELECT
    toUnixTimestamp64Nano(timestamp) AS observed_nanos,
    service_name,
    severity,
    severity_number,
    body,
    replaceAll(trace_id, '\\0', '') AS trace_ref,
    replaceAll(span_id, '\\0', '') AS span_ref,
    toJSONString(log_attrs) AS attributes_json
FROM logs
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND trace_id = {trace_id:String}
  AND received_at >= {received_from:DateTime64(9)}
  AND received_at < {received_to:DateTime64(9)}
ORDER BY timestamp, severity_number
LIMIT {limit:UInt32}
"""
