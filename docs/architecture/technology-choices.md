# Technology Choices

> **Type:** Architecture reference · **Last verified:** 2026-08-25
> **Rule:** nothing is added to this stack without searching first — current version, maintenance signal, and licence. Recorded here with the source, so a future reader can tell what was checked and when.

## Data plane (Go)

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **Apache Kafka 4.x (KRaft)** | ZooKeeper removed in 4.0; [April 2026 benchmark](https://computingforgeeks.com/kafka-vs-redpanda-benchmarks/) measured higher sustained throughput at 3× less memory than Redpanda on identical hardware | Apache 2.0 | **Redpanda** — [BSL 1.1](https://docs.redpanda.com/current/get-started/licensing/overview/); its Streaming/Queueing Service clause is an unresolved ambiguity for a commercial SaaS and would forbid per-tenant topics permanently |
| **[franz-go](https://github.com/twmb/franz-go)** | Pure Go, feature-complete through Kafka 4.2+, no cgo | Apache 2.0 | **confluent-kafka-go** — more stars, but wraps librdkafka in C, complicating static builds and cross-compilation |
| **[ch-go](https://clickhouse.com/docs/integrations/language-clients/go/index)** (write path) | ClickHouse's own docs recommend it for insert-heavy workloads; `clickhouse-go` pays a row→column pivot plus reflection per insert | Apache 2.0 | **clickhouse-go/v2** — friendlier `database/sql` semantics we do not need on a pure insert path |
| **[SeaweedFS](https://productimpossible.com/articles/self-hosted-s3-after-minio/)** (local dev) + **AWS S3** (deployment) | S3-compatible, actively maintained, Apache 2.0 — so no archived or copyleft component exists anywhere in the stack. Code touches the **generic S3 API only**, so the deployment swap is configuration. SeaweedFS also remains a viable production target if a fully on-premise deployment is ever needed | Apache 2.0 / — | **MinIO** — community edition archived 25 Apr 2026, unpatched, AGPLv3. **Garage** — maintained but AGPLv3 |
| **Go stdlib `net/http` + `google.golang.org/grpc`** | Stdlib routing is sufficient since 1.22; one less dependency on the hot path | BSD-3 | **chi** — fine, but earns nothing here |
| **Protobuf + [buf](https://buf.build)** for the internal envelope | Cross-language contract between Go producers and Go/Python consumers; renames fail CI instead of silently zeroing data; `buf breaking` enforces compatibility. Envelope holds our metadata only — the payload is untouched OTLP | Apache 2.0 | **JSON + JSON Schema** — readable on the wire, but no compile-time safety across two languages, 2–5× larger messages, slower parsing at ingest volume |

## Control plane (Python)

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **[uv](https://cuttlesoft.com/blog/2026/01/27/python-dependency-management-in-2026/)** | ~75M monthly downloads, ahead of Poetry's ~66M; Astral acquired by OpenAI March 2026; 10–100× faster than pip | MIT/Apache 2.0 | **Poetry** — mature, still preferred for publishing libraries; we publish nothing |
| **FastAPI + Pydantic v2** | Async, typed, OpenAPI generation feeds `packages/client-ts` | MIT | — |
| **Celery + Beat** | DB-backed schedules editable from the UI; process-based model suits heavy Playwright checks | BSD-3 | **[Taskiq / FastStream / Dramatiq](https://docs.bswen.com/blog/2026-06-18-celery-alternatives-2026/)** — genuinely faster on I/O-bound work, but our probe volume is per-minute, not per-second, so the overhead never binds |
| **clickhouse-connect** | Official Python client for the read path | Apache 2.0 | — |

## Testing & telemetry tooling

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **[Playwright](https://playwright.dev/docs/docker)** | Two jobs: browser-flow synthetic checks (P1-35) and our own e2e tests (P1-60/61). Three engines including WebKit, and auto-waiting — which is what keeps unattended checks from producing 3am false alarms. Official ARM64 images | Apache 2.0 | **Puppeteer** (Chromium only) · **Selenium/Cypress** (weaker multi-engine and waiting semantics). Checkly, the category leader in browser synthetics, is Playwright-based |
| **[telemetrygen](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/cmd/telemetrygen)** | Synthetic OTLP traces/metrics/logs over HTTP and gRPC with no application to deploy — the right shape for load and soak testing (P1-18, P1-50) | Apache 2.0 | Hand-rolled load generator — more work, less realistic protocol coverage |
| **Own demo app** (`tools/demo-app`) | Daily driver: 2–3 services, ~300 MB, deterministic fault injection on command, and it dogfoods the exact customer onboarding path every day | — | Using the OTel Demo daily — it needs 4–8 GB and does not coexist with our stack on a 24 GB machine |
| **[OpenTelemetry Demo](https://opentelemetry.io/docs/demo/requirements/system/)** | Periodic realism gate (P1-62): 18 services across ~10 languages with `flagd` fault injection. Answers what our tidy demo app cannot — cardinality, semconv variation, service-map density | Apache 2.0 | Building our own realistic polyglot fleet — enormous effort to reproduce something maintained by the OTel community |

## Frontend (TypeScript)

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **Vite + React** | App is entirely behind auth — no SEO surface to justify SSR complexity | MIT | **Next.js** — RSC complexity buys nothing here |
| **[TanStack Router](https://tanstack.com/router)** | Typed search params matter unusually much when time ranges, filters, service names and trace IDs all live in the URL; pairs natively with TanStack Query | MIT | **React Router v8** — larger ecosystem, weaker search-param inference, yearly majors with a painful migration history |
| **TanStack Query** | Server-state cache, request dedup, background refetch | MIT | — |
| **[uPlot](https://github.com/leeoniya/uPlot)** | Time-series specialist; [ECharts hits a canvas ceiling past ~500K–1M points](https://lightningchart.com/blog/best-apache-echarts-alternative-in-2026/) | MIT | **ECharts / Recharts** — Recharts cannot handle telemetry density at all |
| **[React Flow / xyflow](https://xyflow.com/open-source)** | Service-map nodes are React components, so a box can hold name, health colour, rate and a sparkline. Our customers run 5–50 services, well inside its DOM-node comfort zone. Layout via dagre/elkjs | MIT | **[Cytoscape.js](https://radar.firstaimovers.com/react-flow-vs-cytoscape-graph-engine-choice)** — better at thousands of nodes and richer built-in layouts, but a separate non-React rendering world; right answer only if graphs get far larger than our customers have |
| **Tailwind v4** | CSS-first config built on custom properties with OKLCH native, so our design tokens *are* the framework's tokens; zero runtime | MIT | **Panda CSS** — stronger when tokens must cross a design tool and code; we have one brand and no Figma handoff. **CSS Modules/vanilla-extract** — most control, far more hand-written CSS |
| **[Base UI](https://blog.logrocket.com/headless-ui-alternatives/)** | Built by the original Radix engineers, now at MUI with a full-time team and monthly releases; v1.0 December 2025 | MIT | **Radix UI** — maintenance visibly slowed, long-open issues. **React Aria** — best-in-class accessibility, considerably more verbose |
| **[shadcn/ui](https://ui.shadcn.com/docs/registry)** | A code distribution registry built on Base UI, not a dependency — the CLI copies source we own and restyle. Defaults to Base UI since Dec 2025 | MIT | Building every primitive from scratch — slower with no ownership benefit, since shadcn output is ours to rewrite anyway |
| **TanStack Virtual + Table** | Log viewer and trace waterfall render 100k rows with ~60 in the DOM | MIT | — |
| **[Biome](https://biomejs.dev/blog/roadmap-2026/)** | Format + lint in one Rust binary; 2.2's `noRestrictedImports` patterns and `noImportCycle` cover our import-boundary rules | MIT | **ESLint + plugins** — kept in reserve; `dependency-cruiser` is the fallback if Biome's boundary support proves too weak ([Nx-style module boundaries are still only a discussion](https://github.com/biomejs/biome/discussions/6245)) |

## Still unpicked

*(Nothing blocking. Add here as gaps appear.)*

## Structure references

Layout decisions and their sources are in [repository-structure.md](repository-structure.md#references).

---

## Verification log

Checked 2026-08-26. Results feed the tasks named.

| Area | Finding | Outcome |
|---|---|---|
| **OTel browser SDK** | The core JS SDK is stable and production-used, but [browser instrumentation is "experimental and mostly unspecified"](https://opentelemetry.io/docs/languages/js/) and lives in a separate `opentelemetry-browser` repo | ⚠️ **Risk on P1-39.** Door 3 rests on an experimental component. Budget for churn, or pin hard and accept manual upgrades |
| **OTel semantic conventions** | [HTTP conventions stable since Nov 2023, with 17 attributes renamed](https://opentelemetry.io/docs/specs/semconv/non-normative/http-migration/) (`http.status_code` → `http.response.status_code`, etc.). SDKs support `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup` to emit both | ⚠️ **Schema impact.** Promoted columns use stable names, and normalization must accept **old and new** — customers run varying SDK versions. GenAI conventions renamed three times in one 2026 cycle, so `gen_ai.*` stays in attributes, never promoted |
| **ClickHouse Kafka engine** | [At-least-once, offsets committed after insert](https://clickhouse.com/docs/integrations/kafka); it moves messages into a table and nothing more | ✅ **Our Go workers stand.** Trace assembly, tail sampling, masking and derivation have nowhere to run inside a table engine |
| **Collector `tailsampling`** | [Cannot scale easily — state is in memory and every span of a trace must reach the same instance](https://opentelemetry.io/docs/collector/scaling/), needing a two-layer collector deployment. Its future is under active discussion. The docs name our exact design as the alternative: "route spans through Kafka using trace ID as the partition key" | ✅ **Strongly validates P1-21.** Our approach is the recommended pattern and avoids the two-layer topology |
| **ClickHouse per-tenant retention** | Conditional TTL works: `MODIFY TTL ... INTERVAL 30 DAY WHERE tenant_id IN (...)`, plus `ttl_only_drop_parts=1` to drop whole parts instead of running mutations | ✅ **Long-standing open question closed.** No scheduled deletes needed — a job regenerates the TTL expression from plan data in Postgres. Note: partitioning *by* tenant_id would explode part counts with many small tenants, so date partitioning stays |
| **testcontainers** | Actively developed for both Go and Python; ClickHouse and Kafka modules are [community-maintained rather than first-party](https://testcontainers.com/modules/clickhouse/) | ✅ Fine for P1-04, with the note that two modules we depend on are community-owned |
| **Playwright footprint** | [~1 GB per concurrent browser, 2 GB+ under load](https://bug0.com/knowledge-base/playwright-docker); needs `--ipc=host` and an enlarged `/dev/shm` or Chromium crashes leaving zombie processes; roughly one vCPU per concurrent browser | ✅ **Concrete sizing for P1-35 and M7.** The `/dev/shm` default of 64 MB is the most common cause of "unstable" Playwright in Docker |
| **APCA** | [WCAG 3 is not a recommendation and "WCAG 3 Compliant does not exist yet"](https://git.apcacontrast.com/documentation/README); the `apca-w3` package is licensed to W3/AGWG for WCAG-related web content use | ⚠️ **Acted on already.** The palette is now verified against APCA *and* WCAG 2.1 AA, since only the latter is something a buyer can hold us to. Use the reference library as a dev-time check at P1-08, not shipped in the bundle |
| **Kafka 4.x single-host config** | Not researched — this is a measurement task, not a literature question | Open: settled by the load test at P1-50 |
