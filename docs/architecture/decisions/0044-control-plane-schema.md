# 44. Control-plane schema

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

With tenancy ([0042](0042-tenancy-model-organization-and-project.md)) and identity
([0043](0043-one-user-one-organization.md)) settled, the Postgres schema could be laid out. Full
detail in `../tenancy-and-data-model.md`.

## Decision

Four choices worth naming:

- **`org_id` is denormalized onto every table**, so the tenant filter is one column away on both
  Postgres and ClickHouse — one rule, one repository layer, both stores.
- **API keys hash with SHA-256, never argon2.** argon2 is designed to be slow, which is correct for
  a human password and catastrophic on an ingest path verifying keys thousands of times per second.
  High-entropy random keys gain nothing from stretching.
- **One `api_keys` row carries both ids**, so the receiver's hot path is a single cached lookup.
- **UUIDv7 primary keys** (RFC 9562) for time-ordered index locality.

## Consequences

Tenant isolation is enforceable in one place for both databases, which is what makes the repository
-layer invariant practical. Key verification stays cheap enough to sit on the ingest path. The costs
are ordinary denormalization costs — `org_id` must be written correctly everywhere, and the schema
carries a redundancy that a purist would remove.
