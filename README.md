# nexovue

Nexovue is an insight-first observability platform. It receives OpenTelemetry telemetry and browser RUM, processes it through Kafka and ClickHouse, and serves a React console through a FastAPI control plane.

## Quick start

```bash
just setup
just dev
```

The local console runs at `http://localhost:15173`; the API is on `http://localhost:18000`; OTLP/HTTP ingestion is on `http://localhost:14318`.

Use `just check` for the offline quality gate. See `just` for all supported commands. `just down-hard` is destructive: it deletes local volumes, including Kafka.

## Repository map

```text
apps/       deployable services: receiver, workers, API, scheduler, web
packages/   shared contracts, browser SDK, Python platform helpers, UI tokens
deploy/     Compose, databases, Kafka, object storage, proxy, and backup configuration
tools/      E2E, chaos, load, rebalance, and telemetry-demo tools
docs/       product, architecture, engineering standards, quality, operations,
            decisions, feature and change workspaces, templates, research
```

## Documentation and source of truth

- [Agent operating manual](AGENTS.md) — how AI agents and contributors work in this repository.
- [Product vision](docs/product/vision.md) — product intent, not implementation status.
- [Feature catalog](docs/product/feature-catalog.md) — feature queue; selected work lives in `docs/features/`.
- [Architecture](docs/architecture/system.md) — verified current-system design.
- [Engineering standards](docs/engineering/README.md) — coding and testing rules.
- [Design system and principles](docs/design/design-system.md) — the visual system `packages/ui` implements, and the rules for a screen nobody has designed yet.
- [Quality gates](docs/quality/quality-gates.md) — review, validation, and release expectations.
- [Operations](docs/operations/README.md) — verified runbooks and production gaps.
- [Decision records](docs/decisions/README.md) — why material technical decisions were made.
- [Feature workspaces](docs/features/README.md) — current-state baselines for implemented capabilities.
- [Change records](docs/changes/README.md) — non-feature work: fixes, refactors, migrations, infrastructure.
- [Document templates](docs/templates/README.md) — the spec, design, plan, status, change, bug, and migration forms.
- [Detailed source-derived reference](docs/architecture/current-system-reference.md) — the original code-traced system reference, retained for depth and traceability.

For current behavior, code, migrations, tests, and deployment configuration override documents when they disagree. For intended behavior, approved feature specifications override implementation assumptions.
