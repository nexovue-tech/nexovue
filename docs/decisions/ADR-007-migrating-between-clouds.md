# ADR-007: Migrating between clouds loses no data, and costs the console seconds

## Status

Accepted 2026-09-16 — explicit founder decision. Not yet executed: no migration has been performed,
and three of the preparations below do not exist.

## Context

[ADR-006](ADR-006-terraform-with-two-delivery-paths.md) keeps the cloud provider switchable — Oracle
today, AWS or another later. Switching providers with customers already on the platform means moving
live data: accounts and billing in Postgres, telemetry in ClickHouse, objects in storage.

The requirement stated was zero downtime and zero data loss. Those are not equally achievable, and the
difference had to be decided rather than discovered during a migration.

Checked before deciding:

- **The OTLP specification** says a client *should* retry `429`, `502`, `503` and `504` with
  exponential backoff and honour `Retry-After`, and retry failed connections the same way.
- **Our receiver already answers that way** when Kafka is unreachable: `code.Code_UNAVAILABLE` with
  `brokerRetryAfter` (`apps/receiver/internal/otlp/gates.go:110`), rendered as HTTP 503
  (`codec.go:168`).
- **PostgreSQL logical replication** needs `wal_level = logical`, which *"can only be set at server
  start"*; needs a replica identity — the primary key by default — to replicate updates and deletes;
  does not replicate sequences; and the PostgreSQL documentation describes a switch-over this way as
  costing *"only several seconds of downtime"*.
- **Sessions live in Redis**, keyed by token fingerprint (`data/postgres/models.py:180`). *(Since
  2026-10-10, ADR-020: in the Postgres `sessions` table, so they move with Postgres's replication.)*

## Decision

**Zero data loss is a hard requirement. Downtime is effectively zero for ingest, and a window of
seconds for the console.** A truly zero-downtime Postgres cut-over would need bidirectional
replication and a controlled dual-write, which is far more machinery than a few seconds of read-only
console are worth.

Ingest loses nothing across a short pause because well-behaved exporters retry a 503. That rests on a
*should* in the specification: SDKs differ, and client buffers are finite. A pause of seconds is safe;
a pause of minutes is not something to promise.

### The procedure

**Before cut-over — days, invisible to customers:**

1. Stand the full stack up on the new provider.
2. **Postgres:** logical replication from old to new, running continuously.
3. **ClickHouse history:** restore the latest backup with `just backup`, checked with `verify.sh`.
4. **ClickHouse live data:** a second set of workers on the new provider consumes the **same Kafka
   topics** under its own consumer group and writes the new ClickHouse. Kafka is the replay log, which
   is what makes this possible at all.
5. **Object storage:** sync to the new provider.

**Cut-over — seconds:** console read-only → replication catches up → set sequences → promote the new
Postgres → switch DNS → old receivers drain → retrying clients arrive at the new receivers.

**After:** compare row counts, keep the old environment read-only for a rollback window, then retire
it.

### Prepared now, because each is expensive to add later

| # | Preparation | Why it cannot wait |
|---|---|---|
| 1 | Customers are only ever given **our hostname** for ingest and the console — never a cloud load-balancer name or an IP | `otlp_endpoint` is printed at signup and written into customers' exporters and pages; a provider address there forces every customer to reconfigure |
| 2 | Postgres runs with **`wal_level = logical`** from its first production start | Setting it later requires a restart of production |
| 3 | **Every Postgres table has a primary key**, enforced by a structural test in the manner of `test_postgres_tenancy_structure.py` | Logical replication cannot replicate an update or delete without one |
| 4 | **Backup, restore and verify for every store** | Today they cover Postgres and ClickHouse. **Not object storage** (Redis was removed 2026-10-10, ADR-020) |
| 5 | **The open overcount defect is fixed** (decisions 0060 → 0061 → 0075) | A migration replays and backfills overlapping data by design, which is exactly where duplicate rows appear — and this defect's stated cause has already been disproven once |
| 6 | **Workers can target a different ClickHouse under a different consumer group by configuration alone** | Step 4 depends on it |
| 7 | **A rehearsed migration** between two throwaway hosts, before any customer depends on one | A procedure that has never run is not a procedure |

## Rejected

- **Dump and restore with the platform stopped.** Downtime grows with data size, and ClickHouse is the
  store that grows.
- **Bidirectional replication for a true zero on Postgres writes.** Its conflict handling and
  operational cost are disproportionate to a control plane whose writes are logins, key creation and
  settings.

## Consequences

- ~~**Everyone is signed out once** unless Redis keys are migrated too.~~ No longer true since 2026-10-10
  (ADR-020): sessions are Postgres rows and are carried by the same logical replication.
- The tables inspected use UUID primary keys, so the sequence restriction barely touches us. Item 3's
  test is what turns "the tables inspected" into "every table".
- **Unverified:** whether OTLP over **gRPC** passes through Cloudflare's proxy. Fronting both hosts
  with Cloudflare would make the DNS switch immediate; that must be tested before it is relied on.
- Moving *one customer* between regions is a different operation from moving the whole platform
  between clouds. [ADR-009](ADR-009-regions-are-cells.md) covers it.
