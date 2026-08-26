# 29. Confirmed by research: franz-go, uv, uPlot, Biome, Celery

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Five earlier choices were made on reputation rather than current evidence. Each was re-checked.

## Decision

All five stand.

- **franz-go** — feature-complete through Kafka 4.2+ and pure Go. `confluent-kafka-go`'s
  librdkafka cgo dependency complicates static builds.
- **uv** — has overtaken Poetry in downloads, with Astral/OpenAI backing.
- **uPlot** — still the time-series specialist; ECharts degrades past roughly 500K–1M points.
- **Biome** — 2.2's `noRestrictedImports` patterns plus `noImportCycle` cover our import-boundary
  rules, so no second linter is needed (dependency-cruiser stays the fallback).
- **Celery** — kept despite async-native queues (Taskiq, FastStream, Dramatiq) outperforming it on
  I/O-bound work: our probe volume is per-minute, not per-second, and Playwright checks suit a
  process-based model.

## Consequences

Five decisions now rest on current evidence rather than familiarity, and the rejected alternatives
are named so the question does not get reopened from scratch. One gap surfaced: the service map
needs a **graph** library, which none of these cover — picked in
[0031](0031-service-map-rendering-with-react-flow.md).
