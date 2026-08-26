# 10. Tail sampling defaults

- **Status:** Accepted
- **Date:** 2026-08

## Context

Storing every healthy trace is expensive and adds nothing — the interesting traces are the ones
that failed or were slow. But sampling storage must not sample the numbers, or every dashboard and
anomaly detector starts lying.

## Decision

Keep 100% of error traces, 100% of slow traces (a fixed threshold first, per-endpoint baseline
later), and 10% of healthy traces. Tunable per tenant.

**Metrics are always derived from ALL traces, before sampling.**

## Consequences

Storage cost drops by roughly an order of magnitude while the traces anyone actually opens are
still there. Counts, error rates and percentiles remain exact because they are computed upstream of
the sampler — the cost is that metric derivation must happen in the worker before the sampling
decision, which fixes the order of the pipeline.
