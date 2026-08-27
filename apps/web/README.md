# web — the nexovue interface

Vite + React 19 + TanStack Router + TanStack Query. Styling and tokens come from `@nexovue/ui`; nothing in here defines a colour.

```
pnpm --filter @nexovue/web dev        # http://localhost:15173, /api proxied to :18000
pnpm --filter @nexovue/web build      # tsc -b && vite build
pnpm exec biome check .               # from the repo root
```

`NEXOVUE_API_ORIGIN` overrides the dev proxy target.

## Layout, and the direction imports flow

```
src/
├─ shared/     used by anything — api client, logger, theme, toasts, shared components
├─ features/   one folder per domain; a feature may use shared, never another feature
└─ app/        routing, providers, layout — the only layer that composes features
```

**shared → features → app, one direction only.** A feature that needs another feature's
code is telling you the code belongs in `shared/`. `biome.json` is meant to enforce this;
see "Still owed" below.

Cross-boundary imports use the `@/` alias (`@/shared/api-client`), which is what makes the
direction visible in the import line and what the lint rule can see.

## The parts worth knowing before adding a screen

**`shared/api-client.ts` is the only way to reach the API.** It injects a W3C
`traceparent`, applies `AbortSignal.timeout`, and turns every failure into one `ApiError`
carrying the status, the parsed RFC 9457 problem body, the trace id and the server's
correlation id. axios is banned repo-wide.

**`app/query-client.ts` is the only place a status code is interpreted.** 401 clears the
session and redirects; 403 is thrown to the route boundary; 429 toasts with `Retry-After`;
5xx toasts and retries. A feature that writes `if (error.status === 401)` has duplicated a
decision that lives here.

**Failures have three presentations, and they are not interchangeable.** A toast for
something transient. Inline text for a form or a field. A full screen only when a boundary
caught something and there is no surrounding component left to attach a message to.

**Loading is a skeleton, never a spinner.** `shared/components/Skeleton.tsx`.

**Every unbuilt route renders `ComingSoon` and names the task that delivers it.** Routes and
sidebar are both generated from `app/routes/route-manifest.ts`, so navigation cannot point
at a route that does not exist.

**Routing is code-based, not file-based.** `biome.json` has no exclusion for generated
files, so a `routeTree.gen.ts` would have to satisfy lint and format on every regeneration.
The route table in `app/router.tsx` is also easier to read against the plan than twenty
near-identical files.

## Still owed

- `biome.json`'s import-boundary patterns match nothing today — the second override
  replaces the first's options, and two of the globs never match. Until that is fixed the
  direction above is a convention, not a gate.
- `/` stays a placeholder for the whole of Phase 1. That is deliberate: the findings feed
  belongs there in Phase 2, and no dashboard may drift into being the front door.
