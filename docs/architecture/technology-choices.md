# Technology Choices

> **Type:** Architecture reference · **Last verified:** 2026-08-25
> **Rule:** nothing is added to this stack without searching first — current version, maintenance signal, and licence. Recorded here with the source, so a future reader can tell what was checked and when.

## Data plane (Go)

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **Apache Kafka 4.x (KRaft)** | ZooKeeper removed in 4.0; [April 2026 benchmark](https://computingforgeeks.com/kafka-vs-redpanda-benchmarks/) measured higher sustained throughput at 3× less memory than Redpanda on identical hardware | Apache 2.0 | **Redpanda** — [BSL 1.1](https://docs.redpanda.com/current/get-started/licensing/overview/); its Streaming/Queueing Service clause is an unresolved ambiguity for a commercial SaaS and would forbid per-tenant topics permanently |
| **[franz-go](https://github.com/twmb/franz-go)** | Pure Go, feature-complete through Kafka 4.2+, no cgo | Apache 2.0 | **confluent-kafka-go** — more stars, but wraps librdkafka in C, complicating static builds and cross-compilation |
| **[ch-go](https://clickhouse.com/docs/integrations/language-clients/go/index)** (write path) | ClickHouse's own docs recommend it for insert-heavy workloads; `clickhouse-go` pays a row→column pivot plus reflection per insert | Apache 2.0 | **clickhouse-go/v2** — friendlier `database/sql` semantics we do not need on a pure insert path |
| **Go stdlib `net/http` + `google.golang.org/grpc`** | Stdlib routing is sufficient since 1.22; one less dependency on the hot path | BSD-3 | **chi** — fine, but earns nothing here |
| **Protobuf + [buf](https://buf.build)** for the internal envelope | Cross-language contract between Go producers and Go/Python consumers; renames fail CI instead of silently zeroing data; `buf breaking` enforces compatibility. Envelope holds our metadata only — the payload is untouched OTLP | Apache 2.0 | **JSON + JSON Schema** — readable on the wire, but no compile-time safety across two languages, 2–5× larger messages, slower parsing at ingest volume |

## Control plane (Python)

| Choice | Why | Licence | Alternative rejected |
|---|---|---|---|
| **[uv](https://cuttlesoft.com/blog/2026/01/27/python-dependency-management-in-2026/)** | ~75M monthly downloads, ahead of Poetry's ~66M; Astral acquired by OpenAI March 2026; 10–100× faster than pip | MIT/Apache 2.0 | **Poetry** — mature, still preferred for publishing libraries; we publish nothing |
| **FastAPI + Pydantic v2** | Async, typed, OpenAPI generation feeds `packages/client-ts` | MIT | — |
| **Celery + Beat** | DB-backed schedules editable from the UI; process-based model suits heavy Playwright checks | BSD-3 | **[Taskiq / FastStream / Dramatiq](https://docs.bswen.com/blog/2026-06-18-celery-alternatives-2026/)** — genuinely faster on I/O-bound work, but our probe volume is per-minute, not per-second, so the overhead never binds |
| **clickhouse-connect** | Official Python client for the read path | Apache 2.0 | — |

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
