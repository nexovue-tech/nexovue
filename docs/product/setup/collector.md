# Using an OpenTelemetry Collector

A Collector is a process that receives telemetry, does something to it, and
sends it on. It is not required — every SDK can export to nexovue directly — and
for a single service it is a container you do not need.

It starts earning its keep at around the third service, and there are four
specific reasons.

**One place holds the credential.** Your services export to the Collector with
no key at all. Rotating the ingest key is one restart rather than a redeploy of
every service.

**One place decides the destination.** Changing where telemetry goes, or sending
it to two places during a migration, is a config change rather than a fleet-wide
redeploy.

**It buffers.** If the network to nexovue is down for ten minutes, a Collector
with a persistent queue holds the data and delivers it when the network returns.
An SDK's in-process buffer holds a few seconds and then drops.

**It adds what the SDK cannot see.** Kubernetes pod and namespace, EC2 instance
id and availability zone, host CPU and memory. The SDK inside your process does
not know any of it; the Collector does.

## The minimum configuration

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
      grpc:
        endpoint: 0.0.0.0:4317

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000
    send_batch_max_size: 2000

exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/nexovue]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/nexovue]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/nexovue]
```

Four things in there are specific to nexovue.

**`otlphttp`, not `otlp`.** The exporter named `otlp` in a Collector config is
the gRPC one. nexovue does not publish a gRPC port on the hosted endpoint today,
so it is `otlphttp`. Your services can still speak gRPC *to the Collector* — the
receiver block above accepts both — and the Collector translates.

**The endpoint is a base URL.** The `otlphttp` exporter appends `/v1/traces`,
`/v1/logs` and `/v1/metrics` itself. Writing the path into `endpoint` produces
`/v1/traces/v1/traces` and a 404.

**`${env:NEXOVUE_KEY}` rather than the key itself.** A Collector config is a file
you will commit. The key comes from the environment.

**`send_batch_max_size`.** nexovue refuses a request larger than 4 MiB
compressed, or 16 MiB once decompressed, with a 413. The Collector does not know
that limit and will happily assemble a batch past it, which then fails on every
retry. A ceiling on the batch is what stops one enormous trace becoming a
permanent export failure.

`otlphttp` compresses with gzip by default, which nexovue accepts. Leave it on.

## Do not sample in the Collector

This is the one that costs you something you cannot get back.

nexovue derives your request counts, error counts and latency percentiles at
ingest, from everything that arrives, and samples afterwards for storage. That
is why the rates on every screen are exact even though only a fraction of traces
are kept: the counting happens before the discarding.

A `tail_sampling` or `probabilistic_sampler` processor in your Collector discards
traces *before* nexovue ever sees them. The counters then count the sample rather
than the traffic, and every rate in the console is wrong by whatever factor you
sampled at — silently, because nothing anywhere knows what was dropped upstream.

Send everything. nexovue keeps every errored trace, every trace over 2 seconds,
one healthy trace in ten, and the first 100 traces a project ever sends. If
those thresholds are not what you want, change them under **Settings →
Projects** rather than in the Collector.

The exception is a Collector deliberately placed to cut cost before egress, on a
link where egress is the binding constraint. If you do that, know that you are
trading exact rates for bandwidth, and say so somewhere your team will find it.

## Making it survive a network outage

```yaml
exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 300s
    sending_queue:
      enabled: true
      num_consumers: 4
      queue_size: 1000
      storage: file_storage

extensions:
  file_storage:
    directory: /var/lib/otelcol/queue

service:
  extensions: [file_storage]
```

`storage: file_storage` is what makes the queue survive a Collector restart.
Without it the queue is in memory and a restart loses whatever was waiting.

nexovue sends a `Retry-After` header on every refusal where waiting actually
helps, and the Collector honours it.

**A 429 with no `Retry-After` means something different**, and it is worth
knowing before you see one: it means your plan's monthly ingest allowance is
spent. The header is omitted deliberately, so that a well-behaved exporter drops
the batch instead of retrying for the rest of the month. Retrying it spends your
egress to learn nothing. The refusal body names the date the allowance returns.

## Adding what the SDK cannot see

```yaml
processors:
  resourcedetection:
    detectors: [env, system, ec2]   # or: gcp, azure, eks, ecs
    override: false

  k8sattributes:                    # in-cluster only
    passthrough: false
```

`override: false` matters: a service that set `service.name` itself keeps it, and
detection only fills in what is missing. With `override: true` a detector can
rename every service in your cluster to the host name, which is a confusing
afternoon.

`k8sattributes` needs a ServiceAccount with read access to pods, and it fills in
pod name, namespace, node and workload for everything passing through — which is
the thing that removes the per-Deployment `fieldRef` block from
[kubernetes.md](platforms/kubernetes.md).

## Protecting the Collector from itself

```yaml
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128
```

Put `memory_limiter` **first** in every pipeline's processor list, before
`batch`. It refuses new data when memory is tight, which is a recoverable
failure. Without it a Collector that falls behind grows until the kernel kills
it, and everything queued dies with it.

## Sending to two places at once

The whole reason to own a Collector during a migration:

```yaml
exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}
  otlphttp/incumbent:
    endpoint: https://otlp.example-vendor.com
    headers:
      authorization: ${env:INCUMBENT_TOKEN}

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/nexovue, otlphttp/incumbent]
```

Both destinations get everything. Run it for a week, compare, then delete a
line.

One caveat: the two exporters share a pipeline, so a destination that is down
applies backpressure to the pipeline feeding both. Each exporter having its own
`sending_queue` is what keeps one vendor's outage from stalling the other.

## Where to run it

| Shape | When |
|---|---|
| Sidecar, one per pod or task | Per-service isolation, no shared failure domain. Most containers, most cost |
| DaemonSet, one per node | The usual Kubernetes answer. Node-local, collects host metrics too |
| Deployment, a few replicas behind a service | Central processing, tail sampling, fan-out. Needs to be sized and watched |
| One process on the host | EC2 and bare metal. See [aws-ec2-and-bare-metal.md](platforms/aws-ec2-and-bare-metal.md) |

A node-local Collector plus, where you need it, a central one behind it is the
arrangement most estates end up at. Start with one and add the second when you
have a reason.

## Which distribution

`otel/opentelemetry-collector-contrib` has every receiver, processor and
exporter, including the `k8sattributes` and `resourcedetection` processors above.
`otel/opentelemetry-collector` is the core distribution and does not.

Use contrib unless image size is a constraint you have actually measured. Pin a
released version rather than `latest`: a Collector that changes underneath a
running estate changes what your telemetry looks like.

## Checking it works

The Collector's own diagnostics tell you whether it is receiving and whether it
is exporting, which is exactly the split you need when nothing arrives:

```yaml
service:
  telemetry:
    logs:
      level: info
    metrics:
      level: detailed
```

`otelcol_receiver_accepted_spans` going up and `otelcol_exporter_sent_spans`
staying flat means the problem is between the Collector and nexovue —
credential, network, or endpoint. Both flat means your services are not reaching
the Collector, and nothing past it is worth looking at yet.

[troubleshooting.md](troubleshooting.md) covers the rest.
