# contracts — the internal envelope

The one message every door writes and every worker reads. See `docs/architecture/ingestion-envelope.md`.

The envelope carries **our metadata only** — tenant, project, door, timing — wrapped around an untouched OTLP payload. We do not define span, log or metric schemas: OpenTelemetry did, and it is what customer SDKs emit.

Generated code is committed so no service needs the codegen toolchain to build.

Field numbers are never reused. `buf breaking` fails the build on an incompatible change.
