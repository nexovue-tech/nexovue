# nexovue

nexovue is an insight-first observability platform. Send it OpenTelemetry traces, logs, metrics and
profiles, and browser telemetry from your users' pages, and it tells you what changed and why — not only
what the graphs show.

- **Signals:** traces, logs, metrics, continuous profiles, errors, a service map, LLM calls.
- **Surfaces:** real user monitoring, synthetic checks, infrastructure and database health.
- **Alerts** to email, Slack, Microsoft Teams, Discord, PagerDuty and webhooks.
- **Data protection:** masking and tail sampling before anything is stored; each organization's data
  isolated from every other's.
- **A read API and an MCP server**, so your own tools and AI assistants can query what nexovue holds.

Built on OpenTelemetry, Kafka, ClickHouse and Postgres. Run it yourself, or use nexovue's hosted service.

## Try it

On any machine with Docker (8 GB of memory for Docker), from the
[latest release](https://github.com/nexovue-tech/nexovue/releases)'s `nexovue-X.Y.Z.tar.gz`:

```bash
tar -xzf nexovue-X.Y.Z.tar.gz && cd nexovue-X.Y.Z
./nexovue quickstart
```

The console opens at `http://localhost:8080` with an account made for you, and applications send
telemetry to `http://localhost:4318`. The quickstart is for trying nexovue, not for production.

## Run it yourself

One Linux server with Docker and two DNS names:

```bash
curl -fsSLO https://github.com/nexovue-tech/nexovue/releases/latest/download/install.sh
sudo bash install.sh
```

It generates every secret, prepares the databases, obtains certificates and schedules daily backups.
Upgrades, rollbacks, backups off the server and installs with no internet access are each one command.
The guide, written for the team that runs the server:
**[docs/self-hosting](docs/self-hosting/install.md)**.

## Licence

nexovue is [AGPL-3.0](LICENSE). The browser SDK in `packages/browser-sdk` is Apache-2.0, so it can be
embedded in any page.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md): open an issue first for anything larger than a small fix, sign
the [CLA](CLA.md) on your first pull request (the browser SDK takes a DCO sign-off instead), and run
`just check`. Report security problems privately, as [SECURITY.md](SECURITY.md) describes.

## Developing nexovue

```bash
just setup
just dev
```

The development console runs at `http://localhost:15173`; the API is on `http://localhost:18000`;
OTLP/HTTP ingestion is on `http://localhost:14318`. `just check` is the quality gate CI runs; `just`
lists every command. `just down-hard` is destructive: it deletes local volumes, including Kafka.

```text
apps/       deployable services: receiver, workers, API, scheduler, web
packages/   shared contracts, browser SDK, Python platform helpers, UI tokens
deploy/     the install: Compose files, the nexovue command, backups, monitoring, edge and stores
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
- [Operations](docs/operations/README.md) — verified runbooks: deployment, releases, backups, monitoring.
- [Decision records](docs/decisions/README.md) — why material technical decisions were made.
- [Feature workspaces](docs/features/README.md) — current-state baselines for implemented capabilities.
- [Change records](docs/changes/README.md) — non-feature work: fixes, refactors, migrations, infrastructure.
- [Document templates](docs/templates/README.md) — the spec, design, plan, status, change, bug, and migration forms.
- [Detailed source-derived reference](docs/architecture/current-system-reference.md) — the original code-traced system reference, retained for depth and traceability.

For current behavior, code, migrations, tests, and deployment configuration override documents when they disagree. For intended behavior, approved feature specifications override implementation assumptions.
