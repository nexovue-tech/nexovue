# ADR-001: Normalize every ingestion door into one Kafka envelope

## Status

Accepted — existing implementation.

## Context

Nexovue accepts server OTLP, browser RUM, synthetic checks, and poller results. Downstream workers need one stable, tenant-tagged contract regardless of origin.

## Decision

All entrances normalize to the protobuf envelope in `packages/contracts` before publishing to signal-specific Kafka topics. The envelope records tenant, project, door, signal, receipt time, ingest ID, semantic-convention version, and original OTLP payload.

## Consequences

Workers implement one input contract and can route by typed door/signal values. Envelope changes are compatibility-sensitive and checked with Buf. The reserved webhook door is not evidence of an implemented webhook ingestion path.
