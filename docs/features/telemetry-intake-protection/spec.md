# Telemetry intake and data protection

## Status

**Verified current-state baseline.** Related catalog feature: F33. This documents customer-facing
intake behavior; internal topology belongs in `docs/architecture/`.

## Implemented behavior

- Server telemetry is accepted as OTLP traces, logs, and metrics over HTTP and gRPC using secret
  API keys.
- Browser telemetry is accepted under `/browser` using public site keys and an origin allowlist;
  browser beacons may use `text/plain` OTLP/JSON.
- The receiver authenticates, validates origin/allowance/rate limits, and publishes a protobuf
  envelope to Kafka before acknowledging acceptance.
- Workers mask payloads before metric derivation, sampling, or storage. Traces are tail-sampled;
  project retention and masking configuration are applied downstream.
- Tenant/project identity is carried through the envelope and enforced at storage/read boundaries.

## Code evidence

`apps/receiver/internal/otlp/gates.go`, `origin.go`, `handler.go`, and `grpc.go`; the frozen
contract `packages/contracts/proto/nexovue/v1/envelope.proto`; and worker packages `mask`,
`derive`, `sample`, and `projectconfig` are authoritative. Browser boundary tests live in
`apps/receiver/internal/otlp/browserdoor_test.go`.
