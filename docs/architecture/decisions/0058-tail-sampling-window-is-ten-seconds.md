# 0058 — The tail-sampling window is ten seconds, and the lag budget stands

- **Status:** Accepted
- **Fulfils:** [0054](0054-spans-written-through-until-tail-sampling.md), which deferred this decision to P1-21
- **Date:** 2026-08-27

## Context

[0054](0054-spans-written-through-until-tail-sampling.md) removed [0009](0009-trace-assembly-in-worker-memory.md)'s
~30 second buffer for M1, on the grounds that its only real job was letting tail sampling see a
whole trace, and tail sampling was still a milestone away. It recorded that the buffer returns with
P1-21, and that its window would then be re-decided as an explicit trade of ingestion lag against
sampling accuracy rather than inherited.

That trade is now live. Deciding whether to keep a trace requires having seen it, so the window is a
floor under the *span → queryable lag* that §4 budgets at p99 < 15s. M1 measured that lag at
0.65–2.2s with no buffer at all.

## Decision

**Ten seconds.** With batching, end-to-end lag lands around twelve — inside the existing budget, so
§4 does not move and stays usable as a regression gate.

Spans arriving after their trace's window closes are written individually and joined at query time,
exactly as [0009](0009-trace-assembly-in-worker-memory.md) described.

The buffer is bounded in memory explicitly. An unbounded map on the hot path is an outage waiting
for a traffic spike, and the spike is precisely when the platform must not fall over.

## Consequences

The lag budget survives contact with the feature that threatened it, which matters more than the
ten seconds: a budget revised upward every time something makes it inconvenient is not a budget.
Ten seconds also comfortably covers the overwhelming majority of traces, which complete in well
under a second.

Accepted: traces genuinely longer than ten seconds get their sampling decision on partial evidence,
and their late spans are stored unsampled. That biases storage slightly toward keeping long traces —
which is the harmless direction, since a trace that takes ten seconds is usually one worth looking
at.

A worker restart still loses its in-flight buffer. That was true of [0009](0009-trace-assembly-in-worker-memory.md)'s
design too and the query-time join covers it; the exposure is now ten seconds of traces rather than
thirty.
