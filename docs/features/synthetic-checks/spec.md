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

## Code evidence

`apps/api/src/nexovue_api/checks/router.py`, `synthetics/router.py`, scheduler `probes/tasks.py`,
`probes/http_check.py`, `probes/browser.py`, and `probes/addresses.py`, plus
`apps/web/src/features/synthetics/SyntheticsScreen.tsx` are authoritative.
