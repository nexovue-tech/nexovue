# Frontend architecture

## Current verified behavior

`apps/web` is a Vite, React 19, TypeScript application using TanStack Router and Query. Route
declarations live in `app/router.tsx`; sidebar entries live in `app/routes/route-manifest.ts`, with a
test preventing route/sidebar drift.

Imports flow `shared → features → app`. Features do not import other features or `app`.
`shared/api-client.ts` is the single API transport and adds `traceparent`, timeout handling, and
typed API errors. `app/query-client.ts` is the single status-code interpretation point, and
`app/status-interpretation.test.ts` reads `src/` and fails if a feature reimplements one. Three files
may read a status, each for a stated reason; the allowlist in that test is the record of why.

The console implements services, service map, traces, errors, logs, RUM, synthetics, infrastructure,
usage, and account/settings screens. Databases are a view *inside* infrastructure rather than a route
of their own: both are polled rather than pushed, and a database is reached from a trace far more
often than it is browsed. The root Findings route is deliberately a Phase 2 placeholder; it is not an
implemented insight feed.

**Narrowing a list is the server's job.** Filters and ordering travel with the request, never applied
to the answer. Every ranked read is capped, so a filter or a sort over what came back can only
rearrange the rows that survived the cut — it answers "the failing ones among the first hundred" with
nothing on screen able to say so. `shared/filters.ts` holds the URL half of this: a filter lives in
the address bar, so a narrowed screen can be handed to a colleague as a link.
`apps/api/tests/test_filters_reach_past_the_cap.py` is what holds the rule, by seeding a match that
ranks beyond the cap and proving the unfiltered read misses it.

UI tokens come from `@nexovue/ui`, specified in [`../design/design-system.md`](../design/design-system.md);
loading uses skeletons, failures use context-appropriate toast/inline/boundary presentation, and
missing readings display an em dash rather than zero. `shared/components/Sparkline.tsx` is how a trend
is drawn, on a scale shared across the screen rather than per row. `shared/delta.ts` and
`shared/components/Delta.tsx` are how a reading is compared with the previous equivalent window —
and most readings carry no delta, because an annotation spent on every row stops meaning anything.
