# 35. Object storage — MinIO pinned for local dev, AWS S3 in deployment

- **Status:** Superseded by [0037](0037-seaweedfs-for-local-dev-object-storage.md)
- **Date:** 2026-08-25

## Context

The cold tier ([0006](0006-object-storage-cold-tier.md)) needs an S3-compatible target in local
compose and a real one in deployment. MinIO was the obvious local choice — until its community
edition entered maintenance in December 2025 and was archived on 25 April 2026: read-only, no
releases, no security patches, and AGPLv3.

## Decision

Keep MinIO for local development only, pinned to its **last release by digest** — never `:latest`.
Neither the abandonment risk nor the copyleft applies to a local, non-exposed dev container.
Deployments use AWS S3.

**Rule: application and ClickHouse tiering code use the generic S3 API only** — never a
MinIO-specific endpoint or admin call — so the swap is configuration, not code.

Noted deviation: this puts a cloud dependency in the production cold tier, against the otherwise
on-premise stance ([0015](0015-containers-with-on-premise-docker-compose.md)).

## Consequences

Dev and production speak the same API with no code difference, and the archived component is
quarantined to a container that never faces the network. The residual costs are what replaced this
decision: an unpatched image in the dev stack forever, a digest pin to remember, and no
fully-on-premise production option. [0037](0037-seaweedfs-for-local-dev-object-storage.md) removes
all three.
