# Design: Continuous profiling (F17)

## Status

**Approved** — by the founder, 2026-09-19. Written against the approved [spec](spec.md). Brings one ADR:
[ADR-015 — profiles are normalised at the intake](../../decisions/ADR-015-profiles-are-normalised-at-the-intake.md).

## Context and goal

Profiles become the fourth signal. They take the path the other three take — receiver door, Kafka
topic, worker pipeline, ClickHouse table, tenant-bound API reads, a console screen — with two
deliberate differences, both forced by the spec:

1. **The format is contained at the door** (BR4). OTLP profiles is *Development* and breaks
   between releases; the receiver converts it into nexovue's own `ProfileSamples` message, and nothing
   behind the receiver ever sees OTLP profiles. That is an exception to the envelope's "untouched
   OTLP" contract, recorded in ADR-015.
2. **Profiles cannot disturb the other signals** (NFR1). They get their own topic, consumer group and
   rate-limit bucket, and do not spend the allowance while in preview (D1).

## User experience

**The job:** "this service is slow or hot — which code?" It starts in one of three places and ends on
the same flame graph:

- **From the service page** (the usual start): the Profiling strip says the service is profiled and
  since when → *Open profile* → Profiles screen, that service, CPU, the service page's time range.
- **From a slow span** (FR10): the span's *Profile* section → the flame graph of that span's samples
  only; one click back to the trace.
- **From the navigation** (D4): *Profiles* → pick a service (profiled services only, most recent first).

**Compare** (FR9) starts from the Profiles screen: *Compare* → two versions (the latest two by default,
since "after the deploy" is the common question) or two ranges → the difference graph. It is one screen,
not a second tool.

**Setup** (FR14): the empty state of every profiling surface links the setup page; the service page's
strip does too when a service is not profiled.

## UI structure

**Profiles screen** — `/profiles?service=&type=&from=&to=&host=&version=&env=`

- **Header**: service picker; profile type picker (only types this service sent in the range, named for
  people — "CPU time", "Memory in use", "Allocations", "Lock contention" — with the unit); the preview
  label (FR13); *Compare*.
- **Filters**: host, version, environment — values that occur in the range.
- **Timeline**: the type's value over time (`TrendChart`, uPlot, reused); dragging selects the range.
- **Flame graph** (new `shared/components/FlameGraph.tsx`): icicle, root at top; width = share of the
  total, stated in a one-line key above it (FR12); colour by module/language family, never by value
  (colour carries no meaning a reader could mistake for "bad"); search box highlights matches and gives
  their combined share; click a frame to zoom into it, breadcrumb to zoom out; tooltip with function,
  file:line, self, total, share. Unsymbolized frames in a hatched style with "unsymbolized" in the
  tooltip and a one-line reason (FR6, AC11). When nodes were merged for size, a "N small frames merged"
  note (NFR4).
- **Functions table**: function, file, self, total, share of total; sortable; clicking a row
  highlights it in the graph.
- **Compare mode** (same screen, `&compare=`): baseline and comparison pickers; the graph coloured by
  change in share (more / less / only in one), a legend; the table sorted by change.

**Span section** — in `features/traces/SpanDetail.tsx`, a *Profile* section: *Open this span's profile*
when linked samples exist; otherwise the reason (FR10). The flame graph opens on the Profiles screen with
`&trace=&span=` — one flame graph component, one place it lives.

**Service page strip** — beside the Alerts strip on `ServiceDetailScreen`: profiled / not profiled, last
received, types, *Open profile* (FR11).

**Setup page** — in the product docs (`features/docs/pages.ts`), group *Profiling*: the eBPF profiler
Collector config, the `pprof` receiver config, the language table, span-link support, and the preview
note.

**States (principles rule 3), owned by the Profiles screen:**

| State | What it shows |
|---|---|
| Loading | Skeleton of header, timeline and graph |
| Empty — project has no profiles | "No profiles yet" + what profiling is in one line + setup link |
| Empty — service not profiled | "This service isn't profiled" + setup link |
| Zero results — filter matched nothing | Names the filter, offers to clear it |
| Zero results — range empty | "No profiles in this range" + the nearest range that has some (BR6, AC12) |
| Partial | Unsymbolized frames rendered and marked; merged-frames note |
| Validation | Compare with the same side twice: inline, beside the picker |

Unsaved changes, dangerous and success do not apply: the screen writes nothing. 401/403/404/429/5xx
and offline are central and not reimplemented.

**Deliberately not shown:** absolute CPU seconds as the headline (a share is what a flame graph answers);
a "top slow functions" alert; any AI summary.

## Proposed design

### Contracts — `packages/contracts`

- `envelope.proto`: `SIGNAL_PROFILES = 4`; the comment gains the ADR-015 exception.
- New `nexovue/v1/profiles.proto`:

  ```proto
  message ProfileSamples {            // one converted OTLP profiles export request
    repeated ProfileBatch batches = 1;
  }
  message ProfileBatch {              // one resource + one profile type
    repeated opentelemetry.proto.common.v1.KeyValue resource_attributes = 1;
    string sample_type = 2;           // e.g. "cpu", "alloc_space"
    string sample_unit = 3;           // e.g. "nanoseconds", "bytes"
    int64 period = 4; string period_type = 5; string period_unit = 6;
    repeated Sample samples = 7;
  }
  message Sample {
    repeated Frame frames = 1;        // leaf first
    int64 value = 2;                  // summed over the sample's timestamps
    fixed64 time_unix_nano = 3;       // the sample's first timestamp, else the profile's time
    bytes trace_id = 4; bytes span_id = 5;
    repeated opentelemetry.proto.common.v1.KeyValue attributes = 6;  // profile + sample attributes
  }
  message Frame {
    string function = 1; string file = 2; uint32 line = 3;
    string binary = 4; uint64 address = 5;   // set when the function is unnamed
  }
  ```

  `buf.yaml` gains the `buf.build/opentelemetry/opentelemetry` dependency for `common.v1` (already
  used by every service through `go.opentelemetry.io/proto/otlp`) — as the file already says it would.
- **OTLP profiles types** for the receiver only: generated by `buf` from `opentelemetry-proto` v1.10.0
  (`profiles/v1development`, `collector/profiles/v1development`) into
  `packages/contracts/gen/go/otlpprofiles/…`. Pinned; nothing else imports it (a structure test says so).

### Receiver — `apps/receiver`

- `internal/otlp/signals.go`: `ProfilesPath = "/v1development/profiles"`; a `profilesDoor` with unit
  `Samples` (added to `units`); gRPC service
  `opentelemetry.proto.collector.profiles.v1development.ProfilesService/Export`.
- New `internal/profiles/convert.go`: `Convert(request) (*nexovuev1.ProfileSamples, error)` — resolves
  the dictionary (strings, functions, locations, mappings, stacks, links, attributes), sums a sample's
  values, and refuses malformed input (index out of range, overflowing values) with a named reason.
  This is the only file that knows OTLP profiles. As built: indices are read as they stand — the
  contrib Collector's pprof receiver puts real entries at index zero, against OTLP's rule — and
  dictionary-encoded resource keys and values (`key_strindex`, `string_value_strindex`) are resolved.
- **Expansion is bounded** (added at implementation, from the review): every converted sample carries
  its whole stack, so a small request can write out to gigabytes. A request past one million frames
  is refused with 413 (`profiles.MaxFrames`); attribute entries are resolved once and shared; the
  result is split into records under the broker's limit (`profiles.Split`) and published as one
  produce.
- Gate chain unchanged in order. Two differences, both per door:
  - **Rate limit**: the profiles door uses its own bucket — `Limiter.Allow(ctx, keyPrefix+":profiles")`
    — so a profiling flood cannot spend the budget spans need (NFR1, AC15).
  - **Allowance**: the gate runs (a suspended or deleted organization is refused, AC3) but profiles
    spend nothing while in preview (D1); `allowance` gains the door and its "not metered against the
    plan" rule.
  - **Sample budget** (added at implementation): because profiles spend no allowance, the door also
    bounds samples per key per second (`RECEIVER_PROFILE_SAMPLES_PER_SECOND`, 20,000), spent on the
    request's own count before conversion. A measured flood of 400,000 a second from one key
    otherwise stalled profile ingest for every tenant.
- An OTLP request on any other profiles path or version gets 404 with a body naming the supported
  path (FR2, AC4); a decode or conversion failure gets 400 naming what was malformed. Both are counted
  as refused.
- `ingest.Topics` gains `Profiles` (`KAFKA_PROFILES_TOPIC`, default `profiles-raw`); keyed by
  organization, as logs and metrics are.

### Kafka — `deploy/kafka/topics.sh`

`profiles-raw` (4 partitions) and `profiles-dlq` (1), same retention and limits as the others.

### Worker — `apps/workers`

- New pipeline `profile-ingest` (`config.PipelineProfileIngest`, consumer group
  `nexovue-workers-profiles`, `config/profileingest.go`), in `AllPipelines`, wired in `worker/run.go`.
- New `internal/pipeline/profilewriting/`, shaped like `metricwriting`: decode the envelope
  (`SIGNAL_PROFILES`) → `mask` (`MaskAttributes` over resource and sample attributes; frames untouched,
  BR3) → rows → columnar insert into `profile_samples` → advance the `ingeststate` watermark only after
  ClickHouse acknowledges; undecodable messages to `profiles-dlq` before the offset moves.
- **Retention stamp**: `min(raw retention, 14)` (D2), from `Cache.Retention`.
- **Usage**: `usage.Profiles` signal counting samples, `Counts.ProfileSamples`, and a
  `usage_daily.profile_samples` column (Postgres migration) — metered and shown, not billed (D1).

- **Deployment** (added at implementation after the flood measurement in `status.md`; **reversed
  2026-09-20 by the founder** — `docs/changes/one-worker-again/`): profiles ran in a worker service
  of their own, `worker-profiles`, for one day. They run in the single `worker` again, with fetches
  still bounded at 4 MiB and that worker carrying both memory budgets. Pipelines in one process
  share one cap, and a profile flood OOM-killed the shared worker and stopped spans — which is the
  risk that returns with them.
- **Not stored**: a batch's `period` and `period_type` travel in `ProfileSamples` but have no
  column — no screen reads them. Converting CPU samples into time needs them; a column is added when
  a screen does.

### ClickHouse — migration `000032_profile_samples`

```sql
CREATE TABLE profile_samples (
  received_at        DateTime64(3),                         -- as every telemetry table
  retention_days     UInt16 DEFAULT 14,
  tenant_id          LowCardinality(String),
  project_id         LowCardinality(String),
  service_name       LowCardinality(String),
  host_name          LowCardinality(String),
  service_version    LowCardinality(String),
  environment        LowCardinality(String),
  sample_type        LowCardinality(String),
  sample_unit        LowCardinality(String),
  timestamp          DateTime64(9),
  value              Int64,
  stack_hash         UInt64,                               -- FNV-1a of the frames, computed in the worker
  frame_functions    Array(LowCardinality(String)) CODEC(ZSTD(3)),
  frame_files        Array(LowCardinality(String)) CODEC(ZSTD(3)),
  frame_lines        Array(UInt32) CODEC(ZSTD(3)),
  frame_binaries     Array(LowCardinality(String)) CODEC(ZSTD(3)),  -- '' when named
  frame_addresses    Array(UInt64) CODEC(ZSTD(3)),                  -- 0 when named
  trace_id           FixedString(32), span_id FixedString(16),  -- hex, as `spans` stores them
  resource_attrs     JSON, attrs JSON,
  INDEX span_idx span_id TYPE bloom_filter GRANULARITY 4
) ENGINE = MergeTree
PARTITION BY (toDate(received_at), retention_days)
ORDER BY (tenant_id, project_id, service_name, sample_type, toStartOfHour(timestamp), stack_hash, timestamp)
TTL toDateTime(received_at) + toIntervalDay(retention_days)
SETTINGS non_replicated_deduplication_window = 1000;
```

Stacks are stored **inline**, as Parca's ClickHouse store does, and repetition is left to
`LowCardinality` and ZSTD — no join on the read path. Sorting by `stack_hash` inside the hour puts
identical stacks next to each other, which is what makes that compression work. NFR3 is measured on it
before release; if it fails, the fallback is a deduplicated `profile_stacks` table (see *Alternatives*).

### API — `apps/api/src/nexovue_api/profiles/`

`router.py` → `service.py` → `queries.py` → `schemas.py`, member-readable, `TELEMETRY_TIER`:

| Route | Returns |
|---|---|
| `GET /profiles/services?project_id` | profiled services: last received, types |
| `GET /profiles/filters?service&type&from&to` | hosts, versions, environments in range |
| `GET /profiles/timeline?…` | value per bucket |
| `GET /profiles/graph?…[&host&version&env][&trace_id&span_id]` | flame graph tree + functions table |
| `GET /profiles/compare?…&base_from&base_to&base_version&from&to&version` | difference tree + table |

- **Flame graph query** (as built): the heaviest 20,000 stacks are ranked on hash and value alone,
  then one row of frames is read for each (`LIMIT 1 BY stack_hash`) — grouping frame arrays across a
  busy day held 950 MiB for one read and OOM-killed the development store. Every read also bounds
  `received_at` (the partition key), restates the window on `toStartOfHour(timestamp)` (the sort key,
  which a DateTime64 range does not prune), and refuses rows past their retention. `service.py` merges the
  stacks into a tree, then prunes to at most 2,000 nodes, folding the smallest siblings into one
  "N frames merged" node (NFR4); the functions table (self, total) comes from the same stacks.
  Stacks beyond the 20,000th are counted into an "other" remainder so shares still sum to 100 %.
- **Compare**: two graph queries, each normalised to shares, merged by frame path; each node carries
  `base_share`, `share`, `change`.
- **Span**: the same graph query with `trace_id` and `span_id` predicates (bloom filter). The response
  distinguishes "service not profiled", "profiled, no samples in this span" (FR10).
- `purge`: `profile_samples` joins `RAW_SIGNAL_TABLES` with its tenant-bound DELETE — the inventory
  test would fail otherwise. The chokepoint test covers the new table automatically (its list is read
  from the migrations).

### Web — `apps/web`

- `features/profiles/` — `ProfilesScreen.tsx`, `api.ts`, `profile-words.ts` (type names, units,
  reading aid), tests; route `/profiles` in `router.tsx`; nav item in the *Signals* section.
- `shared/components/FlameGraph.tsx` — shared because the Profiles screen and (by link) the span view
  both lead to it, and a feature may not import another. Rendered as absolutely-positioned rows of divs
  from the pruned tree (≤ 2,000 nodes), so it is accessible and testable without a canvas.
- `shared/profiles/ProfilingStrip.tsx` for the service page; the span section in `SpanDetail.tsx`.
- Setup page in `features/docs/pages.ts`.

## Data flow and interfaces

```
Collector (ebpf profiler receiver / pprof receiver)
  └─ OTLP profiles v1development ─▶ receiver :4317/:4318 /v1development/profiles
        auth → allowance (not metered, D1) → ratelimit (own bucket) → decode → convert (ADR-015)
        → Kafka profiles-raw  [envelope SIGNAL_PROFILES, payload = ProfileSamples]  → 200 after ack
  profiles-raw ─▶ worker profile-ingest: decode → mask → rows → profile_samples → watermark
             └─▶ worker usage-counting-profiles: samples per org per day → usage_daily.profile_samples
  profile_samples ─▶ API /profiles/* (tenant-bound) ─▶ console Profiles screen / span section / strip
```

## Data and migration impact

- ClickHouse `000032_profile_samples` (up/down). Postgres: `usage_daily.profile_samples BIGINT NOT NULL
  DEFAULT 0`. Kafka: two topics. Contracts: `envelope.proto` addition (`buf breaking` passes: additive),
  new `profiles.proto`.
- Pre-deployment: no backfill; nothing exists to migrate.

## Performance and scale

- **Receiver**: conversion is linear in the frames it writes out, which `MaxFrames` bounds. Measured on
  a captured eBPF profiler payload: 0.92 ms for 421 samples (`status.md`).
- **Storage (NFR3, per busy core since D6)**: measured with the eBPF profiler profiling the OpenTelemetry Demo on the
  development stack for a day-equivalent; target ≤ 100 MB compressed per host per day.
- **Reads (NFR2)**: the hour query reads one service's rows in one sort-key range; measured with 20
  simulated hosts (AC18). The 20,000-stack cap bounds the transfer; pruning bounds the browser.
- **ClickHouse memory**: inserts only; no mutations except the tenant purge (ADR-014). The table's part
  count is watched in the measurement run.

## Failure handling

| Failure | Behaviour |
|---|---|
| Broker down | Receiver answers retryable (503/UNAVAILABLE); the Collector retries (ADR-002) |
| Unsupported format / path | 404 naming the supported path; refused count |
| Malformed profile | 400 naming the reason; nothing published |
| Worker cannot decode a message | To `profiles-dlq`, then the offset moves |
| ClickHouse insert fails | Retried; watermark not advanced; after the retry budget the pipeline ends and the worker exits and restarts (as fixed in `gate-found-defects`) |
| Profiling flood | Own topic, pipeline and rate bucket: spans, logs and metrics unaffected (AC15) |

## Security, tenancy, and privacy

- Tenant id and project id come from the authenticated key, never from the payload, as for every door.
- Every read is a module-level constant with the tenant predicate; `TenantScopedRepository` only.
- Masking before storage on all attributes (BR3); frames are code and are stored as sent.
- Erasure: `profile_samples` is in the purge inventory (ADR-014 batch when built).
- The eBPF profiler runs privileged on the customer's host — the setup page says so plainly, since it
  is the customer's security decision.

## Observability and operations

- Receiver: the `samples` unit in the per-window summary; refusals by reason, including the new
  unsupported-version reason.
- Worker: the profiles pipeline's heartbeat like the others (records, lag, rows written).
- Usage: samples per organization per day, visible to the operator (F31) and the customer.

## Alternatives and tradeoffs

- **Carry OTLP profiles untouched** — rejected, ADR-015.
- **Deduplicated stack tables** (Parca's original meta store; Pyroscope's symbol dedup): smaller on
  disk, but every read joins, and the tenant chokepoint then guards two tables per query. Kept as the
  fallback if NFR3 fails; inline arrays first because they are simpler and proven on ClickHouse.
- **Build the flame graph in ClickHouse** (recursive aggregation): possible, but the pruning rule and
  the compare merge are clearer and testable in Python over ≤ 20,000 stacks.
- **A canvas flame graph library** (e.g. speedscope-style): faster at 100,000 nodes, but inaccessible and
  a new dependency; the 2,000-node prune makes DOM rendering sufficient.
- **Share the rate-limit bucket** — rejected: a profiling flood would refuse spans.

## Specification impact

None. The design meets the spec as approved; the one spec constraint it resolves — the envelope
contract against BR4 — is resolved by ADR-015, not by changing the spec.

## ADRs

- [ADR-015](../../decisions/ADR-015-profiles-are-normalised-at-the-intake.md) — accepted with this design.
