# Detailed current-system reference

> **Status:** Source-derived reference retained from the former root README. It records behavior verified from the repository as of 2026-09-12. Focused documents in this directory are the easier-to-navigate current-state architecture entry points; code, migrations, tests, and deployment configuration remain authoritative when they disagree.

An engineering reference derived entirely from the source code in this repository — no external documentation was consulted to write it. Every claim below is either observed directly in a file (cited as `path:line`) or explicitly marked as inferred. Where comments in the code point out that an earlier assumption turned out to be wrong, that correction is described rather than the outdated assumption.

## Table of contents

1. [Overview](#1-overview)
2. [High-level architecture](#2-high-level-architecture)
3. [The internal envelope and Kafka topics](#3-the-internal-envelope-and-kafka-topics)
4. [apps/receiver — the ingestion edge](#4-appsreceiver--the-ingestion-edge)
5. [apps/workers — the stream processors](#5-appsworkers--the-stream-processors)
6. [apps/api — the control plane](#6-appsapi--the-control-plane)
7. [apps/scheduler — synthetic checks and pollers](#7-appsscheduler--synthetic-checks-and-pollers)
8. [apps/web — the console](#8-appsweb--the-console)
9. [packages/browser-sdk](#9-packagesbrowser-sdk)
10. [packages/py-platform and packages/ui](#10-packagespy-platform-and-packagesui)
11. [The ClickHouse schema](#11-the-clickhouse-schema)
12. [The Postgres schema](#12-the-postgres-schema)
13. [Six data flows, traced end to end](#13-six-data-flows-traced-end-to-end)
14. [How to run this locally](#14-how-to-run-this-locally)
15. [Testing and the quality gate](#15-testing-and-the-quality-gate)
16. [Deployment shape](#16-deployment-shape)
17. [Coding standards and conventions](#17-coding-standards-and-conventions)
18. [Known gaps and inconsistencies found in the code](#18-known-gaps-and-inconsistencies-found-in-the-code)
19. [Product planning documents](#19-product-planning-documents)

---

## 1. Overview

This is an observability platform: applications and browsers send it OpenTelemetry (OTLP) traces, logs, and metrics; a background scheduler adds synthetic uptime checks and polled cloud/database metrics; everything lands in ClickHouse; a Python control plane serves it to a React console over a REST API; a Postgres database owns everything about *who* is asking (organizations, projects, credentials, billing).

The system is built as five independently deployable services plus two shared libraries:

| Service | Language | Role |
|---|---|---|
| `apps/receiver` | Go | Terminates OTLP over HTTP and gRPC, authenticates the caller, enforces rate limits and plan allowance, converts everything into one internal message shape, publishes to Kafka |
| `apps/workers` | Go | Consumes Kafka, masks sensitive values, derives metrics from every span before any sampling happens, tail-samples traces, writes everything to ClickHouse |
| `apps/api` | Python (FastAPI) | Owns the Postgres schema (organizations, users, projects, credentials, billing), reads ClickHouse, serves the REST API the web app calls |
| `apps/scheduler` | Python | Runs synthetic API, multistep, TCP, DNS and heartbeat checks and polls customer cloud/database metrics, on a schedule stored in Postgres |
| `apps/web` | TypeScript (React) | The console — traces, logs, services, service map, errors, RUM, synthetics, database, infrastructure, usage, billing, settings |
| `packages/browser-sdk` | TypeScript | The JavaScript SDK a customer drops into their own web page to send RUM data |
| `packages/contracts` | Protobuf (Go + Python generated) | The one internal envelope message every ingestion path normalizes into before Kafka |
| `packages/py-platform` | Python | Shared structured logging and request-context code for `apps/api` and `apps/scheduler` |

Everything is orchestrated locally with `docker compose` and a `justfile` task runner (see [§14](#14-how-to-run-this-locally)).

---

## 2. High-level architecture

### 2.1 The five entrances ("doors")

Every message that eventually reaches ClickHouse arrives through one of five typed entrances, recorded on the envelope itself (`packages/contracts/proto/nexovue/v1/envelope.proto:10-17`):

| Door | Enum value | What produces it |
|---|---|---|
| Direct OTLP | `DOOR_OTLP` | An application's OpenTelemetry SDK, authenticated with a secret API key, hitting `apps/receiver` |
| Synthetic checks | `DOOR_SYNTHETIC` | `apps/scheduler`'s probe workers: a check's result as metrics, and what it sent and received as a log record |
| Pollers | `DOOR_POLLER` | `apps/scheduler`'s pollers, reading a customer's AWS CloudWatch or Postgres statistics |
| Browser RUM | `DOOR_RUM` | `packages/browser-sdk` running in a customer's own web page, authenticated with a public site key |
| Webhook | `DOOR_WEBHOOK` | Defined in the envelope contract but not constructed by any code path found in this pass — likely reserved for a door not yet built |

### 2.2 Data plane vs. control plane

**Data plane** (telemetry): `apps/receiver` → Kafka → `apps/workers` → ClickHouse. Nothing in this path ever touches a customer's Postgres row directly except to read tenant/project identity and per-project masking/sampling/retention configuration.

**Control plane** (everything else): `apps/api` owns Postgres — organizations, users, sessions, projects, API keys, site keys, checks, integrations, deploy events, billing, audit log — and is the only service that writes it. `apps/scheduler` reads Postgres for check/integration definitions but writes almost nothing back (only integration health status, and the rows of its own job queue — `jobs`, `job_results` — ADR-020). `apps/receiver` and `apps/workers` read Postgres only for authentication/authorization and per-project configuration; neither ever writes to it.

### 2.3 Infrastructure

| System | Role |
|---|---|
| Kafka | The broker between ingestion and storage. Every telemetry topic has a matching dead-letter topic that nothing drains automatically. |
| Postgres (with the `vector` extension installed) | Control plane: tenancy, credentials, billing, audit. Since ADR-020 also sessions, one-time links, rate-limit windows for the auth tier, the scheduler's job queue and its beat leader lock (an advisory lock). |
| ClickHouse (with an embedded Keeper coordination service) | Telemetry store: raw signals plus pre-computed rollups. Keeper backs the durable offset-watermark tables the workers use to survive a restart without losing data. They bound double-counting rather than eliminating it — see [§18](#18-known-gaps-and-inconsistencies-found-in-the-code). |
| SeaweedFS (S3-compatible) | Object storage for uploaded source maps. |

### 2.4 The shape of a request

A single rule threads through the whole ingestion path: **the receiver never tells a caller their data was accepted until Kafka has actually acknowledged it**, and **a worker never advances a Kafka offset until ClickHouse has actually acknowledged the insert**. Every one of the six flows traced in [§13](#13-six-data-flows-traced-end-to-end) depends on this pair of orderings — it's the entire basis for the system's "nothing acknowledged is ever lost" guarantee. There is no exactly-once delivery anywhere in the system; duplicates from a replay are normally absorbed either by ClickHouse's own block-hash deduplication window or, where that mechanism doesn't apply, by an explicit content-derived deduplication token computed by the writer. *Normally*, not always: a measured replay case where absorption does not happen is recorded in [§18](#18-known-gaps-and-inconsistencies-found-in-the-code).

A second rule shapes the worker pipeline specifically: **metrics that count what happened (request counts, error counts, endpoint latency, service-to-service call graph) are derived from every span that arrives, before the tail sampler decides which traces to keep.** An earlier design derived these counts from stored (already-sampled) spans and measured them under-reporting error rate by roughly an order of magnitude — that design was replaced; see [§11.2](#112-materialized-views-and-write-paths).

---

## 3. The internal envelope and Kafka topics

### 3.1 The envelope

`packages/contracts/proto/nexovue/v1/envelope.proto:29-56` — every message any door writes and every worker reads:

| Field | # | Type | Meaning |
|---|---|---|---|
| `tenant_id` | 1 | string | Organization id |
| `project_id` | 2 | string | Project id (an environment — production, staging, …) |
| `door` | 3 | `Door` enum | Which entrance produced this payload |
| `signal` | 4 | `Signal` enum (`SIGNAL_SPANS`/`SIGNAL_LOGS`/`SIGNAL_METRICS`) | Which OTLP request shape `payload` holds — deliberately redundant with the Kafka topic, so a message on the wrong topic fails with a typed validation error rather than an opaque decode crash |
| `received_at_unix_nano` | 5 | int64 | When *the platform* received it, not the event time |
| `ingest_id` | 6 | string | A UUIDv7 for dead-letter-queue correlation |
| `semconv_version` | 7 | string | Which OpenTelemetry semantic-conventions generation the payload declares (read from the payload's own schema URL, not assumed) |
| `payload` | 8 | bytes | The raw, unmodified OTLP protobuf request — never decoded/re-encoded by the envelope layer |

Field numbers are never reused, and the shape is checked against nine committed binary fixtures (one per door/signal combination, `packages/contracts/fixtures/*.bin`) by two independent test suites — one in Go, one in Python — that assert the identical set of expectations, specifically so agreement between two readers is what makes the contract's frozenness a fact rather than an assertion.

Bindings are regenerated with `buf generate` from `packages/contracts/proto/`, producing Go output at `packages/contracts/gen/go/` and Python (with `.pyi` stubs) at `packages/contracts/gen/python/`. Both are committed to the repository, not built at compile time. Breaking-change checking runs against the strictest available category, described in the module's own configuration as "forbids anything that breaks a generated consumer, not merely anything that breaks the wire."

### 3.2 Kafka topics

Auto-creation is disabled on the broker; topics are created explicitly by the setup step (`/setup topics`, `apps/workers/internal/setup/topics.go`), with as many copies as there are brokers, up to three, and `min.insync.replicas` one fewer.

| Topic | Partitions | Producer(s) | Consumer group(s) | Partition key |
|---|---|---|---|---|
| `spans-raw` | 12 | `apps/receiver` | `apps/workers` (trace assembly), `nexovue-workers-usage` (metering) | trace id — every partition is one trace-assembler's whole world |
| `logs-raw` | 4 | `apps/receiver`, `apps/scheduler` (browser-flow check results) | `apps/workers` (log writing), `nexovue-workers-logs-usage` | tenant (organization) id |
| `metrics-raw` | 4 | `apps/receiver`, `apps/scheduler` (check results, cloud/database poller results) | `apps/workers` (metric ingest), `nexovue-workers-metrics-usage` | tenant id |
| `profiles-raw` | 4 | `apps/receiver` (converted to `nexovue.v1.ProfileSamples` at the door, ADR-015) | `worker` (profile ingest), `nexovue-workers-profiles-usage` | tenant id |
| `spans-dlq` / `logs-dlq` / `metrics-dlq` / `profiles-dlq` | 1 each | `apps/workers`, on decode/processing failure | none — nothing drains a dead-letter topic automatically | inherits the source record's key |

Every raw topic: 7-day retention, roughly 16 MiB max message size (sized to the receiver's decoded-body cap plus envelope overhead — a previous 1 MiB default silently dropped any oversized trace, with the sender retrying an unshrinkable batch forever), `zstd` compression.

Dead-letter records carry triage headers prefixed `nexovue-` (`nexovue-dlq-reason`, `nexovue-source-topic/partition/offset`, `nexovue-parked-at`, `nexovue-consumer-group`, and optionally `nexovue-ingest-id/tenant-id/project-id`) so a human can read why a message was refused without decoding it — `apps/workers/internal/pipeline/dlq/dlq.go:22-35,72-103`.

---

## 4. apps/receiver — the ingestion edge

### 4.1 Purpose

The Go binary at `apps/receiver/cmd/receiver/main.go` terminates OTLP over both HTTP (`:4318`) and gRPC (`:4317`), authenticates the caller, tags tenancy, normalizes everything into the internal envelope, and publishes to Kafka. Every accepted request passes through a fixed gate chain in a fixed order: `auth → origin → allowance → rate-limit → decode → normalize → produce` (`apps/receiver/internal/otlp/gates.go:20-25`), shared identically between the HTTP and gRPC transports.

### 4.2 Startup and shutdown

`main.go:52-168`: load config → build the logger → install a signal-cancellable context → dial Kafka/Postgres (closing whatever was already opened if a later dial fails) → build health checks → wire two credential doors (secret-key and public-site-key) sharing one set of collaborators → start the admin, OTLP/HTTP, and OTLP/gRPC listeners → launch background goroutines for a periodic traffic summary and non-blocking "credential last used" recorders.

On `SIGTERM`/`SIGINT`: every listener gets `SHUTDOWN_TIMEOUT` (default 20s) to drain, on a fresh deadline rather than the already-cancelled request context; the gRPC server calls `GracefulStop()` and falls back to a hard stop if the drain budget expires.

### 4.3 Configuration

Parsed by `apps/receiver/internal/config/config.go`; a missing required variable fails boot with the variable's own name. Selected variables (all have `RECEIVER_`-prefixed or shared names — see the full list in [§14.3](#143-environment-variables)):

- `RECEIVER_OTLP_HTTP_ADDR` (`:4318`), `RECEIVER_OTLP_GRPC_ADDR` (`:4317`), `RECEIVER_HEALTH_ADDR` (`:8081`)
- `RECEIVER_MAX_BODY_BYTES` (4 MiB, wire/compressed cap), `RECEIVER_MAX_DECODED_BODY_BYTES` (16 MiB, decompressed cap)
- `RECEIVER_API_KEY_CACHE_TTL` (30s, capped at 1 min), `RECEIVER_ALLOWANCE_CACHE_TTL` (30s, capped at 5 min); `RECEIVER_API_KEY_MAX_STALE` (10 min) — while the key lookup fails, a key seen valid within it keeps working, counted on `/metrics`; a full cache evicts unknown keys before known ones
- `RECEIVER_RATE_LIMIT_PER_SECOND` (1000, per credential and per signal bucket — profiles have their own); `RECEIVER_INSTANCES` (1) divides it between replicas
- `RECEIVER_PROFILE_SAMPLES_PER_SECOND` (20,000, per credential): profiles spend no allowance while in preview (F17, D1), so the profiles door also bounds samples, counted after conversion
- `KAFKA_SPANS_TOPIC`/`KAFKA_LOGS_TOPIC`/`KAFKA_METRICS_TOPIC`/`KAFKA_PROFILES_TOPIC` (defaults `spans-raw`/`logs-raw`/`metrics-raw`/`profiles-raw`)

### 4.4 HTTP and gRPC surface

| Method | Server path | Browser path | Notes |
|---|---|---|---|
| POST | `/v1/traces` | `/browser/v1/traces` | Full gate chain |
| POST | `/v1/logs` | `/browser/v1/logs` | Full gate chain |
| POST | `/v1/metrics` | `/browser/v1/metrics` | Full gate chain |
| POST | `/v1development/profiles` | — | Full gate chain, then conversion (ADR-015), the sample budget, and a split into records under the broker's limit. Another `/{version}/profiles` answers 404 naming the supported path |
| OPTIONS | the three browser paths | same three paths | CORS preflight only, unauthenticated |

gRPC serves the three standard OTLP collector services and `ProfilesService` (v1development) on `:4317`, for the server (secret-key) door only — no browser credential path exists on gRPC.

Content types: `application/x-protobuf` and `application/json` on both doors; `text/plain` additionally on the browser door only, parsed as OTLP/JSON, because `navigator.sendBeacon` cannot set a `Content-Type` header outside a fixed browser safelist. `gzip` or absent (`identity`) `Content-Encoding` is accepted; anything else is refused.

Status codes: `200` accepted · `400` malformed body/payload · `401` missing/malformed/unknown credential · `403` origin refused or wrong credential kind used from a page · `413` oversized body · `415` unsupported content type/encoding · `429` rate-limited or allowance exhausted · `503` control plane or broker unreachable. Failure bodies are `google.rpc.Status`, matching what an OTLP exporter already understands, never a generic problem-JSON shape. `Retry-After` is set whenever a refusal is genuinely retryable; the allowance-exhausted refusal deliberately omits it (see [§13, Flow 6](#flow-6--sign-up-to-first-data)) so a compliant exporter drops the batch rather than retrying for weeks.

### 4.5 Authentication

Header `x-nexovue-key` on every request; the browser door additionally accepts the key as query parameter `nexovue_site_key` (for beacons, which can't set headers) — but only when its shape matches the public scheme, never the secret one.

Two credential kinds, in two separate Postgres tables, verified two different ways:

- **Secret server keys** (`nvk_live_` + 22 base62 characters): looked up by a 16-character prefix, then verified by comparing a SHA-256 hash in constant time (`crypto/subtle.ConstantTimeCompare`) — a byte-by-byte comparison would leak how much of a guessed key was right.
- **Public site keys** (`nvp_live_` + 22 base62 characters): stored and compared in the clear — the value is already printed in a customer's page source, so hashing it protects nothing while costing the exact-match lookup the browser door needs. Restricted to an `allowed_origins` list on the key row.

Both are cached in-process with asymmetric TTLs: a hit is cached for the full configured TTL, a miss for a much shorter fixed window — because a hit's TTL bounds how long a *revoked* key still works, while a miss only needs to spare Postgres from repeated bad lookups, and a freshly created key must start working almost immediately.

On invalid key: every distinct failure reason (missing, malformed, unknown) collapses to one identical `401`, because distinguishing them would only help someone enumerating keys. On a deleted project: the key stops authenticating entirely. On a deleted or suspended organization: the key still authenticates, but the next gate (allowance) refuses every request — with distinct causes and messages for "organization gone" versus "ingestion suspended" (an operator-controlled stop valve, independent of billing).

### 4.6 Rate limiting and allowance

Two independent gates, run in order allowance → rate-limit.

**Allowance**: is this organization's monthly included volume spent, at the door this export is using? One cached Postgres read per organization joins the plan, its price row, any active paid subscription, and the month's usage sum. The organization's whole allowance is one pool; only the browser door additionally has an optional percentage cap of that pool — an unused browser share remains reachable by the server door, never reserved. Both gates fail open on a control-plane read error, admitting the request, because a brief infrastructure outage is judged to cost less than losing telemetry outright.

**Rate limiting**: an in-process token bucket per credential, refilling at `RECEIVER_RATE_LIMIT_PER_SECOND` ÷ `RECEIVER_INSTANCES` (default 1), so each replica enforces its share of the fleet-wide limit. There is no shared counter store (ADR-020); the share is exact only when traffic spreads evenly across replicas.

### 4.7 Envelope construction and publishing

`apps/receiver/internal/ingest/ingest.go` builds one `nexovuev1.Envelope` per accepted export. A trace export is first split by `apps/receiver/internal/tracesplit` into one group per 16-byte trace id (preserving each span's original resource/scope message by pointer identity, never merging two byte-identical resources), because trace assembly downstream depends on every span of one trace landing on the same Kafka partition. Logs and metrics are **not** split — one envelope per export, because nothing downstream reassembles them.

Partition key: trace id for spans, tenant (organization) id for logs and metrics. The Kafka producer uses a sticky key partitioner set explicitly (not left to the client library's default, since correctness of trace assembly depends on it), a bounded buffer, and a per-record delivery timeout. Publishing is synchronous: the HTTP/gRPC response is written only after Kafka has acknowledged the record, and on a partial batch failure only the unpublished subset is retried (bounded to three attempts) — never the whole batch. When even that retry gives up, the sender is told 503 and resends the whole export, so records that already landed are published again at new offsets; each record therefore carries a digest of its tenant, project, signal and payload (`nexovue-payload-digest`), the first offset to consume a digest claims it in `stored_payload_digests` (15 minutes), and a later record with a claimed digest is acknowledged and skipped by every pipeline and usage meter (`pipeline/resend`, group 9).

### 4.8 Health

`GET /healthz` (liveness) touches nothing external and always returns `200` — checking a shared dependency here would let one bad dependency restart every replica of every service at once. `GET /readyz` (readiness) checks Kafka and Postgres in parallel under individual timeouts and returns `503` if any fails. Both are served on a separate admin port (`RECEIVER_HEALTH_ADDR`, default `:8081`) so probes still answer when the data ports are saturated.

### 4.9 Package map

| Package | Responsibility |
|---|---|
| `internal/allowance` | Plan-volume gate: monthly allowance, browser-door cap, deletion/suspension check |
| `internal/auth` | Credential verification for both key kinds, tenancy resolution, in-process cache, non-blocking "last used" recording |
| `internal/cache` | Generic bounded, TTL-expiring, hit/miss-asymmetric cache shared by auth and allowance |
| `internal/config` | Environment parsing and cross-field validation |
| `internal/health` | Liveness/readiness, admin server, container self-check, optional pprof |
| `internal/ingest` | Envelope construction and Kafka publishing, with retry-of-failed-subset semantics |
| `internal/logging` | Structured logger shared with the workers, credential redaction |
| `internal/otlp` | The HTTP/gRPC doors: gate chain, CORS, origin allow-listing, body decoding, per-signal wiring |
| `internal/ratelimit` | Per-credential in-process token buckets, each replica enforcing its share |
| `internal/semconv` | Extracts the OpenTelemetry semantic-conventions version from a schema URL |
| `internal/tracesplit` | Splits one trace export into per-trace-id groups for correct Kafka partitioning |

---

## 5. apps/workers — the stream processors

### 5.1 Purpose

One Go binary (`apps/workers/cmd/worker`) that consumes the receiver's raw Kafka topics, masks sensitive values, derives pre-sampling metrics, tail-samples traces, and writes everything to ClickHouse. Which pipelines a given process runs is chosen at startup by the `WORKER_PIPELINES` environment variable (default `all`), a comma-separated subset of `trace-assembly`, `log-writing`, `metric-ingest`, `profile-ingest` — there is no separate pipeline name for metric derivation or the service-dependency graph; those run inside `trace-assembly`. A process meters what it ingests.

Every compose target runs one worker service carrying every pipeline (`WORKER_PIPELINES: all`). Profiles ran in a second service, `worker-profiles`, from 2026-09-19 to 2026-09-20, after a profile flood OOM-killed the shared worker and stopped spans with it; the founder reversed that on 2026-09-20 (`docs/changes/one-worker-again/`). The single worker carries both budgets — 1280 MiB in deployment, 1536 MiB in development — and `apps/api/tests/test_compose_worker_memory.py` holds `GOMEMLIMIT` at about 85% of whatever the container limit is, which is the setting that keeps the kernel from resolving the question.

### 5.2 Delivery discipline (applies to every pipeline)

Every pipeline shares one consume loop: poll a batch → hand it to the pipeline's own processing → hold the batch's offsets → commit only the in-order prefix of batches whose output has actually reached storage. A rebalance (partition handed to another worker) drops any unabsorbed, in-memory state for the lost partitions with no I/O — never committing a partial offset.

Two kinds of durable state, stored in ClickHouse's embedded Keeper coordination service so they survive a worker restart:

- **Write watermarks** (logs, metrics, and the raw span rows): the highest offset actually written, advanced *only after* ClickHouse acknowledges the insert, never before — writing it early would turn a duplicate into a hole.
- **Settlement ranges** (the pre-sampling span counters specifically): a before/after bracket around each counter-insert, so a replayed batch can be classified as already-counted, overlapping, or genuinely new, and recounted safely when ambiguous.

Where an insert statement can't rely on ClickHouse's own block-hash deduplication (any insert that goes through `INSERT … SELECT … FROM input(...)`, needed whenever a server-side aggregate fold is required), the writer computes its own content-derived `insert_deduplication_token` — a deterministic hash of the row's own fields.

### 5.3 trace-assembly pipeline

Consumes `spans-raw`. Per batch: decode the envelope → **mask in place** (always first) → flatten to a row → if this span hasn't already been counted, fold it into the pre-sampling counters (`apps/workers/internal/pipeline/derive`) and the service-dependency-edge accumulator (`apps/workers/internal/pipeline/serviceedges`) → if this span hasn't already been stored, hand it to the tail-sampling buffer (`apps/workers/internal/pipeline/sample`).

**Tail sampling** (`sample/buffer.go`, `sample/policy.go`): a trace stays open for a configured window (default 10s) after its first span arrives, then a policy decides whether to keep it, checked in this order: an unconditional keep while the project is still inside its "first traces" carve-out (see [§13, Flow 6](#flow-6--sign-up-to-first-data)) → keep if the trace contains an error → keep if the trace's total duration exceeds a configured threshold → otherwise keep a healthy trace with a configured probability, decided by a deterministic function of the trace id (so every worker that ever sees that trace agrees on the same verdict without coordinating).

**Pre-sampling counters and rollups**, written regardless of the sampling verdict: three per-minute `metrics_sum` series (span count, error count, duration sum), the `endpoint_stats_1m` rollup (request/error counts and latency quantiles per service+endpoint+minute), and `service_edges_1m` (the caller→callee dependency graph). These three write paths are the reason the platform's rate/error/latency dashboards and service map are accurate even though most healthy traces are never actually stored.

**Trace summaries**: a per-trace snapshot (`trace_summaries`) is written *after* the sampling verdict, unlike the three rollups above — it indexes only traces that were actually stored, since it exists so the trace list can page without scanning raw spans. Its sort key's `start_time` is fixed at the row's first write, so a revision (a root arriving after its children) merges into the same row; the true start is the `trace_start` column (migration `000034`). The worker holds a trace until the sampler's `DecisionTTL` after its *last* write, so a trace still arriving stays one row. A span replayed after a crash, once the trace is forgotten, still writes a second row; the list can then show that trace twice, and opening it shows each span once (`docs/changes/trace-list-split-traces`).

### 5.4 log-writing pipeline

Consumes `logs-raw`. Nothing is buffered across messages — a log record has no trace to wait for and no sampling decision to make. Per batch: refresh the durable write watermark → decode → mask in place → flatten, extracting a `timestamp_source` (event time, falling back to observed time, falling back to the platform's own receive time — a timestamp is never left at zero, which would partition the row to 1970 and delete it on arrival) and promoted `exception_type`/`exception_message` columns → the same flatten call also extracts any web-vital readings riding as log records (see [§13, Flow 4](#flow-4--a-browser-page-view)) → insert vitals rows, then the vitals rollup, then the log rows, in that order → advance the watermark.

A permanent ClickHouse refusal (a row shape the store will never accept, as opposed to a transient failure) parks the *entire chunk* to `logs-dlq` and still advances the watermark, so one bad tenant's malformed row can't block every other tenant sharing the partition. A refused vitals insert does not park the chunk — the reading is re-derivable from the log row about to be written anyway.

### 5.5 metric-ingest pipeline

Consumes `metrics-raw`. Decodes and masks the full OTLP metrics payload, then dispatches each data point by its OTLP shape into one of five row builders — gauge, sum, histogram, exponential histogram, summary. A metric whose shape isn't recognized rejects the *whole message* (no table exists to hold it), rather than writing the other four shapes and silently dropping the fifth. Inserts happen in a fixed order across all five target tables, and the write watermark advances only after every one of the five inserts has been acknowledged — there is no cross-table transaction (ClickHouse has none), a gap the code tolerates explicitly because a metric row is never unique on its own; a reader must already group by series and timestamp.

### 5.5a profile-ingest pipeline

Consumes `profiles-raw`, whose payload is `nexovue.v1.ProfileSamples` — the receiver's conversion, not OTLP (ADR-015). Decode → mask resource and sample attributes (never frames) → one row per sample into `profile_samples`, stacks inline as parallel arrays leaf first, with an FNV-1a `stack_hash` → watermark after the insert. Retention is `min(project raw retention, 14)` days (F17, D2). Its fetches are bounded at 4 MiB (`WORKERS_PROFILES_BATCH_MAX_BYTES`), a quarter of the other pipelines', because profile messages decompress far past what they fetch. The usage meter counts samples only — no bytes, no services (D1).

`metrics_sum` is deliberately a **shared table**: this pipeline's raw OTLP sum metrics and the trace-assembly pipeline's derived span counters (§5.3) land in the same table, distinguished only by `metric_name`.

### 5.6 Masking

`apps/workers/internal/pipeline/mask` rewrites values in place, before anything downstream (a row, a counter, a sampling decision) can see them. Five built-in rule families, on by default: credential-shaped keys (password/secret/token/api_key substrings), authorization headers, cookies, card numbers, and bearer/basic authorization values found inside free-text attributes. Per-project configuration (stored on the `projects` row in Postgres, read on a refresh timer) can disable a family, add keys/key-substrings to mask or hash, or allow-list specific values — but never accepts a regular expression, by design. A masked value is replaced outright; a hashed value becomes `[masked:<16-byte HMAC-SHA-256 hex>]`, derived from a per-project key so the same underlying value produces a consistent, correlatable pseudonym within one project but not across projects.

### 5.7 Per-project configuration cache

`apps/workers/internal/projectconfig` reads every live project's masking rules, sampling policy, retention days, and "first traces stored" count from Postgres in one statement, on a refresh timer (default 30s). A rejected masking or sampling document for one project keeps that project's last-known-good configuration rather than falling back to platform-wide defaults or failing the whole refresh.

### 5.8 Health and self-observation

`/healthz` reports whether each selected pipeline's consume loop is actually running (a stopped pipeline is treated as fatal — the process exits rather than continuing half-alive). `/readyz` additionally checks Kafka, ClickHouse, and (if configured) Postgres reachability. `/metrics` (ADR-021) reports every readiness check as up or down, each consumer group's lag (the topics' end offsets minus its committed offsets, read from the broker by `kadm` on scrape — absent rather than zero when Kafka cannot answer), and each dead-letter topic's retained records; the receiver's admin listener reports export totals by outcome, signal and refusal reason. The structured-log "ingest heartbeat" (uncommitted records, per-signal write counts, buffered traces and spans) is unchanged.

### 5.9 Package map

| Package | Responsibility |
|---|---|
| `internal/pipeline/consume` | The shared Kafka consume loop: batching, commit-only-when-stored, retry, dead-letter parking, rebalance handling |
| `internal/pipeline/traceassembly` | The spans pipeline end to end |
| `internal/pipeline/sample` | The tail-sampling buffer and policy |
| `internal/pipeline/derive` | Pre-sampling counters and the endpoint rollup |
| `internal/pipeline/serviceedges` | Caller/callee pairing into the service-dependency graph |
| `internal/pipeline/logwriting` | The logs pipeline, including web-vital extraction |
| `internal/pipeline/vitals` | Web-vital reading extraction, rollup, and writer |
| `internal/pipeline/metricwriting` | Customer OTLP metrics into the five metric tables |
| `internal/pipeline/usage` | Per-signal ingestion metering into Postgres, for billing |
| `internal/pipeline/mask` | Masking rules, families, and hashing |
| `internal/pipeline/ingeststate` | Durable Keeper-backed watermarks |
| `internal/pipeline/dedupe` | In-process (non-durable) redelivery guard within one worker run |
| `internal/pipeline/insertdedup` | The content-derived deduplication token |
| `internal/pipeline/dlq` | Dead-letter republishing with triage headers |
| `internal/pipeline/storefailure` | Classifies which ClickHouse errors mean "this row will never insert" |
| `internal/pipeline/clocktrust` | Decides whether to trust a client's own timestamp or fall back to receive time |
| `internal/projectconfig` | The per-project masking/sampling/retention cache |

---

## 6. apps/api — the control plane

### 6.1 Purpose

A FastAPI application that owns the Postgres schema, reads (never writes) ClickHouse through a single enforced tenancy chokepoint, reads/writes source-map bytes in the object store, and serves the REST API the console calls, under the versioned prefix `/api/v1`.

### 6.2 App composition

The app factory (`wire.py`) builds the FastAPI app with `openapi_url`/`docs_url` deliberately versioned under `/api/v1`, installs middleware in a fixed request-processing order (exception boundary → correlation id → request logging → rate limiting → CORS if configured), registers four exception handlers that turn every raised error into one uniform RFC 9457 problem+json shape, and mounts one router per domain package.

**Lifespan**: Postgres connects lazily (the pool opens no connection eagerly); ClickHouse is deliberately *not* connected at boot — connecting eagerly was observed to make the whole API refuse to start during a brief ClickHouse restart, so a ClickHouse failure now surfaces as a `503` on first use instead. The object store and the email sender are each optional as a whole group: if any one of their required settings is missing, the entire feature reports itself unconfigured rather than half-working, and the screens that need it degrade explicitly (source-map upload refuses by name; email-dependent flows either refuse with a named cause or, for signup verification specifically, degrade to a logged warning without blocking signup).

### 6.3 Configuration

Selected settings (see `apps/api/src/nexovue_api/settings.py`):

- Pool sizing is always explicit, never a driver default: `POSTGRES_POOL_SIZE` (10), `POSTGRES_MAX_OVERFLOW` (5), `CLICKHOUSE_POOL_SIZE` (8).
- ClickHouse query guards, applied both at the client-connection level and again per-statement: a 15–30 second execution timeout and a 100,000–500,000 row cap, with overflow treated as an error rather than a silent truncation.
- `SESSION_TTL_SECONDS` (7 days) — stored as the session row's `expires_at`; every read filters on it, and the scheduler's expiry sweep deletes lapsed rows.
- `API_INSTANCES` (1) — how many API processes share the in-process rate limits; each enforces the limit divided by this (§6.8).
- `TRUSTED_PROXY_COUNT` (0 by default) — how many `X-Forwarded-For` hops to trust when resolving a caller's real address; left at 0 behind a real reverse proxy, every caller collapses onto one rate-limit bucket. Both deployment files set 1, for the Caddy edge.
- `EMAIL_PROVIDER` — `resend` or `console`; blank means unset. Production refuses `console`, which writes mail to the log and sends none, in the API and the scheduler alike; mail left unconfigured is allowed, and each sender refuses by variable name.
- **Request bodies are capped** before any route reads them (`middleware/body_limit.py`): counted as they arrive, whatever `Content-Length` says, and refused with a `413` problem past 2 MiB — or past the source-map upload's 65 MiB and a check import's 21 MiB on those routes. The routes that stream keep their own smaller caps under it.
- Email, object storage, and domestic payments are each an all-or-nothing settings group.

### 6.4 Tenant isolation

Two independent enforcement points, each backed by a structural test that inspects the source code itself rather than trusting today's queries:

- **Postgres**: no base repository class or automatic filter exists. Every query function takes `org_id` (and usually `project_id`) as an explicit parameter and threads it into the statement by hand. A static test parses every function touching a tenant-scoped model and fails if a select/update/delete against it has no clause referencing that model's `org_id` column, with a small, explicitly documented exemption list (resolving a webhook's own reference to its tenant, global-uniqueness checks like "is this email already taken").
- **ClickHouse**: every statement is a module-level string constant, never assembled from a runtime value, and must contain the literal predicate `tenant_id = {tenant_id:String}` outside of comments and string literals. A repository class binds `tenant_id` (and optionally `project_id`) onto every call and raises before running any statement that fails this check. A structural test derives the list of telemetry tables directly from the ClickHouse schema, so a newly added table is covered by the check the day it's created, not the day someone remembers to update a list.

A concrete request path: a query parameter naming a project is first proven — via a lookup requiring `org_id == the caller's own organization` — to actually belong to the calling organization, *before* that project id is ever used to scope a ClickHouse read. The ClickHouse repository itself is always constructed from the authenticated session's organization id, never from anything in the URL or request body.

### 6.5 Authentication and sessions

**Password hashing**: Argon2. Verification always runs the hasher, even for an unknown email (checked against a fixed dummy hash) — so an unknown-account lookup costs the same time as a wrong password, closing a timing-based account-enumeration channel.

**Sign-in and cross-site requests** (group 9): five failed sign-ins in a row lock the account for a minute, doubling to fifteen, always temporary (`auth/lockout.py`, `users.failed_logins`/`locked_until`); a locked account answers exactly as a wrong password. An unsafe request carrying the session cookie from another site — `Sec-Fetch-Site`, or failing that `Origin` — is refused 403 (`middleware/cross_origin.py`); requests without the cookie (webhooks, heartbeats, API tokens, MCP) are untouched. The operator enters admin mode with a code once two-factor is enrolled.

**A person's own data** (personal-data spec): `GET /profile/data.json` downloads one JSON file of what nexovue holds about the person (profile, live sessions, their audit entries newest first up to 10,000, tokens they created, invitations sent and received), audited and refused to a support view. `POST /profile/deletion` (password, and a code with two-factor) deletes the account; `GET /profile/deletion` says whether the person may — the organization's last owner may not. Leaving and an admin's removal both run `members/service.py::scrub`: the name becomes "Former member", the address is freed, every audit entry names the user id instead of the address or name, and session rows, password, second factor and recovery codes are deleted.

**Sessions**: stored in the Postgres `sessions` table, keyed by a SHA-256 hash of the token (the store never holds the plaintext, so a database dump alone is not a usable credential) and read on every authenticated request. The token itself is 256 bits of randomness, never stretched. Expiry is the row's `expires_at`; `last_seen_at` is rewritten at most once a minute. Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` everywhere except local development, and deliberately carries **no `Domain` attribute** — a host-only cookie can never travel to a sibling subdomain such as the public ingestion hostname.

**Password reset / email change**: one-time tokens live in the Postgres `one_time_tokens` table, keyed by purpose so a verification token can't be replayed against the reset endpoint, and are spent with `DELETE … RETURNING` on the request's own transaction so two racing requests can't both succeed. Requesting a password reset always returns the same response regardless of whether the email exists — including when the mail provider itself fails — closing an account-existence oracle that an earlier version of the endpoint had. Resetting a password invalidates *every* existing session for that user in one write (`users.sessions_valid_from`) and spends every other outstanding reset link for that user. Second-factor state (enrolments, challenges, spent codes) lives in three further tables, each written in its own short transaction.

**Membership roles**: `owner`, `admin`, `member`, `viewer`. Since F26 the roles are one checked-in table of *actions* (`auth/permissions.py`), and each action has a named route dependency (`MayManageMembers`, `MayOperateChecks`, …). A structural test parses every mutating route and fails if it takes only a bare "who is signed in" dependency instead of the exact policy the table names for it, with a small documented exemption list (signup, login/logout, email-confirmation links opened from a mail client, and the signature-verified deploy webhook). `GET /auth/me` returns the caller's action list, and the console hides what a role may not do.

**Two principals, not one.** A *person* is a session (above). A *token* is an API token (§6.6) and reaches only the documented read surface. nexovue's own **operator** is not a third kind: since ADR-018 it is one `users` row with `role = "super_admin"` in a reserved organization, seeded at boot from the environment and reachable only through the ordinary sign-in page. What separates it is the role — which grants nothing inside any organization — plus a step-up: every `/api/v1/admin/…` route is refused unless the session was re-verified with a password or a TOTP code within the last 30 minutes, a stamp held on the session record so signing out revokes it. A third, temporary principal exists during a **support view**: a customer session carrying an impersonation record, refused any method but `GET`/`HEAD`/`OPTIONS` below every route, audited on every request, and live only inside an owner's time-boxed grant.

### 6.6 API keys and read tokens

**Read tokens (`nvt_live_…`)** are the third credential kind and the only one that reads: they belong to the organization rather than to a person (so a departing teammate does not break CI), carry a project scope or read every project, always expire, and are SHA-256-hashed at rest and shown once. A route is on the **read surface** if and only if it depends on `CurrentReader` rather than `CurrentIdentity`; a structural test holds that set against a checked-in list, so widening what a token can read is a visible change. A token on any other route is refused 403, an ingest key presented to the API is refused with its own sentence, and the receiver refuses a read token by its scheme. Tokens are counted per token (300/minute, under an organization-wide ceiling of 32 in flight) and their use is counted in memory and added to `api_token_usage_daily` once a minute and at shutdown — a crash loses up to a minute of counts. The same token authenticates the **MCP server** mounted at `/api/mcp`, whose tools call the same service functions the surface routes call, bounded to 100 rows and 32 KiB per answer.

Two entirely separate ingest credential kinds in two separate tables — secret server keys and public browser site keys — matching the receiver's own split (§4.5). Secret keys are SHA-256-hashed at rest and shown once, at creation, and never again; a listing shows only a short hint (scheme plus the last few characters). Site keys are stored in the clear (they're already public, printed in a customer's page source) and restricted by an allowed-origins list. Revocation, for both kinds, is one-way and idempotent — there is no un-revoke.

### 6.7 Organizations, projects, and the ingest stop valve

**Deleting an organization** is a two-phase, cross-store operation (ADR-014). The request is one Postgres transaction: it cancels every live payment arrangement at the processor (refusing if it cannot), deletes the rows of every directly-owned table (so the owner's email is free for a new signup at once), scrubs the organization row's identifying fields and stamps a deletion timestamp — the row itself is deliberately kept, because the receiver's allowance gate reads exactly this row to decide whether to refuse ingestion, and "no row" there would mean *admit* rather than *refuse*. The telemetry and uploaded files are erased by the scheduler once a night (`ERASURE_HOUR_UTC`, default 02:00): one `ALTER TABLE … DELETE WHERE tenant_id IN (…)` per table naming every owed organization, one table at a time so only one table's superseded parts are held in memory; then, per organization proved to have no rows left, its object-store prefixes are deleted and a tenant-less audit certificate is written. One with rows left (records that landed after the mutation) stays owed for the next night; one owed past three days is logged at ERROR. The API holds no ClickHouse credential that writes. The scheduler holds two, both in `scheduler-light` alone. `CLICKHOUSE_ERASER_DSN` connects as `nexovue_eraser` (`deploy/clickhouse/users.d/eraser.xml`), which holds `SELECT` and `ALTER DELETE` on the telemetry database and `SELECT` on `system.mutations`, nothing else. `CLICKHOUSE_DETECTION_WRITER_DSN` connects as `nexovue_detection_writer` (`users.d/detection-writer.xml`, ADR-023), which holds `INSERT` on `detection_readings` and nothing else, under its own profile whose time and memory bounds it cannot raise, an hourly quota on inserts and bytes written, and private networks only; it is written only from `nexovue_scheduler.detection.store`, which the tenant-structure test enforces. Without it, `detection.store.open_store` opens nothing and logs that at ERROR. An upgrade adds an account password the settings file lacks (`add_missing_secrets` in `deploy/nexovue`) and changes no other value.

**Deleting a project** revokes every live credential the project holds (API keys, site keys, webhook secrets) immediately, because the receiver resolves an ingest key without joining the `projects` table — a soft-deleted flag alone would not actually stop new data arriving under that project's key.

**Suspending an organization's ingest** writes exactly two columns on the organization row (a timestamp and a required, freely-typed reason — there is no default reason, because "a default reason is a reason nobody actually wrote"). Since F31 there are two ways in and one valve: the command-line tool, which records the actor as *an operator at a terminal*, and the platform area of the console, which records the operator by name. Both go through `organizations/valve.py` and both are audited.

### 6.8 Rate limiting

A sliding-window log held in each API process (not a fixed-window counter, which would let a caller burst a full window's allowance twice in one second at the boundary), plus a separate concurrency-slot cap for the most expensive endpoints (telemetry reads); each process enforces the limit divided by `API_INSTANCES`, which is exact on one process and exact across several only when traffic spreads evenly (ADR-020). The authentication tier is the exception: counted exactly in Postgres (`rate_limit_windows`, one upsert per attempt on a fixed one-minute window, keyed by a fingerprint rather than the address), and refused with `429` when Postgres cannot answer. Five named tiers, and the credential now decides the tier before the path does: an API token is counted against itself (300 a minute, 8 in flight) wherever it is pointed, including the MCP endpoint. A signed-in person is counted **per person** under a per-organization ceiling — counting a whole office against its one shared address was a defect fixed on 2026-09-20 — while signed-out callers stay on their address. Beyond that: authentication routes get the tightest per-minute limit (checked per IP *and* per email, closing the credential-stuffing surface); telemetry reads get a moderate per-minute limit plus a hard concurrency cap (the concurrency cap is what actually protects ClickHouse from many simultaneous scans); everything else gets a generous default; the one unauthenticated write (the deploy webhook) gets its own tier so it can't consume budget signed-in users need.

### 6.9 Domain read packages

Each follows the same internal layout: `router.py` (HTTP only, no SQL, no business logic) → `service.py` (logic) → `queries.py` (the actual SQL/ClickHouse statements) → `schemas.py` (request/response models). Every read enforces the tenancy chokepoint described in §6.4.

| Package | Serves |
|---|---|
| `traces` | Trace list, facets, and single-trace detail |
| `logs` | Trace-scoped log correlation, free-text search, and a live WebSocket tail |
| `services` | Per-service and per-endpoint health/trend, ranked worst-first by absolute error count |
| `servicemap` | The service dependency graph, derived from the same edges rollup the workers write |
| `errorgroups` | Exception fingerprinting and grouping, reading both spans and logs together |
| `rum` | Core Web Vitals summary and breakdown |
| `synthetics` | Synthetic check results over a window, phase breakdown, individual runs, a run's evidence, run counts |
| `checks` | Synthetic check *definitions* (Postgres): typed configuration, project-aware validation including request-forgery target rules, confirmed state and state changes, heartbeat tokens |
| `checkruns` | Run now: inserts a job for a probe worker into the Postgres `jobs` table, sends `NOTIFY`, and waits for its result in `job_results` (ADR-011) |
| `checkimports` | Previewing and confirming checks from a cURL command, an OpenAPI description or a Postman collection; parsing only, no network |
| `checksecrets` | Project secrets (sealed, write-only) and variables that checks use as `{{secret.NAME}}` and `{{name}}` (ADR-010). Sealed values name the key that sealed them; `python -m nexovue_api.keys status\|reseal` moves them to the current key after a rotation (`docs/operations/key-rotation.md`) |
| `checkfiles` | Files a check sends in a request: rows in Postgres, bytes in object storage under `synthetics/{org_id}/files/` |
| `maintenance` | Maintenance windows during which a failing check changes no state |
| `heartbeats` | `GET\|POST /heartbeat/{token}`: the unauthenticated URL a customer's job calls |
| `deploys` | Signed deploy-event webhook ingestion and listing |
| `sourcemaps` | Source map upload, storage, and stack symbolication |
| `dbstats` | Slow-statement and connection-health readings from a polled customer database |
| `usage` | Per-organization usage counters and the same allowance arithmetic the receiver enforces |
| `billing` | Plan, pricing, checkout, and the payment-processor webhook |
| `integrations` | Cloud/database connector definitions, with no credentials ever stored server-side |

### 6.10 Billing, in more detail

Usage is measured as **distinct service names per project per day**; the billable monthly quantity is the union of distinct services across the whole calendar month, not a sum of daily counts. The byte allowance is sized either by a free tier's own capped service count or, on a paid tier, by the customer's own committed service count from checkout — a paid tier with no active subscription is left unenforced rather than capped at zero. This arithmetic is implemented independently in Go (the receiver, for enforcement) and Python (the usage/billing screens, for display) — the code notes explicitly that the two must be kept in lockstep by hand, since neither language can import the other's implementation.

The payment-processor integration is behind an abstract port with no vendor vocabulary in its interface; one concrete adapter exists today. Starting checkout never changes an organization's plan — it only records a pending subscription row and hands the browser off to the processor. The plan changes only on the processor's own signed webhook event, verified by signature before any body is even parsed, with delivery-id-based idempotency so a repeated webhook delivery changes nothing on a replay.

---

## 7. apps/scheduler — synthetic checks and pollers

### 7.1 Purpose

A job-queue service running two kinds of measurement: synthetic checks (API and multistep API requests, TCP connections, DNS resolution, and heartbeats judged against their last call) and pollers (reading a customer's AWS CloudWatch RDS metrics or Postgres statistics). There is deliberately no result store for measurements — each task builds its own OTLP-shaped envelope and publishes it directly to Kafka, following the exact same internal envelope contract every other door uses.

Every scheduler process — beat and each worker — answers `/healthz`, `/readyz` and `/metrics` on `SCHEDULER_HEALTH_ADDR` (`:8081`) from a thread that reads timestamps only (`health.py`): liveness fails when the loop has been silent for 30 s, readiness when it has not reached Postgres for 30 s (a worker with every slot busy asks Postgres every 10 s for exactly this). `python -m nexovue_scheduler health` is the compose healthcheck. Beat's leader reads the queues every 15 s for `/metrics`: jobs ready per queue and the oldest's wait, alerts waiting, erasures overdue (`metrics.py`).

### 7.2 Scheduling

The schedule is not code — beat (`python -m nexovue_scheduler beat`, its own asyncio loop) re-reads the `checks` and `integrations` tables from Postgres on a refresh timer (default 30s). It builds one entry per enabled check **per probe location**, dispatched to that location's queue (`light.<location>`), with the *entire* definition in the task's arguments — variables, file names, maintenance windows, and the secrets it uses **still sealed** — so the worker that runs the check never queries Postgres on the path of a measurement (ADR-010's amendment). Each entry expires with its interval, so a late run is dropped rather than queued, and a check's first run is offset by a stable hash of its id so checks saved together do not fire together. Heartbeats have no entry of their own; a sweep judges them every minute. A second sweep deletes uploaded files no saved check uses, a day after upload.

Beat inserts each due entry as a row in the Postgres `jobs` table (ADR-020), and marks the entry fired only after the insert. Workers (`python -m nexovue_scheduler worker`, one for `light` and `light.<region>`, one for `deliveries`) claim rows with `FOR UPDATE SKIP LOCKED` under a lease of the task's hard limit plus 30 seconds, run each in a fixed pool of spawned child processes (a soft limit raises inside the task; the child is killed at the hard limit and replaced), and poll every second, waking early on `LISTEN`/`NOTIFY`. A worker that dies leaves its job to be re-claimed once the lease lapses, if the job has attempts left.

Because two beat replicas run for availability, only one may actually schedule at a time: a Postgres advisory lock, held on a dedicated connection, decides leadership. A follower polls for the lock every five seconds and does nothing else. When a leader dies, its connection drops, the lock is freed and the new leader rebuilds its schedule from zero — deliberately not firing a backlog of everything that was "due" while no leader existed.

### 7.3 Synthetic checks

Five kinds exist, validated by one shared model (`packages/py-platform/.../checks/config.py`) in the API and parsed from it in the scheduler: `api` (one request — any method, headers, query, a text/JSON/XML/form/multipart/binary body, basic/bearer/API-key auth from secrets — judged by assertions on status, headers, body text, a JSONPath value, response time or certificate days), `multistep` (requests in order, passing extracted values forward), `tcp`, `dns` (via dnspython, optionally against a chosen resolver), and `heartbeat`. Requests go through libcurl, timing DNS/TCP/TLS/time-to-first-byte/transfer phases separately; multipart bodies are built in memory, never on the worker's disk. Every connection — each redirect hop, a TCP host, a chosen DNS resolver — passes a strict egress guard that refuses loopback, link-local, private and cloud-metadata addresses at the moment of connection (not merely at validation time), with a narrower, development-only relaxation for private ranges alone. Customer patterns run on RE2, and JSONPath's regex functions are disabled, so no customer input can backtrack a worker.

A run's outcome is `up`, `degraded` (passing but slower than a threshold), `down`, `misconfigured` (an undefined `{{name}}`, a deleted secret or file — the author's to fix) or `prober_error` (ours). A failing target is still a successful task — "down" is a measurement and always published. A `down` result is **confirmed** by re-dispatching the check to the next location (or the same one when there is only one) before it counts; every attempt is published, marked `final` or not. After publishing, the final attempt dispatches a separate task that records the check's state in Postgres (`check_states`, `check_state_changes`); a run during a maintenance window is marked and changes no state. Every opened secret is redacted from the evidence by value (as typed, URL-encoded, JSON-escaped and base64) before anything leaves the worker. Run now executes the same code path, publishes nothing, and leaves its result in `job_results` for five minutes.

### 7.4 Pollers

Two kinds: CloudWatch (AWS RDS metrics) and Postgres (`pg_stat_statements`/`pg_stat_activity`). No credential is ever stored — instead, the platform generates a random external id and hands the customer an exact AWS IAM trust-policy condition to paste into their own role, the standard cross-account "confused deputy" mitigation, and assumes that role via STS for each poll. Postgres connections require IAM-based authentication; a stored password is explicitly refused. No SQL statement text is ever read or stored — only `pg_stat_statements`'s own query hash.

### 7.5 Publishing

Results are encoded as OTLP protobuf — for a check, gauges carrying the run's outcome, attempt, location and whether it was final, plus one log record whose body is the run's evidence as JSON (the request sent, the response received, each assertion with expected and actual) — and produced directly to Kafka with `acks=all` and idempotence enabled, and the publish call blocks until the broker confirms delivery — a probe that reported success while its result sat undelivered would be claiming a check ran when nothing downstream could ever see it.

---

## 8. apps/web — the console

### 8.1 Stack

React 19, code-based routing (not file-based) via TanStack Router, TanStack Query for data fetching, Tailwind CSS v4 driven entirely by design tokens (no default Tailwind palette classes are permitted — enforced by a script that greps the source for any default color-family name and fails the build if one appears), `uplot` for line/area charts, `@xyflow/react` (React Flow) for the service map, no external state-management library (cross-cutting UI state like the selected project and time range is a small hand-rolled module store), no form library, and Vitest for tests. No component library exists — every UI primitive (button, text field, select, skeleton) is hand-rolled directly on native HTML elements, deliberately, to keep native accessibility, autofill, and mobile-picker behavior.

### 8.2 Routing and layout

Every signed-in screen shares one shell: a sidebar (three groups — telemetry signals, telemetry surfaces, account/settings), a top bar (project switcher, time-range switcher, a command palette placeholder), and an error boundary around the routed content. There is no route-level auth guard; instead every signed-in screen polls a shared "who am I" session query, and a `401` anywhere is caught by one central handler that clears the session and redirects to login — no individual screen ever branches on an authentication failure itself. `403` responses are instead handled by the route's own error boundary. `429` responses show a toast honoring the server's retry timing. Project and time-range selection live only in memory — reset on reload, never persisted.

### 8.3 API client

A thin `fetch` wrapper (never axios — banned by the linter after a real supply-chain incident with that package), always same-origin, `credentials: "same-origin"` (cookie-based sessions), a generated W3C `traceparent` header on every request, and one typed error class carrying the HTTP status, a parsed problem-details body when present, and a correlation id — every non-2xx response and every network failure funnels through this one type, so no screen writes its own ad hoc `response.ok` branching.

### 8.4 Screens

One feature folder per screen area under `src/features/`, each holding its own typed API hooks and pure computation modules kept separate from the screen component so the computation can be unit-tested without rendering anything. Import direction is enforced by the linter, not just convention: a feature may never import another feature directly, may never escape its own directory with a relative `../` path, and may never import the app-composition layer — only shared code and the app layer may see across features.

Screens present: traces (list + waterfall detail), logs (search + live tail), services (ranked list + trend), service map, errors (grouped, with browser-stack symbolication), RUM (Core Web Vitals), profiles (flame graph with search, zoom, filters, timeline and compare — preview; also a span's *Profile* section and a Profiling strip on the service page), synthetics (every check with its confirmed state; new check with a Postman-style request editor and Run now; import from cURL, OpenAPI or Postman; a check page with runs, evidence, phase breakdown, state changes, edit, pause and delete), database (polled statement/connection stats), infrastructure (polled host/container metrics), usage, billing, settings (API keys, site keys, projects, profile), and onboarding.

Every numeric formatter follows one convention throughout the codebase: an absent or negative reading renders as an em-dash, never as `"0"` — a real zero and "we don't know" must never look the same to a reader of the screen.

---

## 9. packages/browser-sdk

A standalone JavaScript SDK a customer includes in their own page — not consumed by `apps/web` itself; it is only exercised in this repository by the end-to-end test suite (which injects the built bundle into a real page) and by the local demo traffic generator.

`init(options)` never throws and never returns nothing, even on a bad configuration or a browser missing `crypto.getRandomValues` — it degrades to an inert handle so a misconfigured SDK can never break the customer's page. A secret server key pasted into a page is refused outright, with a message telling the customer to rotate it, treated as a leaked credential rather than a simple mistake.

Captures: one span per page load (Navigation Timing), all five Core Web Vitals (via Google's `web-vitals` library, encoded as log records rather than metrics, because "log records are written straight to storage, while traces pass a tail sampler"), and uncaught JavaScript errors/unhandled promise rejections (via capture-phase listeners, never assigning `window.onerror` so a customer's own handler is never clobbered). There is no fetch/XHR auto-instrumentation and no resource-timing capture.

Query strings and URL fragments are stripped from every captured URL by default (a real historical incident is referenced as the reason); the fragment is never sent at all even when the switch is enabled. Transport batches by signal, flushes on a size threshold, a timer, or page-exit (`visibilitychange`/`pagehide`, deliberately never `unload`/`beforeunload`, which would disable the browser's back/forward cache), preferring `sendBeacon` on exit with a `fetch(keepalive)` fallback. A `429` response carrying a `Retry-After` header is treated as the per-second rate limit (retried); a `429` with no such header is treated as the monthly allowance being exhausted and permanently stops sending for the rest of the page's life. The built bundle is checked against a fixed gzip size budget as part of its own test run.

---

## 10. packages/py-platform and packages/ui

**`packages/py-platform`** (`nexovue_platform`) is shared by `apps/api` and `apps/scheduler`: one structured-logging configuration (built on `structlog`, bridged onto the standard library logger so third-party loggers don't double-print) and one request-context module (binding trace id, span id, correlation id, organization id, and project id onto every log line for the life of a request). It contains no OpenTelemetry SDK — despite the "telemetry" name, its only concern is structured logging plus W3C trace-context parsing for log correlation, not distributed tracing instrumentation of the Python services themselves.

**`packages/ui`** ships design tokens and self-hosted fonts only — no components. Colors are OKLCH scales exposed as semantic Tailwind theme variables (so one utility class resolves correctly in both light and dark mode with no separate dark-mode variant needed), checked by a script that re-measures every color pair against both APCA and WCAG contrast formulas directly from the token file, and a second script that fails the build if any default Tailwind color-family name appears anywhere in the web app's source.

---

## 11. The ClickHouse schema

### 11.1 Tables

Every table below carries `tenant_id` (and, except for the trace-lookup index, `project_id`) as the leading columns of its sort key, so every query is naturally scoped and every partition boundary respects tenant ownership.

| Table | Engine | What it holds |
|---|---|---|
| `spans` | MergeTree | Raw stored spans — only the sampler's survivors |
| `logs` | MergeTree | Raw log records, with promoted exception columns and a `timestamp_source` column |
| `metrics_gauge` / `metrics_sum` / `metrics_histogram` / `metrics_exp_histogram` / `metrics_summary` | MergeTree | The five raw OTLP metric point shapes, one table each |
| `endpoint_stats_1m` | AggregatingMergeTree | Per-minute request/error counts and latency quantile state, per service+endpoint — written **before** sampling |
| `service_edges_1m` | AggregatingMergeTree | Per-minute caller→callee call counts and latency quantile state — written **before** sampling |
| `span_trace_lookup` | MergeTree | A trace-id → time-range index, fed by a materialized view over `spans`, so a trace-id lookup doesn't have to scan every partition in the retention window |
| `trace_summaries` | AggregatingMergeTree | One row per stored trace (span count, error count, root service, duration, worst severity) — written **after** sampling, since it indexes what can actually be opened |
| `web_vitals` / `web_vitals_1m` | MergeTree / AggregatingMergeTree | Raw Core Web Vital readings and their per-minute p75 rollup |
| `profile_samples` | MergeTree | One row per profile sample: stack inline (functions, files, lines, binaries, addresses — leaf first), value in its type's unit, service/host/version/environment, span link. Sorted by hour then `stack_hash`; retention ≤ 14 days, and reads refuse rows past it before the TTL merge removes them |
| `span_ingest_offsets` / `span_written_offsets` / `ingest_written_offsets` | KeeperMap | Durable per-partition offset watermarks used for crash recovery — hold no customer data |

Raw signal tables default to 30 days' retention, keyed off the platform's own receive clock rather than a customer-supplied timestamp (a device or application clock can claim any year, so it is never trusted to expire data). Rollup tables default to roughly 13 months. Retention is enforced by expiring whole storage parts rather than mutating rows, which is also what makes the tenant-erasure deletion (§6.7) cheap.

A promoted, typed column is always preferred over a generic JSON attribute lookup for anything actually filtered on — measured directly in the API's own query code as roughly a seven-times difference in bytes scanned and latency for one representative filter.

### 11.2 Materialized views and write paths

Only one materialized view remains live today (`span_trace_lookup`, fed from `spans`). A second view, which once derived `endpoint_stats_1m` from stored spans, was deliberately dropped: because it read only the sampler's survivors, a service with a true 1% error rate displayed roughly 9% in the rollup it fed. The replacement is a direct worker insert, computed before sampling, in the same pass as the pre-sampling span counters — see §5.3.

### 11.3 Deduplication

Every table that receives a retried insert relies on one of two mechanisms: ClickHouse's own block-hash deduplication window (for tables written with a plain columnar insert — `spans`, `logs`, all five raw metric tables), or an explicit content-derived deduplication token computed by the writer (for tables written through a server-side aggregate fold via `INSERT … SELECT … FROM input(...)` — `endpoint_stats_1m`, `service_edges_1m`, the web-vitals rollup). `trace_summaries` needs neither: every field is merged with an idempotent function (max, min, most-recent-value, or set union), so a repeated identical write changes nothing regardless of how many times it lands.

---

## 12. The Postgres schema

Owned entirely by `apps/api`, applied with Alembic. Selected tables (columns abbreviated to what matters for understanding the shape):

| Table | Holds |
|---|---|
| `organizations` | Tenant root: slug, name, plan, soft-delete timestamp, ingest-suspension timestamp and reason |
| `users` | Account, hashed password, role, email-verification timestamp, a "sessions valid from" timestamp used to invalidate every session at once on a password reset |
| `projects` | An environment within an organization: slug, retention days, sampling configuration, masking configuration |
| `plans` / `plan_prices` | Tier definitions and append-only, currency-and-date-keyed price rows — a repricing is always a new row, never an edit, so an existing customer's signed rate never silently changes |
| `api_keys` / `site_keys` | The two ingest credential kinds described in §4.5/§6.6, in separate tables by design |
| `api_tokens` / `api_token_usage_daily` | Organization-owned read credentials (§6.6) and what each spent per day |
| `website_payments` | The payment that bought an organization before it existed (one per organization) |
| `plan_grants` | A complimentary plan until a date. The plan in force is an active grant if there is one, else `organizations.plan`, which nothing rewrites |
| `support_access_grants` | An owner's time-boxed consent for nexovue support to view the console as one of their people |
| `audit_log` | Who did what: actor, action, target, a JSONB detail, and — for a support view — the operator's label and impersonation id |
| `checks` / `integrations` | Synthetic check and cloud/database connector definitions |
| `deploy_events` / `project_webhook_secrets` | Deploy notifications and the signing secrets that authenticate them |
| `usage_daily` / `usage_daily_services` / `usage_ingest_offsets` | Per-day, per-project counters and the distinct-service set that determines the billable quantity |
| `billing_subscriptions` / `billing_webhook_deliveries` | Paid-arrangement state (`pending`, `active` — with `grace_until` while a failed payment's 7-day grace runs — `cancelled`, `lapsed`) and a global (not per-tenant) idempotency record of processed webhook deliveries, whose event id may not be blank. One live checkout per organization; closing an organization cancels its arrangements at the processor first; the scheduler's hourly `billing.end_lapsed_grace` ends a grace that has run out |
| `source_maps` | Metadata pointing at object-store bytes — never the bytes themselves |
| `project_first_traces` | The sampling carve-out counter described in §5.3 and §13, Flow 6 |
| `audit_log` | Every consequential action, with the actor and target recorded but never a credential value |

Primary keys are UUIDv7, generated server-side by Postgres itself, never minted by application code. Every timestamp column is timezone-aware; a naive datetime is refused by the linter across the whole Python codebase.

---

## 13. Six data flows, traced end to end

### Flow 1 — A trace, from an application to the screen

An OTLP export with a secret API key arrives at the receiver → authenticated and tenancy-resolved (§4.5) → checked against origin/allowance/rate-limit gates (§4.6) → split into one group per trace id (§4.7) → published to `spans-raw`, keyed by trace id, HTTP response withheld until Kafka acknowledges → a worker consumes the batch → masks it in place → folds it into the pre-sampling counters and the service-edge accumulator regardless of what happens next → hands it to the tail-sampling buffer → the buffer's window closes and a policy decides keep or drop → kept spans, the pre-sampling rollups, and (after the verdict) the trace summary are all inserted into ClickHouse → the write/settlement watermarks advance only once ClickHouse has acknowledged → the Kafka offset commits only once the watermarks confirm storage → the console's trace list reads `trace_summaries`, and opening one trace reads `span_trace_lookup` to bound a scan of `spans`.

### Flow 2 — A log record

Same gate chain as Flow 1, on the logs door; the whole batch is published as one envelope (never split, since nothing downstream reassembles a log batch), keyed by tenant id → a worker masks it in place, then extracts a `timestamp_source` and promoted exception columns → inserts into `logs`, relying on ClickHouse's own block-hash deduplication window (no application-level token here) → the write watermark advances only after the insert is acknowledged. The console's log-search endpoint deliberately uses a case-insensitive substring match rather than the store's own token-based full-text index, because a token index can't answer a half-typed search term.

### Flow 3 — A metric data point

Same gate chain, on the metrics door, published unsplit → a worker masks the full payload, then dispatches each point by its OTLP shape into one of five row builders → all five tables insert in a fixed order, watermark advancing only after every one succeeds. Two of the five raw metric tables (`metrics_histogram`, `metrics_exp_histogram`, `metrics_summary`) are written, masked, and retained but have no API reader at all today — nothing in the console queries them for anything beyond tenant-data deletion.

### Flow 4 — A browser page view

The SDK sends a page-load span to the traces browser door and web-vital/error log records to the logs browser door, both authenticated with a public site key presented via header (`fetch`) or query string (`sendBeacon`) → the receiver's origin gate is genuinely load-bearing here (unlike the server door): a public key with no `Origin` header, or an `Origin` not on the key's allow-list, is refused → both doors publish through the identical Kafka/worker path as Flows 1–2, tagged with the browser door on the envelope → the log-writing pipeline extracts web-vital readings from the same log records it's already writing (no separate consumer) and writes the reading, then the per-minute rollup, then the underlying log row, in that specific order, so a retry of a partially-failed batch never leaves a promoted reading whose source log row was never written.

### Flow 5 — A synthetic check

A check is defined in the console (or imported) and stored in Postgres → the scheduler's leader-elected beat process picks it up on its refresh timer, embedding the whole definition, sealed secrets included, in one task per location → a probe worker in that location runs it → a down result is retried from the next location before it counts → the result — even a failure, which is itself a valid measurement — is encoded as an OTLP envelope and produced directly to Kafka with a delivery-blocking publish call, using the same `metrics-raw`/`logs-raw` topics and the same worker pipelines as any other metric or log. There is no dedicated synthetic-results pipeline or table; the only synthetic-specific artifacts downstream of Kafka are the envelope's door value and the metric/attribute names the console's synthetics screen filters on.

### Flow 6 — Sign-up to first data

Signup creates an organization, an owner user, a first project, and a first API key in one Postgres transaction, and shows the plaintext key exactly once (it is hashed at rest thereafter and unrecoverable). Email verification is sent best-effort and never blocks signup — an unconfigured or failing mail provider only logs a warning. The onboarding screen's "has anything arrived yet" indicator is not a dedicated endpoint; it simply polls the ordinary trace-list endpoint for a non-empty result. A separate, unrelated mechanism — a per-project "first traces" counter in Postgres — makes it statistically likely that trace actually exists to be found: the tail sampler unconditionally keeps a project's first N traces (a configurable carve-out, default 100) so a customer's first hello-world trace isn't lost to the normal sampling percentage before anyone can see it arrive. Usage ceiling enforcement at the receiver returns `429` with **no** `Retry-After` header when the monthly allowance is exhausted — deliberately, so a compliant exporter drops the batch instead of retrying for weeks — in contrast to a plain rate-limit refusal, which always carries one. Raising the ceiling goes through checkout, which never changes the plan itself; only the payment processor's signed webhook event does.

---

## 14. How to run this locally

### 14.1 Prerequisites

| Tool | Version pinned in the repository |
|---|---|
| Go | 1.27.0 |
| Python | 3.13 |
| uv | 0.12.6 |
| Node | 26.12.0 |
| pnpm | 11.24.0 |
| Docker Compose | v2+ |

Also needed on the host: `just` (the task runner every command below goes through), the `buf` CLI, `golangci-lint`.

### 14.2 First run

```
just setup   # go work sync && uv sync --all-packages && pnpm install
just dev     # brings up every infrastructure and application container, creates
              # Kafka topics, applies every migration, seeds one dev organization,
              # and starts a bundled multi-service demo application that sends
              # itself real traffic through the ingestion path
```

`just fresh` does the same from a completely clean slate (deletes every volume first) — it takes roughly two and a half minutes, mostly waiting on ClickHouse and Kafka.

### 14.3 Ports (local dev defaults)

| Host port | Service | Purpose |
|---|---|---|
| 15432 | Postgres | Control plane |
| 18123 | ClickHouse | HTTP protocol (used by `apps/api`) |
| 19000 | ClickHouse | Native protocol (used by `apps/workers` and the migration tool) |
| 19092 | Kafka | Client bootstrap |
| 18333 / 19333 | SeaweedFS | S3 API / cluster status |
| 14317 / 14318 | receiver | OTLP gRPC / OTLP HTTP (and the browser door) |
| 18000 | api | Control-plane REST API |
| 15173 | web | The console (Vite dev server) |

Every service's admin/health port (`:8081` for the Go services) is container-network-only, reachable via `docker compose exec <service> wget -qO- http://127.0.0.1:8081/readyz`.

### 14.4 Environment variables

Documented in full in the repository's `.env.example`, one file listing every variable any service reads, with the rule stated once at the top: a variable with no default there is required, and every service that needs it exits at boot naming the missing variable by name — never silently starting with a wrong or invented value. Nothing carrying a credential has a default in code.

### 14.5 Migrations

Both through the setup step every deployment runs before its services (docs/changes/ready-to-deploy, Step 1). Postgres: `just migrate-pg` runs `nexovue_api.setup` — Alembic to head under a Postgres advisory lock, then the bucket if missing. ClickHouse: `just migrate-ch` runs `apps/workers/cmd/setup clickhouse` — the migrations on the native client, recorded in golang-migrate's `schema_migrations` format (so a database it migrated carries on), under a second Postgres advisory lock, since ClickHouse has none. Rollback is always per-store (`just migrate-down-pg` / `just migrate-down-ch`); there is deliberately no combined rollback command, since "one step back in each store" is never a coherent single operation.

### 14.6 Day-to-day

`just up` / `just down` (keeps volumes) / `just down-hard` (deletes every volume, including Kafka's buffer) / `just logs [service]`. `just topics` recreates Kafka topics by hand if ever needed (auto-creation is disabled on the broker; a missing topic makes the receiver refuse every export). `just seed` (re)creates the one development organization/user/project/key set. `just load` runs the load-testing traffic generator, and must be run on the host rather than in a container, since its default target is the receiver's host-published port.

---

## 15. Testing and the quality gate

`just check` is the pre-commit gate and runs with no infrastructure required: formatting (per-language), linting (per-language), unit tests (per-language), and the protobuf contract's own lint/breaking-change check.

- **Go**: `go test -count=1 ./...` per module — `-count=1` is called out explicitly in the task runner's own comments as load-bearing, because a cached "ok" result has twice reported success while executing zero tests.
- **Python**: `pytest`, with `asyncio_mode=auto`. Offline test files run against hand-built fakes with no live dependency. A parallel set of `*_live.py` suites runs against a real Postgres/ClickHouse stack; a shared guard function makes an unreachable stack a hard test *failure*, not a silent skip, unless a specific environment variable opts into skipping — because a self-skipping suite reports exactly the same green result as one that actually ran, which is unacceptable for tests proving tenant isolation.
- **TypeScript**: Vitest, colocated `*.test.ts` files next to the source they test.

Outside the standard gate, because each needs Docker or takes real time: `just e2e` (Playwright against a running stack — every test signs up its own fresh organization rather than sharing state), `just chaos` (kills the Kafka broker and ClickHouse mid-load and asserts nothing acknowledged was ever lost, by comparing what the receiver accepted against what the pre-sampling counters actually recorded), and `just load` (the traffic generator, used both standalone and as the load source for the chaos suite).

---

## 16. Deployment shape

Every install is `deploy/compose/nexovue.yaml` (self-hosted, `NEXOVUE_DEPLOYMENT=self-hosted`), overlaid for the hosted service (`nexovue.hosted.yaml`), the laptop quickstart (`nexovue.quickstart.yaml`, plain http on localhost), the production replica's local build (`nexovue.build.yaml`, `just dev-app`) and three Kafka brokers (`nexovue.kafka3.yaml`); overlays change settings, never lists. `deploy/nexovue` installs, upgrades, rolls back, backs up and restores an install kept in `/opt/nexovue` (`docs/operations/deployment.md`); a `monitoring` profile runs Prometheus with `deploy/monitoring/rules`. **Two images (ADR-019):** `nexovue` (`deploy/images/nexovue.Dockerfile` — the API, the scheduler and the console's files; the role is the command, `nexovue api`, `nexovue scheduler-beat`, `nexovue setup`, `nexovue web-assets <dir>`) and `nexovue-pipeline` (`/receiver`, `/worker`, `/dlq`, `/setup`). In the replica, `setup` and `setup-pipeline` run to completion before every other service starts. CI (`.github/workflows/ci.yml`) runs `just check` on every push; a `v*` tag runs `release.yml`, which builds both images natively for amd64 and arm64, publishes them to `ghcr.io/nexovue-tech`, signs and attests them. **Both deployment files put one Caddy edge in front of everything** (`deploy/caddy/Caddyfile`), and nothing else publishes a port. It terminates TLS for two hostnames — `NEXOVUE_APP_HOST` for the console, `NEXOVUE_INGEST_HOST` for ingestion — deliberately different so a session cookie scoped to one can never reach the other. On the app name it serves the console's built files itself (copied from the web image into a volume by `web-assets-init`; `index.html` is sent `no-cache`, hashed assets a year and `immutable`), proxies `/api/*` to every API replica, caps request bodies at the API's largest, and sends the security headers and the content-security policy; the hosted file adds Razorpay's checkout origins to that policy through `NEXOVUE_CHECKOUT_ORIGINS`, the on-prem file none. Every public URL is built from the two names: the API's `OTLP_ENDPOINT` is `https://<ingest name>` and `APP_BASE_URL` `https://<app name>`. `API_INSTANCES` and `RECEIVER_INSTANCES` each set both a service's replica count and the share of the rate limits each copy enforces.

Both image recipes are multi-stage builds; the Go services land on a minimal distroless base running as a non-root user, with no shell — which is why each Go binary has its own `-health` flag for a container healthcheck to invoke instead of `curl`/`wget`. `TRUSTED_PROXY_COUNT` must be set correctly for whatever reverse-proxy hop count actually sits in front of the API in a given deployment, or every caller behind that proxy collapses onto a single shared rate-limit bucket.

---

## 17. Coding standards and conventions

These are the standards actually enforced by the repository's own lint configuration and consistently followed in its code — not aspirational, checked.

### 17.1 Go

- Formatters: `gofmt` + `goimports`, with the module's own import path grouped separately from third-party and standard-library imports.
- Linters beyond the standard set: unclosed HTTP bodies, dropped contexts, errors that don't wrap, repeated literals (magic values), security issues, misspellings, swallowed errors, missing documentation on exported identifiers, unnecessary type conversions, wasted assignments.
- Every configuration struct is loaded once, validated with every violation reported together (not just the first one found), and a missing required variable names itself in the failure.
- Errors are always wrapped with `%w` at every boundary, and every custom error type implements the standard interfaces so callers can use `errors.Is`/`errors.As` rather than string-matching.

### 17.2 Python

- Formatter/linter: `ruff`, 100-character lines, targeting the pinned Python version; selected rule sets include timezone-aware-datetime enforcement, import sorting, naming, and a ban on bare `print()` outside operator command-line tools (whose output *is* their interface, by design).
- `mypy --strict` across the whole codebase, with a small number of narrow, individually justified exemptions (e.g. a third-party library's own lack of type stubs).
- One domain package = one directory: `router.py` / `service.py` / `queries.py` / `schemas.py`, dependency injection entirely via FastAPI's own dependency system, everything `async`, structured logging via the shared `nexovue_platform` package, never a raw `print`.
- Every raised error is a typed domain exception mapped centrally to one HTTP response shape — a route handler never builds an HTTP response or raises a raw framework exception directly.

### 17.3 TypeScript

- Formatter/linter: Biome — no explicit `any`, no non-null assertions, no `console` outside one designated logging module (with `console.error` as the sole always-allowed exception), unused imports forbidden.
- Import direction between the shared/features/app layers is enforced at lint time, not just by convention (see §8.4).
- No axios anywhere (see §8.3); no component library — every primitive is hand-rolled on native HTML elements.
- Field names in TypeScript interfaces describing API responses are kept identical to the API's own field names, unconverted, specifically so the wire shape stays traceable by inspection.

### 17.4 Shared across every language

- **Configuration**: a variable with no default is required, and a service exits at boot naming exactly which variable is missing or invalid — never starts with a guessed value. Credentials never have a default anywhere in code.
- **Logging**: identical field names across the Go and Python services (`ts`, `level`, `msg`, `service`, `env`, `trace_id`, `span_id`, `correlation_id`, `org_id`, `project_id`), identical level meanings (`error` — a human must look; `warn` — degraded but handled; `info` — a state change worth a permanent record; `debug` — development-only detail), JSON in production and a human-readable console format in development.
- **Comments**: short, explaining *why*, not what — code that needs a long comment to explain what it does is restructured instead. Every migration file, in particular, states its own reasoning and, where relevant, the specific measurement that justified it.
- **Naming**: env vars in `SCREAMING_SNAKE_CASE`, shared infrastructure variables use one identical name across every service that reads them (`KAFKA_BROKERS`, `POSTGRES_DSN`, `CLICKHOUSE_DSN`), Kafka topics follow `<signal>-raw` / `<signal>-dlq`, ClickHouse/Postgres tables and columns are snake_case, HTTP routes are versioned and resource-plural.
- **Testing**: a suite that cannot reach a dependency it needs must fail loudly, never silently skip and report the same green as a suite that actually ran — this exact guard exists independently in the Python live-test helper and is the explicit reasoning behind the Go test suite's `-count=1` convention.
- **Generated code is committed, not built on the fly**, and is excluded from every linter/type-checker configuration by name.

---

## 18. Known gaps and inconsistencies found in the code

Recorded here because they were found while tracing the flows above, not because they were documented anywhere else. Re-verified against the source on 2026-09-12. Gaps that turned out to be removable were removed from the code rather than from this list: the `synthetic-results` topic nothing produced to or consumed from, the `sessions` Postgres table nothing wrote at the time (sessions have since moved into a new `sessions` table, §6.5), a development retention-override script written against a schema that no longer exists, and two query helpers (`deploys.queries.webhook_keys`, `integrations.queries.find_integration`) no service called.

- **The `DOOR_WEBHOOK` envelope value** is defined in the protobuf contract but is never constructed by any code path. Deploy webhooks are written to Postgres by `apps/api` and never enter the envelope path.
- **Three of the five raw OTLP metric tables** (`metrics_histogram`, `metrics_exp_histogram`, `metrics_summary`) are written, masked, and retained, but the only API code that touches them is tenant erasure — a customer sending an OTLP histogram or summary metric today has it stored with nothing in the product reading it back.
- **`logs.timestamp_source`** is written by every log insert but is not read by any API endpoint or screen.
- **Pre-sampling span counters over-count by roughly 0.18% across a ClickHouse restart, and the cause is unexplained.** Killing Kafka and then ClickHouse under sustained load counted 283,002 spans against 282,490 acknowledged — **+512, with nothing lost and consumer lag at zero**. The excess is exactly `tools/loadgen`'s batch size of 512: one batch counted twice, not a scattering. It reproduced at that identical magnitude across three chaos runs, including one after the `WriteOnce` read-before-retry guard was removed, which is what proved that guard was never preventing it. [ADR-0061](../decisions/0061-counter-overcount-across-a-store-restart.md) first attributed it to the deduplication log being lost across a restart; [ADR-0075](../decisions/0075-block-deduplication-replaces-the-read-before-retry.md) measured that premise false — the log survives both a graceful restart and a `SIGKILL` — and withdrew the claim. The likelier cause, untested, is that a re-derived block is not byte-identical: a rebuilt minute can legitimately contain late spans the first did not, changing the checksum, so deduplication correctly declines to treat it as a repeat. `KeeperMap` bounds the overcount; it does not remove it. Accepted deliberately rather than chased — `apps/workers/internal/pipeline/ingeststate/state.go:221` records the reasoning, that an undercount is a worse answer than a bounded overcount for a monitoring product.

---

## 19. Product, SDD, and research documents

Everything above this section was written purely from the code. The repository now uses a
spec-driven development structure: `AGENTS.md` explains how agents and contributors work;
`docs/engineering/` records reusable engineering rules; `docs/architecture/` describes current system
truth; and feature work is tracked under `docs/features/`. Product documents describe intent rather
than what is currently built — useful context for understanding why the code is shaped the way it is,
but not a source of truth for implementation status.

- **`docs/product/vision.md`** — product vision, target users, scope, pricing direction, decisions, and open questions.
- **`docs/product/feature-catalog.md`** — the feature inventory and queue. Each selected feature gets a separate SDD workspace under `docs/features/<feature>/`.
- **`docs/research/market-analysis-2026.md`** — dated market research and evidence; it does not assert current implementation.
- **`docs/architecture/`** — current-state architecture, security/tenancy, and deployment documents.
- **`docs/operations/`** — verified operational procedures and the production-readiness documentation still to be completed.

These three files describe what the team intends to build and why; the rest of this document describes what the code actually does.
