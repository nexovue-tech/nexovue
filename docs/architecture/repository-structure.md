# Repository Structure

> **Type:** Architecture reference · **Status:** Design, not yet implemented (P1-02 builds it)
> **Companion docs:** `system-architecture.md` (what the services do) · `../delivery/phase-1-plan.md` (when they get built)

Polyglot monorepo. Go on the data plane, Python on the control plane, TypeScript on the frontend. Go workspaces + uv workspace + pnpm workspace, with one `just` task runner exposing identical verbs per service: `just dev`, `just test`, `just lint`, `just migrate`, `just load`.

```
nexovue/
├─ apps/          runnable services
├─ packages/      shared contracts and libraries
├─ deploy/        compose, migrations, infrastructure config
├─ tools/         demo app, load generator
└─ docs/          product, architecture, delivery, research
```

## Conventions every service follows

Configuration from environment, validated at boot, failing fast · `/healthz` and `/readyz` · structured logging · self-instrumented with OTel exporting to our own receiver · graceful shutdown draining in-flight work · its own `Dockerfile`, `README.md`, and `testdata/`.

**Folders are domain-oriented, never layer-oriented.** `traces/ logs/ metrics/` rather than `models/ controllers/ services/` — features arrive as domains, and layer-first folders force every feature to touch six directories.

---

## apps/receiver — Go, the ingestion edge

Front door for every door. Terminates OTLP, authenticates, tags tenancy, normalizes, publishes.

```
apps/receiver/
├─ cmd/receiver/main.go          wiring only: config → dependencies → serve
├─ internal/
│  ├─ config/                    env parsing, validation, fail fast
│  ├─ transport/
│  │  ├─ httpx/                  OTLP/HTTP :4318, RUM routes, webhook routes
│  │  └─ grpcx/                  OTLP/gRPC :4317
│  ├─ auth/                      API-key verify (Postgres) → Redis cache → tenant resolve
│  ├─ ratelimit/                 Redis counters; also the source of usage metering
│  ├─ decode/                    protobuf → pdata; reject malformed payloads
│  ├─ normalize/                 pdata → contracts envelope — the only place this happens
│  ├─ produce/                   franz-go producer, partition by trace_id, retry/ack policy
│  └─ health/
├─ testdata/                     golden OTLP payloads
└─ Dockerfile
```

Gates run in order: `auth` → `ratelimit` → `decode` → `normalize` → `produce`. Everything downstream of `normalize` is door-agnostic; that is what keeps per-source conditionals from spreading.

## apps/workers — Go, four binaries over one shared internal

```
apps/workers/
├─ cmd/
│  ├─ trace-assembler/           spans → assembled traces → ClickHouse
│  ├─ log-writer/                logs → ClickHouse
│  ├─ metrics-deriver/           exact counters from ALL traces, before sampling
│  └─ edge-aggregator/           service-map dependency edges (P1-55)
├─ internal/
│  ├─ config/
│  ├─ consume/                   consumer group, offset commit policy, dead-letter queue
│  ├─ assemble/                  trace buffer, timeout eviction, latecomer spans
│  ├─ enrich/                    deploy-version join, geo
│  ├─ mask/                      PII rules (P1-24)
│  ├─ sample/                    tail sampling policy (P1-21)
│  ├─ derive/                    counters (P1-22) and dependency edges (P1-55)
│  ├─ sink/clickhouse/           batched inserts, retry, backpressure
│  └─ health/
├─ testdata/
└─ Dockerfile
```

Pipeline order carries the invariants: **`derive` runs before `sample`** so counts stay exact, and **`mask` runs before `sink`** so unmasked data is never written.

Separate binaries because they scale independently — trace assembly is memory-bound, log writing is I/O-bound.

## apps/api — Python, FastAPI, query and control plane

```
apps/api/
├─ src/nexovue_api/
│  ├─ main.py                    app factory
│  ├─ settings.py
│  ├─ deps.py                    request-scoped tenant context
│  ├─ auth/                      sessions, argon2, RBAC checks
│  ├─ domain/                    traces/ logs/ errors/ services/ service_map/ infra/
│  │                             synthetics/ database/ rum/ usage/ projects/ keys/
│  │                             each: router.py · schemas.py · service.py · queries.py
│  ├─ data/
│  │  ├─ clickhouse/base.py      the tenancy choke point (see below)
│  │  ├─ postgres/               SQLAlchemy models + repositories
│  │  └─ redis/
│  ├─ realtime/                  WebSocket live tail (P1-43)
│  └─ telemetry/
├─ migrations/                   alembic
├─ tests/
└─ Dockerfile
```

`data/clickhouse/base.py` is where invariant 1 lives: every query is constructed through it and the tenant filter is injected there, not by callers. This is what makes the lint rule — no raw SQL outside `data/` — enforceable rather than aspirational.

## apps/scheduler — Python, Celery, doors 2 and 4b

```
apps/scheduler/
├─ src/nexovue_scheduler/
│  ├─ celery_app.py              two queues: light (HTTP checks, polls) · browser (Playwright)
│  ├─ beat.py                    schedules read from Postgres, editable in the UI
│  ├─ probes/                    http.py · ssl.py · api_flow.py · browser.py
│  ├─ pollers/                   cloudwatch.py · postgres_stats.py
│  ├─ normalize/                 → contracts envelope, the same shapes the receiver emits
│  ├─ publish/                   Kafka producer; tasks publish directly, no result backend
│  └─ storage/                   failure screenshots → object store
├─ tests/
├─ Dockerfile
└─ Dockerfile.browser            Playwright image, separate because it is much larger
```

## apps/web — TypeScript, Vite + React

```
apps/web/
├─ src/
│  ├─ app/                       router, providers, layout, command palette
│  ├─ features/                  traces/ logs/ errors/ services/ service-map/ infra/
│  │                             synthetics/ database/ rum/ usage/ onboarding/
│  │                             each: routes · components · queries (TanStack) · types
│  ├─ shared/                    api client, time-range state, formatting, auth
│  └─ styles/
├─ index.html · vite.config.ts
└─ Dockerfile                    nginx serving the build
```

The design system lives in `packages/ui`, never here — the anti-pattern rules need a single chokepoint to be enforceable.

## packages/

```
packages/
├─ contracts/                    the internal normalized envelope (P1-07)
│  ├─ proto/nexovue/v1/          span · log · metric · envelope
│  ├─ gen/go/ · gen/python/      generated bindings, committed
│  ├─ fixtures/                  golden payloads, one per door
│  └─ README.md                  versioning and compatibility rules
├─ ui/                           tokens · primitives · uPlot chart wrappers
└─ client-ts/                    API client generated from the OpenAPI schema
```

Contracts are committed generated code so no service needs the codegen toolchain to build.

## deploy/ and tools/

```
deploy/
├─ compose/                      docker-compose.yml + dev override
├─ clickhouse/migrations/        001_spans.sql, 002_logs.sql, ...
├─ postgres/                     init, roles (schema migrations live with the API)
├─ redpanda/                     topic bootstrap: partitions, retention
└─ minio/                        cold tier (P1-57)

tools/
├─ demo-app/                     fake shop emitting real telemetry, with fault injection
└─ loadgen/                      throughput and soak driver
```

`tools/demo-app` is built in week one (P1-11) — no UI screen can be built or demonstrated without data that behaves like production.
