# 24. The internal normalized envelope is a frozen contract

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Every door normalizes into one internal format before the broker. That format is written by
multiple doors and read by every worker — so it is the one piece of the system that, changed later,
touches everything.

## Decision

The envelope lives in `packages/contracts` as a frozen contract with golden fixtures per door, and
it is designed **before the first door is built**. Every door writes it; every worker reads it.

## Consequences

Doors and workers can be built in parallel against a fixed target, and the fixtures make "does this
door emit a valid envelope" a test rather than an integration discovery. The cost is that the
design work happens up front, with less information than we would have later — which is the trade
being made deliberately. Its form is decided in
[0030](0030-envelope-as-protobuf-wrapper-around-otlp.md) and the freeze itself lands in
[0045](0045-envelope-contract-frozen.md).
