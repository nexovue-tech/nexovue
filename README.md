# nexovue

An insight-first observability platform: it tells you **what broke and why**, instead of showing you 50 dashboards.

## Documentation

Full index in [docs/README.md](docs/README.md).

| Document | What it holds |
|---|---|
| [Vision & scope](docs/product/vision-and-scope.md) | Users, signals, product categories, the four data doors |
| [PRD](docs/product/requirements.md) | End-user features F1–F30; F1 is the specification template |
| [Architecture](docs/architecture/system-architecture.md) | Technical design, data layer, decisions log |
| [Phase 1 plan](docs/delivery/phase-1-plan.md) | Core-system implementation plan and task tracker |
| [Market analysis](docs/research/market-analysis-2026.md) | 2026 competitive and feature landscape |

Working rules for this repo are in [CLAUDE.md](CLAUDE.md).

## Status

**Phase 1 — core system:** all four data doors normalized into ClickHouse, with a dashboard UI over every signal. Planned, not started.
**Phase 2 — the insight layer:** findings feed, AI insights, owner status page.
