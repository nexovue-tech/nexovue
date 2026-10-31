# Running nexovue with Docker and Compose

nexovue ships as two public Docker images, and a set of Compose files that run them with the databases
they need. You need no account to pull them.

| Image | What it runs |
|---|---|
| `ghcr.io/nexovue-tech/nexovue` | The API, the console and the scheduler |
| `ghcr.io/nexovue-tech/nexovue-pipeline` | The receiver and the worker |

Both are built for amd64 and arm64. Each release is tagged with its version (`0.1.0`), its minor version
(`0.1`) and `latest`.

```bash
docker pull ghcr.io/nexovue-tech/nexovue:0.1.0
docker pull ghcr.io/nexovue-tech/nexovue-pipeline:0.1.0
```

Pin a version rather than using `latest`, so that nothing changes until you choose to upgrade.

## Why `docker run` alone is not enough

One image does nothing by itself. A running nexovue is 11 containers: the services from these two images,
plus Kafka, ClickHouse, Postgres, SeaweedFS and a Caddy proxy. Compose starts them in the right order,
and a setup step prepares the databases before any service starts.

## The quickest way: the kit

The kit is the Compose files and the `nexovue` command that drives them. On a machine with Docker and
8 GB of memory for it:

```bash
curl -fsSL https://github.com/nexovue-tech/nexovue/releases/latest/download/nexovue-0.1.0.tar.gz | tar -xz
cd nexovue-0.1.0
./nexovue quickstart
```

The command generates every password and key, starts everything, and prints where to sign in:

- The console: **http://localhost:8080**
- Telemetry from your applications: **http://localhost:4318** (OTLP over HTTP)

The quickstart is for trying nexovue, not for production: it has no TLS and no backups, and nothing can
reach it from another machine. To run it for real, follow [the install guide](install.md), which adds
certificates, backups and upgrades.

## Using Compose directly

The quickstart writes its settings to `~/.nexovue/quickstart/nexovue.env`. From the kit's folder, you can
then use Docker Compose yourself:

```bash
alias nv='docker compose --project-name nexovue-quickstart \
  --env-file ~/.nexovue/quickstart/nexovue.env \
  -f compose/nexovue.yaml -f compose/nexovue.quickstart.yaml'

nv ps                # what is running, and whether it is healthy
nv logs -f api       # follow one service's logs
nv down              # stop everything; your data stays
nv up -d --wait      # start it again
```

`./nexovue quickstart remove` deletes the containers and everything they recorded.

**On a server,** the install keeps its settings in `/opt/nexovue/nexovue.env` and the files under
`/opt/nexovue/current`. Use `sudo nexovue status`, `upgrade`, `backup` and the other
[commands](install.md#commands) rather than Compose: an upgrade backs up first and moves the databases
forward in the right order, which `docker compose up` with a new version does not do for you.

## Writing your own settings

Every setting the Compose files need is named in `compose/nexovue.yaml`, and Compose refuses to start and
names the variable if one is missing. The simplest way to get a correct file is to let
`./nexovue quickstart` write one and copy it. Keep it private: it holds every password. Generate new
secrets for each install, never reuse the ones from another.

## Changing how it runs

Don't edit the kit's Compose files, because an upgrade replaces them. Put your changes in a Compose file
of your own and name it in the settings, as [the install guide](install.md) describes:

```bash
NEXOVUE_EXTRA_COMPOSE=/opt/nexovue/my-changes.yaml
```

## Your application in Docker

To send telemetry from your own containers to nexovue, see
[Docker](../product/setup/platforms/docker.md) and
[Docker Compose](../product/setup/platforms/docker-compose.md).
