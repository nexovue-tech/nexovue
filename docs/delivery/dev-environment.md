# Development Environment

> **Type:** Delivery reference · **Target machine:** Apple M4 Pro, 24 GB unified memory
> **Companion doc:** `phase-1-plan.md` (§4 performance budgets)

## The constraint

Docker Desktop on macOS runs a Linux VM. **Measured 2026-08-26: the VM is currently allocated 8.3 GB** (`docker info`), which is below what the dev target needs — raise it to ~13 GB in Docker Desktop settings, leaving ~11 GB for macOS, editor and browser.

Two different budgets apply, and conflating them is what makes the numbers look wrong:

| | What it covers | Total |
|---|---|---|
| **Runtime** | the services doing their job | ~2.2 GB measured steady-state |
| **Dev ceiling** | the same services *plus the compilers they host* — `go build`, `pnpm install`, `uv sync` run inside the containers | ~12 GB of caps |

The dev ceiling is high because a dev container is also a build machine. Go's compiler parallelism drives peak RSS, so `GOFLAGS=-p=2` is set — without it `ch-go/proto` was OOM-killed at 320 MB. `pnpm install` was SIGKILLed at 512 MB. The app target needs neither, and `web` there is nginx at 128 MB rather than a Vite dev server at 1 GB.

## Memory budget

| Service | Limit | Note |
|---|---|---|
| ClickHouse | 3 GB | `max_server_memory_usage` capped; reduced mark cache |
| Kafka (KRaft) | 1.5 GB | `KAFKA_HEAP_OPTS=-Xmx1g` |
| Postgres | 1 GB | |
| Redis | 0.5 GB | |
| SeaweedFS | 0.5 GB | |
| Receiver | 0.5 GB | |
| Worker | 1 GB | one process, all pipelines (ADR 0050); trace assembly is the hungry part |
| API | 0.8 GB | |
| Scheduler | 0.5 GB | |
| Web | 0.1 GB | |
| **Core total** | **~10 GB** | leaves ~3 GB headroom |
| *+ demo app & loadgen* | ~1 GB | `tools` profile |
| *+ Playwright* | ~1.5 GB per browser | `doors` profile |

## Rules

**1. Every service declares a hard `deploy.resources.limits.memory`.** Without one, ClickHouse expands to fill the VM and everything else starts swapping. A limit turns "the laptop got slow" into a clear OOM on one container.

Use `deploy.resources.limits`, **not `mem_limit`**. The widespread advice that `deploy` requires Swarm is wrong for the `resources` subsection — Compose v2+ applies it with no Swarm anywhere. Only `mode`, `placement`, `update_config` and `endpoint_mode` are Swarm-only. Following that advice leaves every service with no limit at all, which is the failure this rule exists to prevent.

ClickHouse is a special case: it misreads cgroup limits in Docker, so its cap is set explicitly in `deploy/clickhouse/config.d/memory.xml` rather than inherited from the container.

**2. Compose profiles, not one big up.** `just dev` starts the core only. `doors` adds Celery and Playwright; `tools` adds the demo app and load generator. The full stack is rarely needed at once.

**3. ClickHouse keeps a 3-day TTL in dev.** Production is 30 days. Without this the SSD quietly fills with test spans.

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
| Insight, Ollama | `--profile insight` / `--profile ollama` | always on (Phase 2) |
| Celery | beat, light and browser as separate containers | the same three |

The rule: development optimises for reading logs and restarting one thing at a
time; production optimises for failure domains. Neither should be bent toward
the other.

On this machine Ollama should run **natively rather than in the `ollama`
profile** — Docker on macOS has no Metal passthrough, so the container is
CPU-only. `LLM_BASE_URL` therefore defaults to `host.docker.internal`.

## Running the OTel Demo (P1-62)

The OpenTelemetry Demo needs [4 GB minimal, 8 GB+ full](https://opentelemetry.io/docs/demo/requirements/system/) across 18 services. It does **not** coexist with our stack in a 13 GB VM, and it is not meant to — it is a periodic realism gate, not a dependency.

To run it: stop the `doors` and `tools` profiles, raise the Docker allocation temporarily, and use the demo's *minimal* deployment (which drops its own Kafka and the services depending on it). Point its OTLP exporter at our receiver.

## What the performance budgets mean here

On a single developer laptop with the whole stack co-resident, the budgets in `phase-1-plan.md` §4 are a **regression gate, not a capacity model**. "10,000 spans/sec on an M4 Pro with everything running" is a number worth defending against regressions, and says almost nothing about how many customers can be served.

Production capacity planning waits for a production host. When one exists, the budgets are re-measured there and this document gains a second column.

## Phase 2 note — Ollama will not run well on this machine

The deployment decision says every service runs in compose, including Ollama. Docker on macOS has no Metal GPU passthrough, so a containerised Ollama is CPU-only, and a useful model wants 8–16 GB that this machine does not have spare.

Options when Phase 2 arrives: run Ollama natively on macOS against Metal and point the API at the host, or use hosted models during development. **Verify before building against it** — this is recorded, not decided.
