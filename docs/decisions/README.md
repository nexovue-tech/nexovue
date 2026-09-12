# Architecture decisions

ADRs preserve why a consequential decision was made; `docs/architecture/` preserves current behavior. Create an ADR only when the decision is material, settled, and supported by repository evidence or an explicit founder decision.

## Recorded

- [ADR-001 — Normalize every ingestion door into one Kafka envelope](ADR-001-internal-envelope-and-kafka.md)
- [ADR-002 — Acknowledge only durable boundaries](ADR-002-at-least-once-delivery.md)
- [ADR-003 — Use OpenTelemetry as the primary collection contract](ADR-003-otel-first-collection.md)

## Not yet written

Tenant-isolation chokepoints and the ClickHouse/Postgres control-plane split are settled in code but have no ADR. Write them when the reasoning next needs to be defended or changed.
