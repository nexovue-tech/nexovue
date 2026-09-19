# Continuous profiling

> **Preview.** Profiles rest on OpenTelemetry's profiles signal, which is still in
> development upstream. The format can change between Collector releases; nexovue
> follows it, and this page names the Collector version it was tested with.

A profile says which code a service spends its time and memory in. nexovue shows it
as a flame graph on the **Profiles** screen, beside your traces and metrics. Nothing
changes in your code: profiles come from an OpenTelemetry Collector.

There are two ways to collect them. Most teams start with the first.

| | eBPF profiler | `pprof` receiver |
|---|---|---|
| What it profiles | Every process on a Linux host | One Go service (or any pprof file) |
| Code change | None | None if the service already serves `/debug/pprof` |
| Profile types | CPU | CPU, heap, goroutines, mutex, block |
| Runs on | Linux ≥ 5.10, privileged | Anywhere a Collector runs |

## The eBPF profiler

The OpenTelemetry eBPF profiler watches every process on a Linux host from the
kernel and samples where each one is running, about 20 times a second. It names
frames for Go, Java and the JVM languages, Python, Node.js, .NET, Ruby, PHP, Perl,
Erlang/Elixir, and native C, C++ and Rust with symbols. A native frame without
symbols is shown by its binary, marked *unsymbolized*.

**What it needs.** Linux 5.10 or newer, and root with the host's process namespace:
`--privileged --pid=host` in Docker, or a privileged DaemonSet with `hostPID: true`
in Kubernetes. That is a real grant — the profiler can see every process on the host
— and it is yours to make. macOS and Windows hosts cannot run it; use the `pprof`
receiver there.

It is its own Collector distribution, `otelcol-ebpf-profiler`:

```yaml
receivers:
  profiling:
    samples_per_second: 19

exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}

service:
  pipelines:
    profiles:
      receivers: [profiling]
      exporters: [otlphttp/nexovue]
```

```sh
docker run -d --name otel-profiler --privileged --pid=host \
  -v /sys/kernel/debug:/sys/kernel/debug \
  -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
  -v /proc:/proc \
  -v "$PWD/profiler.yaml:/etc/otelcol/config.yaml:ro" \
  -e NEXOVUE_KEY \
  otel/opentelemetry-collector-ebpf-profiler:0.161.0 \
  --config /etc/otelcol/config.yaml --feature-gates=service.profilesSupport
```

**`--feature-gates=service.profilesSupport` is required.** Without it the Collector
refuses a `profiles` pipeline and does not start.

**Service names come from the process.** A process started with
`OTEL_SERVICE_NAME` — as every OpenTelemetry-instrumented service is — is profiled
under that name. Anything else appears as `unknown_service:<executable>`, the way
OpenTelemetry's own SDKs name a service nobody named. The Profiles screen lists the
busiest services first, so `dockerd` and friends sit below your own.

## The `pprof` receiver

For a Go service that already serves `net/http/pprof`, the contrib Collector's
`pprof` receiver scrapes it. pprof carries no service name, so set one with the
`resource` processor:

```yaml
receivers:
  pprof:
    remote:
      endpoint: http://checkout:6060/debug/pprof/heap
      collection_interval: 30s

processors:
  resource:
    attributes:
      - key: service.name
        value: checkout
        action: upsert

exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}

service:
  pipelines:
    profiles:
      receivers: [pprof]
      processors: [resource]
      exporters: [otlphttp/nexovue]
```

Run it with `otel/opentelemetry-collector-contrib:0.161.0` and the same
`--feature-gates=service.profilesSupport`. One receiver per profile type: point a
second at `/debug/pprof/profile?seconds=10` for CPU. Each type is its own choice on
the Profiles screen — a heap profile shows memory in bytes, never mixed with CPU.

## Linking profiles to spans

A span's detail shows **Profile** when samples were recorded while that span was
running. That needs the profiler to know which span each thread is serving, and
OpenTelemetry is still standardising how an SDK shares that with the eBPF profiler.
Until it does, most samples arrive without a link, and the span says so — the
service's profile over the same minutes is one click away instead.

## What it costs

Profile samples are counted on the **Usage** screen per day. While profiling is in
preview they do not count against your plan's allowance. Instead, each ingest key may
send up to 20,000 samples a second — a large fleet of busy hosts — and past that the
receiver answers `429` with a `Retry-After`, which the Collector honours. Profiles are
kept for your project's retention, up to 14 days.

## When nothing appears

- **The Collector will not start** — the feature gate is missing.
- **It starts, but nothing arrives** — the Collector's log names the refusal. A
  `401` is the key; a `400` naming a table index is a format from a much older or
  newer Collector than 0.161.0; a `413` is one request whose stacks write out to more
  than a million frames — lower the batch size.
- **Some exports are refused with `429`** — the key sent more than 20,000 samples in a
  second. The Collector retries them; if it happens all the time, split the fleet
  across keys or lower `samples_per_second`.
- **Only `unknown_service:*`** — the processes have no `OTEL_SERVICE_NAME`; set it,
  or the `resource` processor above.
- **A service is missing** — it used no CPU in the window. An idle service has
  nothing to sample.
