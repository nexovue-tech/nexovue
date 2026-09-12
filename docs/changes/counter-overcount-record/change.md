# Change: counter-overcount-record — the architecture reference denied a measured defect

## Classification

Bug (documentation)

## Current behavior and problem

Two claims in `docs/architecture/current-system-reference.md` were contradicted by measurements
recorded in decision records deleted in `e0f11a0`:

| Line | Claim | Measurement |
|---|---|---|
| 78 | Keeper-backed watermarks let workers "survive a restart **without losing or double-counting data**" | +512 spans counted twice across a ClickHouse restart, reproducible |
| 84 | replay duplicates "**are absorbed**" by block-hash deduplication or a content-derived token | 512 were not absorbed |

The evidence existed only in git history, so the document read as authoritative and was wrong on a
correctness property of the data plane — the worst place for a document to be trusted and inaccurate.

## What was actually measured

Killing Kafka and then ClickHouse under sustained load: **283,002 spans counted against 282,490
acknowledged — +512, 0.18%, with nothing lost and consumer lag at zero.** The excess is exactly
`tools/loadgen`'s batch size: one batch counted twice, not a scattering.

ADR-0061 attributed this to ClickHouse's deduplication log not surviving a restart. ADR-0075 measured
that false on the pinned `clickhouse:26.3.20.7` — an identical block re-inserted after a graceful
restart and again after `SIGKILL` was dropped both times, the sum staying at 100 rather than doubling
— and 0061's title claim was withdrawn.

0075 also showed the overcount reproducing at exactly +512 across three chaos runs including one with
the `WriteOnce` read-before-retry guard removed, which is what proved that guard was never preventing
it. The likelier cause, **untested**, is that a re-derived block is not byte-identical: a rebuilt
minute can legitimately contain late spans the first did not, changing the checksum, so deduplication
correctly declines to treat it as a repeat.

## Intended behavior

- Line 78 says the watermarks prevent loss and **bound** double-counting rather than eliminating it.
- Line 84 says duplicates are *normally* absorbed, and points at the exception.
- §18, where this document already keeps known gaps, carries the full finding: the numbers, the
  falsified premise, the untested hypothesis, and the reason it is accepted rather than chased.
- ADRs 0060, 0061 and 0075 are restored to `docs/decisions/` so the evidence lives in the repository.

**The defect is recorded, not fixed.** Founder decision, taken 2026-09-12: accept a bounded overcount
rather than chase it. `apps/workers/internal/pipeline/ingeststate/state.go:221` already states the
reasoning in code — "an undercount is a worse answer than a bounded overcount for a monitoring
product" — and nexovue is pre-deployment with no customer reading these counters.

## Affected specifications and architecture

No code changes. `current-system-reference.md` corrected; three decision records restored;
`docs/decisions/README.md` and `decision-log.md` updated to say the chain is back and the
contradiction resolved.

## Risks and rollback

The restored records assert measurements from 2026-08-27 and 2026-08-29 that were **not re-run
here**. They are labelled with their dates and the untested hypothesis is marked as untested.

The overcount remains open. If it ever grows beyond one batch per restart, or appears without a
restart, this decision should be revisited — §18 is where a future reader will find it.

Rollback is reverting one document and deleting three.

## Plan and validation

1. Restore 0060, 0061, 0075; flatten links to records still only in git.
2. Correct both claims and add the §18 entry.
3. Verify the code citation in the §18 entry actually says what it is quoted as saying.
4. Update the decision index and README.
5. Run `just check`; verify every link resolves.

## Status

See [status.md](status.md).
