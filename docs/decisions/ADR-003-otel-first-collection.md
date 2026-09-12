# ADR-003: Use OpenTelemetry as the primary collection contract

## Status

Accepted — existing implementation and product direction.

## Context

The product needs traces, logs, metrics, RUM, synthetic checks, and integration-derived data without building proprietary host agents.

## Decision

Customer services and collectors send OTLP to Nexovue. Browser RUM uses a Nexovue SDK but normalizes into OTLP. Scheduler-generated probes and pollers also publish OTLP-shaped payloads through the shared envelope pipeline.

## Consequences

Signal semantics and compatibility follow OpenTelemetry. Network monitoring agents, session replay, profiling, and AI observability screens are separate product decisions, not automatic consequences of accepting OTLP attributes.
