# Monitoring

## Current verified behavior

Services emit structured logs and expose health/readiness behavior. Local operators can use `just logs <service>` and internal health endpoints. The repository includes load, E2E, and chaos validation tools.

## Not yet established

No production monitoring backend, dashboards, alert rules, SLOs, paging ownership, or log-retention policy is configured for nexovue itself. These must be selected and implemented for the chosen production target.
