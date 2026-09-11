# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

nexovue is an observability platform: OTLP traces/logs/metrics and browser RUM come in through `apps/receiver`, buffer in Kafka, are processed by `apps/workers` into ClickHouse, and are served by `apps/api` (FastAPI) to `apps/web` (React). `apps/scheduler` (Celery) adds synthetic checks and cloud/database pollers. Postgres holds the control plane (tenancy, keys, billing); ClickHouse holds telemetry.

Polyglot monorepo: Go (`go.work`), Python (`uv` workspace, `pyproject.toml`), TypeScript (`pnpm` workspace). Pre-deployment, no customers, no live data.

This is the only CLAUDE.md in the repo. `README.md` is a long source-derived reference (architecture, schemas, six end-to-end flows, known gaps). Trust code over it when they disagree.

## Commands

Everything goes through `just` (see `justfile`; `just` alone lists recipes).

```bash
just setup        # go work sync && uv sync --all-packages && pnpm install
just dev          # full stack: compose up --build, topics, migrate, seed, OTel demo traffic
just fresh        # down-hard + dev; use when the stack is in a state you stopped trusting (~2.5 min)
just up / down    # start / stop, keeping volumes
just down-hard    # DESTRUCTIVE: deletes every volume including Kafka
just logs [svc]
just check        # the pre-commit gate: fmt-check + lint + test + contracts-check. Needs no docker.
just fmt          # gofmt/goimports, ruff format, biome format
just migrate      # migrate-pg (alembic) then migrate-ch (golang-migrate)
just migrate-down-pg / migrate-down-ch   # per store only; there is deliberately no combined rollback
just contracts    # regenerate Go+Python envelope bindings (buf); generated code is committed
just e2e          # Playwright against a running stack (not part of check)
just chaos        # kill broker/store under load, assert nothing acknowledged is lost (not part of check)
just load         # traffic generator; runs on the host, targets localhost:14318
```

### Running a single test

Go tooling runs per module (`./...` from the root sees nothing). Always pass `-count=1`; a cached "ok" has reported green on suites that ran nothing.

```bash
cd apps/workers && go test -count=1 -run TestName ./internal/pipeline/sample/
```

Python (`pytest` config is in root `pyproject.toml`; `asyncio_mode=auto`):

```bash
uv run pytest apps/api/tests/test_checks.py -k test_name
```

`*_live.py` suites need the dev stack and FAIL (do not skip) when it is unreachable. To run offline gates only: `NEXOVUE_SKIP_LIVE_TESTS=1 uv run pytest`. `just test-py` sources `.env` first because the live suites need it.

TypeScript (Vitest, colocated `*.test.ts`):

```bash
pnpm --filter @nexovue/web exec vitest run src/shared/selection.test.ts
pnpm --filter @nexovue/e2e test -- some.spec.ts   # Playwright, stack must be up
```

Lint/typecheck per language: `just lint-go` (golangci-lint v2), `just lint-py` (ruff + `mypy --strict`), `just lint-web` (biome + `tsc -b`; biome does not typecheck).

### Local ports

Postgres 15432 · ClickHouse 18123 (HTTP, api) / 19000 (native, workers + migrate) · Kafka 19092 · Redis 16379 · receiver 14317 gRPC / 14318 HTTP · api 18000 · web 15173. Go admin/health ports (`:8081`) are container-network only.

## Architecture: the parts that span services

**One envelope for every entrance.** `packages/contracts/proto/nexovue/v1/envelope.proto` is the frozen internal message. Every door (OTLP server key, RUM site key, synthetic probe, poller) normalizes to it before Kafka; every worker reads only it. The envelope's `door` field records the entrance and is decided in exactly one place, `ingest.DoorFor(identity)`, from the kind of key that opened it. Topics are `<signal>-raw` / `<signal>-dlq`; nothing drains a DLQ automatically. `buf breaking` against `main` runs in `just check`, so a contract change that breaks consumers fails the gate.

**Data plane vs control plane.** receiver → Kafka → workers → ClickHouse never writes Postgres (workers write only usage metering). `apps/api` is the sole owner of the Postgres schema. scheduler reads Postgres for schedules and writes only integration health.

**Nothing acknowledged is ever lost, at-least-once everywhere.** The receiver acks a client only after Kafka acks; a worker commits an offset only after ClickHouse acks. Duplicates from replay are absorbed by ClickHouse block-hash dedup (plain inserts) or a content-derived dedup token (aggregate folds).

**Configuration.** A variable with no default in `.env.example` is required and the service exits at boot naming it. Credentials never have a default in code. Shared infra vars use one name everywhere: `KAFKA_BROKERS`, `POSTGRES_DSN`, `CLICKHOUSE_DSN`, `REDIS_URL`.

## Per-service invariants

### apps/receiver (Go)

- Gate order is load-bearing and shared by HTTP and gRPC: `auth → origin → allowance → ratelimit → decode → normalize → produce`.
- `allowance` is the plan gate. It sits above `ratelimit` because it answers from an in-process cache. A control-plane read that fails admits the request. A deleted organization is refused; a missing row is admitted. `organizations.ingest_suspended_at` is the operator stop valve, read in the same statement.
- `origin` is the browser gate: after `auth` because the allowlist belongs to the key, before `ratelimit` so a bad origin cannot spend the owner's budget. Preflight `OPTIONS` is answered unauthenticated; every reply to a request carrying `Origin` gets CORS headers, refusals included.
- The browser door also accepts `text/plain` as OTLP/JSON (for `sendBeacon`). The server door cannot be given one.
- Trace batches are split one envelope per `trace_id` before publishing; Kafka partitions by trace id.
- Errors follow OTLP status semantics, not problem+json. Refusals use `RESOURCE_EXHAUSTED`; only the rate-limit refusal attaches `RetryInfo`, because only it is recoverable.
- The limiter fails open when Valkey errors and bounds itself in-process per instance while it does.
- pprof mounts only on the admin listener and only when `RECEIVER_PPROF_ENABLED` is set.

### apps/workers (Go)

- One binary. `WORKER_PIPELINES` selects `trace-assembly`, `log-writing`, `metric-ingest`. An unknown name fails at boot.
- Stage order `decode → mask → derive → sample → sink` is an invariant: `mask` rewrites the payload in place before anything downstream sees it; `derive` counts every span before `sample` so counters stay exact.
- `trace-assembly` buffers by trace for `WORKERS_SAMPLING_WINDOW` (10s). Offsets commit only after everything a batch produced has reached storage, which puts commits up to ~70s behind ingest by design.
- `spans` is a plain MergeTree; nothing deduplicates it on read. `internal/pipeline/dedupe` catches a redelivery inside one process, `internal/pipeline/ingeststate` across a restart (watermarks in Keeper).
- **Everything that survives one message keeps its partition provenance** (`sample.Buffer`, `derive.Counters`, `serviceedges.Accumulator`, `usage.Meter`) so a rebalance can drop that partition's contribution. Shares are summed in ascending partition order because float order changes the block bytes ClickHouse dedups on. Adding an accumulator without provenance re-opens double-counting silently.
- Revoke releases buffered spans, minute buckets, uncommitted offsets and the in-process watermark in one critical section. For `usage` a hole is an under-bill and a duplicate an over-bill, so its four pieces move together or not at all.
- `usage_daily.browser_bytes` is the `DOOR_RUM` part of `bytes`; `bytes` stays the whole month.
- `WORKERS_*_BATCH_MAX_MESSAGES` bounds messages, `WORKERS_*_BATCH_MAX_ROWS` bounds one insert, `consume` bounds bytes. A message bound alone is not a memory bound.
- `derive` trusts the client span timestamp only within a minute ahead or retention behind `received_at_unix_nano`; otherwise it counts at receive time.
- Undecodable messages park on the DLQ with reason and coordinates before the offset moves.
- ClickHouse writes use `ch-go`, not `clickhouse-go`.

### apps/api (Python, FastAPI)

- Owns Postgres. Reads ClickHouse. Never touches the ingest path. Routes are versioned `/api/v1/…`.
- **`data/clickhouse/base.py` is the tenancy chokepoint.** Every statement is a module-level constant containing the literal `tenant_id = {tenant_id:String}`; the repository binds `tenant_id` and refuses any statement without it. A structural test derives the covered tables from the schema. No raw SQL outside `data/`.
- Postgres queries take `org_id` explicitly; a static test fails any tenant-scoped select/update/delete lacking it.
- Domain packages: `router.py` (HTTP only) · `schemas.py` · `service.py` (logic, no HTTP, no SQL) · `queries.py` (SQL) · `dependencies.py` · `exceptions.py`. Routers raise typed domain exceptions; one handler maps them to RFC 9457 problem+json.
- Rate limiting is a sliding-window log plus a concurrency cap for telemetry reads, both as atomic Lua scripts. `TRUSTED_PROXY_COUNT` must match the deployment or every caller shares one bucket.
- Billing: checkout never changes the plan; only the processor's signed webhook does, with delivery-id idempotency. No vendor vocabulary above the adapter.

### apps/scheduler (Python, Celery)

- Tasks normalize to the envelope and publish to Kafka themselves; there is no result backend.
- Two queues: `light` (HTTP checks, SSL, polls; high concurrency) and `browser` (Playwright; low concurrency, its own image).
- Schedules live in Postgres and are read by our own Beat scheduler, which embeds the whole check definition in the task args so workers never query Postgres.
- A failing target is a successful task; "down" is a measurement and is always published.

### apps/web (TypeScript, Vite + React 19)

- Imports flow `shared → features → app`, enforced by biome. Features never import each other or `app/`; cross-boundary imports use `@/`.
- Colours, type, spacing and radii come from `@nexovue/ui` tokens. No hex, no `slate-*`, no `dark:` variants.
- `shared/api-client.ts` is the only way to reach the API (fetch, never axios). It injects `traceparent`, applies a timeout, and raises one typed `ApiError`.
- `app/query-client.ts` is the only place a status code is interpreted. Features never handle 401.
- Failure presentation: toast for transient, inline for a form/field, full screen only for a boundary failure. Loading is a skeleton, never a spinner.
- Sidebar comes from `app/routes/route-manifest.ts`; routes are declared in `app/router.tsx`. `route-manifest.test.ts` fails if the sidebar offers a path the router does not serve.
- API response interfaces keep wire field names unconverted. An absent or negative reading renders as an em-dash, never `0`.

## Conventions enforced by tooling

- Go: errors wrapped with `%w`; exported identifiers documented (revive); `goimports` groups `github.com/nexovue-tech/nexovue` separately.
- Python: ruff bans naive datetimes (DTZ) and `print()` outside `tools/` and `suspend.py`; `mypy --strict` everywhere.
- TypeScript: no `any`, no non-null assertions, no `console` except `console.error`.
- Generated code (`packages/contracts/gen`, `tools/otel-demo/upstream`) is committed and excluded from every linter by name.
- Comments are short and explain why. Migration files state their own reasoning.
- Structured logging with identical field names across Go and Python: `ts level msg service env trace_id span_id correlation_id org_id project_id`.
