# If you already export OpenTelemetry

This is the whole of it: point your existing exporter at nexovue and restart.
Nothing about your instrumentation changes — not the SDK, not the spans, not the
attribute names.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

Use the endpoint the console shows you; the one above is an example.
[index.md](index.md) explains what each variable is and why the third one is
worth setting. [platforms/](platforms/) covers where to set them in Docker,
Kubernetes, ECS, EC2 and Lambda.

## Check these four things before you restart

**You are pointing at a base URL, not a path.** OpenTelemetry SDKs append
`/v1/traces` themselves. If your old endpoint ended in `/v1/traces`, drop that
part.

**You are on HTTP, not gRPC.** If your current exporter is gRPC — port 4317,
`otlptracegrpc`, `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` — switch it. nexovue does
not publish a gRPC port on the hosted endpoint today. Traces, logs and metrics
all travel over HTTP, so nothing is lost by the change.

**Your services set `service.name`.** nexovue groups everything by it: the
service list, the service map, the per-service charts, and your bill. A resource
with no `service.name` is grouped under `(unknown)`, and every unnamed service in
your estate lands in that one bucket together. Set `OTEL_SERVICE_NAME`, or
`service.name` in `OTEL_RESOURCE_ATTRIBUTES`, per service.

**The key is a secret.** If you are pasting it into a file that is committed,
stop and use your platform's secret store instead.

## If you are sending somewhere else at the same time

Two destinations at once is a Collector's job, not an SDK's. Most SDKs configure
one OTLP endpoint, so the way to fan out is to export to a Collector you run and
give the Collector two exporters. [collector.md](collector.md) has the
configuration.

This is the sensible shape for a migration: run both destinations for a week,
compare, then remove the old one. It also means the cutover is a Collector
config change rather than a redeploy of every service.

## Signal by signal

| Signal | Accepted | What nexovue does with it |
|---|---|---|
| Traces | Yes | Trace list, trace detail, the service map, and the rates and latencies on every screen |
| Logs | Yes | The logs screen, correlated to traces by trace id |
| Metrics | Yes | Stored as sent. Gauges, sums, histograms, exponential histograms and summaries all land |

You do not have to send all three. Traces alone give you most of the console,
because the request rates, error rates and latencies are derived from spans
rather than needing separate metrics.

## Sampling, and why you should probably turn yours down

If you already sample in your SDK — a `ParentBased(TraceIDRatio)` sampler, say —
you are deciding what nexovue ever sees, and that decision is made before any
information about the request exists. A head sampler cannot know a request is
about to fail.

nexovue samples at the other end, after a whole trace has been collected:

- every trace containing an error is kept
- every trace slower than 2 seconds is kept
- one healthy trace in ten is kept
- the first 100 traces a project ever sends are kept whatever the rest of this
  says, so your first test request is there to look at

Request counts, error counts and latency rollups are computed **before** that
decision, from every trace. Dropping nine healthy traces in ten out of storage
leaves the counts, rates and percentiles exact.

So the useful arrangement is: send everything, let nexovue decide what to store.
If your own head sampling stays at 10%, nexovue's counters see 10% of your
traffic and every rate on every screen is wrong by a factor of ten.

The thresholds are per project and you can change all three — keep errors, the
slow threshold, and the healthy percentage — under **Settings → Projects**. The
values above are what a new project starts with.

## Nothing arrives

[troubleshooting.md](troubleshooting.md). The first thing to look at is
**Settings → API keys**: if your key reads "Never used", nothing has ever
authenticated with it, which narrows the problem to the exporter's configuration
rather than anything past the door.
