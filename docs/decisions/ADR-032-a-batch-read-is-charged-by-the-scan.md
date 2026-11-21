# ADR-032: A batch read is charged by the scan

## Status

Accepted 2026-11-02 by the founder with the F20 design ([`design.md`](../features/dashboards/design.md),
*Proposed ADRs*, the second; spec D2, D25, D26, D27, D42, D43); filed at implementation, 2026-11-08 (F20 plan
Step 2, T2.3 and T2.10 to T2.12).

## Context

The rate limiter counts HTTP requests (`middleware/ratelimit.py`), which was true to cost only while one
request meant about one scan (`ratelimit/policy.py`). A dashboard reads 30 panels in one request. Counted as
one, a batch walks around every limit that protects ClickHouse; split into 30 requests, it spends a person's
quota at a 10-second refresh. The first route to break the rule sets the pattern for any later one.

## Decision

A route that runs more than one scan in a request:

1. **merges equal scans** first, and shares a scan's `ok` answer for 30 seconds inside one process, keyed
   by tenant, project, reader, arguments and window, so a team watching one board costs one read
   (`dashboardpanels/shared_reads.py`);
2. **charges each scan the store really runs** through `ratelimit/charge.py`: the middleware leaves the
   buckets it counted the request in, and the request's arrival, in `scope["state"]`; the first scan rides
   on the request's own quota place, and each later one spends a place in every bucket, by the
   middleware's own keys. A kept or joined answer costs nothing. No slot is taken for later scans;
3. **holds what runs at once** with the batch's own permits (4), a process cap (5 scans, 2 raw; 3 and 1 for
   one organization where more than one can exist), and a fixed statement limit sent by
   `TenantScopedRepository.bounded_by`, with ClickHouse told to cancel a read whose client has gone;
4. **reports refusal per item**, never as one: a panel reads `refused` with a real wait, `timed_out` past
   the deadline counted from arrival, or `failed`, and the request still answers 200.

## Consequences

- The limits keep meaning what they say, and the batch is paid for by its reader, not its author.
- Some of the limiter's knowledge is used inside a route. The middleware cannot see scan counts.
- Every number in it is a guess until F20's plan T5.7 measures it, and the arithmetic holds at two API
  processes only: `test_ratelimit_coverage.py` fails on purpose at three.
- Kept answers live in process memory, bounded by rows, like the limiter's own counts. At two processes the
  work is at most doubled. No Redis (ADR-020).

## Rejected

Counting a batch as one request (it hides real load), raising the telemetry quota (it removes the
protection for everyone), and a slot per later scan (it adds a second refusal path and a third wait for a
case the process cap already holds; it comes back only if T5.7 finds what the cap misses).
