# Development Environment

> **Type:** Delivery reference · **Target machine:** Apple M4 Pro, 24 GB unified memory
> **Companion doc:** `phase-1-plan.md` (§4 performance budgets)
> **Verified against `justfile` and `deploy/compose/` on 2026-09-04.**

## The three commands worth knowing first

```
just fresh      wipe everything and rebuild: down-hard → dev → topics → migrate → seed
just dev        the core stack, nothing else
just otel-demo up   realistic traffic from the OpenTelemetry Demo, at our receiver
```

`just topics` is not optional after a `just down-hard`. Auto-creation is off on the broker, so a
wiped Kafka volume comes back with **no topics**, and the receiver then refuses every export with
`UNKNOWN_TOPIC_OR_PARTITION` — silently, at roughly 330 refusals a minute. `just fresh` calls it for
you; a hand-rolled `down-hard` + `dev` does not.

`just --list` is the full set. There is deliberately no combined `migrate-down`: a migration belongs
to one store, so `just migrate-down-pg` and `just migrate-down-ch` are named separately (ADR 0079).
Snapshots and restores follow the same rule — `just backup`, then `just restore-pg` or
`just restore-ch`, never both at once; the runbooks under `runbooks/` own that procedure.

## The constraint

Docker Desktop on macOS runs a Linux VM. **Measured 2026-08-26: the VM was allocated 8.3 GB** (`docker info`), which is below what the dev target needs — raise it to ~13 GB in Docker Desktop settings, leaving ~11 GB for macOS, editor and browser. Re-run `docker info` rather than trusting that number; it is a machine setting nothing in this repo enforces.

Two different budgets apply, and conflating them is what makes the numbers look wrong:

| | What it covers | Total |
|---|---|---|
| **Runtime** | the services doing their job | ~2.2 GB measured steady-state |
| **Dev ceiling** | the same services *plus the compilers they host* — `go build`, `pnpm install`, `uv sync` run inside the containers | ~12 GB of caps |

The dev ceiling is high because a dev container is also a build machine. Go's compiler parallelism drives peak RSS, so `GOFLAGS=-p=2` is set — without it `ch-go/proto` was OOM-killed at 320 MB. `pnpm install` was SIGKILLed at 512 MB. The app target needs neither, and `web` there is nginx at 128 MB rather than a Vite dev server at 1 GB.

## Memory budget

These are the limits in `deploy/compose/docker-compose.yaml` and `compose.infra.yaml` — the **dev**
target. The app and on-premise targets are smaller, because their containers host no compiler.

| Service | Dev limit | App-target limit | Note |
|---|---|---|---|
| ClickHouse | 3 GB | 3 GB | `max_server_memory_usage` capped in `config.d/memory.xml`; reduced mark cache |
| Kafka (KRaft) | 1.5 GB | 1.5 GB | `KAFKA_HEAP_OPTS=-Xmx1g` |
| Postgres | 1 GB | 1 GB | |
| Valkey | 0.5 GB | 0.5 GB | the compose **service is still named `redis`**; the image is `valkey/valkey` (ADR 0048) |
| SeaweedFS | 0.5 GB | 0.5 GB | |
| Receiver | 0.75 GB | 0.5 GB | dev also hosts `go build` under `air` |
| Worker | 1 GB | 0.75 GB | one process, all pipelines (ADR 0050); trace assembly is the hungry part |
| API | 1 GB | 0.81 GB | |
| Web | 1 GB | 0.125 GB | dev is the Vite dev server; the app target is nginx over a static build |
| **Core total** | **~10.25 GB** | | leaves ~2.75 GB headroom in a 13 GB VM |
| *+ loadgen* | 0.5 GB | — | `tools` profile |
| *+ scheduler-beat · light · browser* | 0.25 · 0.75 · 1.5 GB | 0.25 · 0.25 · 1.5 GB | `doors` profile; the browser container is Playwright |
| *+ insight · ollama* | 0.75 GB · 6 GB | 1 GB · 8 GB | `insight` profile, Phase 2 — `apps/insight` does not exist yet |

## Host ports

Every published port sits in a **nexovue-specific `1xxxx` block**, so the stack never
fights another project on this machine for 5432, 6379, 9000, 5173 or 8000. Container-side
ports are unchanged — only the host mapping moved.

| | Host | Container |
|---|---|---|
| Web (Vite) | **15173** | 5173 |
| API | **18000** | 8000 |
| Receiver OTLP HTTP · gRPC | **14318 · 14317** | 4318 · 4317 |
| Postgres | **15432** | 5432 |
| ClickHouse HTTP · native | **18123 · 19000** | 8123 · 9000 |
| Valkey | **16379** | 6379 |
| Kafka | **19092** | 29092 |
| SeaweedFS S3 · master | **18333 · 19333** | 8333 · 9333 |
| Ollama | **21434** | 11434 |

The OpenTelemetry Demo is not in this block: it is a separate compose project with its own ports,
and it reaches our receiver on 14318 like any other customer would.

Ollama is the exception to the pattern: a natively-installed Ollama already owns 11434,
and on this machine native is the right way to run it anyway.

Each has a `*_HOST_PORT` override if even these collide.

## Rules

**1. Every service declares a hard `deploy.resources.limits.memory`.** Without one, ClickHouse expands to fill the VM and everything else starts swapping. A limit turns "the laptop got slow" into a clear OOM on one container.

Use `deploy.resources.limits`, **not `mem_limit`**. The widespread advice that `deploy` requires Swarm is wrong for the `resources` subsection — Compose v2+ applies it with no Swarm anywhere. Only `mode`, `placement`, `update_config` and `endpoint_mode` are Swarm-only. Following that advice leaves every service with no limit at all, which is the failure this rule exists to prevent.

ClickHouse is a special case: it misreads cgroup limits in Docker, so its cap is set explicitly in `deploy/clickhouse/config.d/memory.xml` rather than inherited from the container.

**2. Compose profiles, not one big up.** `just dev` starts the core only. `just dev-doors` adds the three Celery containers, one of them Playwright; `just dev-tools` adds the load generator. `insight` and `ollama` are Phase 2 and start nothing today. The full stack is rarely needed at once.

**3. ClickHouse keeps a short TTL in dev.** The migrations carry the production TTLs — 30 days raw, 90 days metrics. `deploy/clickhouse/dev-retention.sql` shortens spans and logs to 3 days, and it is **not** run by `just migrate` or `just fresh`: apply it by hand after migrating, with the command in its own header comment. Without it the SSD quietly fills with test spans.

> ⚠️ That script is currently broken and has not been fixed here, because this is a documentation change. Its third statement is `ALTER TABLE metrics`, and there is no `metrics` table — ADR 0066 split metrics into five tables (`metrics_sum`, `metrics_gauge`, `metrics_histogram`, `metrics_exp_histogram`, `metrics_summary`). The statement fails with `UNKNOWN_TABLE`, so metric retention in dev is whatever the migrations set. `trace_summaries`, `span_trace_lookup`, `endpoint_stats_1m` and `service_edges_1m` are not shortened either.

**4. Playwright: arm64 image, `--ipc=host`, `shm_size: 1gb`.** The 64 MB `/dev/shm` default is the most common cause of Chromium instability in Docker. The official images ship ARM64 variants covering Chromium, Firefox and WebKit — the Apple Silicon Rosetta problem applies to native installs, not the container.

## Development is not production's shape

`just dev` runs **nine** containers: the five infrastructure services plus
`receiver`, `worker`, `api` and `web`. Production runs **fourteen** (ADR 0052).
The difference is deliberate, not drift:

| | Development | Production |
|---|---|---|
| Web | **Vite dev server** with HMR | Static build behind the proxy |
| Proxy | none — services are reached directly | Caddy, terminating TLS |
| Doors 2 and 4 | `--profile doors` | always on |
| Insight, Ollama | `--profile insight` / `--profile ollama` | defined, both behind `--profile insight`; Phase 2, `apps/insight` does not exist yet |
| Celery | beat, light and browser as separate containers | the same three |

The rule: development optimises for reading logs and restarting one thing at a
time; production optimises for failure domains. Neither should be bent toward
the other.

On this machine Ollama should run **natively rather than in the `ollama`
profile** — Docker on macOS has no Metal passthrough, so the container is
CPU-only. `LLM_BASE_URL` therefore defaults to `host.docker.internal`.

## Running the OTel Demo (P1-62) — the daily traffic source

**This section was wrong until 2026-09-04 and cost time.** It said the demo needs 4–8 GB, does not coexist with our stack, and has to be run by hand against a raised Docker allocation. None of that is true any more: `tools/otel-demo/` trims the upstream compose files and drives them for you.

```
NEXOVUE_INGEST_KEY=nvk_live_devlocaldevlocaldevloc just otel-demo up
just otel-demo stats     memory, this stack and the demo's
just otel-demo logs      follow every demo container
just otel-demo down      stop the demo only; `just down` also takes it with it
```

**Measured 2026-08-30, images already pulled: 1641 MiB resident across 20 containers, about four minutes from `up` to the first full service map.** It fits alongside the core stack in a 13 GB VM, which is why it replaced the hand-written demo shop as the everyday traffic source rather than staying a periodic gate.

What runs: everything upstream's core and full compose files define, minus three — `otel-collector` (we are the backend; its only job there was to be one), `flagd-ui` and `telemetry-docs`, which have no callers. That leaves 20 containers, **18 of them reporting telemetry, in ten languages**, including the Kafka queue hop that produces span-link edges in the service map. The upstream clone is pinned to `3.0.0`; `latest` would silently change what the gate measured between runs.

`NEXOVUE_OTEL_DEMO_SCOPE=core` drops Kafka, accounting and fraud-detection, saving roughly 800 MB on a machine that cannot spare it. `NEXOVUE_INGEST_KEY` selects the project the demo lands in and defaults to the key `just seed` creates.

It is still never part of `just check` or `just e2e`: it wants minutes and a running stack. It is a command you run deliberately.

## What the performance budgets mean here

On a single developer laptop with the whole stack co-resident, the budgets in `phase-1-plan.md` §4 are a **regression gate, not a capacity model**. "10,000 spans/sec on an M4 Pro with everything running" is a number worth defending against regressions, and says almost nothing about how many customers can be served.

Production capacity planning waits for a production host. When one exists, the budgets are re-measured there and this document gains a second column.

## Phase 2 note — Ollama will not run well on this machine

The deployment decision says every service runs in compose, including Ollama. Docker on macOS has no Metal GPU passthrough, so a containerised Ollama is CPU-only, and a useful model wants 8–16 GB that this machine does not have spare.

Options when Phase 2 arrives: run Ollama natively on macOS against Metal and point the API at the host, or use hosted models during development. **Verify before building against it** — this is recorded, not decided.
