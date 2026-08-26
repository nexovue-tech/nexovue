# 42. Tenancy model — organization is the tenant, project is the environment

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

`tenant_id` had been load-bearing since [0012](0012-shared-tables-with-tenant-id-first.md) without
a definition of what a tenant *is*. Sentry's project-per-application model was the obvious
reference.

## Decision

**Organization = `tenant_id`** — the billing, membership and isolation boundary.

**Project = `project_id`** — holds ingest keys, retention, sampling and quotas, with the convention
that a project **is an environment** (production, staging).

**Services are never configured.** `service.name` arrives on every span, so services appear when
telemetry appears.

Rejected: Sentry's project-per-application model, where staging and production would share
retention, quotas and alerting.

## Consequences

Retention, sampling and quotas sit where they genuinely belong, and staging noise stays out of
production findings. Services requiring no setup is what the ten-minute onboarding promise depends
on. Cost: a customer with many applications gets projects multiplied by environments, and
cross-environment views have to be built deliberately rather than falling out of the model.
