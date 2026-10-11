# Deployment

## Current verified behavior

Local development uses Docker Compose through `just dev`. A production-replica Compose configuration builds and starts production images locally. The on-premise Compose configuration is explicitly marked as a skeleton and must not be used as a production deployment procedure.

## The production replica

Run on 2026-10-11 (`docs/changes/deployment-tells-the-truth/`):

1. Copy `deploy/compose/.env.example` to `deploy/compose/.env.replica` and fill it in. On a laptop,
   `NEXOVUE_APP_HOST=app.localhost` and `NEXOVUE_INGEST_HOST=ingest.localhost`; Caddy certifies
   both from its own local authority, so a browser warns and `curl` needs `-k`. **Not
   `deploy/compose/.env`**: Compose reads that file by itself for every file in the directory, and
   the dev stack would pick up the replica's values.
2. Ports 80 and 443 must be free.
3. Nothing creates the schema, the Kafka topics or the storage bucket yet (readiness blocker 5). Start
   the stores (`kafka clickhouse postgres seaweedfs`), then run the alembic and ClickHouse migrations,
   `deploy/kafka/topics.sh` with `KAFKA_CONTAINER` naming the replica's broker, and create `S3_BUCKET`.
4. `just dev-app`. The console is `https://app.localhost`, the ingest endpoint
   `https://ingest.localhost`. Only the proxy publishes ports.

It holds no account: signup is closed. Set `FIRST_OWNER_EMAIL`/`FIRST_OWNER_PASSWORD` in the file to
seed one.

## The first accounts

Two accounts are created from the environment rather than through a screen, and
an install uses one or the other: `FIRST_OWNER_*` for a self-hosted install's own
owner, `SUPER_ADMIN_*` for nexovue's operator on the hosted service. Which to
set, what gets created, and why the first is read once while the second is
rewritten every boot: **[first-accounts.md](first-accounts.md)**.

## Not yet established

There is no approved production hosting target, image registry, CI/CD pipeline, staging environment, immutable release process, or production deployment runbook. Deployment is blocked on those decisions and the readiness items in `production-readiness.md`.
