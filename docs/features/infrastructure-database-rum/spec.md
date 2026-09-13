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

## Duplicate readings

These screens read `metrics_gauge` and `metrics_sum`, where a row is not unique — see
`docs/features/synthetic-checks/spec.md` for why the writer chose that.

**Counts are exact** (2026-09-13). `NODE_READINGS_SQL`, `SIGNALS_SEEN_SQL`, `SLOW_STATEMENTS_SQL`
and `CONNECTION_HEALTH_SQL` count distinct instants rather than rows. Each already groups by the
whole series, so one instant is one reading, and `uniqExact` costs 7% against `count()` where
materialising the deduplicated rows in a subquery costs 22% — measured on the busiest development
project over six hours. This matters most for `reporting_interval_seconds`, which divides the
window by the readings counted: on rows stored twice it reported a host sampling every 33 seconds
when it sampled every 100.

`test_infrastructure_live.py` proves it against a fleet written twice. **The two dbstats statements
carry the same one-line idiom but no seeded regression test** — `test_dbstats_live.py` reads real
rows and has no insert harness.

**Two means are still weighted by duplicates:** `NODE_TRENDS_SQL` and `STATEMENT_TRENDS_SQL` both
`avg(value)` within a bucket. `uniqExact` cannot correct an average, so these need the 22%
subquery. The error is bounded by the spread of readings inside one bucket and is zero when a
bucket holds a single reading. **Open for the founder:** whether a sparkline mean that can drift
inside a bucket is worth 22% on the hottest metric read in the product.

## Code evidence

`apps/api/src/nexovue_api/infrastructure/router.py`, `dbstats/router.py`, and `rum/router.py`,
with `apps/web/src/features/infrastructure/`, `database/`, and `rum/`, implement these surfaces.
Workers' `vitals` pipeline promotes browser vital readings.
