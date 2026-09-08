# web — the interface (TypeScript, Vite + React 19)

Imports flow one direction: **shared → features → app**. A feature may use `shared/`, never another feature; only `app/` composes features. Cross-boundary imports use the `@/` alias so the direction is visible in the import line.

**Colours, type, spacing and radii come from `@nexovue/ui`.** No hex, no `slate-*`, no `dark:` variants — the semantic tokens swap themselves. If the token you want does not exist, add it there, not here.

**`shared/api-client.ts` is the only way to reach the API.** fetch, not axios. It injects `traceparent`, applies `AbortSignal.timeout`, and raises one typed `ApiError` carrying the parsed RFC 9457 problem body and the correlation id.

**`app/query-client.ts` is the only place a status code is interpreted** — 401, 403, 429, 5xx and offline all map there. Features never handle 401.

Failure presentation is fixed: toast for transient, inline for a form or field, full screen only for a boundary failure. Loading is a skeleton, never a spinner.

Every unbuilt route renders `ComingSoon` naming its delivering task. **The sidebar comes from `app/routes/route-manifest.ts`; the routes are declared in `app/router.tsx`** — which is why `/login`, `/signup`, `/onboarding` and the recovery screens are routes with no manifest nav entry. `route-manifest.test.ts` fails if the sidebar offers a path the router does not serve.

`/` is reserved for the Phase 2 findings feed and stays a placeholder for all of Phase 1.
