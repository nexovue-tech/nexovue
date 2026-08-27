# loadgen

Drives synthetic OTLP/HTTP traffic at the receiver and reports the numbers behind
the performance budgets in [§4 of the phase 1 plan](../../docs/delivery/phase-1-plan.md).

```sh
just load
```

That runs a one-minute test against the local dev stack, offering 12,000 spans/sec
— just above the budget — with no arguments needed.

## What it does

Spans come from [telemetrygen](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/cmd/telemetrygen),
imported as a library rather than shelled out to, so the version is pinned in
`go.mod` and `just load` needs nothing installed first.

Every export carries `x-nexovue-key`. The receiver runs its auth and rate-limit
gates *ahead* of decoding, so a run without a key would be measuring a receiver
we do not ship.

telemetrygen reports nothing about what happened to its exports — it neither
counts what the receiver accepted nor times the answer. So the generator sends to
a measuring proxy inside this process, which forwards to the receiver and records
the status, the span count and the latency of every request. **The clock covers
the call to the receiver only**: the generator's own loopback hop into the proxy
is outside the measured window.

For the queryable lag, the proxy hands one accepted trace per sample interval to
a tracker that polls ClickHouse until the row can be read back. Those are real
load traces, not injected probes, so what is timed is the pipeline the load is
already driving.

## Settings

All environment variables, all with working defaults.

| Variable | Default | Meaning |
|---|---|---|
| `LOADGEN_ENDPOINT` | `http://localhost:14318` | Receiver OTLP/HTTP base URL |
| `LOADGEN_INGEST_KEY` | dev key | Sent as `x-nexovue-key` |
| `LOADGEN_DURATION` | `60s` | How long to generate |
| `LOADGEN_WORKERS` | `8` | Generator workers |
| `LOADGEN_SPAN_RATE` | `12000` | Offered spans/sec across all workers; `0` is unthrottled |
| `LOADGEN_BATCH_SIZE` | `512` | Spans per export, matching the OTel SDK default |
| `LOADGEN_CHILD_SPANS` | `4` | Children per trace, so a trace is 5 spans |
| `LOADGEN_SERVICE_NAME` | `loadgen` | `service.name` on the spans, and what the lag query matches |
| `LOADGEN_REQUEST_TIMEOUT` | `10s` | Bound on one export |
| `LOADGEN_PROXY_ADDR` | `127.0.0.1:0` | Where the measuring proxy listens |
| `LOADGEN_CLICKHOUSE_URL` | `http://localhost:18123` | Where the lag tracker looks |
| `LOADGEN_CLICKHOUSE_USER` / `_PASSWORD` / `_DATABASE` | dev values | ClickHouse credentials |
| `LOADGEN_LAG_SAMPLE_INTERVAL` | `200ms` | Spacing of traces followed into storage |
| `LOADGEN_LAG_POLL_INTERVAL` | `250ms` | Poll period, and the resolution of every lag figure |
| `LOADGEN_LAG_DRAIN_TIMEOUT` | `60s` | How long to keep looking after the load stops |

## Reading the output

- **spans accepted** counts only what came back `200`. Refused spans are reported
  separately, because reporting refused load as throughput is exactly the mistake
  the budget table exists to catch.
- **throughput / sustained** is the median whole second of the run. The first and
  last seconds are partial and are dropped.
- **accept latency** percentiles use nearest rank, so every figure printed is a
  latency that was actually observed — nothing is interpolated.
- **queryable lag** is accurate to one poll interval and never better.

## Known limits

- **The generator saturates before the receiver does.** telemetrygen installs one
  global `TracerProvider`, so a process has a single batch span processor and
  exports one request at a time. One process tops out around 60k spans/sec
  against the dev stack while the receiver is still near idle. To drive the
  receiver harder, run several `loadgen` processes at once and add up their
  reported throughput.
- **Receiver CPU and memory are not measured here.** Read them from
  `docker stats` during a run.
- **The trace-split re-encode share is not measured here.** The receiver exposes
  no profile endpoint, and `tracesplit` is internal to `apps/receiver`, so an
  honest figure needs a benchmark or a profile inside that module.
