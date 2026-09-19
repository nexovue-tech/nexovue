# Plan: Continuous profiling (F17)

> **Status:** Approved 2026-09-19 — in progress
>
> **One document: the order of the work and its tasks.** The founder approves this plan before any file is
> edited.

## Preconditions

- [spec.md](spec.md) approved 2026-09-19 (D1–D5 decided); [design.md](design.md) approved 2026-09-19;
  [ADR-015](../../decisions/ADR-015-profiles-are-normalised-at-the-intake.md) accepted.
- The development stack running (`just up`), with the OpenTelemetry Demo for real services to profile.

## Current system

Read from the code on 2026-09-19. **Three findings correct the design** (marked ⚠).

### Receiver
- Doors: `apps/receiver/internal/otlp/signals.go:66-110` — `tracesDoor`, `logsDoor`, `metricsDoor`, each a
  `signalDoor` with `endpoint{httpPath, grpcPath, unit}`, `countItems` and `accept`. Paths at `:26-28`.
- Units: `otlp/traffic.go:98-109` — `Spans`, `LogRecords`, `DataPoints`, and `units` must list every one.
- Gates, in order `auth → origin → allowance → ratelimit → decode → normalize → produce`
  (`otlp/handler.go:7`; HTTP `:223-226`, gRPC `otlp/grpc.go:85-111`).
- ⚠ **Rate limit is per key, across signals**: `otlp/gates.go:206` `o.Limiter.Allow(ctx, identity.KeyPrefix)`;
  the fallback bound too (`:223`). A profiling flood would spend the budget spans need — the design's own
  bucket is a real change to `throttle` (and `handler.go:280`, `grpc.go`), which currently cannot see the door.
- ⚠ **The allowance "door" is the key kind, not the signal**: `ingest/ingest.go:44` `DoorFor` returns
  `DOOR_OTLP` or `DOOR_RUM`; `gates.go:149` checks the organization's month for that door. **The design's
  "allowance gains the door and its not-metered rule" is unnecessary**: the allowance is spent from bytes the
  usage meter records, so D1 is met by the meter (below), and the gate as it stands already refuses a deleted,
  suspended or over-allowance organization (AC3). No allowance change.
- Every door is also mounted on the browser entrance (`otlp/handler.go:195-207`, `mountSignals`). Profiles
  must not be — a public site key cannot send them.
- Envelope and topics: `ingest/ingest.go:57` `Topics{Spans, Logs, Metrics}`, `topicFor :134-142`,
  `recordFor :241-272`; `config/config.go:61-63,176-178` `KAFKA_*_TOPIC`; wired in `cmd/receiver/main.go:107-110`.

### Contracts
- `packages/contracts/proto/nexovue/v1/envelope.proto` — `Signal` has 1–3; `buf.yaml` has no deps and says to
  add `buf.build/opentelemetry/opentelemetry` "from the BSR, never vendored" when a proto imports OTLP.
  `buf.gen.yaml` uses managed mode with a `go_package_prefix` override — which would also rewrite an OTLP
  dependency's Go package unless that module is excluded.
- **OTLP profiles for Go are published** — `go.opentelemetry.io/proto/otlp/profiles/v1development` and
  `…/collector/profiles/v1development`, both **v0.4.0** (2026-07-22, `opentelemetry-proto-go`), carrying the
  current format (`*_strindex`, `stack_index`) and requiring `go.opentelemetry.io/proto/otlp v1.11.0` — the
  version already in use. ⚠ The design's "generate them with `buf` into `gen/go/otlpprofiles`" is replaced by
  depending on these modules, in the receiver only.
- Python already has `opentelemetry-proto` (`uv.lock`), so a generated `profiles_pb2` importing OTLP's
  `common_pb2` resolves.

### Kafka
- `deploy/kafka/topics.sh` creates every topic (auto-create is off): `*-raw` and `*-dlq`, 7-day retention.

### Workers
- Pipelines: `apps/workers/internal/config/pipelines.go:30-44`; per-signal config `config/metricingest.go`;
  wiring `internal/worker/run.go:194-206`; usage per pipeline `run.go:297-302`.
- `pipeline/metricwriting/` — decode (`flatten.go:43`), mask then flatten (`metricwriting.go:311-326`),
  retention stamp (`:321`), columnar insert (`writer.go:30`), watermark (`ingeststate`, `:287`).
- Masking: `pipeline/mask/masker.go:85` `maskAttributes(rules, []*commonpb.KeyValue)` already masks a plain
  attribute list.
- ⚠ **The usage meter bills services**: `pipeline/usage/usage.go:56-80` — `Counts{Spans, Logs, MetricPoints,
  Bytes, BrowserBytes}` and *"The services are the bill — the plan prices per service monitored"*; `postgres.go`
  writes `usage_daily` and `usage_daily_services`. A profiles meter shaped like the others would make every
  profiled process — the eBPF profiler profiles whole hosts — a billed service. D1 requires the profiles meter
  to count samples only: no bytes, no services.

### ClickHouse
- Migrations `deploy/clickhouse/migrations/0000NN_*.{up,down}.sql`, latest `000031`; `just migrate-ch`.
  `metrics_gauge` (`000004`) is the pattern: `retention_days`, `LowCardinality` ids, `JSON` attributes,
  `PARTITION BY (toDate(received_at), retention_days)`, row TTL.

### API
- Module layout `metrics/{queries,service,router,schemas}.py`; SQL as module constants with the tenant
  predicate; `TenantScopedRepository` (`data/clickhouse/base.py:62`).
- `test_tenant_chokepoint_structure.py` reads its table list from the ClickHouse migrations — a new table is
  guarded without editing it.
- `purge/tables.py:22` `RAW_SIGNAL_TABLES`; `test_purge_inventory.py` fails on an unpurged tenant table.
- `usage_daily` model `data/postgres/models.py:830-840`; `usage/schemas.py:23`; shown in
  `apps/web/src/features/usage/UsageScreen.tsx:243,476`.

### Web
- Nav `app/routes/route-manifest.ts:18-30,58` (*Signals* section); routes `app/router.tsx`.
- `features/traces/SpanDetail.tsx` — sections at `:78-162`.
- `features/servicedetail/ServiceDetailScreen.tsx:885` — the Alerts strip; the Profiling strip goes beside it.
- Charts: `uplot` only (`shared/components/TrendChart.tsx`); no flame graph component.

## Change required

As approved in [spec.md](spec.md) and [design.md](design.md), with the three corrections above.

## Impact

| Area | What changes |
|---|---|
| Files | receiver `otlp/`, new `internal/profiles/`, `ingest/`, `config/`; contracts `profiles.proto`, `envelope.proto`, `buf.yaml`, `buf.gen.yaml`; workers `config/`, new `pipeline/profilewriting/`, `mask/`, `usage/`, `worker/run.go`; API new `profiles/`, `purge/`, `usage/`, Postgres migration; web new `features/profiles/`, `shared/components/FlameGraph.tsx`, `shared/profiles/`, traces, servicedetail, usage, docs pages, router, nav |
| Services | receiver, workers, API, web; Kafka topics |
| Schema / migrations | ClickHouse `000032_profile_samples`; Postgres `usage_daily.profile_samples`; contracts (additive) |
| UI | Profiles screen, span section, service strip, usage row, setup page |
| Tests | receiver door and conversion, contracts round trip, worker pipeline and meter, ClickHouse live, API live, tenant isolation, purge inventory, web screens, E2E |

## Reuse

`signalDoor` and the gate chain; `ingest.Publish`; `consume` loop, `ingeststate` watermark, `chpool`,
`maskAttributes`; `usage.Signal` machinery; `TenantScopedRepository`; `TrendChart`; `features/docs` pages;
the render harness pattern (`features/alerts/render-harness.test-util.ts`); `tests/helpers/clickhouse_cleanup.py`
for test tenants.

## Gaps

`ProfileSamples` and its converter; the profiles door, rate bucket, topics; the `profilewriting` pipeline and
samples meter; `profile_samples`; the `profiles` API; `FlameGraph`, the Profiles screen, span section and strip;
the setup page.

## Steps and tasks

### Step 1 — A real Collector's CPU profile reaches a flame graph

*Judgeable:* run the OpenTelemetry eBPF profiler against the OpenTelemetry Demo on the development stack; open
`/profiles`, pick `checkout`, and see a CPU flame graph of its code.

- [x] **T1.1** — `packages/contracts/proto/nexovue/v1/profiles.proto` (new), `envelope.proto` (`SIGNAL_PROFILES
  = 4`, the ADR-015 exception in its comment), `buf.yaml` (BSR `opentelemetry` dependency), `buf.gen.yaml`
  (managed mode excludes it), regenerated `gen/` · proved by `roundtrip_test.go` + a
  `fixtures/door1-profiles.bin` round trip and `just contracts-check` (additive).
- [x] **T1.2** — `apps/receiver/go.mod` · the two `…/profiles/v1development` v0.4.0 modules, receiver only ·
  proved by a structure test in `apps/workers` / `apps/api` style that no other module imports them
  (`apps/receiver/internal/profiles/imports_test.go` walks the repo's other `go.mod`s).
- [x] **T1.3** — `apps/receiver/internal/profiles/convert.go` (new) · OTLP profiles → `ProfileSamples`:
  dictionary resolution, summed values, leaf-first frames, unnamed frames as binary+address, links, attributes;
  refuses out-of-range indices and broken zero-value rules with a named reason · proved by `convert_test.go`
  (table tests, plus a captured eBPF profiler payload in `testdata/`, observed red first on each refusal).
- [x] **T1.4** — `otlp/signals.go`, `otlp/traffic.go` · `ProfilesPath`, `profilesDoor` (unit `Samples`,
  `countItems` = samples, `accept` = convert then publish), `units` · `otlp/handler.go` mounts it on the OTLP
  entrance only, `otlp/grpc.go` registers the ProfilesService · proved by `signals_test.go`/`handler_test.go`:
  accepted over HTTP protobuf, HTTP JSON and gRPC; 404 naming the supported path on another version (AC4);
  400 on malformed; not served on the browser entrance.
- [x] **T1.5** — `otlp/gates.go`, `handler.go`, `grpc.go` · the rate-limit bucket per door
  (`keyPrefix` for the three existing doors — unchanged — and `keyPrefix+":profiles"` for profiles) · proved by
  `gates_test.go`: exhausting the profiles bucket leaves spans admitted (AC15, receiver half).
- [x] **T1.6** — `ingest/ingest.go`, `config/config.go`, `cmd/receiver/main.go` · `Topics.Profiles`,
  `KAFKA_PROFILES_TOPIC` (default `profiles-raw`), `topicFor` · proved by `ingest_test.go` and
  `config_test.go`.
- [x] **T1.7** — `deploy/kafka/topics.sh` · `profiles-raw` (4) and `profiles-dlq` (1) · proved by
  `apps/api/tests/test_compose_dependencies.py` topic list (if it lists topics) or a new assertion there.
- [x] **T1.8** — `deploy/clickhouse/migrations/000032_profile_samples.{up,down}.sql` · the table as designed ·
  proved by `apps/workers` store probe `profilewriting/storeprobe_test.go` (`storeprobe` tag: insert, read back,
  TTL column present) and the chokepoint test picking the table up.
- [x] **T1.9** — `apps/workers/internal/config/{pipelines.go,profileingest.go}` · `PipelineProfileIngest`,
  topic, group `nexovue-workers-profiles`, DLQ · proved by `pipelines_test.go`, `config_test.go`.
- [x] **T1.10** — `pipeline/mask/profiles.go` · `MaskProfileSamples(projectID, *ProfileSamples)` over resource
  and sample attributes via `maskAttributes`; frames untouched (BR3) · proved by `profiles_test.go` (AC6).
- [x] **T1.11** — `pipeline/profilewriting/` (new: pipeline, rows, blocks, writer) · decode → mask → rows
  (`stack_hash` = cityHash64 of frames) → insert → watermark; retention `min(raw, 14)` (D2); undecodable to DLQ
  · proved by unit tests on rows and retention, and the store probe.
- [x] **T1.12** — `internal/worker/run.go` · wire the pipeline · proved by `config_test.go` (profile-ingest among the default pipelines, on the receiver's topics).
- [x] **T1.13** — `apps/api/src/nexovue_api/profiles/{queries,service,router,schemas}.py`, `main.py` ·
  `/profiles/services` and `/profiles/graph` (stack query, tree merge, 2,000-node prune with the merged note,
  remainder so shares sum to 100 %) · proved by `tests/test_profiles_tree.py` (pure: merge, prune, shares) and
  `tests/test_profiles_live.py` (tenant-bound reads, AC7 part).
- [x] **T1.14** — `apps/web/src/shared/components/FlameGraph.tsx`, `features/profiles/{ProfilesScreen.tsx,
  api.ts,profile-words.ts}`, `app/router.tsx`, `route-manifest.ts` (*Profiles* under Signals, D4) · service
  picker, CPU graph · proved by `FlameGraph.test.ts` (widths are shares; merged note; unsymbolized marked —
  AC11) and `ProfilesScreen.test.ts`.

### Step 2 — The Profiles screen, complete

*Judgeable:* on the same data, switch types, filter by host and version, search a function, zoom, and read the
functions table; every empty state shows its own message.

- [x] **T2.1** — `profiles/queries.py`, `service.py`, `router.py` · `/profiles/filters`, `/profiles/timeline`,
  graph filters (host, version, environment), types per service, the functions table (self, total) · proved by
  `test_profiles_live.py` (AC8) and `test_profiles_tree.py`.
- [x] **T2.2** — `FlameGraph.tsx` · search highlight with combined share, click-to-zoom and breadcrumb, tooltip
  (function, file:line, self, total, share), the one-line reading key (FR12) · proved by `FlameGraph.test.ts`.
- [x] **T2.3** — `ProfilesScreen.tsx` · type picker in people's words with units (`profile-words.ts`), filters,
  `TrendChart` timeline with range selection, functions table linked to the graph, preview label (FR13, AC16),
  every rule-3 state from the design (AC12, AC17) · proved by `ProfilesScreen.test.ts` and
  `profile-words.test.ts`.
- [x] **T2.4** — `unsaved-changes-coverage.test.ts`, `route-manifest.test.ts`, `landmarks.test.ts`,
  `status-interpretation.test.ts` · the new screen joins or passes each.

### Step 3 — Compare

*Judgeable:* compare the demo's two latest versions (or two ranges) and see which functions grew.

- [x] **T3.1** — `profiles/service.py`, `router.py` · `/profiles/compare`: two graphs normalised to shares,
  merged by frame path, `change` per node, table by change · proved by `test_profiles_tree.py` (a function
  whose share doubled ranks first — AC9) and `test_profiles_live.py`.
- [x] **T3.2** — `FlameGraph.tsx` (difference colouring and legend), `ProfilesScreen.tsx` (compare mode,
  baseline/comparison pickers, same-side validation) · proved by `FlameGraph.test.ts`,
  `ProfilesScreen.test.ts`.

### Step 4 — From a span, and from the service page

*Judgeable:* open a trace of a service whose samples carry span links, open a span's profile; on a service
page, see whether it is profiled.

- [x] **T4.1** — `profiles/queries.py`, `service.py` · span-scoped graph (trace and span predicates) with the
  three outcomes: linked samples / profiled but none in this span / not profiled (FR10) · proved by
  `test_profiles_live.py` (AC10).
- [x] **T4.2** — `features/traces/SpanDetail.tsx` (+ `shared/profiles/api.ts`) · the *Profile* section ·
  proved by `SpanDetail.test.ts`.
- [x] **T4.3** — `shared/profiles/ProfilingStrip.tsx`, `ServiceDetailScreen.tsx` · profiled / not, last
  received, types, *Open profile*, setup link (FR11) · proved by `ServiceDetailScreen.test.ts`.

### Step 5 — Metered, erased, isolated, documented, measured

*Judgeable:* the usage screen shows profile samples; the proofs below pass; the numbers are in `status.md`.

- [x] **T5.1** — `pipeline/usage/{usage.go,postgres.go}` · `usage.Profiles` counting samples into
  `Counts.ProfileSamples` only — **no bytes, no services** (D1, and the ⚠ above) · `run.go` usage map · proved
  by `usage` unit tests (a profiles message adds zero bytes and zero services) and the storeprobe.
- [x] **T5.2** — `apps/api/migrations/versions/0030_usage_profile_samples.py`, `models.py`, `usage/{service,
  schemas}.py`, `web features/usage/{api.ts,UsageScreen.tsx}` · the column and its row on the usage screen ·
  proved by `test_usage*` and `UsageView.test.ts` (AC13).
- [x] **T5.3** — `purge/{tables.py,queries.py}` · `profile_samples` purged · proved by
  `test_purge_inventory.py` and `test_purge_live.py` (AC14).
- [x] **T5.4** — `apps/api/tests/test_tenant_isolation_live.py` · a profile sent with one organization's key
  never appears under another (AC7).
- [x] **T5.5** — `features/docs/pages.ts` + page · the setup page (FR14): eBPF profiler Collector config
  (Linux ≥ 5.10, privileges), `pprof` receiver, language table, span links, preview note · proved by
  `pages.test.ts`.
- [x] **T5.6** — `tools/e2e/tests/profiles.spec.ts` · a profile posted with a new account's key → the flame
  graph on the Profiles screen (AC1 path, fixture-driven so it runs anywhere).
- [x] **T5.7** — measurement, recorded in `status.md`: storage per host per day with the eBPF profiler on the
  demo (NFR3), flame graph p95 for one hour and one day at 20 simulated hosts (NFR2, AC18), receiver conversion
  cost, and a profiles flood not delaying spans end to end (AC15).
- [x] **T5.8** — docs: `docs/architecture/{system,database,deployment}.md` (the fourth signal),
  `docs/product/feature-catalog.md` (F17 built), `status.md`, this plan's Outcome.
- [x] **T5.9** — `/review-diff`, `/readiness-check`.

## New risks

- **The eBPF profiler under Docker Desktop.** It needs a Linux ≥ 5.10 kernel and privileges; Docker Desktop's VM
  should provide both, but it is unproven here. If it cannot run, Step 1's judgeable result uses the Collector's
  `pprof` receiver fed by a Go service's `/debug/pprof` instead, and AC1 is proved on a Linux host later.
- **Whole-host profiling floods the service picker.** The eBPF profiler reports every process; services named
  after system processes will appear. They cost nothing (T5.1) but clutter; the picker lists by recent volume.
- **OTLP profiles v0.4.0 is the only version supported.** A customer's older Collector sends an older format and
  is refused with the supported path named (AC4) — the setup page names the minimum Collector version.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | Which Collector image runs the eBPF profiler in development (the official `otelcol-ebpf-profiler` distribution)? **Assumption:** that distribution, pinned; checked at T1.3's capture. | No | `design.md`, *Performance* |

## Gates

- [x] Specification approval — 2026-09-19
- [x] Design approval — 2026-09-19
- [x] **Plan approval — before the first edited file** — 2026-09-19
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Design QA
- [x] Production-readiness assessment
- [x] Documentation and status update

## Validation plan

- Per step: the step's tests, each regression observed red first; `just check` with counts read; the step's
  judgeable result shown on the development stack.
- End: `just check`; `just e2e` with the new spec; T5.7's measurements; `/review-diff`; `/readiness-check`.
- Every acceptance criterion AC1–AC18 is mapped to a task above; one without a proof at the end is a gap,
  reported rather than rounded up.

## Rollout and rollback

Pre-deployment. Rollback is `git revert` plus the two migration downgrades (`000032`, `0030`) and removing
the two topics. Behind the preview label (D5) from the first release.

## Outcome

*All five steps built 2026-09-19. Not committed: the founder reviews the diff first.*

### What was built

- **Intake** — `/v1development/profiles` over HTTP (protobuf, JSON) and gRPC; conversion to `ProfileSamples`
  (ADR-015); its own rate bucket; a per-key sample budget; a frame bound; a split into records under the
  broker's limit, published as one produce; 404 naming the supported path for any other version.
- **Pipeline** — `profiles-raw` → `profile-ingest` in its own worker service → `profile_samples`; the usage
  meter counting samples only.
- **Reads** — `/profiles/{services,latest,filters,timeline,graph,span,compare}`, every statement tenant-bound,
  partition-bounded, retention-bounded.
- **Console** — the Profiles screen (service and type pickers, host/version/environment filters, timeline with
  drag-to-select, flame graph with search and combined share, click-to-zoom with breadcrumb, file:line
  tooltips, functions table, compare by versions or ranges with ▲/▼ difference colouring, every state of
  principles rule 3), a span's *Profile* section, the service page's Profiling strip, the Usage screen's
  profile samples, and the setup page.

### Step 1's judgeable result

The OpenTelemetry eBPF profiler (`0.161.0`, pinned — Q1) ran under Docker Desktop against the OpenTelemetry Demo
and fed the development stack; the Profiles screen drew the demo's own services. **`checkout` was not the
subject:** it placed 51 orders in 15 minutes, a tenth of a second of CPU. The contrib Collector's `pprof`
receiver, scraping the worker's heap, proved AC2 live: four heap types in bytes and counts, beside CPU.

### Found by real data and measurement, fixed — each proved red first

1. Dictionary-encoded resource keys and values were not resolved: every service name was lost.
2. Timestamps-only samples were multiplied by the period: CPU samples inflated ~52 million-fold.
3. Unnamed processes shared a blank service → `unknown_service:<executable>` (spec FR5).
4. The pprof receiver breaks OTLP's "entry zero is empty" rule → indices read as they stand.
5. The flame graph's stack read held 950 MiB for one day and OOM-killed ClickHouse → two-step read (rank on
   hash and value, then one row of frames per stack), 196 MiB.
6. An hour's read scanned every granule: ClickHouse does not derive the sort key's `toStartOfHour` bounds from a
   DateTime64 range → the window restated on the key; 76 of 1,192 granules.
7. A profile flood OOM-killed the shared worker and stopped spans (AC15 failed) → profiles in their own worker
   service, 4 MiB fetches, and a per-key sample budget at the door.
8. A converted request could exceed the broker's record limit → split, one produce.
9. Review: a 223 KB request could expand to 3.2 GB, and a 496 KB one to 1 GB of copied attributes → a frame
   bound (413) and attributes resolved once; the budget spent before conversion.
10. Review: a span's profile link opened on the last hour → it pins the window around the span.
11. QA: the timeline drew nothing for isolated readings (points added); a whole-host profiler's
    `containerd-shim-<hex>` names pushed the pickers off a phone screen (width cap).

### Changed from the plan, and why

- T5.1–T5.3 were done in Step 1: the usage wiring, migration chain and purge-coverage test required them.
- `stack_hash` is FNV-1a in the worker, not cityHash64; ids are `FixedString`, `received_at` `DateTime64(3)`, as
  every other table. The design now says so.
- The OTLP profiles types come from the published Go modules, not a `buf` generation.
- The allowance gate needed no change.
- T1.12 is proved by `config_test.go`; T4.3 by `ProfilingStrip.test.ts` (the strip is shared, not in the screen).
- A box's line is shown only when every frame merged into it was on that line: a flame graph merges call sites,
  and one line for several would be a guess (FR12).
- A batch's `period` is carried but not stored (design, *Not stored*).
- **Outside F17, found here:** air's `rerun` ran two worker copies (recorded under
  `docs/changes/gate-found-defects/`); `pyproject.toml` now tells mypy not to type protoc's stubs, which the new
  `profiles_pb2.pyi` needed; `organizations/router.py`'s comment and the purge table counts belong to ADR-014.

### Decided after the build

- **NFR3** restated per busy core (spec D6, option A) after the measurement in `status.md`.
