# 0060 — Counter idempotency rests on ClickHouse's own insert deduplication

- **Status:** Accepted — [superseded in part by 0061](0061-counter-overcount-across-a-store-restart.md): it does not survive a ClickHouse restart, as this ADR predicted it might
- **Date:** 2026-08-27

## Context

Invariant 3 says counts derived before sampling stay exact. M2 built that path and then found a
hole in it: offsets commit after the counters are written, so a worker crash *between* the write and
the commit leaves the minute already stored and its records still uncommitted. On restart the worker
resumes from the last commit, re-counts the same spans, and writes the minute a second time.

This is not theoretical. During M2's verification, worker restarts produced 15–88 duplicated rows
per minute in `spans` — harmless there, because a repeated span row changes nothing — and
correspondingly inflated `nexovue.spans.count`, which is not harmless at all.

In-process deduplication by `(topic, partition, offset)` already covers redelivery inside one
process: a retry, a poll replay, a rebalance returning a partition. The watermark dies with the
process, which is exactly the case that matters.

Two candidates: open ClickHouse's insert-deduplication window on `metrics`, or checkpoint the
per-partition watermark somewhere durable and seed it on partition assignment.

## Decision

**ClickHouse's own insert deduplication**, with `non_replicated_deduplication_window = 1000` on
`metrics` (migration `000010`).

ClickHouse hashes an inserted block and drops one it has already seen. No token is needed — a token
would only be required to make *different* data deduplicate, which is the opposite of what we want.
The setting defaults to **0**, so a plain MergeTree deduplicates nothing until the window is opened;
that default is the whole reason this was silently broken.

This works only if the re-derived block is **byte-identical**, and it was not: Go randomises map
iteration, so `derive.Drain` returned the same counts in a different order every call and every
replay produced a block ClickHouse could not recognise. `Drain` now sorts its points on a total
order, guarded by a test that builds the same minute twenty-five times and compares byte for byte.

The durable-watermark alternative is rejected for now as more machinery than the problem needs: it
means a second store on the write path, or a schema in ClickHouse read on every partition
assignment, to solve what the database already solves.

## Consequences

Verified rather than assumed: three identical inserts collapse to one row, and a genuinely different
value still lands. The fix costs one table setting and a sort.

**Two limits, recorded rather than hidden.** The deduplication log is bounded — if more than 1000
other inserts occur during a replay, the window has rolled past and the duplicate lands. At the
per-minute flush rate that is many hours of margin, but it is a window, not a guarantee.

And the log's persistence across a **ClickHouse** restart is not documented, so this is proven only
for the case that actually happens — the worker restarting while the store stays up. P1-27's chaos
test kills both, which makes it the right place to find out; if deduplication does not survive
there, the durable watermark returns as the answer and this ADR gets superseded by evidence rather
than by argument.

`spans` deliberately keeps no deduplication window. A repeated span row is idempotent by nature, and
opening a window on the hot write path would cost a hash comparison per batch to prevent nothing.
