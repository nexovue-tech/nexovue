# Frontend architecture

## Current verified behavior

`apps/web` is a Vite, React 19, TypeScript application using TanStack Router and Query. Route declarations live in `app/router.tsx`; sidebar entries live in `app/routes/route-manifest.ts`, with a test preventing route/sidebar drift.

Imports flow `shared → features → app`. Features do not import other features or `app`. `shared/api-client.ts` is the single API transport and adds `traceparent`, timeout handling, and typed API errors. `app/query-client.ts` is the single status-code interpretation point.

The console implements services, service map, traces, errors, logs, RUM, synthetics, database, infrastructure, usage, and account/settings screens. The root Findings route is deliberately a Phase 2 placeholder; it is not an implemented insight feed.

UI tokens come from `@nexovue/ui`, specified in [`../design/design-system.md`](../design/design-system.md); loading uses skeletons, failures use context-appropriate toast/inline/boundary presentation, and missing readings display an em dash rather than zero.
