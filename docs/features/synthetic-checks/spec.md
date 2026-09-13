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

## Known limitation — counts over duplicate-capable rows

A metric row is not unique. `metricwriting.Absorb` accepts a duplicate rather than risk a lost
point and leaves the reader to "group by series and timestamp first"; two whole probes were
redelivered in three days of development data, both around a ClickHouse restart.

`CHECK_RUNS_SQL` was corrected (2026-09-13) because it summed, and a redelivered probe therefore
reported twice the time its request took. Three sibling statements in
`apps/api/src/nexovue_api/synthetics/queries.py` still read the same rows without collapsing them:

| Statement | Aggregate | Effect of one redelivered probe |
|---|---|---|
| `CHECK_SUMMARIES_SQL` | `countIf` runs / successful_runs, `avgIf` / `quantileIf` duration | one extra run counted; mean and p95 weighted toward that probe |
| `CHECK_TRENDS_SQL` | `countIf` runs / successful_runs, `avgIf` duration | same, within one bucket |
| `PHASE_BREAKDOWN_SQL` | `avg` mean, `count` samples | samples over-reported; the mean is unchanged when the duplicate is the bucket's only probe |

**Left as-is deliberately.** The writer's stated obligation covers summing, which these do not do,
and the harm is an over-count of one rather than a doubling. Fixing them costs an inner
`GROUP BY metric_name, series_hash, timestamp` on three more statements. **Open for the founder:**
whether a run count that can over-report by one is acceptable, or whether uptime percentage is
exact-or-nothing.

The same question applies beyond synthetics — `dbstats/queries.py` and `infrastructure/queries.py`
both `count()` and `avg()` over metric rows — and has not been audited.

## Code evidence

`apps/api/src/nexovue_api/checks/router.py`, `synthetics/router.py`, scheduler `probes/tasks.py`,
`probes/http_check.py`, `probes/browser.py`, and `probes/addresses.py`, plus
`apps/web/src/features/synthetics/SyntheticsScreen.tsx` are authoritative.
