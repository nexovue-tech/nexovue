# Change: otel-demo-profiling-door — the demo opens a fifth door into the Profiles screen

## Classification

Infrastructure (local development tooling). Touches one production file
(`apps/receiver/internal/otlp/grpcserver.go`) as a necessary dependency, documented below.

## Current behavior and problem

`just fresh`/`just dev` always ran the full OpenTelemetry Demo traffic source, and the Profiles
screen was always empty. The backend pipeline — receiver's gRPC `ProfilesService`, the
`profiles-raw` Kafka topic, the worker's `profile-ingest` pipeline, the `profile_samples`
ClickHouse table — already works; nothing produces continuous profiling data for it to carry.

Found while answering why the founder saw no profiling data after `just fresh`:
`docs/features/profiling/status.md`'s own *Development stack, as left* section says the eBPF
profiler container used during F17's testing was deliberately removed afterward, and
`tools/otel-demo/otel-demo.sh`'s own door list (1–4a) never included it. Both are honest about the
gap; neither closes it.

**Found while closing it:** the upstream eBPF profiler's own gRPC exporter compresses with gzip by
default, and the receiver's gRPC server never registered a gzip decompressor — confirmed live:
every export was refused `Unimplemented: Decompressor is not installed for grpc-encoding "gzip"`.
This is not demo-specific. Any real customer's Collector or SDK that compresses its gRPC OTLP
export — several do by default, the opposite of the HTTP SDK exporters this demo otherwise uses —
would be refused the same way in production today. `grpcserver.go`'s own comment on
`MaxRecvMsgSize` already assumed decompression happens ("the decoded cap is therefore the one to
use"); the codec registration to make that true had never been added.

## Intended behavior

`just fresh`/`just dev` starts a profiler against the demo's own services, matching every other
signal: data lands in `profile_samples` continuously, under the correct tenant, with real service
names, with no manual step. The receiver accepts a gzip-compressed gRPC OTLP export, from any
client, not only this one.

## Affected specifications and architecture

- `docs/features/profiling/status.md` — *Development stack, as left* described the profiler as
  removed, which stays true of the one from testing; this change adds a new, non-removed instance.
  No F17 specification or acceptance criterion changes — the backend behavior proved there is
  unchanged, confirmed by this change exercising it rather than altering it.
- `apps/receiver/internal/otlp/{grpc.go,grpcserver.go}` — two comments said "three collector
  services," stale since profiles' `ProfilesService` was added; corrected to "four" in the same
  change that depends on reading them accurately.

## Risks and rollback

- **Privileged container.** `otel-ebpf-profiler` runs `--privileged --pid=host` with
  `/proc`, `/sys/kernel/debug`, `/sys/kernel/tracing` and the Docker socket mounted — it can see
  every process in the Docker VM, nexovue's own containers included. This is upstream's own
  `compose.profiling.yaml` definition, unmodified beyond the exporter's auth header; the same grant
  F17's own testing already used on this machine (`status.md`, 2026-09-19).
  Rollback: `tools/otel-demo/otel-demo.sh down` already tears it down with everything else; to stop
  opening this door specifically, drop it from `compose()`'s `-f` list and from `cmd_up`.
- **Resource cost, measured, not assumed:** 254 MiB resident, ~1% CPU once traffic was flowing, on
  top of the existing 22-container total. Not gated behind `NEXOVUE_OTEL_DEMO_SCOPE=core` — the
  same unconditional treatment `host-metrics` already gets, for the same reason: filling a screen
  that is otherwise always empty.
  on a Linux 5.10+ host (Docker Desktop's own VM qualifies; proved live on this machine) — no
  preflight check exists for a host where it does not, by design: the existing `require_*` guards
  in this script check things that fail every single run on a true gap (no receiver, no built SDK,
  no probe workers); whether this host's kernel exposes debugfs is not knowable generically, and a
  wrong guess would either block a host where it works or wave through one where it does not.
- **Version drift.** The upstream demo pins the eBPF profiler at 0.157.0; F17's own setup
  page and `status.md` validated 0.161.0. Both are OTLP profiles' still-"Development" format, which
  the setup page already warns can change between Collector releases. Proved working at 0.157.0
  here; nothing suggests re-checking it is urgent, but it is the first thing to look at if a future
  `UPSTREAM_REF` bump makes this door silent again.
- **The gRPC gzip fix widens what the receiver accepts; it narrows nothing.** No existing caller
  sent gzip over gRPC before (confirmed: `OTEL_EXPORTER_OTLP_PROTOCOL` is `http/protobuf` for every
  demo SDK service but one, and that one's SDK does not compress by default), so there is no
  existing behavior this changes other than refusing less than it used to.
- **No automated regression test for the gRPC fix** — see *Plan and validation*. Recorded as a
  residual risk, not silently accepted.

## Plan and validation

1. `tools/otel-demo/otelcol-ebpf-profiling.yml` — a patched copy of upstream's Collector config,
   adding a `headers: x-nexovue-key: ${env:NEXOVUE_INGEST_KEY}` block to the existing
   `otlp_grpc/collector` exporter. Same pattern already used for `image-provider` and
   `frontend-proxy`'s non-SDK exporters; unlike those two, the Collector expands `${env:NAME}`
   natively, so there is no envsubst step to extend.
2. `tools/otel-demo/compose.nexovue.yaml` — a service override for `otel-ebpf-profiler`: adds
   `NEXOVUE_INGEST_KEY` to its environment, mounts the patched file at a new path, and replaces
   `command` to point at it. `firepit` (upstream's own profiling UI) is not started anywhere — its
   default port is 8080, which the storefront already owns, and nexovue has its own Profiles screen.
3. `tools/otel-demo/otel-demo.sh` — adds `compose.profiling.yaml` to the `compose()` function's
   `-f` list, a `WAVE_PROFILING=(otel-ebpf-profiler)` array started after the application services
   (there is nothing to profile before they run), and door 5 in the header comment.
4. `apps/receiver/internal/otlp/grpcserver.go` — `_ "google.golang.org/grpc/encoding/gzip"`,
   registering the decoder grpc-go needs to accept a compressed export. Comment corrected from
   "three" to "four" collector services, matching `grpc.go`'s own two occurrences (also corrected).

**Validation, live, on this machine:**

- Before the fix: `otel-ebpf-profiler`'s own log, verbatim —
  `error internal/queue_sender.go:50 Exporting failed. Dropping data. ... "error": "not retryable
  error: Permanent error: rpc error: code = Unimplemented desc = grpc: Decompressor is not installed
  for grpc-encoding \"gzip\"", "dropped_items": 53` — repeating every export attempt.
- The fix landed through the dev stack's own hot reload (`air`); no rebuild step needed. Immediately
  after: the receiver's own log —
  `export accepted ... path=/opentelemetry.proto.collector.profiles.v1development.ProfilesService/Export
  status=200 items=71 ... user_agent="OpenTelemetry Collector for eBPF Profiling/0.157.0
  (linux/arm64) grpc-go/1.82.1"`.
- `profile_samples` row count: 2 (gateseed's synthetic rows only) before `otel-ebpf-profiler` ran.
  974 within a minute of it starting, 7,186 four minutes in, 12,063 by the time this record was
  written — climbing continuously, not a one-time batch.
- Service names present and correctly attributed: `payment`, `frontend`, `frontend-proxy`,
  `product-catalog`, `kafka`, `load-generator` (the demo's own services) alongside
  `unknown_service:{clickhouse,dockerd,containerd,worker,…}` (everything else in the Docker VM) —
  matching exactly what `status.md`'s own prior run documented, and what `transform/set_service_name`
  in the patched config is for.
- `otel-ebpf-profiler` resident memory: 254.5 MiB; CPU: ~1.08%, once traffic was flowing.
- A transient `grpc: addrConn.createTransport failed ... error reading server preface: EOF` at the
  exact moment the hot reload restarted the receiver process — expected, and recovered on its own;
  samples kept accumulating afterward with no gap in the count.
- **No automated regression test for the gzip fix.** A `bufconn`-based unit test
  (`otlp_test`, this package's own existing pattern) was written, then found to pass whether or not
  `grpcserver.go`'s import was present — because the test file's own `encoding/gzip` import, needed
  so the *client* half of the test could compress, registers the decoder process-wide for Go's
  gRPC codec registry, which has no per-server scoping. A test and the server it tests share one
  process in this harness, so there is no way to prove this import's absence would fail without a
  second OS process — a pattern this test suite does not otherwise use. Removed rather than kept as
  a test that can never go red for the right reason (`grpc_test.go` is unchanged in this diff). The
  live evidence above is the proof that exists; a genuine process-boundary test is a candidate for
  its own change if this class of bug recurs.
- `just check`, full run, after both changes: **exit 0.** Python `pytest -n 6 --dist loadfile`:
  4142 passed, 4 skipped, 0 failed (unchanged from before this change — nothing here touches
  Python). Go: every package `ok`. `apps/web`: 1335 passed. `packages/browser-sdk`: 120 passed.
  `buf lint`/`buf breaking`: clean.

## Status

See `status.md`.
