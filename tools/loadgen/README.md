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
| `LOADGEN_SITE_KEY` | seeded dev key | Door 3's public browser key; empty turns the browser door off |
| `LOADGEN_BROWSER_ORIGIN` | `http://localhost:15173` | Sent as `Origin`; must be one the site key allows |
| `LOADGEN_BROWSER_VISITORS` | `12` | Simulated browsers, each keeping its own session id |
| `LOADGEN_BROWSER_PAGE_RATE` | `20` | Offered page loads/sec across all visitors; `0` is unthrottled |
| `LOADGEN_BROWSER_ERROR_RATE` | `0.15` | Share of page loads that also throw |
| `LOADGEN_BROWSER_SERVICE_NAME` | `acme-storefront` | `service.name` on the browser telemetry |
| `LOADGEN_BROWSER_RELEASE` | `2026.09.06-1` | `service.version`, which is what a source map is matched on |

## Reading the output

- **spans offered** is everything put on the wire to the receiver. It splits
  exactly three ways: accepted, refused, unknown.
- **spans accepted** counts only what came back `200`. Refused spans are reported
  separately, because reporting refused load as throughput is exactly the mistake
  the budget table exists to catch.
- **spans unknown** is this tool's contribution to the tolerance on an exact
  count. These went to the receiver and no answer could be read — a timeout, a
  reset, a cancelled caller. The receiver may already have published them before
  the answer was lost, and the SDK retries the batch either way, so a downstream
  count exceeding *accepted* by up to this many spans is correct behaviour, not
  loss and not double counting. It is a lower bound on the whole tolerance: what
  a partly-published refusal leaves downstream is the receiver's to account for,
  so `0` here does not by itself license an equality assertion.
- **never sent** is the opposite case: the export failed before any connection to
  the receiver existed, so those spans are nowhere downstream and are not counted
  as offered. Only a dial failure earns this; anything after a socket exists is
  unknown, because Go's client cannot tell a request the receiver never read from
  one it read and acted on.
- **throughput / sustained** is the median whole second of the run. The first and
  last seconds are partial and are dropped.
- **accept latency** percentiles use nearest rank, so every figure printed is a
  latency that was actually observed — nothing is interpolated. Only answered
  requests contribute; an unanswered one would enter the request timeout as if
  the receiver had taken that long.
- **queryable lag** is accurate to one poll interval and never better.

## The browser door

Door 3 is driven beside the spans, because a receiver under one door's load is
not the receiver a customer has. It authenticates the way a page does — a public
site key plus an `Origin` header the key allows — and sends the same OTLP log
records the browser SDK sends, so a run exercises the door a customer reaches
rather than an approximation of it.

It goes straight at the receiver rather than through the measuring proxy. The
proxy's latency figures are the span numbers §4's budgets are written about, and
mixing a second signal into them would report a figure that means neither.

This is also the only thing that fills the RUM and Errors screens on a dev stack:
every other door has a generator and this one had none, so a freshly seeded stack
showed "No browser has reported yet" until somebody opened a browser by hand.

The default site key is the one `just seed` writes. A stack seeded before that
existed has no such key, and the run reports every export refused with the origin
to check — re-seed, or set `LOADGEN_SITE_KEY` to a key you minted.

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
