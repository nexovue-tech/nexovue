# Design System

> **Type:** Design reference · **Status:** Decided, not yet implemented (P1-08 builds the token layer)
> **Companion docs:** `../architecture/repository-structure.md` (where it lives) · `../architecture/technology-choices.md` (licences and sources)

## Thesis

**This is a reading product, not a charting product.** The primary object is a sentence — "Checkout is 4× slower than normal since 15:04." Everything else is evidence supporting a sentence.

Consequences that follow from that, not from taste: typography-led rather than card-based; density closer to a terminal than a marketing page; charts as sparklines inside sentences rather than panels beside them; dark mode first-class, because this gets read at 2am; keyboard-first with a command palette, because the primary user lives in a terminal.

## Stack

| Layer | Choice | Note |
|---|---|---|
| Styling | **Tailwind v4** | CSS-first config on custom properties, OKLCH native. Our tokens *are* the framework's tokens |
| Primitives | **Base UI** | Built by the original Radix engineers, now at MUI. v1.0 December 2025 |
| Scaffolding | **shadcn/ui** | A code distribution registry built on Base UI, not a dependency — the CLI copies source we then own and restyle |
| Charts | **uPlot** | Time series |
| Graphs | **React Flow** | Service map |
| Virtualization | **TanStack Virtual** | Log viewer and trace waterfall render 100k rows with ~60 in the DOM |

### Rules for using shadcn

The risk is never the library — it is the default look, which is the recognizable AI-generated signature. Because the code is copied into our repo, we control both:

1. Initialize with Base UI, then delete the default theme before the first screen is written.
2. `shadcn add` output is a **first draft**. It is re-tokened before landing in `packages/ui`, never committed as generated.
3. **Lint gate:** default Tailwind palette names (`slate-*`, `zinc-*`, `gray-*`, `blue-500`, …) fail CI anywhere in the repo. Only our token classes exist. This is what stops defaults creeping back over months of adding components.
4. Waterfall, log viewer, service map, and sparkline are ours from scratch — no registry provides them.

---

## Colour

### Foundations

**OKLCH, never hex or HSL.** Perceptually uniform: a 0.1 lightness change looks like the same amount of change on a dark navy as on a pale yellow, which is false in HSL. Native CSS since 2023, supported everywhere current.

**Two palettes, not one.** A single palette cannot serve light and dark. Dark is designed properly; light is derived from it.

**Twelve steps, each with a job** — app background, subtle background, element background, hover, active, subtle border, border, strong border, solid, solid hover, low-contrast text, high-contrast text. This is what prevents "which grey was that" across thirty screens.

**APCA contrast, target Lc ≥ 60 for body text.** WCAG 2.x ratios systematically misjudge dark backgrounds, and we are dark-first.

### The rule that matters most

Colour in an observability tool is asked to do two incompatible jobs:

1. **Status** — a small fixed set that must be unmistakable
2. **Data series** — many categories that must be distinct from each other

They collide constantly in existing tools. **If a chart series can be red, red stops meaning "bad."** So the status hues are reserved and excluded from the categorical palette. Everything else in this section follows from that.

### Severity: health is the absence of colour

| State | Colour | Also encoded by |
|---|---|---|
| Critical | Red | Filled shape, heaviest weight, top of feed |
| Warning | Amber | Outlined shape |
| Info | Blue | Small dot |
| **Healthy** | **Neutral — no colour** | Absence of a badge |

Colour means *something needs you*. A wall of green dots is a tool shouting that nothing is happening, which contradicts the whole product: when everything is fine, the feed should be calm.

This also resolves the accessibility problem for free. Red/green pairs are the classic failure for deuteranopia and protanopia — both read as the same indistinct pale colour, and roughly 1 in 12 men has some red-green deficiency. Red survives because it is too deeply learned to fight; green is simply never asked to carry meaning opposite it.

**Colour is never the only carrier.** Every severity also has a shape, a weight, and a position.

### Starting values

Dark theme. Neutral carries a trace of chroma so it reads as considered rather than dead.

```css
--n-1:  oklch(0.16 0.005 260);  /* app background      */
--n-2:  oklch(0.19 0.006 260);  /* subtle background   */
--n-3:  oklch(0.23 0.007 260);  /* element background  */
--n-4:  oklch(0.27 0.008 260);  /* hover               */
--n-5:  oklch(0.31 0.009 260);  /* active              */
--n-6:  oklch(0.36 0.010 260);  /* subtle border       */
--n-7:  oklch(0.43 0.011 260);  /* border              */
--n-8:  oklch(0.52 0.012 260);  /* strong border       */
--n-9:  oklch(0.62 0.012 260);  /* solid               */
--n-10: oklch(0.70 0.011 260);  /* solid hover         */
--n-11: oklch(0.78 0.008 260);  /* secondary text      */
--n-12: oklch(0.96 0.004 260);  /* primary text        */

--severity-critical: oklch(0.62 0.20 25);
--severity-warning:  oklch(0.75 0.15 70);
--severity-info:     oklch(0.70 0.13 240);
--severity-healthy:  var(--n-11);      /* no hue, by design */
```

Categorical series, with the status hues (red ~15–40, amber ~60–85) excluded and ordered so adjacent picks stay far apart:

```css
--series-1: oklch(0.72 0.13 200);   /* cyan    */
--series-2: oklch(0.70 0.15 290);   /* violet  */
--series-3: oklch(0.75 0.14 140);   /* green   */
--series-4: oklch(0.72 0.14 330);   /* magenta */
--series-5: oklch(0.78 0.11 175);   /* teal    */
--series-6: oklch(0.80 0.10 110);   /* lime    */
```

Green is available as a *series* colour precisely because it carries no status meaning.

> These are starting values, not verified ones. P1-08 validates them under APCA and under deuteranopia/protanopia/tritanopia simulation, and adjusts. Record the measured values here when it does.

### Other colour uses

- **Percentiles** (p50/p95/p99) are *sequential*, not categorical — one hue, three lightness steps.
- **Heatmaps** — single-hue sequential. Never rainbow.
- **Deploy markers** — neutral vertical rules, never a series colour.

---

## Components

| Category | Components |
|---|---|
| Shell | AppShell · CommandPalette · TimeRangePicker · FilterBar |
| Data | VirtualTable · LogViewer · KeyValueList · StackTrace · JsonTree |
| Signal | SeverityBadge · StatusDot · Sparkline · MiniChart |
| Custom, high-risk | **TraceWaterfall** · **LogViewer** — no library provides these; both need virtualization |
| Feedback | Drawer · Toast · EmptyState · LoadingSkeleton |

## Banned

The recognizable AI-generated UI signature: default shadcn styling · slate-grey palette · uniform rounded card grids · Lucide icons as the entire visual language · gradient hero accents · 16px padding everywhere · colour as the sole carrier of meaning.
