# Docker Compose

Compose reads a `.env` file next to the compose file automatically, and
interpolates `${...}` into the YAML. That gives you one place for the two
variables and no key in the committed file.

## The shape

`.env`, which is in `.gitignore`:

```
NEXOVUE_ENDPOINT=https://ingest.nexovue.com
NEXOVUE_KEY=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
```

`compose.yaml`, which is committed:

```yaml
x-nexovue: &nexovue
  OTEL_EXPORTER_OTLP_ENDPOINT: ${NEXOVUE_ENDPOINT:?set NEXOVUE_ENDPOINT in .env}
  OTEL_EXPORTER_OTLP_HEADERS: x-nexovue-key=${NEXOVUE_KEY:?set NEXOVUE_KEY in .env}
  OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf

services:
  checkout:
    image: checkout:1.4.0
    environment:
      <<: *nexovue
      OTEL_SERVICE_NAME: checkout

  inventory:
    image: inventory:2.1.0
    environment:
      <<: *nexovue
      OTEL_SERVICE_NAME: inventory
```

Three things about this that are deliberate.

**The anchor.** `x-nexovue: &nexovue` is a Compose extension field — Compose
ignores keys starting `x-` — and `<<: *nexovue` merges it into each service.
Three services means three merges, not three copies to keep in step.

**`OTEL_SERVICE_NAME` per service, never in the anchor.** It is the one value
that must differ. Put it in the shared block and every container reports as the
same service, the service list has one row, and the service map has one node.

**`:?message`.** Compose fails to start rather than interpolating an empty
string. Without it, a missing `.env` produces containers configured with an empty
endpoint, which fail silently for as long as nobody looks.

## Adding it to an existing compose file

If you would rather not restructure, `env_file` on each service is the smaller
change:

```yaml
services:
  checkout:
    env_file: [nexovue.env]
    environment:
      OTEL_SERVICE_NAME: checkout
```

`nexovue.env` holds the three lines verbatim, with no quoting and no `${}`:

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

`env_file` values are passed through literally; `environment` values are
interpolated by Compose first. That difference is why a `$` in a value behaves
differently between the two, and why the file above needs no escaping.

## Pointing at a local nexovue

Containers on a Compose network reach your host through `host.docker.internal`
on Docker Desktop:

```
NEXOVUE_ENDPOINT=http://host.docker.internal:14318
```

On plain Linux Docker that name does not exist unless you add it:

```yaml
services:
  checkout:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

`localhost` inside a container is the container. Pointing an exporter at
`http://localhost:14318` from inside one is the most common way to send nothing
and see no error.

## Adding a Collector

If you run several services in one Compose project, a Collector between them and
nexovue is worth the extra container: your services then export to
`http://collector:4318` with no key at all, and only the Collector holds the
credential.

```yaml
services:
  collector:
    image: otel/opentelemetry-collector-contrib:latest
    command: ["--config=/etc/collector.yaml"]
    volumes:
      - ./collector.yaml:/etc/collector.yaml:ro
    environment:
      NEXOVUE_ENDPOINT: ${NEXOVUE_ENDPOINT:?}
      NEXOVUE_KEY: ${NEXOVUE_KEY:?}

  checkout:
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: http://collector:4318
      OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
      OTEL_SERVICE_NAME: checkout
```

Pin the Collector image to a released version rather than `latest` before this
reaches anything you care about. [collector.md](../collector.md) has the
Collector's own configuration and the reasons you might want one.
