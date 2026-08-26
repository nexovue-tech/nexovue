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

**Start simple and grow into it.** The trees below are the shape at Phase 1 completion, not what gets created on day one. Official Go guidance is explicit that layout is a function of size; create a package when there is code to put in it.

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
│  ├─ sink/clickhouse/           batched inserts via ch-go, retry, backpressure
│  └─ health/
├─ testdata/
└─ Dockerfile
```

Pipeline order carries the invariants: **`derive` runs before `sample`** so counts stay exact, and **`mask` runs before `sink`** so unmasked data is never written.

Separate binaries because they scale independently — trace assembly is memory-bound, log writing is I/O-bound.

`cmd/` + `internal/` is the layout the official Go docs recommend for a repo of several binaries sharing server logic. We deliberately do **not** use `pkg/` — it means nothing to the toolchain, the official layout doc does not recommend it, and it comes from a community repo the Go team has disowned.

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
│  │                             dependencies.py · exceptions.py · constants.py
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

Domain packages follow the Netflix Dispatch–derived FastAPI convention, with one deliberate deviation: **database access does not live inside the domain**. The convention puts `models.py` in each package; we centralize it in `data/` because twelve domains holding their own query builders means twelve places to forget the tenant filter.

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
│  ├─ app/                       routes, providers, router, layout, command palette
│  ├─ features/                  traces/ logs/ errors/ services/ service-map/ infra/
│  │                             synthetics/ database/ rum/ usage/ onboarding/
│  │                             each: api/ · components/ · hooks/ · types/
│  └─ shared/                    api-client · logger · session · global-handlers
│                                theme/ · toast/ · components/ · hooks/
├─ index.html · vite.config.ts
└─ Dockerfile                    nginx serving the build
```

**Imports flow one direction only: shared → features → app.** Shared code may be used anywhere; a feature may import shared code but not another feature; only `app/` composes features together. Cross-feature reuse is a signal that the shared thing belongs in `components/` or `lib/`.

This is the part that makes feature folders worth having, and it only holds if a tool enforces it — a lint rule restricting import paths between these zones, failing CI like any other rule. Without enforcement, `features/` degrades into `components/` with extra nesting within months.

Shared code sits under one `shared/` directory with **named buckets inside it** — `theme/`, `toast/`, `components/`, `hooks/` — rather than as top-level siblings. Revised 2026-08-26: the buckets are what stop a junk drawer forming, and a single top-level name is what the import rule can actually point at. `features/**` may import `@/shared/*` freely and may import neither `@/app/*` nor another feature.

**Cross-feature imports written as relative paths cannot be caught by a specifier glob** — `../../logs/api` is indistinguishable from `../../../shared/api-client`. The enforceable rule is therefore *use the `@/` alias for anything outside your own feature*, which is what the lint rule checks.

The design system lives in `packages/ui`, never here — the anti-pattern rules need a single chokepoint to be enforceable.

## packages/

```
packages/
├─ contracts/                    the internal normalized envelope (P1-07)
│  ├─ proto/nexovue/v1/          envelope.proto — our metadata only
│  ├─ gen/go/ · gen/python/      generated bindings, committed
│  ├─ fixtures/                  golden payloads, one per door
│  ├─ buf.yaml · buf.gen.yaml    codegen + `buf breaking` compatibility checks
│  └─ README.md                  versioning and compatibility rules
├─ ui/                           tokens · primitives · uPlot chart wrappers
└─ client-ts/                    API client generated from the OpenAPI schema
```

The envelope carries **our** metadata as typed fields — `tenant_id`, `project_id`, `door`, `received_at` — around an untouched **OTLP payload**. We do not define our own span/log/metric messages: OpenTelemetry already did, and it is what customer SDKs emit. Door 1 therefore validates and forwards the same bytes rather than re-modelling them, which is the cheapest possible hot path at 99% of ingest volume.

`tenant_id` being a typed field rather than a map key is deliberate — the entire multi-tenancy invariant rests on it, and a typo in a string key should not be possible.

Contracts are committed generated code so no service needs the codegen toolchain to build.

## deploy/ and tools/

```
deploy/
├─ compose/
│  ├─ compose.infra.yaml         shared infrastructure, pulled in with `include:`
│  ├─ docker-compose.yaml        DEV — bind mounts, hot reload          ← default
│  ├─ docker-compose.app.yaml    production replica, built images
│  └─ docker-compose.onprem.yaml deployment target, registry refs, TLS
├─ clickhouse/migrations/        001_spans.sql, 002_logs.sql, ...
├─ postgres/                     init, roles (schema migrations live with the API)
├─ kafka/                        topic bootstrap: partitions, retention
└─ seaweedfs/                    cold tier, local dev (P1-57)

tools/
├─ demo-app/                     fake shop emitting real telemetry, with fault injection
└─ loadgen/                      throughput and soak driver
```

`tools/demo-app` is built in week one (P1-11) — no UI screen can be built or demonstrated without data that behaves like production.

---

## Reference implementation

The structure above is a description; these are the files that make it real. Each is the shape to copy when adding the next one.

**Config fails at boot, naming the variable** — `apps/receiver/internal/config/config.go`, `apps/api/src/nexovue_api/settings.py`

```go
type Config struct {
    Env         string   `env:"ENV,required"`
    KafkaBrokers []string `env:"KAFKA_BROKERS,required"`
    PostgresDSN  string   `env:"POSTGRES_DSN,required"`
}
```
```
$ env -i ./receiver
receiver: invalid configuration: env: required environment variable "ENV" is not set; ...
exit=1
```
Never a nil dereference at first use, never a default that works locally and is wrong everywhere else. Secrets carry no default at all.

**The tenancy chokepoint** — `apps/api/src/nexovue_api/data/clickhouse/base.py`

```python
async def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if TENANT_BINDING not in sql:          # "{tenant_id:String}"
        raise TenantFilterMissingError(sql)
    bound = {**(parameters or {}), "tenant_id": self._tenant_id}
    return await self._client.query(sql, parameters=bound, settings={
        "max_execution_time": MAX_EXECUTION_SECONDS,
        "max_result_rows": MAX_RESULT_ROWS,
        "result_overflow_mode": "throw",
    })
```
Every ClickHouse read in the service is built here. The multi-tenancy invariant rests on this file: a query that reaches the driver unscoped means isolation is a convention rather than a guarantee. It raises rather than silently scoping, because a query written without the binding was written by someone not thinking about tenancy.

**Routers raise, one handler formats** — `apps/api/src/nexovue_api/errors/`

```python
class TraceNotFound(DomainError):
    problem_type = "https://nexovue.com/problems/trace-not-found"
    status = 404
```
Routers raise `TraceNotFound()` and never build a response. One handler turns every `DomainError` into RFC 9457 problem+json carrying the correlation id. Nothing else knows an HTTP status code.

**Health is two different questions** — `health/` in every service

`/healthz` checks nothing external and answers *restart me*. `/readyz` probes each dependency under **its own deadline** and answers *route to me*. The deadline lives in the runner, not the driver: `franz-go`'s `Ping` ignores context cancellation, and trusting it hung readiness past ten seconds.

**Gate order is visible in the handler** — `apps/receiver/internal/transport/httpx/`

`auth` → `ratelimit` → `decode` → `normalize` → `produce`. Everything after `normalize` is door-agnostic. Trace batches split one envelope per `trace_id` before publishing, because assembly depends on Kafka partitioning by trace id.

**When M1 adds the first domain package**, it splits three ways: `router.py` knows HTTP, `service.py` knows rules and touches neither HTTP nor SQL text, `queries.py` holds the SQL. A change to a query never reaches the router.

## References

Structure validated against current production practice rather than invented:

- [Organizing a Go module](https://go.dev/doc/modules/layout) — official Go guidance on `cmd/` and `internal/` for multi-binary server repos
- [bulletproof-react — project structure](https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md) — feature folders, unidirectional imports, no cross-feature imports
- [Feature-Sliced Design](https://feature-sliced.design/docs/get-started/overview) — layered alternative; heavier, aimed at large teams. Compatible enough to migrate to later
- [FastAPI best practices](https://github.com/zhanymkanov/fastapi-best-practices) — Netflix Dispatch–derived domain packages and their file set
- [Go project layout controversy](https://waken.dev/go-project-layout-controversy/) — why `pkg/` is avoided here
