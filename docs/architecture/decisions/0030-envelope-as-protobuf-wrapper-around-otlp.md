# 30. Internal envelope = a thin protobuf wrapper around OTLP

- **Status:** Accepted — the "forward the same bytes" description corrected for traces by [0045](0045-envelope-contract-frozen.md)
- **Date:** 2026-08-25

## Context

[0024](0024-frozen-internal-envelope-contract.md) froze the envelope as a contract but not its
form. The temptation was to define our own span, log and metric schema. JSON was the other obvious
encoding.

## Decision

`packages/contracts` defines one protobuf message carrying our metadata as typed fields —
`tenant_id`, `project_id`, `door`, `received_at` — around an untouched OTLP payload.

We do **not** define our own span/log/metric schema. OTel already did, it is what customer SDKs
emit, and reusing it means door 1 — 99% of volume — validates and forwards the same bytes instead
of re-modelling and re-encoding them. Other doors build OTLP-shaped payloads with the official
libraries, so downstream still sees exactly one format.

Codegen and compatibility checking via `buf`, with `buf breaking` in CI.

Protobuf over JSON because the envelope is written by two languages and read by four workers at
thousands of messages per second: a renamed field must fail CI, not silently write zeros for three
weeks. `tenant_id` is a typed field rather than a map key because the multi-tenancy invariant rests
on it.

## Consequences

No parallel telemetry schema to maintain against OTel's, and the highest-volume door does the least
work. Cost: protobuf toolchain and codegen in three languages, and our metadata is now coupled to
OTLP's message shapes.

The "forward the same bytes" claim turned out to be false for traces —
[0045](0045-envelope-contract-frozen.md) records the correction.
