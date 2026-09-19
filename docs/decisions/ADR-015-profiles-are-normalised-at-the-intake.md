# ADR-015: Profiles are normalised at the intake, the one exception to the untouched envelope

## Status

Accepted 2026-09-19, with the F17 design.

## Context

The envelope contract (`packages/contracts/proto/nexovue/v1/envelope.proto`) wraps "an untouched OTLP
payload": *"We define no span/log/metric schema of our own: OpenTelemetry did, and it is what customer
SDKs emit."* Every worker decodes OTLP. For traces, logs and metrics that is right — their OTLP formats
are stable, and a second schema would only drift from them.

Profiles are different. OTLP profiles are *Development* (`opentelemetry.proto.profiles.v1development`,
sent to `/v1development/profiles`), and broke compatibility four times in sixteen months: proto v1.7.0
(May 2025, lookup tables into a dictionary), v1.8.0 (Sep 2025, `sample_type` single, stack
representation simplified), v1.9.0 (Oct 2025, fields renamed), v1.10.0 (Mar 2026, reference-based
attributes, `*_ref` → `*_strindex`). F17's spec requires that such a change be absorbed by changing the
intake alone — without migrating stored data, changing screens, or losing what is stored (BR4).

Carried untouched, a profiles payload would be decoded by the receiver (to count and validate it), the
worker (to store it), and the usage meter (to count it again). Each OTLP profiles release would touch all
three, and a message already on Kafka in the old format would meet a worker that only reads the new one.

## Decision

1. **The receiver converts OTLP profiles into a nexovue message** — `nexovue.v1.ProfileSamples` in
   `packages/contracts` — and publishes that in the envelope, as `SIGNAL_PROFILES`. It resolves the
   dictionary indirection into what storage needs: per sample, its stack as frames (function, file,
   line; or binary and address when unnamed), its sample type and unit, value, time, trace and span ids,
   and its attributes as OTLP `KeyValue`s (so the existing masker applies unchanged).
2. **Only the receiver imports the OTLP profiles definitions.** They are generated from the
   `opentelemetry-proto` release the supported Collector speaks (v1.10.0; v1.11.0 made no profiles
   changes), pinned, through `buf` like the rest of the contracts.
3. **Supporting a new OTLP profiles version** is a new converter in the receiver, beside the old one for
   as long as Collectors still send it. `ProfileSamples` changes only by addition, under the envelope's
   `buf breaking` rule, like every other contract.
4. **The exception is this signal's and ends with its instability.** When OpenTelemetry declares profiles
   stable, this decision is revisited: carrying OTLP untouched may become right again, at the cost of one
   migration of the worker's decoder.

## Rejected

- **Carry OTLP profiles untouched, as the other signals.** Each format change would touch three
  components and strand messages in flight across a deploy (above).
- **Use the Collector's `pdata/pprofile` library in the worker.** It is itself marked unstable, brings the
  Collector's dependency tree into the worker, and still moves with each proto change.
- **Store OTLP profile payloads as blobs and decode at read time.** Every read would pay the decode and
  the format would reach the API — the widest possible spread of the problem.

## Consequences

- nexovue owns one schema it otherwise would not: `ProfileSamples`. Its fields are the facts a profile
  holds in every format version so far (stack, value, type, time, link, attributes), which is why it can
  stay additive.
- The receiver does the dictionary resolution, which costs CPU on the ingest path; it is bounded by the
  request size limit and measured (design, *Performance*).
- A malformed or unsupported profiles request is refused at the door, synchronously, with an error the
  Collector logs — never accepted and then dropped in a worker.
- `envelope.proto`'s comment gains the exception, pointing here.
