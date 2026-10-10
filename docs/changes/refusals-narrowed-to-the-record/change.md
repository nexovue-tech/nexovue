# Change: refusals-narrowed-to-the-record — logs, metrics and profiles park only what was refused

## Classification

Bug — a refusal costs records that were never refused.

## Current behavior and problem

Since `ingest-cannot-be-knocked-over` (`455410b`), a row ClickHouse refuses no longer stops ingest.
How much it costs depends on the pipeline:

- **Spans** split the refused block and retry each half, down to the one refused row, which alone is
  parked (`traceassembly/refusal.go`, `salvageRows`).
- **Logs, metrics and profiles** park **every record in the refused chunk**
  (`consume.ParkRefused`, called from `logwriting/pipeline.go`, `metricwriting/metricwriting.go`,
  `profilewriting/profilewriting.go`). A chunk holds up to `maxRows` rows from many records and
  tenants.

Observed live on 2026-10-10: one poisoned metric data point parked **9** messages on `metrics-dlq` —
the poisoned one and 8 healthy messages from the development host-metrics agent. They are kept, not
lost, but no replay tool exists, so in practice a healthy tenant's telemetry vanishes from its screens
because of another tenant's bad record.

## Intended behavior

A refusal in any pipeline parks only the records whose own rows the store refuses; every other record
in the chunk is stored.

## Affected specifications and architecture

None changed. ADR-002 (at-least-once) holds: nothing is acknowledged before it is stored or parked.

## Risks and rollback

See `plan.md`. Rollback is reverting the commit; no schema, topic or data change.

## Plan and validation

See [`plan.md`](plan.md).

## Status

See [`status.md`](status.md).
