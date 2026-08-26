# 22. Monorepo, polyglot

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Go on the data plane, Python on the control plane, TypeScript on the front end, plus a contract
package all three depend on. Split repositories would make every contract change a multi-repo
version dance.

## Decision

One repository: `apps/` (receiver, workers, api, scheduler, web) · `packages/` (contracts, ui,
client-ts) · `deploy/` · `tools/` · `docs/`. Go workspaces, a uv workspace and a pnpm workspace
coexist, with one `just` task runner exposing identical verbs per service.

Inside a service, folders are **domain-oriented** (`ingest/ traces/ logs/ metrics/ tenancy/`),
never layer-oriented.

## Consequences

A contract change and its consumers land in one commit, and every service is driven with the same
commands regardless of language. Cost: three toolchains in one checkout, and CI has to be selective
about what it rebuilds.
