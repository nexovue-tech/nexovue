# 0052 — The full production container inventory

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [0051](0051-combined-control-plane-container.md) — reverts it

## Context

With the whole feature set in view — every table-stakes screen, the insight
layer, synthetics, replay, profiling, RUM — the question was how many
containers production actually needs.

The answer is smaller than the feature list suggests, because **container count
follows failure domains and scaling axes, not features**. Session replay,
profiling, RUM, service map, SLOs, case management, alert delivery, the RAG
chatbot, status pages, dashboards and outlier analysis all land inside services
that already exist. Eleven of the largest features on the roadmap add no
process at all.

## Decision

**Fourteen services in production**, in both `docker-compose.app.yaml` and
`docker-compose.onprem.yaml`:

| | | Why it is separate |
|---|---|---|
| 1–5 | kafka · clickhouse · postgres · valkey · seaweedfs | Infrastructure |
| 6 | proxy (on-prem) / web (replica) | TLS and routing; the replica serves the build directly |
| 7 | receiver | Must survive worker restarts — the reason Kafka sits between them |
| 8 | worker | Pipelines by config (ADR 0050); splits along those names under load |
| 9 | api | A slow query must never share a process with ingest |
| 10 | scheduler-beat | Singleton: two instances fire every customer's checks twice |
| 11 | scheduler-light | HTTP and SSL checks, cloud polls, alert delivery |
| 12 | scheduler-browser | Playwright wants ~1 GB per browser and must not queue behind cheap checks |
| 13 | insight | Detection → correlation → LLM → finding. Phase 2 |
| 14 | ollama | Self-hosted inference; unnecessary if `LLM_BASE_URL` points at a hosted provider |

**Development is not the same shape and should not be.** `just dev` runs nine:
the five infrastructure services plus receiver, worker, api and web. Doors 2/4,
the insight service and Ollama sit behind profiles, because they are not needed
to work on the spine. The web container runs the **Vite dev server** — there is
no proxy in development, and the production `web`/`proxy` arrangement is a
deployment concern.

## Consequences

- The deployment target is complete on paper before the code exists, so adding
  Phase 2 is filling in services rather than redesigning compose. `insight` and
  `ollama` carry explicit TODOs because `apps/insight` does not exist yet.
- Nine of the fourteen can go: managed S3 removes seaweedfs, a hosted LLM
  removes ollama, and doors 2/4 are only needed once synthetics ship.
- Beat stays its own container everywhere. That is what keeps the API
  horizontally scalable, which reverting 0051 was about.
- On-prem additionally runs `web-assets-init`, a one-shot that copies the built
  UI into the volume the proxy serves and then exits. It is not a running
  service and is not counted in the fourteen.
