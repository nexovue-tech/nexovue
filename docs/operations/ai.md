# The AI container (`scheduler-insight`)

F3's AI role: it words a finding's facts through an organization's own provider. Plan Step 3
built the fence only; it runs idle, with no provider code. Steps 4 and 5 add the connection
test, the wording job, their metrics and the rest of this runbook.

## What it can reach

- **Postgres, as `nexovue_insight`**, which `setup` creates and grants after every migration
  (`apps/api/src/nexovue_api/insight_account.py`). It holds no right on the job queue; it claims,
  retries and finishes `insight` jobs through four functions (migration 0070).
- **The Compose network `ai`**, shared with Postgres (and Prometheus when monitoring is on). Kafka,
  ClickHouse and the object store are not on it. It is not an outbound firewall: the host's rules
  decide what else it can open.

## `setup` said the AI account was not created

`ai account: POSTGRES_INSIGHT_PASSWORD is not set`: the settings file lacks it. `nexovue upgrade`
adds one; or set any long random value and run `nexovue setup grants`.

`ai account: Postgres refused to prepare nexovue_insight (...)`: the install account may not
create or change roles, as on some managed Postgres services. Give it `CREATEROLE` and run
`nexovue setup grants`: an account with `CREATEROLE` that creates the role may manage it
(Postgres 16 and later; measured on 18). If the role was created by someone else, the install
account also needs `ADMIN OPTION` on it, and `CREATEROLE` still. **Not tried on a managed
service.**

`ai account: nexovue_insight holds a right no grant can fence`: someone gave it superuser,
role or database creation, replication or row-security bypass. Only a superuser can take
those back; `scheduler-insight` must not run until one has.

Nothing else depends on this account: detection, alerts and every screen run without it, and
only `scheduler-insight` fails to start.

## `setup` said it could not take advisory locks from PUBLIC

`ai account: ready, but Postgres refused to take advisory locks, pg_notify and temporary tables
from PUBLIC`: the install account does not own those built-in functions. **What is left:** a
broken-into AI process could take beat's leader lock (`leader.py`), and no check, sweep or alert
would be scheduled until that process stopped. Stop `scheduler-insight`
(`SCHEDULER_INSIGHT_REPLICAS=0` in `nexovue.env`, then `nexovue install`) until the database's
administrator runs, as a superuser:

```sql
REVOKE EXECUTE ON FUNCTION pg_try_advisory_lock(bigint) FROM PUBLIC;  -- and every overload
```

`insight_account.SHARED_FUNCTIONS` lists them all.

## Known gaps no grant closes (F3 status, *Open decisions* 7)

- **`NOTIFY` needs no privilege.** A broken-into process can send notifications; the workers'
  wake channel tolerates them, but a listener that never reads can fill Postgres's notification
  queue, after which every `NOTIFY` in the database fails at commit (Run now, send tests).
- **Its timeouts are defaults.** Any session may `SET statement_timeout = 0`, so a broken-into
  process can hold a transaction open: that holds back vacuum across the database and blocks a
  migration's lock on the tables it read. Postgres warns about an old transaction in
  `pg_stat_activity`; `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename =
  'nexovue_insight' AND xact_start < now() - interval '5 minutes'` ends it.

## Running with no AI container

`SCHEDULER_INSIGHT_REPLICAS=0` in `nexovue.env`, then `nexovue install`. Nothing else changes.
