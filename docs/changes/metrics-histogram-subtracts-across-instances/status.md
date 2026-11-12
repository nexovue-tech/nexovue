# Status: metrics-histogram-subtracts-across-instances

## Lifecycle status

**Recorded, not started.** Found by F20 Step 8 (plan T8.1), which records it and does not fix it.

## Current task

None.

## Completed

- The record (`bug.md`) and its two strict `xfail` cases, 2026-11-12.

## Next action

Founder: decide when to schedule it.

## Open decisions

Whether a series' instance is read from the resource attributes per row or from a new
materialized column (a ClickHouse migration), after measuring both.

## Blocked

Nothing.

## Risks

Until fixed, the metrics screen's histograms and every service's latency heatmap are wrong for
any service running more than one instance.
