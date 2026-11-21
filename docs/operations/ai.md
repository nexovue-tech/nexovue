# The AI container (`scheduler-insight`)

F3's AI role: it words a finding's facts through an organization's own provider. Plan Step 3
built the fence; Step 4 added the connection test, the one task it runs today. Step 5 adds the
wording job, its metrics and the rest of this runbook.

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

## Allowing a local model (`AI_PRIVATE_ENDPOINTS`)

A connection may reach a private address only if it is listed here, by address and port:
`AI_PRIVATE_ENDPOINTS=192.168.1.20:11434` in `nexovue.env`, comma-separated for more, a range as
`192.168.1.0/24:11434`, an IPv6 address as `[fd00::7]:11434`. Set it on `scheduler-insight`
(`nexovue install` after editing). The scheduler refuses to start on an entry with no port, one
not wholly private, loopback or link-local, or a range wider than /16 (IPv4) or /48 (IPv6); the
error names the entry. Loopback (`localhost`, `127.0.0.1`) is never reachable from a container:
bind the model server to the host's private address instead. A connection test against an
address not listed fails with "The address was refused".

## The connection test failed with "The saved key could not be opened"

The key is sealed under `AI_SECRET_KEY`, for the connection's address. After a rotation, keep the
old key in `AI_SECRET_PREVIOUS_KEYS` until `just keys-status` shows nothing under it
(`docs/operations/key-rotation.md`). If the old key is gone, an admin saves the key again.

## Running with no AI container

`SCHEDULER_INSIGHT_REPLICAS=0` in `nexovue.env`, then `nexovue install`. Nothing else changes.
`/settings/ai` says the container is not running and will not switch AI on. Where AI was
already on, turn it off there too: otherwise its wording waits ("not available right now") and
`NexovueInsightMissing` opens a ticket. Beat schedules the AI sweep only while a connection is on.

## How a wording run goes (F3 Step 5)

The evidence job, on `scheduler-light`, decides each new version's wording with no AI code:
`off` (AI off, project opted out, hosted, or causes still held in production), `held` (muted or
flapping), `abstained` (only absences), or `pending`, which adds a `word` job to the `insight`
queue with the version's id and a generation. `scheduler-insight` runs it as `nexovue_insight`:
it re-reads the connection, consent, opt-out and project, takes the run cap (3 per finding in
24 hours) and a reservation of its input estimate plus 800 output tokens from the month's limit
(80% for warning findings, all of it for critical ones), records an `ai_runs` row, then makes one
call. A 5xx, a 429 (waiting `Retry-After`) or an answer failing the checks gets one more attempt.
The words are checked by code when written and again when the API reads them; nothing a
provider says is kept anywhere else.

## A finding's wording says "Plainer wording is not available right now"

It has been `pending` for over 10 minutes: `scheduler-insight` is stopped, far behind, or the
job expired. Check `up{job="scheduler-insight"}` and the `insight` queue's oldest job
(`nexovue_scheduler_oldest_ready_job_seconds{queue="insight"}`). A person may ask again at once.
Once the container runs, the AI sweep (every 5 minutes) marks what was left `failed`, reason
`expired`, and a run whose child was killed `died`, with its reservation counted as used
(overstated on purpose). If that child was not in fact dead and its call later returns, the
`scheduler-insight` log says "a wording run finished after the sweep had ended it" (a warning):
the sweep's charge stands and its words are dropped. It means a child outlived its hard limit,
which the worker's kill should prevent, so look at that container's memory and kills.

## Alerts

- `NexovueFindingsUnexplained`: open findings over 15 minutes old with no facts read. The
  evidence job is down or far behind (`queue="evidence"`); AI plays no part in it.
- `NexovueEvidenceBehind`: facts have waited over 10 minutes to be read.
- `NexovueInsightMissing`: an AI connection is on and no `scheduler-insight` answers a scrape.
  Start it, or turn AI off in `/settings/ai`. Facts and alerts are unaffected.
- `NexovueInsightBehind`: wording jobs have waited over 30 minutes while AI is on.

Beat also reports, with no tenant label: `nexovue_scheduler_ai_runs_recent{purpose,outcome}`,
`nexovue_scheduler_ai_tokens_month`, `nexovue_scheduler_ai_connections_enabled`,
`nexovue_scheduler_evidence_reads_failed_recent` and `nexovue_scheduler_evidence_truncated_recent`.

## Not yet run against a real provider

Every wording path is tested against a fake provider on this machine. No format has completed a
real call yet (`nexovue_platform/ai/real_calls.py`), so the settings screen offers none.
