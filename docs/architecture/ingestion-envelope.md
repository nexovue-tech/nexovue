# The Ingestion Envelope (P1-07)

> **Type:** Frozen contract · **Status:** Built. Every ingest path publishes this message and every worker reads it. Verified field by field against the proto and both producers on 2026-09-04.
> **Lives in:** `packages/contracts/proto/nexovue/v1/envelope.proto`
> **Companion docs:** `tenancy-and-data-model.md` (where the ids come from) · `system-architecture.md` (the pipeline)

## What "frozen" covers, and what it does not

**Frozen and enforced by CI:** the eight fields of `nexovue.v1.Envelope` and their numbers. `just check` runs `buf lint` and `buf breaking --against` the `main` branch, so a rename, a renumber or a removed field fails the build. The eight fields below are byte-for-byte what the proto file holds today.

**Not frozen:** everything *around* the message — topic names, partition keys, which door enum a given entrance stamps, and what each worker does with the payload. Those are conventions this document records, and no gate enforces any of them. Read the code before relying on one; the sections below name the file for each.

Saying "the envelope contract is frozen" without that split has already misled an agent into treating the whole of this document as settled fact. The message is settled. The document is not the message.

## Why the message itself is the most expensive thing to change

Two producers write it — the receiver (Go) and the scheduler (Python). One worker binary reads it, running up to three pipelines (`trace-assembly`, `log-writing`, `metric-ingest`) selected by `WORKER_PIPELINES` ([ADR 0050](decisions/0050-one-worker-process-pipelines-by-config.md)); the usage meter reads the same messages alongside them. It sits on the hot path at thousands of messages per second. Changing its shape means changing every one of those programs simultaneously, which is why it was designed and frozen **before** the first door was built.

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

The split lives in `apps/receiver/internal/tracesplit`; the publish in `apps/receiver/internal/ingest`.

## The receiver decodes all three signals

The payload is forwarded **unmodified**, which is not the same as unread. `apps/receiver/internal/otlp/signals.go` defines one `signalDoor` per signal, and each one unmarshals the body into the OTLP request its endpoint names — `ExportTraceServiceRequest`, `ExportLogsServiceRequest`, `ExportMetricsServiceRequest`. A body that will not parse is refused with `INVALID_ARGUMENT` rather than published.

The receiver reads the decoded request for three things, all of them metadata:

- **the item count** — spans, log records or metric data points — which is what the traffic counters and the OTLP `partial_success` reply are expressed in;
- **`semconv_version`**, taken from the scope schema URL where there is one and the resource schema URL otherwise;
- **`trace_id`**, for traces only, to split and key the batch.

It re-encodes only what it split. Logs and metrics travel as the exact bytes that arrived.

## What each producer stamps

| Entrance | `door` | Signals | Where |
|---|---|---|---|
| OTLP server door (HTTP `:4318`, gRPC `:4317`) | `DOOR_OTLP` | spans, logs, metrics | `apps/receiver/internal/ingest/ingest.go` |
| OTLP browser door (`/browser/v1/*`, public site keys) | `DOOR_OTLP` | spans, logs, metrics | same file — the browser entrance is a separate route and gatekeeper, not a separate door value |
| Synthetic checks and browser flows | `DOOR_SYNTHETIC` | metrics, logs | `apps/scheduler/src/nexovue_scheduler/publish.py`, `probes/browser.py` |
| Cloud and database pollers | `DOOR_POLLER` | metrics | `apps/scheduler/src/nexovue_scheduler/pollers/envelope.py` |

`DOOR_RUM` and `DOOR_WEBHOOK` are declared in the proto and **nothing in the running system emits either**. The `door3-rum.bin` fixture asserts `DOOR_RUM`, while the browser door that carries RUM traffic stamps `DOOR_OTLP` and has a test asserting it does. Do not branch on `door` to identify RUM traffic until that disagreement is settled.

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
- `buf breaking` runs against the `main` branch in `just contracts-check`, which `just check` includes; a breaking change fails the gate before it can be committed.
- `semconv_version` records which OTel convention generation the payload speaks. HTTP conventions stabilized with 17 attribute renames and SDKs can emit both old and new, so normalization must read this field rather than assume.

## Golden fixtures

`packages/contracts/fixtures/` holds one recorded payload per door, committed and asserted against in both languages:

`door1-traces.bin` · `door1-logs.bin` · `door1-metrics.bin` · `door2-synthetic-http.bin` · `door2-synthetic-browser.bin` · `door3-rum.bin` · `door4b-cloudwatch.bin` · `door4b-dbstats.bin`

`packages/contracts/roundtrip_test.go` and `packages/contracts/tests/test_envelope_roundtrip.py` each hold the same table of expected field values, independently. What they prove is that every fixture decodes to those values, re-encodes to the exact bytes on disk, and carries a payload that still parses as the OTLP request its signal names — plus that the fixture count matches the table, so a door cannot quietly gain or lose one.

They do **not** reach ClickHouse. Whether a fixture produces the expected rows is proved by each pipeline's own tests, not here.

## What is deliberately not in the envelope

**Deploy events** — low volume, and they belong in Postgres joined against cases and findings, not in the telemetry pipeline.
**Session replay blobs** — DOM events are not OTLP; they go to object storage indexed by session id.
**Anything derived** — enrichment, masking, sampling decisions and pattern ids are added by workers downstream. The envelope carries what arrived, not what we concluded.
