# 45. Envelope contract frozen

- **Status:** Accepted
- **Supersedes in part:** [0030](0030-envelope-as-protobuf-wrapper-around-otlp.md) — its "forward the same bytes" description, for traces
- **Date:** 2026-08-26

## Context

[0024](0024-frozen-internal-envelope-contract.md) required the envelope to be frozen before the
first door; [0030](0030-envelope-as-protobuf-wrapper-around-otlp.md) chose its form. Writing it out
exposed an error in the earlier description.

## Decision

Fields: `tenant_id`, `project_id`, `door`, `signal`, `received_at`, `ingest_id`, `semconv_version`,
and an unmodified OTLP payload. Full contract in `../ingestion-envelope.md`.

**Correction to the earlier "forward the same bytes" description:** it holds for logs and metrics
but *not* for traces. One `ExportTraceServiceRequest` can carry spans from many traces, and trace
assembly depends on Kafka partitioning by `trace_id`
([0009](0009-trace-assembly-in-worker-memory.md)) — so the receiver must **split trace batches into
one envelope per `trace_id`** and re-encode.

This is what the OTel collector's `loadbalancing` exporter does, so it is the established pattern.

## Consequences

Splitting is real CPU on the hot path, so it now carries its own performance budget: under 20% of
receiver CPU, measured at M1. If it proves too costly at the edge, splitting moves to a worker
between two topics — the envelope shape is unaffected, which is exactly the point of freezing it
first.
