# Security and tenancy architecture

## Current state

Postgres control-plane access is scoped by organization. ClickHouse access goes through the API repository chokepoint, whose statements must bind a tenant identifier. Tests enforce both patterns.

**The scheduler reads ClickHouse too**, held to the same rule by `test_tenant_chokepoint_structure.py`: only named modules may use the raw client (`alerts.telemetry`, `erasure.store`, `detection.store`, `detection.settle`). Anomaly detection (F6) adds three rules there:

- **Every detection statement binds the project as well as the tenant** (AC28), so a job can only judge the project it was given. `detection.store` builds every read and write for one project, from the `projects` row rather than from a caller.
- **Only `detection.store` may write telemetry**, as `nexovue_detection_writer`, an account that can insert into `detection_readings` and nothing else (ADR-023). Its profile's bounds cannot be raised by the client, an hourly quota caps inserts and bytes, and it is reachable from private networks only. It is one of four ClickHouse accounts, beside the scheduler's read-only `nexovue_reader`, the API's read-only `nexovue_api_reader` and the nightly `nexovue_eraser`; `scheduler-light` holds the writer's and eraser's credentials, and the API holds neither. The API reads through its own account, whose per-user memory and concurrency bounds the server enforces, so API reads at their worst cannot starve the scheduler's sweeps or the inserts (ADR-045, F20).
- **One table may be read without a tenant, by one module**: `span_derived_progress`, how far each Kafka partition is counted, which names no tenant. The exemption is a named entry, with its reason, for `detection.settle` alone; a join from it to a tenant's table still fails.

**Install-wide Postgres tables.** `intake_refusals`, `detection_events` and `detection_sweep` carry no `org_id`, because they describe nexovue's intake and detection, not a customer. Nothing in them reaches a tenant except as *data delayed*. **The scheduler's Postgres statements bind `org_id` too**, held by `test_scheduler_postgres_tenancy_structure.py` (in `apps/api/tests`, because the models say which tables carry a tenant). It reads every `text()` statement in the scheduler and the shared platform package: one that reads, changes or deletes a tenant's rows must bind `org_id` to a parameter that `:org_id IS NULL OR` cannot switch off, and an insert must name `org_id`. A table without `org_id` that the scheduler names must be declared install-wide, with its reason. The sweeps that must see every tenant (the coordinator's claim, the job queue, the alert outbox, the check schedule, the erasure, the install-wide gauges) are named one statement at a time, each with its reason, and an exemption that no longer names an unbound statement fails. `test_postgres_tenancy_structure.py` still covers the API's SQLAlchemy statements.

**Fairness between tenants.** Each project job has its own lock and time budget. An organization counts at most 2,000 service names a minute into `service_stats_1m` (the worker's cap, keeping the busiest, with each capped minute recorded in `services_over_cap_1m`), and a project judges at most 500 services (`MAX_JUDGED_SERVICES`): those with an open finding first, then the busiest by the larger of the window's requests and what their normal expects, then by name. The rest are not judged and read *too many services*.

**Findings keep telemetry text**, service and endpoint names, and are purged once closed and past their project's derived retention. A read token sees a finding without who acknowledged it or why it was called a false alarm. Detection's settings, mutes, new normals and *anomaly finding* alert rules need `CONFIGURE_DETECTION`, which admins and owners hold.

Server OTLP uses secret API keys; browser RUM uses public site keys restricted by an origin allowlist. A site key is public, so the worker trusts nothing it carries to describe a server: browser-door spans count in no rollup, browser-door telemetry marks no service as heard in `service_last_seen` (`docs/changes/browser-door-last-seen`), and only the prober's door may write a synthetic check's readings or evidence (`docs/changes/browser-door`). Secret keys are prefix-looked-up and hash-verified; public keys are intentionally visible to browser code. Receiver gates execute in this order: authentication, browser-origin validation, allowance, rate limiting, decode, normalize, and Kafka publish.

Telemetry is masked in workers before derive, sample, and storage. The worker masking hash secret is required in production configuration. The API uses typed domain errors and a central RFC 9457 error boundary.

**Suggested fixes (F5).** Source code is the largest disclosure Nexovue makes, so each step
needs its own person and grant. An owner agrees per repository and folder, and the consent is
compared with the provider, key and model at every use (ADR-105). The process that calls the
model holds no repository key, and the process with the key calls no model; code from a model
reaches a person or a repository only through Nexovue's rules, run again in the room with the key
(ADR-103). F5's GitHub App is its own, read only until an owner wants drafts (ADR-106). **Write
access is off in code** (`write_gate.WRITE_ACCESS_GO` is None, ADR-097): every write path asks
first, before anything that can reach GitHub, which `test_fix_write_gate.py` holds structurally.
Under an owner's requirement, quoted code waits for the person's own GitHub access check. A
support view reads no repository paths (`audit/read` strips `paths` and `files`; the admin
summary's notices carry none). Every new scheduler statement binds `org_id`; the sweeps and
gauges that must see every tenant are named in `test_scheduler_postgres_tenancy_structure.py`
one statement at a time, and `fix_sweep_status` is declared install-wide.

## Future state

No production secret-management platform is implemented. F5's data-access rules are above; other AI features' rules are in their own designs. Those require separate approved design before implementation.
