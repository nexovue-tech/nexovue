# Frontend engineering standard

- Preserve `shared → features → app` import direction.
- Use `shared/api-client.ts` for all API access and keep HTTP status interpretation in `app/query-client.ts`.
- Use TypeScript types without explicit `any` or non-null assertions.
- Use `@nexovue/ui` tokens; do not introduce raw hex, default Tailwind palettes, or unrelated component libraries.
- Follow [`../design/principles.md`](../design/principles.md) when adding or materially changing a screen; [`../design/design-system.md`](../design/design-system.md) is the token and component reference.
- Cover loading, empty, error, and mobile states. Use skeletons for loading and the established failure-presentation rules.
