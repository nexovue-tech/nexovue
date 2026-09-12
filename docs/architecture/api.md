# API architecture

## Current verified behavior

The FastAPI service exposes versioned `/api/v1/` routes and operational health routes outside that prefix. Domain packages separate HTTP router, schemas, service logic, queries, dependencies, and exceptions. A central exception boundary produces RFC 9457 problem responses.

Postgres queries pass an organization identifier explicitly. ClickHouse access is centralized in `apps/api/src/nexovue_api/data/clickhouse/base.py`: statements include a tenant binding and the repository refuses statements that do not. Static and structural tests enforce these tenant boundaries.

Sessions live in Redis. API telemetry reads use rate limits and concurrency controls. Authentication, password recovery, API/site keys, projects, billing, checks, integrations, deploys, telemetry reads, data deletion, and source-map metadata are implemented domains.
