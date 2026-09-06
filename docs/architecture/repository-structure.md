# Repository Structure

> **Type:** Architecture reference · **Status:** Built. The trees below were diffed against the working tree on 2026-09-04 and describe what is there, not what was planned.
> **Companion docs:** `system-architecture.md` (what the services do) · `../delivery/phase-1-plan.md` (when they get built)

Polyglot monorepo. Go on the data plane, Python on the control plane, TypeScript on the frontend. Go workspaces + uv workspace + pnpm workspace, with one `just` task runner exposing identical verbs per service: `just dev`, `just test`, `just lint`, `just migrate`, `just load`.

```
nexovue/
├─ apps/          runnable services
├─ packages/      shared contracts and libraries
├─ deploy/        compose, migrations, infrastructure config
├─ tools/         load generator, end-to-end suite, chaos suite, the OTel Demo gate
└─ docs/          product, architecture, delivery, research
```

## Conventions every service follows

Configuration from environment, validated at boot, failing fast · `/healthz` and `/readyz` · structured JSON logging with identical field names across languages — `ts`, `level`, `msg`, `service`, `env`, `correlation_id`, `org_id`, `project_id`, plus `trace_id` and `span_id` taken from an inbound W3C `traceparent` header where the caller sent one · graceful shutdown draining in-flight work · its own `Dockerfile` and `README.md`.

**Not built:** self-instrumentation. ADR 0053 decided that nexovue monitors nexovue, but no service carries an OpenTelemetry SDK — the Go modules import `go.opentelemetry.io/proto/otlp` only, to read and write OTLP messages, and the Python services import `opentelemetry-proto` for the same reason. Nothing exports a span about itself. There are also no `testdata/` directories anywhere in the repo; Go tests build their fixtures in code and the golden payloads live in `packages/contracts/fixtures/`.

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
│  ├─ auth/                      two gatekeepers over two key tables (ADR 0086):
│  │                             secret keys for servers, public site keys for
│  │                             browsers; pgx lookup → TTL cache → tenant resolve,
│  │                             plus throttled last-used writes
│  ├─ cache/                     the generic TTL cache auth and allowance share
│  ├─ allowance/                 the plan gate: has this org volume left this month
│  ├─ ratelimit/                 Valkey counters
│  ├─ otlp/                      both entrances and the gates they share:
│  │                             handler.go  the gate chain and HTTP routing
│  │                             signals.go  what each signal decodes, counts, publishes
│  │                             gates.go    the verdicts, written once for both transports
│  │                             grpc.go · grpcserver.go  OTLP/gRPC :4317
│  │                             codec.go · body.go · jsonids.go  protobuf/JSON, caps, refusals
│  │                             cors.go · origin.go  the browser entrance's own rules
│  │                             traffic.go  accepted/refused counters per path and unit
│  ├─ semconv/                   which convention generation a schema URL names
│  ├─ tracesplit/                one group per trace_id, resource and scope preserved
│  ├─ ingest/                    envelope build + franz-go produce
│  └─ buildinfo/ · logging/ · health/
└─ Dockerfile
```

> The two entrances share the gate chain deliberately. Everything from `accept` onward — splitting,
> envelope construction, tenancy tagging, the limiter policy — is one code path with two
> transports in front of it. A second copy of the splitting logic is the failure mode this layout
> exists to prevent, which is why the transports are neighbours in one package rather than siblings
> under `transport/`.
>
> An earlier draft of this file described `transport/httpx`, `transport/grpcx`, and separate
> `decode/`, `normalize/` and `produce/` packages. That structure was never built; this is the code
> as it stands (rule 4).

Gates run in order, and the order is load-bearing:

`auth` → `origin` → `allowance` → `ratelimit` → `decode` → `normalize` → `produce`

Authentication first, so no endpoint is ever unauthenticated. `origin` next because the allowed-origin list belongs to the key, and a request from an origin the key does not cover must not spend the owner's rate budget. `allowance` next because it answers from an in-process cache, so a spent plan month is refused without a Valkey round trip. `ratelimit` before the body is read, so a throttled tenant costs a round trip rather than a megabyte of parsing. Everything downstream of `normalize` is door-agnostic; that is what keeps per-source conditionals from spreading.

## apps/workers — Go, one binary over one shared internal

```
apps/workers/
├─ cmd/worker/                   one binary; WORKER_PIPELINES selects which
│                                pipelines it runs (ADR 0050)
├─ internal/
│  ├─ config/                    env parsing plus the per-pipeline settings
│  ├─ worker/run.go              starts the selected pipelines and the usage meter
│  ├─ projectconfig/             per-project settings (masking, first-traces) with refresh
│  ├─ pipeline/
│  │  ├─ consume/                consumer group, commit policy, retry, park, DLQ
│  │  ├─ traceassembly/          trace buffer, eviction, flatten, spans + summaries
│  │  ├─ sample/                 tail sampling policy and buffer (ADR 0010, 0058)
│  │  ├─ derive/                 pre-sampling counters and endpoint stats (P1-22)
│  │  ├─ serviceedges/           the service-map rollup, including span-link queue edges
│  │  ├─ logwriting/             flatten, severity, blocked writes
│  │  ├─ metricwriting/          the five OTLP metric shapes into five tables (ADR 0066)
│  │  ├─ mask/                   PII rules (P1-24)
│  │  ├─ ingeststate/            watermarks and the write guard that make rows idempotent
│  │  ├─ dedupe/ · insertdedup/  offset ranges and ClickHouse insert-dedup tokens
│  │  ├─ usage/                  the ingest meter that feeds usage_daily
│  │  ├─ clocktrust/ · latencysample/ · otlpvalue/ · dlq/
│  └─ buildinfo/ · logging/ · health/
└─ Dockerfile
```

Pipeline order carries the invariants: **`derive` runs before `sample`** so counts stay exact, and **`mask` runs before the writer** so unmasked data is never written.

There is no `sink/clickhouse` package: each writing pipeline owns its own `writer.go` over `ch-go`, because the row shapes and the idempotency tokens differ per table. There is no `enrich` package either — deploy-version join and geo are not built.

One binary, because the pipelines were identical code and separate containers cost ~2.5 GB to run four copies of it. Only three names survive — `trace-assembly`, `log-writing`, `metric-ingest`. Two more (`metric-derivation`, `service-edges`) were removed because nothing started them: a process asked for one of them alone ran no pipeline at all and said at boot that it had. Derivation and the edge rollup run inside `trace-assembly`. See ADR 0050.

`cmd/` + `internal/` is the layout the official Go docs recommend for a repo of several binaries sharing server logic. We deliberately do **not** use `pkg/` — it means nothing to the toolchain, the official layout doc does not recommend it, and it comes from a community repo the Go team has disowned.

## apps/api — Python, FastAPI, query and control plane

```
apps/api/
├─ src/nexovue_api/
│  ├─ main.py · wire.py          app factory, router registration
│  ├─ settings.py · deps.py      config; request-scoped tenant context
│  ├─ lifespan.py · buildinfo.py
│  ├─ auth/                      sessions, argon2, cookies, RBAC checks
│  ├─ signup/ · projects/ · apikeys/    onboarding and the control-plane objects
│  ├─ traces/ logs/ errorgroups/ services/ servicemap/ infrastructure/
│  ├─ synthetics/ checks/ dbstats/ deploys/ integrations/ usage/ system/
│  │                             domain packages, top-level — each holds
│  │                             router.py · schemas.py · service.py · queries.py
│  ├─ audit/                     the audit log, written by every mutating service
│  ├─ data/
│  │  ├─ clickhouse/base.py      the tenancy choke point (see below)
│  │  └─ postgres/               SQLAlchemy models + engine + repository base
│  ├─ errors/ · middleware/ · ratelimit/ · health/ · telemetry/
│  └─ rollup.py · window.py      shared time-bucketing used by several domains
├─ migrations/                   alembic
├─ tests/
└─ Dockerfile
```

Domain packages follow the Netflix Dispatch–derived FastAPI convention, with two deliberate deviations. **Database access does not live inside the domain**: the convention puts `models.py` in each package, and we centralize it in `data/` because a dozen domains holding their own query builders means a dozen places to forget the tenant filter. And the packages sit at the **top level** rather than under a `domain/` parent — there is no `domain/` directory, so an import is `nexovue_api.traces`, not `nexovue_api.domain.traces`.

There is no `data/redis/` package: the client is created in `lifespan.py` and used directly by `auth/sessions.py` and `ratelimit/limiter.py`. There is no `realtime/` package either — the WebSocket log live tail is `logs/tail.py`, served by the logs router.

`data/clickhouse/base.py` is where invariant 1 lives: every query is constructed through it and the tenant filter is injected there, not by callers. This is what makes the lint rule — no raw SQL outside `data/` — enforceable rather than aspirational.

## apps/scheduler — Python, Celery, doors 2 and 4b

```
apps/scheduler/
├─ src/nexovue_scheduler/
│  ├─ celery_app.py              two queues: light (HTTP checks, polls) · browser (Playwright)
│  ├─ beat.py · leader.py        schedules read from Postgres; our own scheduler (ADR 0036)
│  ├─ checks.py                  what is due, read from the control plane
│  ├─ probes/                    http_check.py · browser.py · addresses.py (SSRF guard)
│  │                             result.py · tasks.py
│  ├─ pollers/                   cloudwatch.py · aws.py · postgres_stats.py ·
│  │                             postgres_connect.py · integrations.py · window.py
│  │                             envelope.py — door 4b's OTLP + envelope build
│  ├─ publish.py                 door 2's envelope build, plus the shared
│  │                             wrap_envelope() both doors use, and the Kafka
│  │                             producer; tasks publish directly, no result backend
│  └─ health.py · telemetry/
├─ tests/
├─ Dockerfile
└─ Dockerfile.browser            Playwright image, separate because it is much larger
```

There is no `normalize/` package: envelope construction is `publish.wrap_envelope`, shared by both
doors, with each door's OTLP payload built beside it.

There is no `storage/` package, and **failure screenshots are not yet uploaded**. `synthetics_bucket`
and `synthetics_endpoint_url` exist in settings and `ProbeFailure.screenshot_ref` is carried onto the
log record, but nothing writes an image to the object store — the ref is always empty today.

## apps/web — TypeScript, Vite + React

```
apps/web/
├─ src/
│  ├─ app/                       App.tsx · router.tsx · query-client.ts · routes/
│  │                             layout/ — AppShell, Sidebar, TopBar, command palette
│  ├─ features/                  auth/ traces/ logs/ errors/ services/ servicedetail/
│  │                             servicemap/ infrastructure/ database/ synthetics/
│  │                             usage/ settings/ onboarding/
│  │                             each: api/ · components/ · hooks/ · types/
│  └─ shared/                    api-client · logger · session · projects ·
│                                selection · service-peers · global-handlers
│                                theme/ · toast/ · components/ · hooks/ · format/
├─ index.html · vite.config.ts
└─ Dockerfile                    nginx serving the build
```

There is no `rum/` feature: door 3 accepts browser telemetry, but the RUM screens are not built.

**Imports flow one direction only: shared → features → app.** Shared code may be used anywhere; a feature may import shared code but not another feature; only `app/` composes features together. Cross-feature reuse is a signal that the shared thing belongs in `components/` or `lib/`.

This is the part that makes feature folders worth having, and it only holds because a tool enforces it. Biome's `noRestrictedImports` in `biome.json` refuses `@/features/**` and `@/app/**` from inside `apps/web/src/features/**`, with the reason in the message, and `just lint-web` runs it. Without enforcement, `features/` degrades into `components/` with extra nesting within months.

Shared code sits under one `shared/` directory with **named buckets inside it** — `theme/`, `toast/`, `components/`, `hooks/` — rather than as top-level siblings. Revised 2026-08-26: the buckets are what stop a junk drawer forming, and a single top-level name is what the import rule can actually point at. `features/**` may import `@/shared/*` freely and may import neither `@/app/*` nor another feature.

**Cross-feature imports written as relative paths cannot be caught by a specifier glob** — `../../logs/api` is indistinguishable from `../../../shared/api-client`. The enforceable rule is therefore *use the `@/` alias for anything outside your own feature*, which is what the lint rule checks.

The design **tokens** live in `packages/ui`, never here — the anti-pattern lint rules need a single chokepoint to be enforceable. The components themselves are hand-written under `src/shared/components`; no component library is installed and none is planned ([`../design/design-system.md`](../design/design-system.md)).

## packages/

```
packages/
├─ contracts/                    the internal normalized envelope (P1-07)
│  ├─ proto/nexovue/v1/          envelope.proto — our metadata only
│  ├─ gen/go/ · gen/python/      generated bindings, committed
│  ├─ fixtures/                  golden payloads, one per door
│  ├─ roundtrip_test.go · tests/ the same expectations asserted in both languages
│  ├─ buf.yaml · buf.gen.yaml    codegen + `buf breaking` compatibility checks
│  └─ README.md                  versioning and compatibility rules
└─ ui/                           tokens.css · fonts.css · the contrast and token
                                 lint gates that `just test-web` runs
```

`packages/ui` holds **no components**. It is the token layer plus the two verification scripts that
enforce it; every component is hand-written inside `apps/web` — see
[`../design/design-system.md`](../design/design-system.md).

There is no `packages/client-ts`. The web app calls the API through a hand-written
`shared/api-client.ts`; no client is generated from the OpenAPI schema.

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
│  └─ docker-compose.onprem.yaml deployment target, registry refs, TLS — skeleton,
│                                not yet runnable; every unknown marked TODO
├─ docker/                       the three dev images: go, python, web
├─ air/                          live-reload config for receiver and worker
├─ clickhouse/                   migrations/ (golang-migrate, numbered up/down pairs)
│                                config.d/ memory, keeper and backup settings
├─ postgres/init/                roles (schema migrations live with the API)
├─ kafka/topics.sh               topic bootstrap: partitions, retention — `just topics`
├─ nginx/web.conf                serves the built web app in the app target
├─ caddy/Caddyfile               the TLS edge in the on-premise target
└─ seaweedfs/                    cold tier, local dev (P1-57)

tools/
├─ loadgen/                      throughput and soak driver, telemetrygen as a library
├─ otel-demo/                    the OpenTelemetry Demo pinned and pointed at our
│                                receiver — `just otel-demo up` (P1-62)
├─ e2e/                          Playwright suite against the running stack (P1-60)
├─ chaos/                        kill the broker and the store under load (P1-27)
└─ rebalanceprobe/               reproduces a partition moving mid-batch (ADR 0090)
```

**`tools/demo-app` was deleted on 2026-09-03.** It was a hand-written three-service shop that
emitted telemetry agreeing with itself. The OpenTelemetry Demo is the traffic source now — twenty
containers, eighteen of them reporting, in ten languages — because a service map is only worth
trusting once it has drawn something we did not write. See `tools/otel-demo/otel-demo.sh`.

---

## Reference implementation

The structure above is a description; these are the files that make it real. Each is the shape to copy when adding the next one.

**Config fails at boot, naming the variable** — `apps/receiver/internal/config/config.go`, `apps/api/src/nexovue_api/settings.py`

```go
type Config struct {
    Env          string   `env:"ENV,required,notEmpty"`
    KafkaBrokers []string `env:"KAFKA_BROKERS,required,notEmpty" envSeparator:","`
    PostgresDSN  string   `env:"POSTGRES_DSN,required,notEmpty"`
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
    if not binds_tenant(sql):  # the `tenant_id = {tenant_id:String}` predicate
        raise TenantFilterMissingError(sql)

    bound: dict[str, Any] = {**(parameters or {}), "tenant_id": self._tenant_id}
    if self._project_id is not None:
        bound["project_id"] = self._project_id

    result = await self._client.query(
        sql,
        parameters=bound,
        settings={
            "max_execution_time": MAX_EXECUTION_SECONDS,
            "max_result_rows": MAX_RESULT_ROWS,
            "result_overflow_mode": "throw",
        },
    )
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
```
Every ClickHouse read in the service is built here. The multi-tenancy invariant rests on this file: a query that reaches the driver unscoped means isolation is a convention rather than a guarantee. It raises rather than silently scoping, because a query written without the binding was written by someone not thinking about tenancy. The tenant and the project are bound by the repository from the session; nothing a caller sends can name either.

**Routers raise, one handler formats** — `apps/api/src/nexovue_api/errors/`

```python
class NotFoundError(DomainError):
    """No such resource inside the caller's organization."""

    problem = problems.NOT_FOUND
```
Routers raise `NotFoundError()` and never build a response, and never import `fastapi.HTTPException`. One handler turns every `DomainError` into RFC 9457 problem+json carrying the correlation id. The status code lives on the `ProblemType` in `errors/problems.py`; nothing else knows one. A missing resource is deliberately indistinguishable from one that exists in another organization — telling them apart would leak the other tenant's data.

**Health is two different questions** — `health/` in every service

`/healthz` checks nothing external and answers *restart me*. `/readyz` probes each dependency under **its own deadline** and answers *route to me*. The deadline lives in the runner, not the driver: `franz-go`'s `Ping` ignores context cancellation, and trusting it hung readiness past ten seconds.

**Gate order is visible in the handler** — `apps/receiver/internal/otlp/handler.go`

`auth` → `origin` → `allowance` → `ratelimit` → `decode` → `normalize` → `produce`, written once as nested `http.Handler` wrappers in `route()` and run by both entrances. Everything after `normalize` is door-agnostic. Trace batches split one envelope per `trace_id` before publishing, because assembly depends on Kafka partitioning by trace id.

**Every domain package splits three ways**: `router.py` knows HTTP, `service.py` knows rules and touches neither HTTP nor SQL text, `queries.py` holds the SQL. A change to a query never reaches the router. `apps/api/src/nexovue_api/services/` is the shortest example to copy.

## References

Structure validated against current production practice rather than invented:

- [Organizing a Go module](https://go.dev/doc/modules/layout) — official Go guidance on `cmd/` and `internal/` for multi-binary server repos
- [bulletproof-react — project structure](https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md) — feature folders, unidirectional imports, no cross-feature imports
- [Feature-Sliced Design](https://feature-sliced.design/docs/get-started/overview) — layered alternative; heavier, aimed at large teams. Compatible enough to migrate to later
- [FastAPI best practices](https://github.com/zhanymkanov/fastapi-best-practices) — Netflix Dispatch–derived domain packages and their file set
- [Go project layout controversy](https://waken.dev/go-project-layout-controversy/) — why `pkg/` is avoided here
