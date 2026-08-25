# Development Environment

> **Type:** Delivery reference · **Target machine:** Apple M4 Pro, 24 GB unified memory
> **Companion doc:** `phase-1-plan.md` (§4 performance budgets)

## The constraint

Docker Desktop on macOS runs a Linux VM, so allocate it **~13 GB** and leave ~11 GB for macOS, editor and browser. Everything below must fit inside that 13 GB.

## Memory budget

| Service | Limit | Note |
|---|---|---|
| ClickHouse | 3 GB | `max_server_memory_usage` capped; reduced mark cache |
| Kafka (KRaft) | 1.5 GB | `KAFKA_HEAP_OPTS=-Xmx1g` |
| Postgres | 1 GB | |
| Redis | 0.5 GB | |
| SeaweedFS | 0.5 GB | |
| Receiver | 0.5 GB | |
| Workers ×4 | 1.6 GB | trace-assembler is the hungry one |
| API | 0.8 GB | |
| Scheduler | 0.5 GB | |
| Web | 0.1 GB | |
| **Core total** | **~10 GB** | leaves ~3 GB headroom |
| *+ demo app & loadgen* | ~1 GB | `tools` profile |
| *+ Playwright* | ~1.5 GB per browser | `doors` profile |

## Rules

**1. Every service declares a hard `mem_limit`.** Without one, ClickHouse expands to fill the VM and everything else starts swapping. A limit turns "the laptop got slow" into a clear OOM on one container.

**2. Compose profiles, not one big up.** `just dev` starts the core only. `doors` adds Celery and Playwright; `tools` adds the demo app and load generator. The full stack is rarely needed at once.

**3. ClickHouse keeps a 3-day TTL in dev.** Production is 30 days. Without this the SSD quietly fills with test spans.

**4. Playwright: arm64 image, `--ipc=host`, `shm_size: 1gb`.** The 64 MB `/dev/shm` default is the most common cause of Chromium instability in Docker. The official images ship ARM64 variants covering Chromium, Firefox and WebKit — the Apple Silicon Rosetta problem applies to native installs, not the container.

## What the performance budgets mean here

On a single developer laptop with the whole stack co-resident, the budgets in `phase-1-plan.md` §4 are a **regression gate, not a capacity model**. "10,000 spans/sec on an M4 Pro with everything running" is a number worth defending against regressions, and says almost nothing about how many customers can be served.

Production capacity planning waits for a production host. When one exists, the budgets are re-measured there and this document gains a second column.

## Phase 2 note — Ollama will not run well on this machine

The deployment decision says every service runs in compose, including Ollama. Docker on macOS has no Metal GPU passthrough, so a containerised Ollama is CPU-only, and a useful model wants 8–16 GB that this machine does not have spare.

Options when Phase 2 arrives: run Ollama natively on macOS against Metal and point the API at the host, or use hosted models during development. **Verify before building against it** — this is recorded, not decided.
