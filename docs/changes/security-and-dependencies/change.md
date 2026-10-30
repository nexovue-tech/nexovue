# Change: security findings and dependency upgrades

## Classification

Security and maintenance. No customer-facing behaviour changes, except that the receiver again answers a
malformed gRPC export `INVALID_ARGUMENT` on gRPC-Go 1.84.

## Current behavior and problem

Once the repository went public (2026-10-29), GitHub's Security tab held two secret-scanning alerts and 13
CodeQL alerts; Dependabot held three upgrade pull requests that could not be merged as they were (#10 Go,
#11 JavaScript, #12 Python). CI on `main` also failed once on a test that races the stack's own deliveries
worker.

## Intended behavior

The founder chose one pull request for all of it (2026-10-30). The Security tab empty, every finding fixed
or closed with its reason; every Dependabot upgrade applied and working; the flaky test made deterministic.

## Affected specifications and architecture

None changed. The receiver's gRPC door gains a codec (`apps/receiver/internal/otlp/grpccodec.go`).

## Risks and rollback

Each upgrade is its own concern and can be reverted alone. The risky ones were the major versions: each
broke something, found by a test and fixed at the cause (`status.md`).

## Plan and validation

Item by item, `just check` once at the end, CI on the pull request.

## Status

See `status.md`.
