# Sizing a server

Every service has a memory ceiling, so one that misbehaves cannot take the server down with it. The
defaults suit the smallest server we support; a larger one is given more through `nexovue.env`.

## The smallest server: 16 GB, 4 CPUs

The defaults add up to about 14.1 GB of ceilings (6 + 2 + 1 + 1.25 + about 3.5, and 0.375 for the
AI container, by arithmetic; this page said "about 13 GB" until 2026-11-08), and about 14.5 GB with
tracker connections on, leaving the rest to the operating system and the page cache ClickHouse reads
through. Whether what is left is
enough has not been measured on a 16 GB server. Most services use well under their ceiling.

| Service | Ceiling | Setting |
|---|---|---|
| ClickHouse (telemetry) | 6 GB | `CLICKHOUSE_MEMORY`, `CLICKHOUSE_MAX_SERVER_MEMORY` |
| Kafka (the buffer in front of it) | 2 GB | `KAFKA_MEMORY`, `KAFKA_HEAP_OPTS` |
| Postgres (accounts and settings) | 1 GB | `POSTGRES_MEMORY` |
| Worker (writes telemetry) | 1.25 GB | `WORKER_MEMORY`, `WORKER_GOMEMLIMIT` |
| API, receiver, schedulers, edge, object store | about 3.5 GB together | — |
| Tracker connections (`scheduler-connections`), only while `NEXOVUE_CONNECTIONS=yes` | 384 MB ceiling. **Read once, indicatively (2026-11-22):** about 250 MB of proportional memory for the worker, its forkserver and three children after a job each, read from the processes on a shared 4-CPU development VM, not from the container on a real server | — |
| AI explanations (`scheduler-insight`), idle until an organization turns AI on | 384 MB: four children kept under 48 MiB each beside the parent, by arithmetic; **not yet measured** at 4 or 6 children | `SCHEDULER_INSIGHT_REPLICAS=0` runs with no AI container at all |

**Case timelines (F22).** One case's timeline is one Postgres read. At the largest case nexovue
allows (90 days kept alive, a recurrence joining every 4 hours, 500 notes of 4,000 characters,
beside 150,000 other events), the console's first page took a median of 20 to 37 ms and the
oldest page 5 to 6 ms, over three runs of 50 reads (`tools/detection-seed/timeline_seed.py`,
2026-11-22, shared 4-CPU VM under load: indicative only). On one run the slowest 5% reached 265 ms.
The read scans the events table, so it grows with every organization's events, not only the
case's; it is not measured at millions of events.

A local model (Ollama, for example) is not in these figures: it runs outside nexovue, and needs its
own memory on top.

**Two settings that move together.** ClickHouse caps itself below its container's ceiling, and the
worker's garbage collector needs to know its ceiling:

- `CLICKHOUSE_MAX_SERVER_MEMORY` is about 80% of `CLICKHOUSE_MEMORY`, in bytes.
- `WORKER_GOMEMLIMIT` is about 85% of `WORKER_MEMORY`.

Raising one without the other leaves the extra memory unused, or lets the process be killed.

## Dashboards

Dashboards read ClickHouse through their own account, `nexovue_api_reader`, which every API screen uses. Two
server-side bounds keep API reads from starving the workers' inserts and the alert reads, which use another
account:

| Bound | Value | Where |
|---|---|---|
| Memory for all of the account's running reads | 2.4 GiB, a guess | `max_memory_usage_for_user` in `deploy/clickhouse/users.d/api-reader.xml` |
| Reads of the account at once | 12, a guess | `max_concurrent_queries_for_user`, same file |
| One read's memory | 1 GiB | `max_memory_usage`, same file |

Over the first, a panel reads "failed"; over the second, "refused". Both are **guesses**: the measurement of
2026-11-22 ([report](../research/f20-measurement-2026-11-22.md)) never came near either (the API account's
largest read held 10.7 MiB), on demo data of 15 services over 7 days. It says nothing about customer volume.

A self-hosted install may tune the limits on the **API** with `DASHBOARD_MAX_PANELS` (only lower than 30),
`DASHBOARD_MAX_SCANS_PER_READ`, `DASHBOARD_REFRESH_FLOOR_SECONDS` (30, 60 or 300), `DASHBOARD_SCANS_AT_ONCE`,
`DASHBOARD_RAW_WINDOW_DAYS` and `DASHBOARD_SINGLE_SERIES_RAW_WINDOW_DAYS`, and switch dashboards' reads off with
`DASHBOARD_SCANS_ENABLED=false`. The API refuses to start on a set that breaks the per-person read quota.
Hosted installs refuse them: the hosted limits are product policy. These names are not listed in
`deploy/compose/.env.example` today.

**Not yet established:** how many people can read dashboards at once. In the measurement, 20 people each reading
their own 30-panel dashboard at once on **one** API process gave HTTP 500 for 7 of 60 reads, because each read held
a Postgres connection while it waited (pool of 15); on two processes there were none, and 383 of 1,064 panels were
refused instead. Size for fewer than about 15 simultaneous dashboard readers per API process until that is resolved.

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
