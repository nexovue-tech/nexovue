# 0100 — A database we cannot reach is polled by the customer's own collector

- **Status:** Accepted
- **Date:** 2026-09-06
- **Extends:** [0076](0076-door-4b-holds-no-customer-credential-it-can-avoid-holding.md), whose
  principle is unchanged and whose "self-hosted Postgres is deliberately not implemented" is
  answered here without weakening it.

## Context

ADR 0076 asked the right question — *"do we need to hold one at all"* — and answered it for AWS
and RDS. For everything else it recorded a gap:

> *"Self-hosted Postgres has no equivalent. That case genuinely requires storing a secret, and
> storing one properly requires a key-management decision this project has not taken."*

The consequence is narrower than that sentence suggests, and it was not written down anywhere.
`postgres_connect.py` accepts one auth mode, `rds_iam`, and refuses `password`. `postgres_stats.py`
can emit one `db.system.name`, `postgresql`. So door 4b's poller reaches **AWS RDS PostgreSQL with
IAM database authentication, and nothing else** — not RDS MySQL, not Cloud SQL, not on-premise
anything. The Database screen is empty for every other customer, with no message explaining why.

**Two walls stand between our poller and a self-hosted database, and only one of them is about
credentials.**

**The credential.** A self-hosted database authenticates with a password, and using one on a timer
means storing one. ADR 0076's objection to that is not the storage, it is the liability: a stored
password works from anywhere, outlives the relationship, is revocable only by us acting, and —
decisively — *"the poller must decrypt to use it, so anything that can read our process can read
the key."* Encryption at rest defends a stolen backup, not a compromised scheduler, which is the
attack that matters here.

**The network, and it is the larger one.** A self-hosted database sits at a private address inside
the customer's own network. No credential makes `10.0.3.14:5432` routable from our cloud. Closing
that would mean asking the customer to expose a production database publicly, allowlist our egress
addresses, or run a VPN to us. It also runs against [0096](0096-a-browser-flows-only-way-onto-the-network-is-a-proxy-that-judges-the-address.md)
and [0098](0098-a-probe-reaches-a-private-address-only-when-two-settings-both-say-development.md),
which exist to stop this system reaching private addresses.

## Decision

**The customer's own OpenTelemetry Collector runs the queries and exports the results to us. We
hold neither the credential nor the route.**

This is ADR 0076's move applied a second time. There, the answer to holding an AWS key was to let
the customer grant a role. Here, the answer to holding a database password is to let the customer's
own process do the reading — inside their network, where the database is already reachable and the
credential already lives beside their application, which holds the same password for the same
database.

**The Collector's `sqlquery` receiver is the producer.** It runs arbitrary SQL on a schedule and
turns rows into metrics, over PostgreSQL, MySQL, Oracle, SQL Server, ClickHouse, Snowflake and SAP
HANA drivers.

**Nothing in the API or the web app changes, and that is the point.** `dbstats/queries.py` already
reads OpenTelemetry's generic names — `db.client.operation.duration` and
`db.client.connection.count` — with `db.system.name` carried through to the response. Those names
are a **contract**, and any producer that satisfies it fills the screen.

**The mechanism that makes it need no code is the quoted alias.** `sqlquery` names an attribute
after its column, so a query selects `statements.queryid::text AS "db.query.hash"` and the value
lands where the screen looks. `server.address` and `server.port` are resource attributes in the
contract, so they come from a `resource` processor on a pipeline of their own.

**The statement text is never selected**, exactly as the poller refuses it: `pg_stat_statements`
does not always strip literals, and a query built by concatenation carries its parameters into
`query`.

**The two routes are not rivals.** Same screen, same metric names, same tenant, opposite direction.
A customer picks by where their database lives:

| Database is | Route | They install |
|---|---|---|
| RDS with IAM auth | our poller — we dial out | nothing |
| anywhere else | their collector — it dials in | a collector |

## Verified, not reasoned about

Measured 2026-09-06 against the demo's own Postgres, which upstream already runs with
`shared_preload_libraries=pg_stat_statements`:

- **`GET /api/v1/dbstats/statements` returned 36 rows**, across two databases (`postgres` and
  `astronomy_db`), slowest 92.8ms, with call counts and `db_system: postgresql`.
- **`GET /api/v1/dbstats/connections` returned 2 states** — 3 idle, 1 active, on
  `astronomy-db:5432`.
- The screen renders both halves.
- The stored attributes are on contract exactly:
  `{"db":{"client":{"operation":{"calls":"1"}},"namespace":"postgres","query":{"hash":"-60009367514838436"},"system":{"name":"postgresql"}}}`
  with resource `{"server":{"address":"astronomy-db","port":5432}}`.
- **Zero lines of API, query or screen code were changed.**

## Alternatives considered

**Store the password, encrypted at rest.** Rejected, and the reasons are stronger than ADR 0076's
because two of them are new. It buys *hosting reach*, not engines — adding MySQL needs a client and
its own stats queries whatever the auth, and RDS MySQL supports IAM auth today, so engines can grow
without storing anything. It does not solve the network wall, so "any database" would in practice
mean "any database exposed to the public internet", which is a precondition a customer's own
security team would refuse and which we should not be rewarding. And it would change what this
company is: holding standing credentials to *N* customers' production databases makes one breach of
us a breach of *N* of them. Not refused forever — refused until a real customer needs it and no
other route fits, at which point it is a KMS, a key hierarchy, rotation and audit, as its own ADR.

**Extend the poller to MySQL first.** Rejected as the wrong order rather than wrong. It would reach
*RDS MySQL with IAM* — a slice of a slice — where the collector route already reaches MySQL
everywhere. Worth doing later if agentless proves to be what customers buy.

**Ask the customer to expose the database or allowlist our egress.** Rejected. Egress addresses
change with scale and region, and the ask is larger than "run a collector".

## Consequences

**Self-hosted, on-premise and non-AWS managed databases are supported**, over five SQL engines,
through documentation rather than code.

**MongoDB is not, and that boundary is recorded so nobody re-derives it.** Its
`$queryStats`/`queryShapeHash` is a genuine analogue of `pg_stat_statements`, but the Collector's
`mongodb` receiver emits `db.server.top_query` and `db.server.query_sample` under an `events:` key —
the **logs** signal, at *development* stability. They would land in `logs`, and this screen reads
`metrics_gauge`. Supporting Mongo means a second query path and a decision about what a document
database's row shows, since it reports `docs_examined` and an explain plan rather than calls and a
mean. It is its own piece of work, not a free extension.

**Redis, Elasticsearch and Cassandra are out of scope for this screen entirely** — they have no
normalized-statement identity, so a table whose columns are *statement, mean, calls* has nothing to
put in them. Their metrics reach us through door 1 today via the Collector's own receivers, and
belong on Infrastructure.

The rule underneath all three: **this screen needs a normalized statement identity.** Postgres has
`queryid`, MySQL has `DIGEST`, MongoDB has `queryShapeHash`. Redis and Elasticsearch have nothing of
the kind.

**The poller's real scope should be said out loud in the product.** Nothing today tells a customer
the screen needs RDS with IAM; they connect an integration and get an error. That is the smallest
outstanding gap here.

**A cost accepted:** the query we publish runs against a customer's production database, so it must
be read-only and vetted, and a customer can edit it. What arrives is what they chose to send, and
the metric names are the whole contract.
