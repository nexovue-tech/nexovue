# 21. Go on the data plane from day one

- **Status:** Accepted
- **Supersedes in part:** [0001](0001-ingestion-edge-custom-receiver.md) — the "Python acceptable for v1" clause
- **Date:** 2026-08-25

## Context

[0001](0001-ingestion-edge-custom-receiver.md) allowed a Python receiver for v1 with a Go rewrite
later; [0008](0008-go-on-the-data-plane-python-on-the-control-plane.md) settled Go as the
production data-plane language. That combination means writing the hot path twice, and scheduling
the port for exactly the moment volume makes it urgent.

## Decision

Go on the data plane from the first line. No interim Python receiver.

## Consequences

The hot path is written once, and the rewrite never has to be scheduled during a capacity crisis.
Cost: Go is required from day one rather than at some later point, and the first walking skeleton
carries that up-front cost.
