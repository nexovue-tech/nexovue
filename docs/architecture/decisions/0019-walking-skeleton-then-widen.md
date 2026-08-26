# 19. Phase 1 sequencing = walking skeleton, then widen

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Phase 1 opens four ingestion doors. Built in parallel, they integrate only at the very end — which
is where every integration problem would surface at once.

## Decision

Build one thin end-to-end path first: demo app → receiver → broker → worker → ClickHouse → API →
UI, running in compose. Only then widen into the individual doors. See
`../../delivery/phase-1-plan.md`.

## Consequences

A running spine de-risks every later door: each one plugs into something already proven rather than
into an idea. The cost is that the first milestone ships no complete feature — it produces a
skeleton that only makes sense to us.
