# AWS EC2 and bare metal

A service running directly on a host, under systemd. The variables go in a
systemd drop-in, and the key goes in a file that only root can read.

## The key file

```bash
install -o root -g root -m 600 /dev/null /etc/nexovue/ingest.env
cat > /etc/nexovue/ingest.env <<'EOF'
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
EOF
```

`<<'EOF'` with quotes, so the shell expands nothing inside. `0600` and root, so
the service account cannot read it — systemd reads the file as root before
dropping privileges, which is what makes that work.

## The drop-in

```bash
systemctl edit checkout
```

```ini
[Service]
Environment=OTEL_SERVICE_NAME=checkout
Environment=OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
Environment=OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
EnvironmentFile=/etc/nexovue/ingest.env
```

```bash
systemctl daemon-reload
systemctl restart checkout
```

A drop-in rather than editing the unit file: the package's own unit stays
untouched, so a package upgrade cannot overwrite your configuration and cannot be
blocked by it.

`systemctl show checkout --property=Environment` prints what the service will
actually get, which is worth checking once before you start hunting elsewhere.
It prints the key, so do not paste the output anywhere.

## Attaching the host's identity

```ini
Environment=OTEL_RESOURCE_ATTRIBUTES=service.version=1.4.0,deployment.environment.name=production
```

On EC2, the language SDKs mostly do not read instance metadata on their own. If
you want instance id, availability zone and instance type on every span, that is
what a Collector's `ec2` resource detector is for — see below.

## Shutting down cleanly

systemd sends SIGTERM on `systemctl stop` and on a restart. A batch processor
holds spans for a few seconds, so a service that exits immediately on SIGTERM
loses the last batch on every deploy.

Handle the signal, shut the tracer provider down, then exit. The language pages
show where. `TimeoutStopSec` in the unit needs to be longer than your flush, and
its default of 90 seconds normally is.

## A Collector on the host

On a long-lived host, a Collector running as its own service alongside your
application is usually worth it. Three reasons that apply specifically here:

**Host metrics for free.** The `hostmetrics` receiver reports CPU, memory, disk
and network for the machine, with no agent of ours and no code in your service.
Those are the readings that explain a slow service when the traces do not.

**Resource detection.** The `resourcedetection` processor with the `ec2`
detector stamps instance id, type, region and availability zone onto everything
passing through, so you can tell two instances of the same service apart.

**One credential on the host.** Your services export to `http://localhost:4318`
with no key. Only the Collector's own config file holds one.

```yaml
# /etc/otelcol/config.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
  hostmetrics:
    collection_interval: 30s
    scrapers:
      cpu:
      memory:
      filesystem:
      network:

processors:
  resourcedetection:
    detectors: [env, ec2]
  batch:

exporters:
  otlphttp/nexovue:
    endpoint: https://ingest.nexovue.com
    headers:
      x-nexovue-key: ${env:NEXOVUE_KEY}

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [resourcedetection, batch]
      exporters: [otlphttp/nexovue]
    metrics:
      receivers: [otlp, hostmetrics]
      processors: [resourcedetection, batch]
      exporters: [otlphttp/nexovue]
    logs:
      receivers: [otlp]
      processors: [resourcedetection, batch]
      exporters: [otlphttp/nexovue]
```

`endpoint: 127.0.0.1:4318` rather than `0.0.0.0:4318`. The Collector's OTLP
receiver has no authentication: bound to all interfaces on a host with a public
IP, it accepts telemetry from the internet and forwards it into your project on
your key.

The `${env:NEXOVUE_KEY}` reference means the key lives in the Collector's own
systemd `EnvironmentFile` rather than in the YAML, which is likely to be in
version control.

[collector.md](../collector.md) has the rest.

## Checking reachability from the host

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X GET https://ingest.nexovue.com/v1/traces
```

`405` means you reached the receiver: the path exists and accepts POST only. A
timeout or a DNS failure is a network or security-group problem, and neither has
anything to do with your instrumentation. [troubleshooting.md](../troubleshooting.md)
covers the rest.
