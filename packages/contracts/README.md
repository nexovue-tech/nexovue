# contracts — the internal envelope

The one message every door writes and every worker reads. See `docs/architecture/ingestion-envelope.md`.

The envelope carries **our metadata only** — tenant, project, door, timing — wrapped around an untouched OTLP payload. We do not define span, log or metric schemas: OpenTelemetry did, and it is what customer SDKs emit.

Generated code is committed so no service needs the codegen toolchain to build.

Field numbers are never reused. `buf breaking` fails the build on an incompatible change.

## Layout

```
proto/nexovue/v1/envelope.proto   the contract — the only hand-written proto
gen/go/nexovue/v1/                committed Go bindings
gen/python/nexovue/v1/            committed Python bindings + .pyi stubs
fixtures/                         one golden envelope per shape a door sends, committed
tools/build_fixtures.py           rebuilds fixtures deterministically
roundtrip_test.go                 Go round-trip proof
tests/                            Python round-trip proof
```

## Working on it

```sh
just contracts          # regenerate gen/go and gen/python
just contracts-check    # buf lint + buf breaking against main
```

Codegen uses **remote** BSR plugins, so no local `protoc` is needed — only the `buf`
CLI and network access.

### Why there is no OTLP buf dependency

`buf.yaml` declares no `deps`. The envelope carries OTLP as opaque `bytes` and
imports no OTLP proto, so declaring one made `buf dep update` warn on every run —
and a warning that is always there teaches everyone to ignore warnings.

**When a proto here does import OTLP, add `buf.build/opentelemetry/opentelemetry`
to `deps` — from the BSR, never vendored.** A vendored copy drifts from the version
customer SDKs emit.

This has nothing to do with the Go test's `go.opentelemetry.io/proto/otlp`
dependency: that is a Go module used to parse fixture payloads, not a buf dep.

Rebuilding the fixtures:

```sh
uv run --no-project --with protobuf --with opentelemetry-proto \
    python packages/contracts/tools/build_fixtures.py
```

Every value in the builder is fixed — no clocks, no randomness — so a rebuild that
changes a byte means the contract or a payload shape changed, and the diff says which.

## Enum value names deviate from the design doc

`ingestion-envelope.md` writes the enum values unprefixed (`OTLP`, `SPANS`). Proto3
enum values live in the **enclosing package** scope, not the enum's, so `buf lint`'s
`ENUM_VALUE_PREFIX` rejects them and a third enum added later could collide. The
values here are `DOOR_OTLP`, `SIGNAL_SPANS` and so on. Numbers and semantics are
unchanged; only the identifiers differ.
