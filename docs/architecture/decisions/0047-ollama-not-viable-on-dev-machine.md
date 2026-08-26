# 47. Flagged for Phase 2 — Ollama will not run usefully on the dev machine

- **Status:** Proposed (flagged, not decided)
- **Date:** 2026-08-26

## Context

[0015](0015-containers-with-on-premise-docker-compose.md) puts every service in compose, Ollama
included, and [0014](0014-provider-flexible-llm-interface.md) makes local models one of the two
providers. But Docker on macOS has no Metal GPU passthrough, so a containerised Ollama is CPU-only
— and a useful model wants 8–16 GB that this machine ([0046](0046-dev-machine-m4-pro-24gb.md))
does not have spare.

## Decision

None yet. Options when Phase 2 arrives:

- run Ollama natively against Metal and point the API at the host, or
- use hosted models during development.

**Recorded, not decided — verify before building against it.**

## Consequences

The compose stack should not assume a working local Ollama during Phase 1, and any Phase 2 work
that depends on local inference has to resolve this first. Because the provider interface from
[0014](0014-provider-flexible-llm-interface.md) already exists, either option is a configuration
change rather than a code change.
