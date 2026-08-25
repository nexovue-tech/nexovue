# Documentation

| Document | Type | What it holds |
|---|---|---|
| [product/vision-and-scope.md](product/vision-and-scope.md) | Vision & Scope | Why this exists, who it serves, which signals and categories are in scope, how data gets in |
| [product/requirements.md](product/requirements.md) | PRD | End-user features F1–F30 — user stories, acceptance criteria, UI notes. F1 is the specification template |
| [architecture/system-architecture.md](architecture/system-architecture.md) | Architecture | Components, data plane vs control plane, data layer and schemas, decisions log |
| [architecture/repository-structure.md](architecture/repository-structure.md) | Architecture reference | Monorepo layout and what lives inside each service |
| [architecture/decisions/](architecture/decisions/) | ADRs | One decision per file, with context and consequences *(P1-06 — to be backfilled from the decisions log)* |
| [delivery/phase-1-plan.md](delivery/phase-1-plan.md) | Delivery plan | Phase 1 milestones, tasks `P1-nn`, exit criteria, performance budgets |
| [research/market-analysis-2026.md](research/market-analysis-2026.md) | Research snapshot | 2026 competitive and feature landscape, table stakes vs gaps |

## Conventions

- **Living docs** (`product/`, `architecture/`, `delivery/`) are edited in place and carry a decisions log.
- **Research** is a point-in-time snapshot with the year in the filename. Never edit it to stay current — write a new dated file and supersede.
- Filenames are kebab-case and describe the document *type*, not the product; the repo already supplies the product name.
- Feature specifications will split out to `product/features/F01-findings-feed.md` as the PRD grows, so a session loads one feature rather than thirty.
