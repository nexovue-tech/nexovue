# 1. Ingestion edge is our own custom receiver

- **Status:** Accepted — receiver language superseded in part by [0021](0021-go-data-plane-from-day-one.md)
- **Date:** 2026-08

## Context

Telemetry needs a front door. The alternative was running the upstream OpenTelemetry collector at
the edge and letting the collector community own the protocol surface. The door also has to serve
two very different customers — those whose SDKs point straight at us, and those who run their own
collector and export onward to us — and it will later have to host doors that are not OTLP at all
(RUM, session replay, webhooks).

## Decision

Build our own receiver (Option B). OTLP/HTTP first on the standard paths `/v1/traces`,
`/v1/metrics`, `/v1/logs`, protobuf parsed with the official `opentelemetry-proto` libraries;
OTLP/gRPC added later. Python is acceptable for v1 — the receiver is stateless, so it can be
rewritten in Go if it becomes the bottleneck, and that rewrite is invisible to customers.

One endpoint serves both patterns: SDK-direct (Pattern A) and customer-owned collectors exporting
via `otlphttp` (Pattern B). The receiver authenticates the API key, rate limits, tags the tenant,
normalizes, and publishes to Kafka. Other doors' routes land here later.

**Kafka is internal-only, always.** Customers never touch the broker; the receiver is the security
and tenancy boundary.

## Consequences

We own the receiver code — protocol updates and its performance are our problem, not the
collector community's. In exchange, one process enforces auth, rate limiting and tenancy for every
door, and the broker never has to be exposed or made multi-tenant-safe.

[0021](0021-go-data-plane-from-day-one.md) later removed the interim Python step: the receiver is
written in Go from the first line. Everything else here stands.
