# 25. Phase 1 signal, category and door coverage

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Phase 1 needed a hard line between what ships and what waits, across four doors, several signal
types and the full category list from the market analysis.

## Decision

**All four doors open in Phase 1.**

Signals: traces, logs, application and infrastructure metrics, deploy events, synthetic results.
Profiles and session replay defer to Phase 2.

Categories covered: synthetic, error tracking, APM, logs, RUM, infrastructure, database, **plus
service map** (F9 — derived edge aggregation over spans, no new collection).

Kubernetes telemetry is ingested through door 1 and is queryable, but pod/node/namespace screens
are Phase 2. The UI is fixed screens per signal — no dashboard builder (F20 stays Phase 2).

The object-storage cold tier is built in Phase 1 rather than letting the 30-day TTL delete raw data
(MinIO plus ClickHouse-native S3 tiering at the time of writing). Source-map symbolication is added
to door 3, without which RUM errors are unusable.

Network monitoring (eBPF) and SIEM remain unplaced — open.

## Consequences

Phase 1 is broad rather than deep: every door works, but several categories ship with fixed screens
and no drill-down. Building the cold tier early costs Phase 1 time but means no telemetry is ever
irrecoverably deleted while we are still learning what we need.
