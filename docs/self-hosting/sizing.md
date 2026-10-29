# Sizing a server

Every service has a memory ceiling, so one that misbehaves cannot take the server down with it. The
defaults suit the smallest server we support; a larger one is given more through `nexovue.env`.

## The smallest server: 16 GB, 4 CPUs

The defaults add up to about 13 GB of ceilings, leaving the rest to the operating system and the page
cache ClickHouse reads through. Most services use well under their ceiling.

| Service | Ceiling | Setting |
|---|---|---|
| ClickHouse (telemetry) | 6 GB | `CLICKHOUSE_MEMORY`, `CLICKHOUSE_MAX_SERVER_MEMORY` |
| Kafka (the buffer in front of it) | 2 GB | `KAFKA_MEMORY`, `KAFKA_HEAP_OPTS` |
| Postgres (accounts and settings) | 1 GB | `POSTGRES_MEMORY` |
| Worker (writes telemetry) | 1.25 GB | `WORKER_MEMORY`, `WORKER_GOMEMLIMIT` |
| API, receiver, schedulers, edge, object store | about 3.5 GB together | — |

**Two settings that move together.** ClickHouse caps itself below its container's ceiling, and the
worker's garbage collector needs to know its ceiling:

- `CLICKHOUSE_MAX_SERVER_MEMORY` is about 80% of `CLICKHOUSE_MEMORY`, in bytes.
- `WORKER_GOMEMLIMIT` is about 85% of `WORKER_MEMORY`.

Raising one without the other leaves the extra memory unused, or lets the process be killed.

## A larger server

On 32 GB, for example:

```bash
CLICKHOUSE_MEMORY='14336M'
CLICKHOUSE_MAX_SERVER_MEMORY='12025908428'
KAFKA_MEMORY='4096M'
KAFKA_HEAP_OPTS='-Xmx2g -Xms2g'
POSTGRES_MEMORY='2048M'
WORKER_MEMORY='2560M'
WORKER_GOMEMLIMIT='2176MiB'
```

then `sudo /opt/nexovue/current/nexovue install` to apply them.

## Disk

Telemetry is most of it, and how much depends entirely on what is sent and for how long it is kept. A
rough guide, compressed as ClickHouse stores it: **a million spans take about 100 MB**. Kafka holds
7 days of raw telemetry before it is written, on top of that. Start with 40 GB free and watch the first
weeks: `docker system df -v` shows each volume.

The bundled object store holds uploaded source maps and check files, up to 100 GB by default
(`SEAWEEDFS_VOLUME_MAX`, in 256 MB volumes). To keep them in your own S3-compatible store instead, set
`S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` before installing.

## Three Kafka brokers

One Kafka broker is the default. If it stops — a crash, a full disk — telemetry is refused until it
returns; nothing already accepted is lost. With **`NEXOVUE_KAFKA_BROKERS=3`** set before the first
install, nexovue runs three, keeps every record on three of them, and accepts a write once two hold it,
so any one can stop without losing or refusing anything. It costs two more Kafka ceilings of memory.

On one server the three share its disk and power, so this guards against a broker failing, not the
server. Choose it when installing: topics are created with the number of brokers that exist then, and
moving an install from one broker to three means reassigning them by hand — ask us.

## The quickstart

`./nexovue quickstart` sets smaller ceilings — about 8 GB in total, far less in use — so it fits in
Docker Desktop's default. It is for trying nexovue, not for any amount of real traffic.
