# 6. Object storage as the cold tier

- **Status:** Accepted
- **Date:** 2026-08

## Context

ClickHouse holds a hot window of 15–30 days. Telemetry older than that still has value but does not
justify hot-storage cost, and the retention TTL would otherwise simply delete it.

## Decision

Add an S3-compatible object store as the cold tier behind ClickHouse, which can tier to it natively.

## Consequences

Old data survives the hot-window TTL at low cost, without a separate export pipeline. Queries
reaching into cold data are slower. Which implementation backs this changed twice later — see
[0035](0035-minio-for-local-dev-s3-in-deployment.md) and
[0037](0037-seaweedfs-for-local-dev-object-storage.md).
