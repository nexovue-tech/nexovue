# Sending data to nexovue

nexovue accepts OpenTelemetry. There is no nexovue agent to install and no
nexovue SDK to import on the server side: the endpoint speaks OTLP, which is the
same protocol every OpenTelemetry SDK, the OpenTelemetry Collector and most
other observability tools already speak.

That means setup is one of two jobs, and which one you are doing decides where
to go next.

| Where you are | Start here |
|---|---|
| Your service already exports OpenTelemetry somewhere | [already-instrumented.md](already-instrumented.md) — two variables, a restart, done |
| Your service has never been instrumented | Your language's page under [languages/](#languages) |
| You run an OpenTelemetry Collector | [collector.md](collector.md) |
| You want page-load, web-vital and JavaScript-error data from a browser | [browser.md](browser.md) |
| You have set it all up and nothing has arrived | [troubleshooting.md](troubleshooting.md) |

## The two variables

Everything on the server side comes down to these, and they are the standard
OpenTelemetry variables rather than nexovue's own. An SDK, a Collector or a
language agent reads them without knowing nexovue exists.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
```

**`OTEL_EXPORTER_OTLP_ENDPOINT`** is the base URL of the nexovue receiver. It is
a base, not a full path: an OpenTelemetry SDK appends `/v1/traces`, `/v1/logs`
or `/v1/metrics` to it itself. Adding the path yourself produces
`/v1/traces/v1/traces`, which is a 404.

The console shows you the exact value when you sign up, and again on the
onboarding screen. `https://ingest.nexovue.com` above is an example. In local
development the receiver's published port is `http://localhost:14318`.

> **The hosted hostname is not settled.** nexovue is not deployed yet, so there
> is no published ingest hostname to put here. Every example in this section
> uses `https://ingest.nexovue.com` as a placeholder. The real value is whatever
> the console hands you — it comes from the `OTLP_ENDPOINT` setting on the API,
> which falls back to `http://localhost:14318` when a deployment does not set
> it. A deployment that leaves it unset will hand its customers a localhost
> endpoint, so it must be set alongside `NEXOVUE_INGEST_HOST`.

**`OTEL_EXPORTER_OTLP_HEADERS`** carries your ingest key in the header
`x-nexovue-key`. The variable holds a comma-separated list of `name=value`
pairs, so the whole value is `x-nexovue-key=` followed by the key. The key
begins `nvk_live_` and is 31 characters in total.

The key is shown once, at the moment it is created, and never again. nexovue
stores a SHA-256 hash of it, so nobody — including nexovue — can read it back.
If you lose it, revoke that key under **Settings → API keys** and mint another.

### The third variable you will probably need

```
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

The endpoint above is OTLP over HTTP. Which protocol an OpenTelemetry SDK picks
when you do not tell it is not the same across languages, and has changed
between versions within a language. An SDK that defaults to gRPC, pointed at an
HTTP endpoint, fails with a connection or protocol error that names neither
problem.

Setting it costs nothing and removes a whole class of first-day failure. Every
page in this section sets it.

## What the endpoint accepts

| | |
|---|---|
| Protocol | OTLP over HTTP — `http/protobuf` and `http/json` |
| Paths | `/v1/traces`, `/v1/logs`, `/v1/metrics` |
| Method | `POST` only |
| Content types | `application/x-protobuf`, `application/json` |
| Compression | `gzip`, or none. Nothing else |
| Credential | the `x-nexovue-key` header |
| Request size | 4 MiB on the wire, 16 MiB after decompression |
| Rate | 1000 requests per second per key |

Refusals come back as a `google.rpc.Status` — OTLP's own error shape, which
every OpenTelemetry exporter already understands — and carry a `Retry-After`
header whenever waiting will actually help. [troubleshooting.md](troubleshooting.md)
lists what each status code means.

### OTLP over gRPC

The receiver implements OTLP/gRPC on port 4317, and it works against a local
receiver. **It is not published on the hosted ingest endpoint today.** The TLS
edge in front of the receiver proxies the HTTP paths only; gRPC needs its own
port and its own TLS termination, and that decision has not been taken.

If your exporter is configured for gRPC, switch it to `http/protobuf`. No signal
is missing over HTTP — all three go over both transports.

> **Not settled yet.** Whether OTLP/gRPC gets a public port, or is multiplexed
> on 443, is an open deployment decision. Until it is taken, treat gRPC as a
> local-development transport. The decision is recorded in
> `deploy/caddy/Caddyfile`.

## Languages

Each page covers both audiences: the minimum change if the service already
exports OpenTelemetry, and the full path from nothing.

- [Node.js](languages/nodejs.md)
- [Python](languages/python.md)
- [Go](languages/go.md)
- [Java](languages/java.md)

Nothing here is nexovue-specific beyond the two variables. If your language is
not listed, its OpenTelemetry SDK reads the same variables, and
[already-instrumented.md](already-instrumented.md) is the whole of what nexovue
adds.

## Platforms

Where to put the two variables, per place a service runs.

- [Docker](platforms/docker.md)
- [Docker Compose](platforms/docker-compose.md)
- [Kubernetes](platforms/kubernetes.md)
- [AWS ECS](platforms/aws-ecs.md)
- [AWS EC2 and bare metal](platforms/aws-ec2-and-bare-metal.md)
- [AWS Lambda](platforms/aws-lambda.md)

## One rule that applies everywhere

**The ingest key is a secret.** It grants the right to write telemetry into your
project, and anything holding it can spend your plan's monthly allowance. Keep
it out of version control, out of container images and out of anything a browser
downloads. Every platform page above uses that platform's own secret mechanism
rather than a literal in a config file.

The browser is the exception, and it is a different key: a page cannot hold a
secret, so browser monitoring uses a public site key restricted by origin. See
[browser.md](browser.md).
