# 32. UI stack = Tailwind v4 + Base UI, scaffolded with shadcn/ui

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

We need accessible primitives without adopting a design language, and a way to scaffold components
we then own. Radix was the incumbent choice for primitives.

## Decision

Tailwind v4 with Base UI, scaffolded using shadcn/ui.

shadcn is a code *distribution registry* built on Base UI, not a competing library — the CLI copies
source into our repo, which we then own and restyle. It has defaulted to Base UI since December
2025.

Base UI over Radix because the original Radix engineers now build it at MUI with a full-time team
and monthly releases, while Radix maintenance has visibly slowed.

The risk shadcn carries is its default look — the recognizable AI-generated signature. That is
handled by a lint gate: default Tailwind palette names fail CI anywhere in the repo, so only our
tokens exist.

## Consequences

We get accessible primitives and fast scaffolding while owning every line of component source —
which also means we own their maintenance, with no upstream fixes arriving for free. The lint gate
makes "looks like every other shadcn app" a build failure rather than a matter of discipline. The
waterfall, log viewer, service map and sparkline are built from scratch regardless — no library
covers them.
