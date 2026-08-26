# ui — design system

Tokens and primitives. Colour scales are OKLCH, verified against both APCA and WCAG 2.1 AA — see `docs/design/design-system.md`.

Raw hex outside this package fails lint. So do the default Tailwind palette names, which are the recognizable AI-generated signature.

## What is here (P1-08)

| File | Contents |
|---|---|
| `tokens.css` | The three token layers: raw OKLCH scales per theme, the semantic `@theme inline` mapping components name, and the type/spacing/radius/motion scales |
| `fonts.css` | `@font-face` for the eight IBM Plex faces in `fonts/` |
| `fonts/` | Self-hosted WOFF2, latin subset only. Regenerate with `pnpm fonts:sync` |
| `scripts/verify-contrast.js` | Re-measures the shipped palette with the official `apca-w3` package and WCAG 2.1. `pnpm test` |

## Using it

```css
@import "tailwindcss";
@import "@nexovue/ui/fonts.css";
@import "@nexovue/ui/tokens.css";
```

Tailwind v4 is configured entirely from CSS; there is no JS config to import.

## The rules the tokens enforce

**Name the job, not the step.** Components use `bg-surface`, `text-ink-dim`, `border-line` — never `--n-2`. The twelve-step scale exists so that each step has exactly one job; reaching past the semantic layer throws that away.

**No `dark:` variants.** `--color-surface` resolves to `var(--n-2)`, and `--n-2` swaps with the theme. One class is correct in both themes. If you find yourself writing `dark:`, the token you want does not exist yet — add it here.

**Severity is four tokens, not one colour.** `-subtle` (badge fill), `-border`, `-text` (label), `-solid` (dot or accent bar). A saturated fill with white text sits in the 4.5:1 dead zone; the badge anatomy exists to avoid it. Healthy has no colour at all.

**Brand green is interactive only.** Logo, links, focus rings, primary buttons, active navigation. Never a status, never a chart series. Text on a brand fill is `text-brand-contrast` (black) — white on brand measures Lc 29.5.

**The default Tailwind palette does not exist.** `@theme` resets `--color-*`, `--font-*`, `--text-*`, `--radius-*` and `--shadow-*` to `initial`, so `bg-slate-800` and `rounded-xl` resolve to nothing. That is the structural half of the anti-default rule; the lint half is still owed (see below).

## Verifying the palette

```
pnpm --filter @nexovue/ui test
```

Measures all 28 pairs from the design doc's verification table plus the two colourblind
lightness separations. It reads `tokens.css` directly, so the values measured are the
values that ship.

A missed target is a palette decision. Do not edit a token to make the script pass — the
values belong to the design doc, and changing one silently puts code and doc out of step.

## Still owed

- The lint gate banning default Tailwind palette names in `.tsx` (P1-08 delivers the token-level removal; the lint rule needs an entry in `biome.json`).
- Primitives. Base UI is the chosen library but is still at `1.0.0-rc.0`, so nothing is adopted yet.
