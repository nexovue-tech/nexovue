# 12. Multi-tenancy = shared tables with tenant_id first in ORDER BY

- **Status:** Accepted
- **Date:** 2026-08

## Context

The alternatives for isolating tenants are a database (or table set) per tenant, or shared tables
with a discriminator column. Database-per-tenant isolates hard but multiplies operational work by
the customer count.

## Decision

Shared tables. `tenant_id` is first in every ClickHouse `ORDER BY`, and the backend API always
injects the tenant filter. Database-per-tenant only if a giant customer ever demands it.

## Consequences

Isolation and query performance come from the same mechanism: every query prunes to one tenant's
data blocks before reading anything. The risk is that isolation is now a code invariant rather than
a physical boundary — one missing filter is a cross-tenant leak, which is why enforcement was later
placed in the repository layer and `org_id` denormalized onto every table
([0044](0044-control-plane-schema.md)).
