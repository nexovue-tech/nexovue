# The Ingestion Envelope (P1-07)

> **Type:** Frozen contract · **Status:** Designed 2026-08-26, not yet implemented
> **Lives in:** `packages/contracts/proto/nexovue/v1/envelope.proto`
> **Companion docs:** `tenancy-and-data-model.md` (where the ids come from) · `system-architecture.md` (the pipeline)

## Why this is the most expensive thing to change

Two producers write it — the receiver (Go) and the scheduler (Python). Four workers read it. It sits on the hot path at thousands of messages per second. Once real telemetry is flowing, changing its shape means changing six programs simultaneously, so it is designed and frozen **before** the first door is built.

## The contract

```proto
syntax = "proto3";
package nexovue.v1;

enum Door {
  DOOR_UNSPECIFIED = 0; DOOR_OTLP = 1; DOOR_SYNTHETIC = 2;
  DOOR_POLLER = 3; DOOR_RUM = 4; DOOR_WEBHOOK = 5;
}
enum Signal {
  SIGNAL_UNSPECIFIED = 0; SIGNAL_SPANS = 1; SIGNAL_LOGS = 2; SIGNAL_METRICS = 3;
}

message Envelope {
  string tenant_id             = 1;  // organization id (UUIDv7, canonical string)
  string project_id            = 2;
  Door   door                  = 3;  // which entrance produced this
  Signal signal                = 4;  // redundant with the topic; kept for validation and DLQ triage
  int64  received_at_unix_nano = 5;  // when WE received it, not when it happened
  string ingest_id             = 6;  // UUIDv7 — pipeline tracing and DLQ correlation
  string semconv_version       = 7;  // which OTel convention generation the payload speaks
  bytes  payload               = 8;  // OTLP protobuf, unmodified
}
```

> **Enum values carry their enum's prefix**, corrected 2026-08-26 after `buf lint` rejected the original unprefixed form. This is not cosmetic: proto3 enum values live in the **enclosing package** scope, not the enum's, so `OTLP` and `SYNTHETIC` would become package-level identifiers in `nexovue.v1` and a third enum added later could collide — on a contract whose whole premise is that it is never renumbered. Silencing the rule was the alternative, and that is the kind of workaround this project does not take.

## Why the payload is OTLP, not our own schema

OpenTelemetry already designed protobuf messages for spans, logs and metrics, and it is what customer SDKs emit. Defining our own would mean re-modelling work already done well, re-encoding every message on the hot path, and owning that schema forever. Instead the envelope carries **only our metadata**, typed, around an OTLP payload.

Doors that do not speak OTLP natively — synthetic checks, cloud pollers — construct OTLP-shaped payloads using the official Python libraries. Downstream therefore sees exactly one format and never learns that doors exist.

`tenant_id` is a typed field rather than a key in an attribute map because the entire multi-tenancy invariant rests on it. A typo in a map key must not be possible.

## Traces must be split by trace_id before publishing

One `ExportTraceServiceRequest` can carry spans belonging to **many different traces**. Trace assembly depends on Kafka partitioning by `trace_id` so that every span of a trace reaches the same worker with no shared state.

So for the SPANS signal, an incoming batch is **split into one envelope per trace_id**, each published with `trace_id` as the Kafka key. Logs and metrics have no such requirement and forward as received.

This is the same approach the OpenTelemetry collector's `loadbalancing` exporter takes, so it is the established pattern rather than an invention — but it costs a re-encode on the hot path and belongs in the performance budget, measured at M1.

> If that cost proves too high at the edge, splitting moves to a lightweight worker between two topics. **The envelope shape does not change either way** — which is precisely why it is frozen first.

## Topics and keys

| Topic | Signal | Kafka key | Notes |
|---|---|---|---|
| `spans-raw` | SPANS | `trace_id`, **lower-case hex** (32 chars) | One trace per message, guaranteed by the split above |
| `logs-raw` | LOGS | `tenant_id` | Batches forwarded intact |
| `metrics-raw` | METRICS | `tenant_id` | Batches forwarded intact |

**The key encoding is part of the contract, not an implementation detail.** A trace id is 16
raw bytes; written as lower-case hex it is the same string the `spans` table stores in its
`FixedString(32)` column, so one spelling serves the partitioner and the row. Two producers
disagreeing on the encoding would hash the same trace to different partitions, and trace
assembly — which rests entirely on all of a trace's spans reaching one consumer
([0009](decisions/0009-trace-assembly-in-worker-memory.md)) — would fail silently, producing
partial traces rather than an error.

## Versioning rules

- Field numbers are **never** reused or renumbered.
- Fields are added, never removed — deprecated ones are marked `reserved`.
- `buf breaking` runs in CI against the previous commit; a breaking change fails the build.
- `semconv_version` records which OTel convention generation the payload speaks. HTTP conventions stabilized with 17 attribute renames and SDKs can emit both old and new, so normalization must read this field rather than assume.

## Golden fixtures

`packages/contracts/fixtures/` holds one recorded payload per door, committed and asserted against in both languages:

`door1-traces.bin` · `door1-logs.bin` · `door1-metrics.bin` · `door2-synthetic-http.bin` · `door2-synthetic-browser.bin` · `door3-rum.bin` · `door4b-cloudwatch.bin` · `door4b-dbstats.bin`

A door is not "done" until its fixture round-trips through the envelope and produces the expected rows in ClickHouse.

## What is deliberately not in the envelope

**Deploy events** — low volume, and they belong in Postgres joined against cases and findings, not in the telemetry pipeline.
**Session replay blobs** — DOM events are not OTLP; they go to object storage indexed by session id.
**Anything derived** — enrichment, masking, sampling decisions and pattern ids are added by workers downstream. The envelope carries what arrived, not what we concluded.
