# 37. Object storage = SeaweedFS (local dev) + AWS S3 (deployment)

- **Status:** Accepted
- **Supersedes:** [0035](0035-minio-for-local-dev-s3-in-deployment.md)
- **Date:** 2026-08-26

## Context

[0035](0035-minio-for-local-dev-s3-in-deployment.md) contained an archived, AGPLv3 component pinned
by digest and quarantined by argument. SeaweedFS is S3-compatible, actively maintained and Apache
2.0.

## Decision

SeaweedFS for local development, AWS S3 in deployment. Replaces the MinIO-pinned-by-digest plan.
The generic-S3-API-only rule stands.

## Consequences

No archived or copyleft component exists anywhere in the stack, and the "pin by digest, never
latest" caution becomes unnecessary. SeaweedFS also remains a viable production target should a
fully on-premise deployment ever be required — which the MinIO plan had foreclosed.
