# Infrastructure, database monitoring, and RUM

## Status

**Verified current-state baseline.** This groups implemented surfaces corresponding to F13–F15.

## Implemented behavior

- Infrastructure screen and API expose hosts/reporters and available infrastructure metric signals.
- Database screen exposes normalized statement performance and connection-state data produced by the
Postgres polling integration.
- RUM screen exposes Core Web Vital percentile readings and JavaScript-error-related telemetry
received through the browser intake path.

## Not implemented

Carbon calculations, Kubernetes-specific product depth, session replay, and a full mobile-RUM
product are not implemented.

## Code evidence

`apps/api/src/nexovue_api/infrastructure/router.py`, `dbstats/router.py`, and `rum/router.py`,
with `apps/web/src/features/infrastructure/`, `database/`, and `rum/`, implement these surfaces.
Workers' `vitals` pipeline promotes browser vital readings.
