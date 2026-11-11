# ADR-040: AI runs in its own role, with its own account and network

## Status

Accepted 2026-11-02 by the founder with the F3 design ([`design.md`](../features/ai-insights/design.md),
*Proposed ADRs*, the first); filed 2026-11-11 with F3 plan Step 3, which built the fence and ran its
spike. Closes the open point in ADR-019. Some of the evidence the design asked for is still owed by
`just insight-fence` (below).

## Context

ADR-019 ships two images and leaves an AI role open. `scheduler-light` holds every sensitive telemetry
account, `control-plane-env` carries the keys to every check and channel secret, all Python roles
shared one Postgres account, and Kafka needs no password. A process that calls a model on text an
outsider can influence should hold none of that.

## Decision

1. **A role, `scheduler-insight`, from the `nexovue` image** (`roles.py`): `worker --queues insight
   --concurrency 4 --tasks insight`, `SCHEDULER_ROLE=insight`, its own task set and forkserver preload,
   so no ClickHouse, Kafka or object-store client is in its processes. It runs idle by default (FD6);
   `SCHEDULER_INSIGHT_REPLICAS=0` leaves it out.
2. **Its own settings anchor**, `x-insight-env`: Postgres as itself, `AI_SECRET_KEY` and its previous
   keys, `AI_PRIVATE_ENDPOINTS`, and nothing else. A missing key stops this role alone.
3. **Its own Postgres account, `nexovue_insight`**, created and granted by `setup` after every
   migration, so a restore (which drops grants) is covered by `start`. Column-level rights only, no
   table-level `UPDATE` or `DELETE`, no right on `jobs` or `job_results`, and four `SECURITY DEFINER`
   functions limited to the `insight` queue and `ai_check` results in place of the queue statements.
   Advisory locks, `pg_notify` and `TEMP` are taken from `PUBLIC` where Postgres allows.
4. **Its own Compose network, `ai`**, shared with Postgres (and Prometheus under monitoring) alone.

## Evidence (measured 2026-11-11, Postgres 18)

- A column-level `UPDATE` grant does **not** permit `LOCK TABLE` in any mode; a table-level `INSERT`
  permits `ROW EXCLUSIVE`, which is why the account has none on the queue.
- The functions and `_CLAIM`, `_RETRY`, `_FINISH`, `_STORE_RESULT` agree on one table of cases,
  `SKIP LOCKED` included, and never touch another queue.
- Revoking every advisory-lock overload and `pg_notify` from `PUBLIC` holds; the install account keeps
  them. The connection limit holds. (`test_insight_account_live.py`, `test_insight_grants_live.py`.)
- An idle insight child keeps about 21 MiB of its own memory after a job, under its 48 MiB limit.

**Owed by `just insight-fence`** (written, not yet run): reach from inside the container on the
installed layout (AC42), a real `nexovue restore` onto an empty volume (AC46), memory of the running
container at 4 and 6 children.

## What it does not stop

- **`NOTIFY` needs no privilege**, so revoking `pg_notify` does not stop the command.
- **Timeouts set on the role are defaults** the session can change; a broken-into process can hold a
  transaction open, holding back vacuum and a migration's lock on what it read.
- Where `setup` cannot revoke from `PUBLIC`, the beat lock stays reachable.

These are F3's open decision 7 (`status.md`), with a recommended watchdog.

## Alternatives rejected

In the API or in `scheduler-light` (a model beside every secret); a third image (no heavy dependency);
row-level security on `jobs` (permits `LOCK TABLE`, and puts an AI rule on the alert queue); holding no
key but sharing a network (Kafka is `PLAINTEXT`).

## Consequences

The first named network and the first second Postgres account in this repository. Four statements
exist twice, held in step by a test. One more container, 384 MB ceiling, idle. A managed Postgres needs
`CREATEROLE` for `setup` to make the account, and may leave the beat-lock risk.
