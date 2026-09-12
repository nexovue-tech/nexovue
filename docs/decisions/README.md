# Architecture decisions

ADRs preserve why a consequential decision was made; `docs/architecture/` preserves current behavior. Create an ADR only when the decision is material, settled, and supported by repository evidence or an explicit founder decision.

## Recorded

- [ADR-001 — Normalize every ingestion door into one Kafka envelope](ADR-001-internal-envelope-and-kafka.md)
- [ADR-002 — Acknowledge only durable boundaries](ADR-002-at-least-once-delivery.md)
- [ADR-003 — Use OpenTelemetry as the primary collection contract](ADR-003-otel-first-collection.md)

## Earlier decisions, 0001–0112

Before this series, 112 decision records lived under `docs/architecture/decisions/`. They were
deleted in `e0f11a0`; their full text is intact in git history.

**[decision-log.md](decision-log.md)** indexes all 112 — what each decided, whether it still stands,
and the ten chains where a decision was superseded or reversed. Read it before proposing anything
that sounds like a fresh architecture choice; several already have an answer, including one that was
decided, reversed, and reversed back in a day.

Three of the 112 are restored in full here — **0060 → 0061 → 0075**, the counter-idempotency chain —
because they record a measured, open defect: a reproducible span overcount across a ClickHouse
restart whose stated cause was later falsified. `docs/architecture/current-system-reference.md` §18
now carries that finding.

## Not yet written

Tenant-isolation chokepoints and the ClickHouse/Postgres control-plane split are settled in code but have no ADR. Write them when the reasoning next needs to be defended or changed.
