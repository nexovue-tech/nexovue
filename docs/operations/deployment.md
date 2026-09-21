# Deployment

## Current verified behavior

Local development uses Docker Compose through `just dev`. A production-replica Compose configuration builds and starts production images locally. The on-premise Compose configuration is explicitly marked as a skeleton and must not be used as a production deployment procedure.

## The first accounts

Two accounts are created from the environment rather than through a screen, and
an install uses one or the other: `FIRST_OWNER_*` for a self-hosted install's own
owner, `SUPER_ADMIN_*` for nexovue's operator on the hosted service. Which to
set, what gets created, and why the first is read once while the second is
rewritten every boot: **[first-accounts.md](first-accounts.md)**.

## Not yet established

There is no approved production hosting target, image registry, CI/CD pipeline, staging environment, immutable release process, or production deployment runbook. Deployment is blocked on those decisions and the readiness items in `production-readiness.md`.
