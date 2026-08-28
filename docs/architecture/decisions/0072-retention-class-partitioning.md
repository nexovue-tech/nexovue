# 0072 — Retention is stamped on the row and partitioned by

- **Status:** Accepted
- **Date:** 2026-08-28
- **Supersedes:** [0038](0038-per-tenant-retention-via-conditional-ttl.md)


> **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named
> here no longer exist as separate files; each table's final shape is now one file, and the
> links point there. See [0073](0073-migrations-squashed-before-m3.md).

## Context

[0038](0038-per-tenant-retention-via-conditional-ttl.md) decided per-tenant retention would be a
conditional TTL, regenerated from plan tiers by a job:

```sql
ALTER TABLE spans MODIFY TTL toDateTime(ts) + INTERVAL 30 DAY WHERE tenant_id IN (...)
```

with `ttl_only_drop_parts = 1`, and recorded the benefit as *"no scheduled delete job, and no
mutation load — expiry stays a cheap part drop."*

That claim was never tested. [0071](0071-plan-tiers-and-retention.md) fixed the tiers, which made
building the mechanism the next step — so it was tested first.

## What the test showed

One part, two tenants, a conditional TTL naming one of them, on 26.3:

```sql
CREATE TABLE ttl_probe (ts DateTime, tenant String)
ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY ts
TTL ts + INTERVAL 1 DAY DELETE WHERE tenant = 'shorty'
SETTINGS ttl_only_drop_parts = 1;

INSERT … (now() - INTERVAL 10 DAY, 'shorty'), (now() - INTERVAL 10 DAY, 'keeper');
OPTIMIZE TABLE ttl_probe FINAL;

→ keeper  1        -- shorty is gone
```

The expired row was removed while its neighbour in the same part survived. **`ttl_only_drop_parts`
did not prevent it — ClickHouse rewrote the part.** It cannot do otherwise: a part holding rows from
several tenants can never satisfy "every row has expired", so the only way to honour a conditional
TTL is to rewrite.

So 0038's mechanism works, and its stated benefit is the opposite of what happens. At production
volume it is a part rewrite on every merge, on the largest tables in the system, plus a generated
DDL statement growing with the tenant list and a job to keep it current.

## Decision

**Stamp the retention a row is entitled to onto the row, and put that column in the partition key.**

```sql
PARTITION BY (toDate(received_at), retention_days)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS ttl_only_drop_parts = 1
```

Proven with the same probe shape:

```
partition            rows
('2026-08-18', 30)   1     -- kept
('2026-08-18',  7)   0     -- dropped whole
```

Every row in a part now shares one expiry, so the part drops as a unit — the cheap path 0038 wanted
and did not get. **And there is no job.** Nothing regenerates DDL, nothing has to run when a plan
changes, and there is no statement whose length grows with the customer list.

Partition cardinality multiplies by the number of *distinct retention values* — three or four — not
by the number of tenants. Partitioning by `tenant_id` is still rejected for exactly the reason 0038
gave; this is not that.

Raw signals carry `retention_days`; rollups and metrics carry `derived_retention_days`, which
`spans` also carries so the materialized views feeding `endpoint_stats_1m` can pass it through
without a second lookup.

## Where the number comes from

One definition, in Postgres, so the worker and the control plane cannot drift:

```sql
CREATE VIEW project_retention AS
SELECT projects.id,
       LEAST(projects.retention_days, plans.raw_retention_days) AS raw_retention_days,
       plans.derived_retention_days
FROM projects JOIN organizations … JOIN plans …
```

`LEAST`, not the plan's value: a project may ask for **less** than its tier grants — staging rarely
wants thirty days — and never more. The worker reads the view on its existing config refresh and
stamps every row it writes.

`plans` is a table rather than constants because retention days, and later prices, are the numbers
most likely to change after contact with customers, and changing one should not need a deploy.

## Consequences

**A plan change applies to new data only.** A row keeps the retention it was written with, so
upgrading `free` → `pro` does not extend the life of data already stored; only new rows get thirty
days. Downgrading is the same in reverse, which is the safe direction — existing data is not
deleted the moment somebody switches plan. This is the accepted trade, and it is what makes the
whole-part drop possible: retroactive retention would mean rewriting parts, which is the thing being
avoided.

**A project the refresher has not seen yet takes the deployment default**, not zero. A zero would
expire its rows on arrival, which is the failure this whole area keeps producing.

**Nine tables were rebuilt** across twenty migrations, since squashed into one file per table,
because a partition key cannot be altered. Done while `spans` held 12M rows and every other table
was small or empty — the same cheap-now argument as [0067](0067-storage-partitions-on-our-clock-not-the-client-s.md),
and the last time it will be this cheap.

**`endpoint_stats_1m` was not backfilled.** Its counters merge by summing, so a minute counted by
both a backfill and the live view is counted twice. It refills from live traffic.
