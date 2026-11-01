# Security and tenancy architecture

## Current state

Postgres control-plane access is scoped by organization. ClickHouse access goes through the API repository chokepoint, whose statements must bind a tenant identifier. Tests enforce both patterns.

**The scheduler reads ClickHouse too**, held to the same rule by `test_tenant_chokepoint_structure.py`: only named modules may use the raw client (`alerts.telemetry`, `erasure.store`, `detection.store`, `detection.settle`). Anomaly detection (F6) adds three rules there:

- **Every detection statement binds the project as well as the tenant** (AC28), so a job can only judge the project it was given. `detection.store` builds every read and write for one project, from the `projects` row rather than from a caller.
- **Only `detection.store` may write telemetry**, as `nexovue_detection_writer`, an account that can insert into `detection_readings` and nothing else (ADR-023). Its profile's bounds cannot be raised by the client, an hourly quota caps inserts and bytes, and it is reachable from private networks only. It is the third ClickHouse account, beside the read-only `nexovue_reader` and the nightly `nexovue_eraser`; `scheduler-light` holds the writer's and eraser's credentials, and the API holds neither.
- **One table may be read without a tenant, by one module**: `span_derived_progress`, how far each Kafka partition is counted, which names no tenant. The exemption is a named entry, with its reason, for `detection.settle` alone; a join from it to a tenant's table still fails.

**Install-wide Postgres tables.** `intake_refusals`, `detection_events` and `detection_sweep` carry no `org_id`, because they describe nexovue's intake and detection, not a customer. Nothing in them reaches a tenant except as *data delayed*. The scheduler's own Postgres statements bind `org_id` by convention and review only: `test_postgres_tenancy_structure.py` scans `apps/api/src` alone, and the scheduler structure test the F6 design called for was not built.

**Findings keep telemetry text**, service and endpoint names, and are purged once closed and past their project's derived retention. A read token sees a finding without who acknowledged it or why it was called a false alarm. Detection's settings, mutes, new normals and *anomaly finding* alert rules need `CONFIGURE_DETECTION`, which admins and owners hold.

Server OTLP uses secret API keys; browser RUM uses public site keys restricted by an origin allowlist. Secret keys are prefix-looked-up and hash-verified; public keys are intentionally visible to browser code. Receiver gates execute in this order: authentication, browser-origin validation, allowance, rate limiting, decode, normalize, and Kafka publish.

Telemetry is masked in workers before derive, sample, and storage. The worker masking hash secret is required in production configuration. The API uses typed domain errors and a central RFC 9457 error boundary.

## Future state

No AI tool permissions, RAG data-access model, or production secret-management platform is implemented. Those require separate approved design before implementation.
