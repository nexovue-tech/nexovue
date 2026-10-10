# Change: tests-do-not-need-the-demo — the gate seeds what three live suites took from the demo

## Classification

Infrastructure — test tooling. No product code changed.

## Current behavior and problem

Three live suites asserted on data only the OpenTelemetry demo produced, so `just check` failed
whenever the demo was not running — which, on 2026-10-10, it was not: its containers no longer
existed on the development machine.

| Suite | What it took from the demo |
|---|---|
| `test_synthetics_live.py` | Probe results — both development checks probed the demo's storefront, down since 2026-10-09 18:34 |
| `test_servicemap_live.py` | Service edges, including a failing one ("run `just otel-demo up`") |
| `test_dbstats_live.py` | `db.client.operation.duration` and `db.client.connection.count`, from the demo's Postgres |

`test_metrics_live.py` had already been freed (its histogram comes from `tools/gateseed`).

## Intended behavior

`just check` needs only the nexovue stack. The demo stays for realistic traffic and showing the product,
never required for the gate.

## What changed

`tools/gateseed`, which `just test-py` runs before the Python suites, now also:

- **Creates the gate's own synthetic check** once, by name — "Gate: API health", probing the API's
  liveness endpoint through the Docker host's published port (`http://host.docker.internal:18000/
  healthz`). Not `http://api:8000`: the API refuses a check aimed at one of our own service names
  (`checks/targets.py`, `INTERNAL_HOSTNAMES`), correctly. It signs in as the seeded owner, as
  `tools/otel-demo/doors.py` does (`api.go`).
- **Sends two calls** — a CLIENT span answered by a SERVER child, once to a healthy callee and once to
  a failing one — timestamped three minutes back, so their minute has already closed and the edges are
  written on the worker's next settle rather than a minute later (`ServiceEdges`).
- **Sends the two database gauges** with the attributes the database screens group by
  (`DatabaseReadings`).
- **Waits for each**: two new edges, four new database readings, and a passing probe of the gate's
  check within the last hour. The probe is not this run's row — the scheduler probes on its own clock —
  so it is a new kind of wait, `Arrival.Exists`, and it must carry a time window (its test says so).

The two suites' failure messages now say `just gate-seed` instead of `just otel-demo up`.

## Risks and rollback

- **The development seed credentials have defaults in `config.go`** (`nexovue@nexovue.com` /
  `nexovue-dev`), as `doors.py` and the ClickHouse password beside them already do. Development stack
  only; overridable by environment.
- **A fresh stack's first gate run waits for the new check's first probe** — measured 48.5 s, once.
  Every later run: 12.1 s, as before (12.9 s).
- Rollback: revert the commit. The gate check stays in the development database; harmless.

## Status

See [`status.md`](status.md).
