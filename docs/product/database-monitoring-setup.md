# Connecting a database

*Customer-facing. This is the copy P1-54 turns into published documentation; it lives here until
that row ships. The PostgreSQL collector configuration was run end to end on 2026-09-06 and the
screen was checked; the MySQL notes are from the reference manual and are marked as unrun. See
[ADR 0100](../architecture/decisions/0100-a-database-we-cannot-reach-is-polled-by-the-customers-own-collector.md).*

There are two ways to fill the Database screen, and which one you use depends on where your
database runs. Both produce the same screen.

| Your database | Use | You install |
|---|---|---|
| AWS RDS PostgreSQL with IAM authentication | **the connector** — we read it for you | nothing |
| Anywhere else — self-hosted, on-premise, another cloud, RDS MySQL | **your collector** — it reads and sends | an OpenTelemetry Collector |

**We never store a database password.** The connector uses a role you grant and a token that
expires in 15 minutes. The collector route keeps your credential inside your own network, where it
already lives.

---

## Option A — the connector (AWS RDS PostgreSQL)

Available when your database is RDS PostgreSQL with IAM database authentication enabled. You give
us an address and a role; we connect out to it every 60 seconds.

1. **Enable IAM database authentication** on the instance.
2. **Create a read-only database user** and let it log in with IAM:

   ```sql
   CREATE USER nexovue_monitor;
   GRANT rds_iam TO nexovue_monitor;
   GRANT pg_monitor TO nexovue_monitor;
   ```

   `pg_monitor` is a read-only role. We ask for nothing beyond it, and the connection is opened
   with `default_transaction_read_only`.

3. **Create an IAM role** whose trust policy names our account and **requires the ExternalId we
   show you**. That value is generated per integration and is what stops anyone else's role being
   assumed on your behalf — do not reuse it or change it afterwards.
4. **Paste the role ARN, host and database name** into Settings → Integrations.

Nothing is stored but the role ARN and the ExternalId, and neither grants anything on its own. You
can revoke access in your own account without telling us.

---

## Option B — your own collector (everything else)

Your collector runs where your database already is, so there is no address to expose and no
firewall to open. The credential stays in your configuration.

Works with **PostgreSQL, MySQL, Oracle, SQL Server and ClickHouse**.

### 1. A read-only user

PostgreSQL:

```sql
CREATE USER nexovue_monitor WITH PASSWORD 'choose-your-own';
GRANT pg_monitor TO nexovue_monitor;
```

For slow-statement data you also need the `pg_stat_statements` extension. It requires a restart:

```
shared_preload_libraries = 'pg_stat_statements'
```

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

**Connection health works without it.** If you cannot enable the extension, skip the first query
below and the screen still shows backends by state.

### 2. Collector configuration

```yaml
receivers:
  sqlquery/db:
    driver: postgres
    datasource: "host=YOUR_DB_HOST port=5432 user=nexovue_monitor password=${env:DB_PASSWORD} database=YOUR_DB sslmode=require"
    collection_interval: 30s
    queries:
      # Slowest statements. The statement TEXT is never selected — only its hash,
      # because pg_stat_statements does not always strip literals out of `query`.
      - sql: >
          SELECT statements.queryid::text AS "db.query.hash",
                 databases.datname AS "db.namespace",
                 statements.calls::text AS "db.client.operation.calls",
                 (statements.mean_exec_time / 1000.0) AS mean_seconds
          FROM pg_stat_statements AS statements
          JOIN pg_database AS databases ON databases.oid = statements.dbid
          WHERE statements.queryid IS NOT NULL
          ORDER BY statements.total_exec_time DESC
          LIMIT 100
        metrics:
          - metric_name: db.client.operation.duration
            value_column: mean_seconds
            unit: s
            value_type: double
            data_type: gauge
            attribute_columns:
              ["db.query.hash", "db.namespace", "db.client.operation.calls"]
            static_attributes:
              db.system.name: postgresql
      # Backends by state. Needs no extension.
      - sql: >
          SELECT COALESCE(state, 'unknown') AS "db.client.connection.state",
                 count(*)::text AS backends
          FROM pg_stat_activity
          WHERE state IS NOT NULL
          GROUP BY state
        metrics:
          - metric_name: db.client.connection.count
            value_column: backends
            unit: "{connection}"
            value_type: int
            data_type: gauge
            attribute_columns: ["db.client.connection.state"]

processors:
  # Which server these readings describe. `server.address` and `server.port` are
  # required — the screen groups by them.
  resource/db:
    attributes:
      - key: server.address
        value: YOUR_DB_HOST
        action: upsert
      - key: server.port
        value: 5432
        action: upsert
      - key: service.name
        value: your-database
        action: upsert
  batch:

exporters:
  otlp_http:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_INGEST_KEY}

service:
  pipelines:
    metrics/db:
      receivers: [sqlquery/db]
      processors: [resource/db, batch]
      exporters: [otlp_http]
```

**The quoted column aliases are not cosmetic.** `sqlquery` names each attribute after its column, so
`AS "db.query.hash"` is what puts the value where we look for it. Renaming one empties that column
on the screen.

Readings appear within about a minute.

### Other databases

Change the `driver`, the `datasource` and the two queries; keep every alias and metric name exactly
as above. `db.system.name` should name your engine — `mysql`, `oracle`, `mssql`, `clickhouse`.

**MySQL** — the equivalent sources are `performance_schema.events_statements_summary_by_digest`
(use `DIGEST` as `db.query.hash`, `COUNT_STAR` as the call count, and `AVG_TIMER_WAIT` divided by
`1e12`, because Performance Schema timers are in picoseconds) and `performance_schema.threads` for
connection states. Select `DIGEST`, never `DIGEST_TEXT` — the latter is the statement itself.

> The PostgreSQL configuration above was run end to end and the screen was checked. **The MySQL
> notes are from MySQL's reference manual and have not been run.** Treat them as a starting point
> until someone points this at a real MySQL and confirms the numbers.

---

## What we collect, and what we do not

**Collected:** a statement's hash, the database it ran in, its call count, its mean execution time,
the server address and port, and backend counts by state.

**Never collected by either route above:** the statement text, query parameters, table contents,
or any row of your data. A statement is identified by its hash; to see the text, look the hash up
in your own `pg_stat_statements`.

**Your traces are a separate matter, and worth knowing about.** If your application is
instrumented with OpenTelemetry, its database spans carry `db.query.text` — the statement your code
ran — because that is what makes a slow query debuggable from the trace. Most SDKs send it
parameterised, so values appear as `@p0` or `?` rather than as data, but that is the SDK's
behaviour and not a guarantee: a query built by joining strings together has no parameters to
strip.

To have it removed before we store it, set your project's masking configuration:

```json
{ "mask_keys": ["db.query.text"] }
```

Masking happens before the write and cannot be undone — the original is gone, so a statement you
mask is one you cannot read back later. If you would rather values never left your process at all,
most SDKs can be configured to sanitise before sending, which is the stronger option.

**Read-only, enforced rather than promised.** The connector opens its session with
`default_transaction_read_only` and contains only two `SELECT` statements. On the collector route
the queries are the ones above and nothing else — a monitoring user with write privilege is a
monitoring user that will eventually write, so do not grant one.

---

## A note on AI and LLM calls

If your application calls an LLM and is instrumented with OpenTelemetry, **those calls already
arrive** — no extra configuration, no separate endpoint. They appear in Traces alongside the HTTP
request that triggered them and any database query beside it, which is the point: a slow response
is answerable in one place rather than three.

Instrumentation is usually a package rather than a code change —
`opentelemetry-instrumentation-genai-openai`, `opentelemetry-instrumentation-google-genai`, and
equivalents for the Claude Agent SDK, OpenAI Agents, LangChain and LlamaIndex.

**What does not exist yet is a dedicated screen** for token usage and cost per model. The data is
stored and queryable; the lens over it is not built.

## Not supported yet

**MongoDB, Redis, Elasticsearch, Cassandra.** The Database screen groups by a *normalized statement*
— PostgreSQL's `queryid`, MySQL's `DIGEST`. Redis and Elasticsearch have no equivalent, so there is
nothing to put in the table. MongoDB has one (`queryShapeHash`) but reports it in a different shape;
it is separate work rather than a missing driver.

Redis and Elasticsearch resource metrics — memory, cache hit rate, heap, index size — do reach us
today through the Collector's own `redis` and `elasticsearch` receivers, and appear under
Infrastructure rather than here.
