# Synthetic checks

## Status

**Verified current-state baseline.** Related catalog feature: F18.

## Implemented behavior

- Customers can create, list, edit/pause, and remove checks, then view check readings and runs.
- HTTP checks collect timing phases and certificate-expiry information.
- Browser checks execute configured Playwright navigation/action/assertion flows.
- Failed targets are published measurements, not failed Celery tasks. Results flow through the same
Kafka/worker telemetry pipeline as other signals.
- Probe egress rejects loopback, private, link-local, and cloud-metadata destinations at connection
time.

## Duplicate readings

A metric row is not unique. `metricwriting.Absorb` accepts a duplicate rather than risk a lost
point — no data loss is a phase-1 invariant, no duplication is not — and leaves the reader to
"group by series and timestamp first". Two whole probes were redelivered in three days of
development data, both around a ClickHouse restart.

All four statements in `apps/api/src/nexovue_api/synthetics/queries.py` collapse to
`GROUP BY check_id, metric_name, series_hash, timestamp` before they count, average or sum
(2026-09-13). The key names more than the series because `series_hash` is
`cityHash64(toJSONString(attrs))`: it tells two phases apart, but not two checks — the check id
lives in `resource_attrs` — and not two metrics whose attributes match, since a duration point
carries `attrs = {}`.

`test_synthetics_live.py` seeds a probe stored twice under two deduplication tokens and proves the
duplication reached the store before asserting the reader survives it. Without the fix it reports
double the phase total, two runs instead of one, and ten phase samples instead of five.

## Code evidence

`apps/api/src/nexovue_api/checks/router.py`, `synthetics/router.py`, scheduler `probes/tasks.py`,
`probes/http_check.py`, `probes/browser.py`, and `probes/addresses.py`, plus
`apps/web/src/features/synthetics/SyntheticsScreen.tsx` are authoritative.
