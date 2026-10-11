# Architecture decisions

ADRs preserve why a consequential decision was made; `docs/architecture/` preserves current behavior. Create an ADR only when the decision is material, settled, and supported by repository evidence or an explicit founder decision.

## Recorded

- [ADR-001 — Normalize every ingestion door into one Kafka envelope](ADR-001-internal-envelope-and-kafka.md)
- [ADR-002 — Acknowledge only durable boundaries](ADR-002-at-least-once-delivery.md)
- [ADR-003 — Use OpenTelemetry as the primary collection contract](ADR-003-otel-first-collection.md)
- [ADR-004 — AGPL-3.0 for the platform, permissive for the browser SDK](ADR-004-agpl-with-a-permissive-browser-sdk.md)
- [ADR-005 — Contributions come with a CLA, not a DCO](ADR-005-a-cla-rather-than-a-dco.md)
- [ADR-006 — Terraform for infrastructure, with two delivery paths — Ansible on Linux servers, Helm on Kubernetes](ADR-006-terraform-with-two-delivery-paths.md)
- [ADR-007 — Migrating between clouds loses no data, and costs the console seconds](ADR-007-migrating-between-clouds.md)
- [ADR-008 — The website takes the first payment, and cloud customers are onboarded by hand](ADR-008-the-website-takes-the-first-payment.md)
- [ADR-009 — Regions are cells, and every organization belongs to one — India by default](ADR-009-regions-are-cells.md)
- [ADR-010 — Check secrets are sealed with their own key, and only their names travel through the queue](ADR-010-check-secrets.md)
- [ADR-011 — The API dispatches run-now tasks to the scheduler's broker](ADR-011-api-dispatches-run-now.md)
- [ADR-012 — The scheduler evaluates alerts, reading ClickHouse through shared, tenant-bound statements](ADR-012-the-scheduler-evaluates-alerts.md)
- [ADR-013 — Notifications are an outbox, written in the transaction that decided them](ADR-013-notifications-are-an-outbox.md)
- [ADR-014 — Telemetry erasure runs as a nightly batch, not per deletion](ADR-014-telemetry-erasure-runs-as-a-nightly-batch.md)
- [ADR-015 — Profiles are normalised at the intake, the one exception to the untouched envelope](ADR-015-profiles-are-normalised-at-the-intake.md)
- [ADR-016 — Staff work on their own host, as their own kind of account](ADR-016-staff-work-on-their-own-host.md) — **superseded by ADR-018**
- [ADR-017 — A read token is its own credential, and the read API is the routes that accept it](ADR-017-a-read-token-is-its-own-credential.md)
- [ADR-018 — nexovue's operator is a user in the console, behind a step-up](ADR-018-the-operator-is-a-user-in-the-console.md)
- [ADR-019 — nexovue ships as two images, and each role runs as its own container](ADR-019-two-images-each-role-its-own-container.md)
- [ADR-020 — Postgres carries what Redis held, Redis goes, and object storage is optional](ADR-020-postgres-carries-what-redis-held.md)
- [ADR-021 — nexovue reports its own health for an outside monitor to read](ADR-021-nexovue-reports-its-own-health-for-an-outside-monitor.md)

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
