# Deployment

## Current verified behavior

Local development uses Docker Compose through `just dev`. A production-replica Compose configuration builds and starts production images locally. The on-premise Compose configuration is explicitly marked as a skeleton and must not be used as a production deployment procedure.

## Not yet established

There is no approved production hosting target, image registry, CI/CD pipeline, staging environment, immutable release process, or production deployment runbook. Deployment is blocked on those decisions and the readiness items in `production-readiness.md`.
