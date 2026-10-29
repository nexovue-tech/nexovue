# Deployment

How nexovue is installed and run, on any target. The customer-facing guide is
[`docs/self-hosting/`](../self-hosting/install.md); this page is the operator's map.

## Current verified behavior

One set of Compose files, `deploy/compose/nexovue.yaml`, is every install (ADR-019, ADR-006 amended
2026-10-28). Overlays change its settings, never its lists:

| Layout | Files | Run by |
|---|---|---|
| Self-hosted | `nexovue.yaml` | `nexovue install` on a customer's server |
| Hosted | `+ nexovue.hosted.yaml` (billing, operator console, support access) | nexovue's own service |
| Quickstart | `+ nexovue.quickstart.yaml` (plain http on localhost) | `nexovue quickstart` on a laptop |
| Replica | `+ nexovue.hosted.yaml + nexovue.build.yaml` (images built here) | `just dev-app` |
| Three brokers | `+ nexovue.kafka3.yaml` | `NEXOVUE_KAFKA_BROKERS=3` at install |

`deploy/nexovue` is the one command: `quickstart`, `install [--offline]`, `upgrade`, `rollback`,
`status`, `backup`, `backups`, `restore`, `stop`. Every release attaches it, with the Compose files and
configuration, as the install kit `nexovue-X.Y.Z.tar.gz`, plus an offline bundle per architecture holding
every image ([release](release.md)).

Every start runs the setup step first — Postgres and ClickHouse to the newest migration, the bucket and
the Kafka topics (`setup`, `setup-pipeline`) — and no service starts until both exit 0.

Rehearsed on 2026-10-28 on the founder's Mac (`docs/changes/ready-to-deploy/status.md`): the quickstart
from built images, a span sent through the edge and read back; three brokers with one stopped under
load; a backup shipped off the host and restored onto an empty install; the monitor's alert on a stopped
worker. **Not yet rehearsed:** `nexovue install` on a fresh Linux server, the offline bundle with the
network cut, and an upgrade between two released versions — they need a Linux VM (Lima) or a server.

## The production replica

1. Copy `deploy/compose/.env.example` to `deploy/compose/.env.replica` and fill it in. On a laptop,
   `NEXOVUE_APP_HOST=app.localhost` and `NEXOVUE_INGEST_HOST=ingest.localhost`; Caddy certifies
   both from its own local authority, so a browser warns and `curl` needs `-k`. **Not
   `deploy/compose/.env`**: Compose reads that file by itself for every file in the directory, and
   the dev stack would pick up the replica's values.
2. Ports 80 and 443 must be free.
3. `just dev-app`. The console is `https://app.localhost`, the ingest endpoint `https://ingest.localhost`.
   It runs as project `nexovue-app`, so its volumes never touch the dev stack's.

It holds no account: signup is closed. Set `FIRST_OWNER_EMAIL`/`FIRST_OWNER_PASSWORD` in the file to
seed one.

## The first accounts

Two accounts are created from the environment rather than through a screen, and an install uses one or
the other: `FIRST_OWNER_*` for a self-hosted install's own owner (`nexovue install` asks for it),
`SUPER_ADMIN_*` for nexovue's operator on the hosted service: **[first-accounts.md](first-accounts.md)**.

## The hosted service

Not deployed: no server has been bought (founder, 2026-10-28). [first-deploy.md](first-deploy.md) is the
runbook for the day one is. Infrastructure code (ADR-006's OpenTofu, Ansible and Helm paths) is written
when a customer chooses a cloud.
