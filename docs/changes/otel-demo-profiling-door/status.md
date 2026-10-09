# Status: otel-demo-profiling-door

## Lifecycle status

**Implemented and verified live against the running dev stack.** Uncommitted.

## Current task

None.

## Completed

- `otel-demo.sh` opens a fifth door: the OpenTelemetry Demo's eBPF profiler, started after the
  application services, pointed at the receiver with the same ingest key every other door uses.
- The receiver accepts a gzip-compressed gRPC OTLP export. It did not before — found while wiring
  this door, not invented for it, and not specific to this door: any real customer's Collector or
  SDK that compresses gRPC OTLP would have hit the identical refusal in production.
- Two stale "three collector services" comments corrected to "four" in the same files the fix
  touched.

## Next action

Founder:
1. Open `/profiles` on the development console — data has been landing continuously since this was
   verified; no further setup step is needed.
2. Read the diff; then the commit. This change touches one production file
   (`apps/receiver/internal/otlp/grpcserver.go`) as a load-bearing dependency of the demo wiring —
   see `change.md`'s *Risks* for why it is in scope rather than deferred.

## Open decisions

None outstanding. One was resolved during the work rather than asked beforehand: whether to fix the
gRPC gzip gap in the receiver or only work around it in the demo's own Collector config. Fixing the
receiver was chosen — it is strictly additive (accepts more, refuses nothing it refused correctly
before), small (one blank import), and leaves a real production gap closed rather than hidden
behind a demo-only workaround. Recorded here rather than asked first because there was no material
downside to weigh against proceeding.

## Blocked

Nothing.

## Risks

- No automated regression test exists for the gRPC gzip fix — see `change.md`'s *Plan and
  validation* for why a unit test in this package's existing style cannot prove it (the test and the
  server under test share one process, and gRPC's codec registry has no per-instance scope). The
  live evidence recorded there is red-then-green, observed, not reasoned — but it is manual, and
  will not run again on its own. Candidate for a process-boundary test if this class of regression
  recurs elsewhere.
- Privileged, host-wide container; no preflight check for a host without `/sys/kernel/debug` or
  `/sys/kernel/tracing`. See `change.md`'s *Risks* for why no check was added rather than guessed at.
- The demo pins the eBPF profiler at 0.157.0; F17's own validation used 0.161.0. Proved working
  here at 0.157.0; worth rechecking if a future `UPSTREAM_REF` bump changes the pinned version and
  the door goes silent.
- Not scope-gated behind `NEXOVUE_OTEL_DEMO_SCOPE=core` — measured at 254 MiB / ~1% CPU, which was
  judged proportionate against the ~800 MiB `core` already saves elsewhere, but that judgment is
  recorded here for the founder to overrule rather than assumed uncontroversial.

## Validation

See `change.md`'s *Plan and validation* — the red/green receiver log lines, the sample-count
progression (2 → 974 → 7,186 → 12,063), the correctly-attributed service names, the measured
container footprint, and the full `just check` run (exit 0, 4142 Python passed / 4 skipped, every
Go package `ok`, 1335 web tests passed, 120 browser-SDK tests passed, `buf lint`/`buf breaking`
clean) are recorded there rather than duplicated here.

## Documentation impact

- `docs/changes/README.md` — this record added to the index.

## Last updated

2026-10-09
