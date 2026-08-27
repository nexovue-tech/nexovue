# 0054 — Spans are written through; the assembly buffer arrives with tail sampling

- **Status:** Accepted
- **Supersedes in part:** [0009](0009-trace-assembly-in-worker-memory.md) — the ~30s wait window only. Kafka partitioning by `trace_id` stays in force.
- **Date:** 2026-08-27

## Context

[0009](0009-trace-assembly-in-worker-memory.md) specified a ~30s in-memory window before a trace is
written. Planning M1 found that this cannot coexist with the §4 budget *span → queryable lag p99 <
15s*: holding a trace for 30 seconds puts a floor of 30 seconds under the lag it is measured by.

Reading [0010](0010-tail-sampling-defaults.md) shows what the buffer is actually for. Deciding
"keep this trace, it errored" requires having seen the whole trace, and that is the only thing in
the pipeline that needs the trace assembled *before* the write. Query-time joining does not — spans
of one trace are rows sharing a `trace_id`, and ClickHouse joins them on read. Tail sampling is
P1-21, in M2.

ClickHouse's own observability schema guidance writes spans immediately and derives trace-level
information afterwards, serving trace-by-id through a companion lookup table rather than a buffer.

## Decision

**M1 writes spans straight through.** The `trace-assembly` pipeline consumes, decodes, flattens and
batch-inserts, with no wait window. It commits offsets only after the insert returns — at-least-once,
which is safe because re-inserting an identical span row changes nothing.

**Trace-by-id is served by a lookup table, not a buffer.** ClickHouse migration `000004` adds a
companion table holding, per `(tenant_id, trace_id)`, the minimum and maximum span timestamp,
maintained by a materialized view over `spans`. The query API reads it first to bound the scan.
Without it, `WHERE trace_id = ?` has no time bound — `spans` is ordered
`(tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)` — and leans entirely on
the bloom-filter index across every partition in the retention range.

The buffer is built with **P1-21**, where its window is re-decided as an explicit trade of ingestion
lag against sampling accuracy, and recorded then.

## Consequences

M1 gets a simpler worker and a lag budget met with room to spare, and the walking skeleton stops
carrying machinery nothing yet uses. The pipeline keeps its name, so introducing the buffer later is
an addition rather than a move — [0050](0050-one-worker-process-pipelines-by-config.md) makes those
names load-bearing precisely so this stays true.

The cost is that the buffer's real cost is deferred, not avoided: when P1-21 lands, a wait window
returns and the lag budget must be revisited honestly rather than quietly. Recording that here is
the point — the budget in §4 was written against a pipeline that did not yet exist, and the number
it holds today is not the number it will hold under tail sampling.

The lookup table is a second write per span batch and a materialized view to maintain. Accepted:
it is small, and it makes a pasted or bookmarked trace URL as fast as one clicked from the list —
the slow path would otherwise be the one a user hits sharing a link with a colleague.
