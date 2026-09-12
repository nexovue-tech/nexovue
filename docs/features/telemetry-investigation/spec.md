# Telemetry investigation console

## Status

**Verified current-state baseline.** This groups implemented surfaces corresponding mainly to
F8–F12; their detailed future acceptance criteria remain to be written.

## Implemented behavior

- Services are listed and drilled into by service and endpoint using request, error, and latency
  rollups derived from spans.
- Service map shows trace-derived dependencies.
- Trace list filters traces and opens a span waterfall for an individual trace.
- Errors are grouped and can be opened for recent instances.
- Logs can be searched, faceted, viewed for a trace, and live-tailed.

## Code evidence

Frontend screens are in `apps/web/src/features/services/`, `servicedetail/`, `servicemap/`,
`traces/`, `errors/`, and `logs/`. Tenant-scoped API readers are in the matching packages under
`apps/api/src/nexovue_api/`, especially `services/router.py`, `servicemap/router.py`,
`traces/router.py`, `errorgroups/router.py`, and `logs/`.
