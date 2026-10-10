# Plan: ingest cannot be knocked over

> **Status:** Complete — awaiting the founder's review before commit
>
> **One document: the order of the work and its tasks.** The founder approves this plan before any
> file is edited.

## Preconditions

- The group structure and the fix-everything-now decision (founder, 2026-10-10,
  `docs/operations/production-readiness.md` *Fix order*).
- The reproduction and root cause in [`bug.md`](bug.md).

## Current system

Read from the code on 2026-10-10.

**Receiver, gRPC door**
- `apps/receiver/internal/otlp/grpcserver.go:52-75` — `grpc.NewServer` with `MaxRecvMsgSize`
  (16 MiB decoded) and keepalive; no tap handle, no stats handler, no `MaxConcurrentStreams`.
- `otlp/grpc.go:176-202` — our own method handler already calls `decode` itself;
  `runExport` (`:92-129`) authenticates after it.
- grpc-go v1.82.1 reads and decompresses before calling any handler (`server.go:1399` vs `:1443`); a
  method grpc-go does not know is answered in `handleStream` without `processUnaryRPC`, so it emits no
  `stats.End` (`server.go:1800-1882`).
- `auth/auth.go:130-170` — `Authenticate`: format check, then a cache that already stores misses
  (`:146-147`), then Postgres. There is no cache-only, I/O-free form of it.
- `config/config.go:86-87` — `RECEIVER_MAX_BODY_BYTES` (4 MiB) and `RECEIVER_MAX_DECODED_BODY_BYTES`
  (16 MiB). No in-flight limit on either door; the HTTP door reads a body only after authenticating
  (`otlp/handler.go:252-344`), so an authenticated sender can still hold any number of 16 MiB bodies
  at once over HTTP too.
- Tests: `otlp/grpc_test.go` (`newGRPCDoor`, twelve `TestGRPC…` cases, including
  `TestGRPCOversizedExportIsRefused` and `TestGRPCUnusableCredentialIsRefusedWithoutReachingTheBroker`).

**Worker**
- `consume/retry.go:51-96` — `Retry`; returns at once on `IsPermanent`, otherwise exits after two
  minutes.
- `consume/park.go:21` — `Loop.Park(ctx, record, cause)` parks a whole Kafka record; `dlq/dlq.go:72`
  `Queue.Park` is what it calls.
- `logwriting/pipeline.go:232-351` — the pattern to extend: `inBlock` tracks the records whose rows are
  in the block; `writeChunk` parks them on a permanent refusal and still advances the watermark;
  `promoteVitals`/`afterPromoting` (`:455-486`) count a refused derived insert and step over it.
- `metricwriting/metricwriting.go:212-259,366-372` — writes inside `Absorb` through `writeChunk` →
  `insertBlocks`, five tables, one `Retry` each; `metricwriting/writer.go:30` returns plain errors.
- `profilewriting/profilewriting.go:180-232` — inside `Absorb`, one table; plain errors.
- `traceassembly/writer.go:42` — plain errors. Called from `Absorb` (`pipeline.go:387`) and `Settle`
  (`:477`) through `insertBounded`, and from `Drain` (`:567`) **without** `Retry`. Rows come from the
  sampling buffer, which keeps each span's partition but not its offset (`sample/buffer.go:150-155`).
- `traceassembly/summarywriter.go`, `serviceedges/writer.go`, `derive/writer.go`,
  `derive/endpointwriter.go` — each under its own `Retry`, from `settleDerived` (`pipeline.go:548-554`)
  and `Absorb`/`Settle`/`Drain`; plain errors.
- `otlpvalue/otlpvalue.go:78,125` — `AttributesMap`/`insertAtPath` split every key on `.` with no depth
  limit; collisions are already recorded under `DroppedAttributesKey` (`:57`).
- `sample/buffer.go:279-296` — `enforceLimits` closes the oldest trace past `MaxTraces` or `MaxSpans`
  (`config/pipeline.go:83-84`); nothing bounds bytes.
- Tests that exist: `consume/permanent_test.go`, `logwriting/promote_test.go`. **Gaps found:** no test
  reaches `parkRefusedChunk` or `writeChunk`'s refusal branch (`logWriter` is a concrete type, so it has
  no seam), and none covers `storefailure.RefusedTheData`.

## Change required

[`bug.md`](bug.md) *Root cause*. No specification or design document: this restores behavior
ADR-002 and the gate order already promise. The one design choice — how the gRPC door refuses before
reading — is in Step 1 below, and the founder approves it with this plan.

## Impact

| Area | What changes |
|---|---|
| Files | Receiver: `otlp/grpcserver.go`, `otlp/grpc.go`, `otlp/handler.go`, new `otlp/admission.go`, `auth/auth.go`, `config/config.go`. Worker: the seven writers above, their pipelines, `otlpvalue/otlpvalue.go`, `sample/buffer.go`, `config/pipeline.go`, `dlq/dlq.go` |
| Services | `apps/receiver`, `apps/workers`. Nothing in Python, nothing in the web console |
| Schema / migrations | None. The dead-letter topics already exist |
| UI | None |
| Tests | New and extended Go tests per task; one live reproduction, red then green |

## Reuse

`consume.AsPermanent`/`IsPermanent`, `storefailure.RefusedTheData`, `Loop.Park`, the log pipeline's
`inBlock`/`parkRefusedChunk`/`afterPromoting` shapes, `otlpvalue`'s `DroppedAttributesKey`, the
receiver's `refuse(...)`/`grpcError()` with `RetryInfo`, the auth cache's stored misses, `grpc_test.go`'s
`newGRPCDoor` harness.

## Gaps

An I/O-free key screen; an admission gate before the gRPC body is read; one in-flight budget shared by
both doors; a way to park a refused **row** rather than a record (spans only); a nesting-depth cap; a
byte bound on the sampling buffer.

## Steps and tasks

### Step 1 — the gRPC door refuses before it reads, and holds a bounded number of exports

*Judgeable:* re-run the bug's flood script against the rebuilt receiver. Keyless exports are refused
`Unauthenticated` and receiver memory stays near its baseline instead of climbing ~46 MiB per export;
past the in-flight budget, exports get `RESOURCE_EXHAUSTED` with a retry delay rather than memory.

**The design choice, for approval.** A tap handle (`grpc.InTapHandle`) runs on the HEADERS frame,
before any body bytes are read. It must not block, so it uses only what is already in memory:
- a missing or malformed key, or a key the auth cache already knows is unknown or revoked →
  `Unauthenticated` at once;
- a method that is not one of our four exports → `Unimplemented` at once (this also guarantees every
  admitted stream reaches `processUnaryRPC`, whose `stats.End` releases the slot);
- a well-formed key the cache has never seen → admitted into a small **unverified** pool;
- a key the cache knows is valid → admitted into the main pool;
- either pool full → `RESOURCE_EXHAUSTED` with `RetryInfo`, which OTLP clients retry.

Slots are released by a `stats.Handler` on `*stats.End`. The main pool is the same budget the HTTP door
takes from after authenticating, so one receiver holds a bounded number of bodies across both doors.
Rejected alternative: authenticating inside our handler before `decode` alone — it saves the unmarshal
but not the 16 MiB read. The cost: `InTapHandle` is marked experimental in grpc-go; it is isolated to
`admission.go` and pinned by tests, so an API change fails the build rather than the door.

- [x] **T1** — `auth/auth.go` · `Screen(presented) Verdict`: format check plus a cache read, no I/O;
  answers missing / malformed / known-bad / known-good / unseen · proved by new cases in
  `auth/auth_test.go`, including that `Screen` never calls the `KeyReader`
- [x] **T2** — new `otlp/admission.go` · the tap handle, the two pools, and the `stats.Handler` that
  releases them · proved by new `otlp/admission_test.go`: each verdict's code, pool exhaustion answering
  `RESOURCE_EXHAUSTED` with `RetryInfo`, every admitted slot released on End, unknown methods refused
- [x] **T3** — `otlp/grpcserver.go` · install the tap handle and stats handler · proved by new
  `TestGRPCKeylessExportIsRefusedBeforeItIsRead` in `otlp/grpc_test.go` — a gzip export decoding to
  15 MiB with no key is `Unauthenticated` and the server records no `*stats.InPayload` for it — **seen
  failing before T2/T3**
- [x] **T4** — `otlp/handler.go` · the HTTP door takes a main-pool slot after authenticating and before
  reading the body; full → 429 with `Retry-After` · proved by a new case in `otlp/handler_test.go`
- [x] **T5** — `config/config.go` · `RECEIVER_MAX_INFLIGHT_EXPORTS` (main pool) and
  `RECEIVER_MAX_UNVERIFIED_EXPORTS`, validated positive · proved by `config/config_test.go`. Defaults
  are an assumption until the hosting load test: 8 and 2, from the measured ≈ 46 MiB per full-size
  export against a 768 MiB container (see *Questions*)
- [x] **T6** — `otlp/grpc.go` · `runExport` keeps the full authentication (the tap screen is a
  pre-check, not the gate) · proved by the existing `TestGRPC…` suite passing unchanged

### Step 2 — one refused row no longer stops ingest

*Judgeable:* send one span, one metric data point and one profile sample carrying the 1,100-deep
attribute key through the development stack. Before this step the worker crash-loops; after it the
worker keeps running, metrics and profiles park their records on their dead-letter topics, the span's
refused row is parked and the rest of its batch is stored, and every other signal keeps flowing.

- [x] **T7** — **live red run**, scratchpad script with the development ingest key: send the poisoned
  span to the current build and record the crash-loop (`docker logs` showing the two-minute retry and
  exit, restart count rising). Recovery is T8-T14's build, which parks the record; the record says so
  before it is sent. Logs are sent the same way as the control: already parked today
- [x] **T8** — `storefailure/refusal_test.go` (new) · cover `RefusedTheData` for each listed code and for
  an outage error · closes the gap found above
- [x] **T9** — `metricwriting/writer.go` and `metricwriting/metricwriting.go` · classify refusals in
  `insert`; keep `inBlock` in `Absorb`; on a permanent refusal park the chunk's records and advance the
  watermark, as `logwriting.writeChunk` does. **Stated in the code:** the five table inserts are not
  atomic, so tables before the refused one keep their rows · proved by new
  `metricwriting/refusal_test.go` through a small inserter interface (the seam `logWriter` lacks)
- [x] **T10** — `profilewriting/profilewriting.go` · the same, one table · proved by new
  `profilewriting/refusal_test.go`
- [x] **T11** — `logwriting/writer.go`/`pipeline.go` · the same inserter seam, so the existing refusal
  branch is finally tested · proved by new `logwriting/refusal_test.go` (`parkRefusedChunk` reached,
  watermark advanced)
- [x] **T12** — `traceassembly/writer.go` and `traceassembly/pipeline.go` · classify refusals; on one,
  split the rows in half and insert each half again, down to single rows; write everything ClickHouse
  accepts and park each refused row on the spans dead-letter topic as its masked row JSON, attributed to
  tenant and project. `Drain` (`:567`) goes through the same path instead of calling `insert` bare.
  **Stated in the code:** the trace's summary already counted the refused span · proved by new
  `traceassembly/refusal_test.go` — one poisoned row among 1,000, 999 stored, 1 parked
- [x] **T13** — `dlq/dlq.go` · park a row that has no source record: same topic and attribution headers,
  plus a header naming the payload as a row, not an envelope · proved by `dlq/dlq_test.go`
- [x] **T14** — `traceassembly/summarywriter.go`, `serviceedges/writer.go`, `derive/writer.go`,
  `derive/endpointwriter.go`, and their callers in `traceassembly/pipeline.go` · classify; on a refusal
  count the rows in the heartbeat, log, and step over so the settlement still completes · proved by a
  refusal case in each package's tests
- [x] **T15** — **live green run**: restart the worker on the T8-T14 build; it parks the T7 record and
  ingest resumes; re-send the span, metric, profile and log — each lands as the step says. Output read
  and recorded in `status.md`

### Step 3 — the attribute that triggered it is stored instead of refused

*Judgeable:* re-send the poisoned span after this step: it is stored, and the console's span detail
shows the deep part under `nexovue.dropped_attributes` rather than the row being parked.

- [x] **T16** — `otlpvalue/otlpvalue.go` · cap nesting depth in `insertAtPath`; the part beyond the cap
  is recorded under `DroppedAttributesKey`, as collisions already are. Cap: 64 levels — far above any
  semantic-convention key, far below ClickHouse's ~1,000 · proved by new cases in
  `otlpvalue/otlpvalue_test.go`, and by the live re-send

### Step 4 — the sampling buffer is bounded in bytes

*Judgeable:* a worker test drives spans with megabyte attributes into the buffer and its held bytes
never pass the bound; oldest traces close first, exactly as the count limits already do.

- [x] **T17** — `sample/buffer.go` · `Options.MaxBytes`; `Facts` carries each row's approximate size;
  `enforceLimits` closes the oldest trace while bytes exceed it · proved by new cases in
  `sample/buffer_test.go`
- [x] **T18** — `config/pipeline.go` and `traceassembly` · `WORKERS_SAMPLING_MAX_BYTES`, default 256 MiB
  (a quarter of the worker's 1 GiB), validated; the span row's size passed in `Facts` · proved by
  `config` tests and the T17 cases

**Not in this group:** consumer-lag and dead-letter-depth metrics move to group 7, which owns how
nexovue reports its own health. The parked counts T9-T14 add to the heartbeat are what it will export.

## New risks

- **`grpc.InTapHandle` is experimental.** A grpc-go upgrade could change or remove it; T2/T3 fail the
  build if it does.
- **Bisecting a refused span block costs extra inserts** — up to about 2·log₂(n) for one bad row in n.
  Only on a refusal, which is rare; worth it against a total outage.
- **A parked span row is a new dead-letter payload shape.** No replay tool exists for any shape yet
  (recommended item, `production-readiness.md`); the header makes the shape explicit for whoever writes
  one.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | The in-flight defaults (8 and 2) and the 256 MiB sampling bound are sized from the laptop | No — configurable; assumed until the load test | `production-readiness.md` *Capacity*, after the group 8 load test |
| 2 | Should the 64-level depth cap be lower? | No — 64 assumed | This record's `status.md` |

## Gates

- [x] Specification approval — not applicable (no behavior change to a specification)
- [x] Design approval — the one design choice is in Step 1, approved with this plan
- [x] **Plan approval — before the first edited file** (founder, 2026-10-10)
- [x] Implementation
- [x] Tests
- [x] Review
- [ ] Design QA — not applicable
- [ ] Production-readiness assessment — blockers 12 and 13 re-checked in `production-readiness.md`
- [x] Documentation and status update

## Validation plan

`just check` once, after Step 4, reading counts. The live runs: T7 (red), T15 (green), T16's re-send,
and Step 1's flood script re-run against the rebuilt receiver. Every new regression test is seen failing
before its fix, with the failure recorded in `status.md`.

## Rollout and rollback

Pre-deployment. The real risk is T7: it deliberately stops the development worker until T8-T14 is
built. If those tasks stall, the recovery is to move the worker's consumer group past the one poisoned
offset by hand — written into `status.md` before T7 runs.

## Outcome

Built as planned in four steps; each was judged live on the development stack. Evidence is in
`status.md`. What changed from the plan, and why:

- **One decision, tested once, instead of a seam per pipeline (T9-T11).** The plan proposed an
  inserter interface in each of logs, metrics and profiles. Reading the three showed the refusal branch
  is the same decision three times, so it became shared code: `storefailure.Judged` (every writer
  returns through it — spans, summaries, metrics, profiles, logs, vitals, service edges, counters,
  endpoint rollup) and `consume.ParkRefused` (logs, metrics, profiles; the log pipeline's private
  `parkRefusedChunk` was deleted). Both are unit-tested, red then green; each pipeline's use of them is
  one branch, proven live for spans, metrics and logs. **Profiles were not sent live** — no OTLP/JSON
  profile fixture exists — and rely on the shared code and the build.
- **Span salvage is a pure function** (`traceassembly.salvageRows`), so it is tested without Kafka or
  ClickHouse; `Loop.ParkRow` and a `nexovue-parked-payload: row-json` header (T13) carry the row.
- **The tap's slot is released when the stream's context ends, not on `stats.End`.** grpc-go can abort a
  stream after the tap without ever running the handler (`http2_server.go` early abort), so an End-based
  release would leak. A deadline of the HTTP door's read plus write timeouts also bounds a slow sender.
- **The key screen trusts an expired hit but not an expired miss** (`cache.Peek`). Found while designing
  T1: a stale miss would refuse a key created since, permanently, because a refused request never
  reaches `Authenticate` to refresh the entry.
- **`MaxConcurrentStreams` was not set:** the two pools bound the same thing across connections.

Found during the work, not in the plan:

- **ClickHouse closes the connection after a refusal**, so the next insert on it fails `packet:
  uvarint: read: EOF`; the ordinary retry absorbs it (observed live, `attempts=2`).
- **Chunk parking costs innocent records.** The live metric refusal parked 9 messages — the poisoned one
  and 8 from the development host-metrics agent. Recoverable from the dead-letter topic, but spans now
  narrow a refusal to its row and logs, metrics and profiles do not. Recorded as a founder decision in
  `status.md`.
- **`test_synthetics_live.py` errors for up to two days after any outage of its target.** It failed in
  this change's `just check` because the OpenTelemetry demo stack — the target of both development
  checks — no longer exists on the machine; both checks have been down since 2026-10-09 18:34. Unrelated
  to this change; recorded in `status.md` for a separate record.
