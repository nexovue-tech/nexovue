# ADR-030: `scheduler-connections` makes every tracker call, on narrow settings

## Status

Accepted 2026-11-02, with the F22 design
([`design.md`](../features/case-management/design.md), *Proposed ADRs*, revision 2).
Filed 2026-11-08, when Step 3 built the role. It carries FD23, amends foundation
section 1's "API, on a person's click", and adds a role under ADR-019.

## Context

A case can be linked to a GitHub issue, and later to a Jira issue. Something must hold
each connection's secret (a GitHub App's private key, a webhook secret), mint tokens,
and call the tracker. The foundation first put the writing in "the API, on a person's
click". Reading the code showed why that fails:

- the API has no connect-time address guard: `probes/addresses.py` needs `pycurl`, and
  the API uses `httpx` only for its payment processor;
- every Python role shares the `control-plane-env` anchor (`deploy/compose/nexovue.yaml`),
  which carries `CHECK_SECRET_KEY`, the mail key and the object store's keys, so any
  existing worker that took tracker work would hold all of them beside repository access;
- `scheduler-deliveries` exists so a slow receiver holds only its own slots; a slow
  GitHub must likewise hold no alert.

F5 reached the same answer for its own GitHub work (`ai-solution/design.md`, D40).

## Decision

1. **A new role, `scheduler-connections`, from the same image** (`nexovue_api/roles.py`):
   `worker --queues connections,connections-interactive --concurrency 3 --tasks
   connections`. Sync runs on `connections`, capped at 2 of the 3 slots
   (`QUEUE_CAPS`), so a hanging tracker always leaves a slot for a connection test,
   setup jobs and sweeps on `connections-interactive` (F5's layout).
2. **It holds `CONNECTION_SECRET_KEY` and not the shared anchor's keys.** Its environment
   is an exact list: `ENV`, `NEXOVUE_DEPLOYMENT`, `LOG_LEVEL`, `LOG_FORMAT`,
   `SCHEDULER_ROLE`, `POSTGRES_DSN`, `APP_BASE_URL` and its key pair (AC33,
   `test_compose_contract.py`). Only the API (to seal and to re-seal) and this role
   hold the key. `SCHEDULER_ROLE` (`full` or `connections`) decides what the scheduler's
   settings demand, so this role starts with no Kafka and no check key.
3. **It loads only its own tasks.** `TASK_SETS["connections"]` names the modules, and its
   forkserver preloads them alone (`jobs/preload_connections.py`), so the process with
   repository access holds no ClickHouse, Kafka or object-store client, and no task of
   `full` runs there (AC34, `test_connections_have_no_ai.py`).
4. **The API adds jobs and reads results, as ADR-011 does for Run now.** It never calls a
   tracker (AC32, `test_no_tracker_calls_in_api.py`). Jobs carry ids only, and every
   connection job binds its own `org_id` on every read (AC74, AC129).
5. **It runs no model,** enforced by an import walk from the modules it loads and a check
   of a running child's `sys.modules`.
6. **Typed addresses are limited to known hosts** until a self-hosted tracker is
   supported: GitHub's address is a constructor argument only (AC89), and a Jira site
   must match `*.atlassian.net` (D26).
7. **It is optional.** `NEXOVUE_CONNECTIONS=yes` (the default) turns on its Compose
   profile; `no` leaves it out, and the API refuses a setup with the reason. Hosted has
   none until founder decision 3 is answered (step H). Beat's `connections_active`
   gauge, not the role's own scrape, tells the monitor whether its absence matters.
8. **One connection layer for every tracker kind** (foundation section 6, item 29): one
   `connections` table with `kind` (`github`, `jira`, `github_fix`), shared jobs, and a
   per-kind adapter (`connections/trackers.py`). Jira is an added adapter, not a rework.

## Consequences

- A slow or failing tracker holds no probe, alert or detection slot. A click's result
  arrives a moment later, through the job, not in the response.
- One more container, with a 384 MB ceiling that is a guess (F22 T4.9 measures it), on a
  16 GB server whose ceilings already add to about 13.75 GB (`docs/self-hosting/sizing.md`).
- **Stated plainly: a compromise of this role still reads and writes all of Postgres,**
  because every role shares one Postgres account (foundation section 1). It cannot open
  check or channel secrets, and holds no AI, mail or object-store key. F22 T8.7 gives it
  its own account once F3's pattern exists; until then that is a named risk.
- F5's GitHub work joins this role and this task set, rather than `TASK_MODULES`.
- **Unverified until the founder's real-GitHub run after Phase 2** (foundation item 28):
  every GitHub answer this role depends on was read from documentation and replayed by a
  simulated GitHub (`apps/scheduler/tests/tracker_fake.py`), not seen from GitHub.

## Alternatives rejected

- **The API calls GitHub directly.** It has no connect-time guard, and moving `pycurl`
  into the API is a larger change than a job.
- **`scheduler-deliveries` takes tracker work.** A slow GitHub would hold alert slots, and
  it would need the tracker key beside the check key.
- **`scheduler-light` takes it, under its floor.** It holds the ClickHouse writer and the
  eraser accounts; repository access does not belong beside them.
