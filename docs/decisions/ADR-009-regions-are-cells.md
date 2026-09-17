# ADR-009: Regions are cells, and every organization belongs to one — India by default

## Status

Accepted 2026-09-16 — explicit founder decision. Not yet executed: the product has one region and no
concept of one. Two design decisions below are deliberately left open.

## Context

nexovue will serve customers from more than one region eventually. For now it serves India only, and
India is the default. A customer must also be able to move between regions later.

"Region" is used for three different things in this repository, and this record concerns only two:

1. **Where infrastructure is deployed** — a Terraform input ([ADR-006](ADR-006-terraform-with-two-delivery-paths.md)).
2. **Where a customer's data lives, and moving it** — the subject of this record.
3. *Where synthetic probes run from* — the `cloud.region` resource attribute on probe results, for F18's
   multi-region checks. A separate feature, not decided here.

Checked before deciding:

- **Organizations and projects have no region field** (`data/postgres/models.py`).
- **No tool copies one customer's data.** `TenantScopedPurger` deletes a tenant's telemetry; nothing
  exports or copies it.
- **`s3_region` defaults to `us-east-1`** (`settings.py:289`). Its comment explains why — SeaweedFS
  ignores the value and AWS signing needs one — but on real S3 in India the default is simply wrong.
- **Provider regions in India**, from each provider's own documentation:

| nexovue region | Oracle Cloud | AWS |
|---|---|---|
| `india` (default) | `ap-mumbai-1` | `ap-south-1` (Mumbai) |
| `india-south` | `ap-hyderabad-1` | `ap-south-2` (Hyderabad) |

- **Oracle's Mumbai and Hyderabad regions each have one availability domain.** AWS's two Indian
  regions each have three zones.

## Decision

**A region is a cell: a complete data plane of its own** — receiver, Kafka, workers, ClickHouse and
object storage — so a customer's telemetry never leaves the region they belong to.

**Every organization belongs to exactly one region**, default `india`, set when the organization is
created through the admin console ([ADR-008](ADR-008-the-website-takes-the-first-payment.md)).

**Moving one customer to another region is a per-customer migration**, distinct from moving the whole
platform between clouds ([ADR-007](ADR-007-migrating-between-clouds.md)):

1. Copy that organization's Postgres rows, ClickHouse rows and objects into the new region.
2. Ingest into both regions for a short window.
3. Change the organization's region.
4. Remove its data from the old region with the existing `TenantScopedPurger`.

### Prepared now

| # | Preparation | Why now |
|---|---|---|
| 1 | Terraform takes `region` as input, default `india`, mapped per provider, with separate state per region | Retrofitting a region into existing state is painful |
| 2 | **No region is hardcoded** anywhere — Terraform, Ansible, Helm values, or settings. **`s3_region` must be stated explicitly in production** rather than defaulting to `us-east-1` | A US default is exactly the silent single-region assumption this record exists to remove |
| 3 | **`region` is added to organizations**, default `india`, when F31 is built | One column now, versus a backfill of every customer later |
| 4 | **Every stored thing carries its tenant** | Enforced for ClickHouse and Postgres by structural tests. **Object storage key layout was not checked** — it must be tenant-prefixed or per-customer copy and purge cannot work |

### Not built now

The second region, the tooling that moves a customer, and any routing between regions. They are built
when a customer outside India pays — the same rule [ADR-008](ADR-008-the-website-takes-the-first-payment.md)
applies to servers.

## Open decisions

Both must be settled before a second region exists, and both are expensive to change once customers
depend on the answer.

**A — One control plane, or one per region?** Postgres holds accounts, users and billing.

- *One global control plane:* one login, one admin console, simple billing. A customer's account
  details sit outside their data's region.
- *One per region:* the strongest residency. Login, billing and the admin console must first find the
  right region.

**B — Does moving region change a customer's ingest address?**

- *Per-region hostnames* such as `ingest.in.…` are simple, but a move forces the customer to reconfigure
  every service — contradicting item 1 of [ADR-007](ADR-007-migrating-between-clouds.md)'s preparations.
- *One global ingest hostname routing by API key* keeps the address stable, at the cost of a routing
  hop and a global component.

## Consequences

- **High availability on Oracle means running in both Indian regions**, since an availability-domain
  failure there is a whole-region failure. On AWS a single Indian region can be enough. This feeds the
  budget decision [ADR-006](ADR-006-terraform-with-two-delivery-paths.md) defers.
