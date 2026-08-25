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

### The brand colour

**`#0BEA91` = `oklch(0.825 0.195 157.1)`** — taken from nexovue.com, where it is the primary across the logo, headings, and buttons.

It is a light, high-chroma spring green, and that has three consequences the system has to handle:

1. **It is light.** Text on a brand-filled button must be **black**, never white — white on brand measures Lc 29.5, a clear fail; black measures 77.8. The marketing site already does this correctly.
2. **It is green, on a monitoring product.** Green traditionally means "healthy", and we decided health is the *absence* of colour. So the rule is strict: **brand green appears only in interactive and brand contexts** — logo, links, focus rings, primary buttons, active navigation. Never in a status, never in a chart. Users learn "green = ours, and clickable," not "green = fine."
3. **Its hue band is reserved.** 130–175° belongs to the brand, so green is removed from the categorical series palette entirely — alongside red 15–40° and amber 55–85°, which belong to severity.

The neutral scale carries a trace of the brand hue (chroma 0.010 at 157.1°) rather than being pure grey. At that level it is not perceptible as green; it stops the interface reading as dead grey-on-grey and quietly ties the greys to the brand.

### Tokens

Generated in OKLCH at fixed hue with chroma gamut-clamped per step, then verified — not eyeballed. Hex comments are the sRGB equivalents.

```css
/* ---------- DARK (default) ---------- */
:root {
  --n-1: oklch(0.150 0.001 157.1);   /* #0B0B0B */
  --n-2: oklch(0.185 0.002 157.1);   /* #121312 */
  --n-3: oklch(0.225 0.003 157.1);   /* #1B1C1B */
  --n-4: oklch(0.265 0.003 157.1);   /* #242624 */
  --n-5: oklch(0.305 0.005 157.1);   /* #2D302E */
  --n-6: oklch(0.355 0.005 157.1);   /* #3A3D3B */
  --n-7: oklch(0.420 0.006 157.1);   /* #4B4E4C */
  --n-8: oklch(0.510 0.007 157.1);   /* #636764 */
  --n-9: oklch(0.620 0.010 157.1);   /* #818884 */
  --n-10: oklch(0.700 0.009 157.1);   /* #9AA09C */
  --n-11: oklch(0.816 0.006 157.1);   /* #C0C4C1 */
  --n-12: oklch(0.960 0.003 157.1);   /* #F0F2F1 */

  --brand-1: oklch(0.150 0.020 157.1);   /* #050E08 */
  --brand-2: oklch(0.185 0.029 157.1);   /* #07170D */
  --brand-3: oklch(0.225 0.049 157.1);   /* #032212 */
  --brand-4: oklch(0.265 0.063 157.1);   /* #002E18 */
  --brand-5: oklch(0.305 0.073 157.1);   /* #003A20 */
  --brand-6: oklch(0.355 0.084 157.1);   /* #004829 */
  --brand-7: oklch(0.420 0.100 157.1);   /* #005D36 */
  --brand-8: oklch(0.510 0.121 157.1);   /* #007A49 */
  --brand-9: oklch(0.825 0.195 157.1);   /* #0AEA91 */
  --brand-10: oklch(0.895 0.179 157.1);   /* #5DFFAC */
  --brand-11: oklch(0.816 0.117 157.1);   /* #7EDAA5 */
  --brand-12: oklch(0.960 0.058 157.1);   /* #D3FEE2 */

  --sev-critical: oklch(0.580 0.200 25);   /* #D73337 — white text */
  --sev-critical-text: oklch(0.775 0.132 25);   /* #FF938B */
  --sev-warning: oklch(0.830 0.135 70);   /* #FFB75D — black text */
  --sev-warning-text: oklch(0.767 0.160 70);   /* #F39E1C */
  --sev-info: oklch(0.630 0.144 240);   /* #0092D7 — white text */
  --sev-info-text: oklch(0.754 0.141 240);   /* #4ABAFF */
  --sev-healthy: var(--n-11);   /* no hue, by design */

  --series-1: oklch(0.800 0.136 200);   /* #00D7E0 cyan */
  --series-2: oklch(0.800 0.140 180);   /* #23DBC1 teal */
  --series-3: oklch(0.800 0.108 290);   /* #BCB2FF violet */
  --series-4: oklch(0.800 0.100 265);   /* #9EBDFF indigo */
  --series-5: oklch(0.800 0.140 330);   /* #F09BE8 magenta */
  --series-6: oklch(0.800 0.118 10);   /* #FF9DAC rose */
}

/* ---------- LIGHT ---------- */
[data-theme="light"] {
  --n-1: oklch(0.995 0.001 157.1);   /* #FDFEFD */
  --n-2: oklch(0.980 0.002 157.1);   /* #F8F9F8 */
  --n-3: oklch(0.955 0.003 157.1);   /* #EFF1EF */
  --n-4: oklch(0.930 0.003 157.1);   /* #E6E8E7 */
  --n-5: oklch(0.905 0.005 157.1);   /* #DDE0DE */
  --n-6: oklch(0.875 0.005 157.1);   /* #D3D7D4 */
  --n-7: oklch(0.830 0.006 157.1);   /* #C4C8C6 */
  --n-8: oklch(0.760 0.007 157.1);   /* #AEB2AF */
  --n-9: oklch(0.620 0.010 157.1);   /* #818884 */
  --n-10: oklch(0.560 0.009 157.1);   /* #707672 */
  --n-11: oklch(0.569 0.006 157.1);   /* #747876 */
  --n-12: oklch(0.310 0.003 157.1);   /* #2F3130 */

  --brand-1: oklch(0.995 0.007 157.1);   /* #FAFFFB */
  --brand-2: oklch(0.980 0.029 157.1);   /* #E9FFF1 */
  --brand-3: oklch(0.955 0.049 157.1);   /* #D6FBE3 */
  --brand-4: oklch(0.930 0.068 157.1);   /* #C3F6D6 */
  --brand-5: oklch(0.905 0.088 157.1);   /* #AFF2C9 */
  --brand-6: oklch(0.875 0.098 157.1);   /* #9FEABC */
  --brand-7: oklch(0.830 0.107 157.1);   /* #8ADDAC */
  --brand-8: oklch(0.760 0.136 157.1);   /* #5ACB8E */
  --brand-9: oklch(0.825 0.195 157.1);   /* #0AEA91 */
  --brand-10: oklch(0.765 0.182 157.1);   /* #00D482 */
  --brand-11: oklch(0.569 0.117 157.1);   /* #2A8B5B */
  --brand-12: oklch(0.310 0.058 157.1);   /* #123924 */

  --sev-critical: oklch(0.530 0.200 25);   /* #C51D28 — white text */
  --sev-critical-text: oklch(0.655 0.160 25);   /* #E2635D */
  --sev-warning: oklch(0.630 0.136 70);   /* #BD7800 — white text */
  --sev-warning-text: oklch(0.645 0.140 70);   /* #C37C00 */
  --sev-info: oklch(0.530 0.121 240);   /* #0073AA — white text */
  --sev-info-text: oklch(0.631 0.145 240);   /* #0093D7 */
  --sev-healthy: var(--n-11);   /* no hue, by design */

  --series-1: oklch(0.600 0.102 200);   /* #009298 cyan */
  --series-2: oklch(0.600 0.109 180);   /* #009583 teal */
  --series-3: oklch(0.600 0.140 290);   /* #806FCD violet */
  --series-4: oklch(0.600 0.140 265);   /* #567CD3 indigo */
  --series-5: oklch(0.600 0.140 330);   /* #AE5EA8 magenta */
  --series-6: oklch(0.600 0.140 10);   /* #C4576D rose */
}
```

### Verification

Measured with APCA (the W3C draft algorithm), not WCAG 2 ratios, because WCAG 2 systematically misjudges dark backgrounds and we are dark-first.

Thresholds: **Lc 75** for body text · **Lc 60** for medium/bold text and chart marks · **Lc 15** for non-text boundaries like a badge fill.

| Pair | APCA Lc | Target | |
|---|---:|---:|:--|
| dark · primary text (n-12) on background | 99 | 75 | ✓ |
| dark · secondary text (n-11) on background | 70 | 60 | ✓ |
| dark · brand link (brand-11) on background | 73 | 60 | ✓ |
| dark · black text on brand solid button | 78 | 75 | ✓ |
| dark · white text on critical badge | 77 | 60 | ✓ |
| dark · critical badge fill vs background | 30 | 15 | ✓ |
| dark · critical text variant on background | 60 | 60 | ✓ |
| dark · black text on warning badge | 73 | 60 | ✓ |
| dark · warning badge fill vs background | 72 | 15 | ✓ |
| dark · warning text variant on background | 60 | 60 | ✓ |
| dark · white text on info badge | 67 | 60 | ✓ |
| dark · info badge fill vs background | 40 | 15 | ✓ |
| dark · info text variant on background | 60 | 60 | ✓ |
| dark · worst chart series vs background | 64 | 60 | ✓ |
| light · primary text (n-12) on background | 98 | 75 | ✓ |
| light · secondary text (n-11) on background | 70 | 60 | ✓ |
| light · brand link (brand-11) on background | 68 | 60 | ✓ |
| light · black text on brand solid button | 78 | 75 | ✓ |
| light · white text on critical badge | 83 | 60 | ✓ |
| light · critical badge fill vs background | 76 | 15 | ✓ |
| light · critical text variant on background | 60 | 60 | ✓ |
| light · white text on warning badge | 69 | 60 | ✓ |
| light · warning badge fill vs background | 62 | 15 | ✓ |
| light · warning text variant on background | 60 | 60 | ✓ |
| light · white text on info badge | 81 | 60 | ✓ |
| light · info badge fill vs background | 74 | 15 | ✓ |
| light · info text variant on background | 60 | 60 | ✓ |
| light · worst chart series vs background | 63 | 60 | ✓ |

### Colourblind separation

Severity fills are deliberately staggered in lightness where the hues are confusable:

| Pair | ΔL | Why |
|---|---|---|
| critical ↔ warning | **0.25** (dark) / **0.10** (light) | Red and amber are the pair that collapses under protanopia and deuteranopia. Lightness carries the difference when hue cannot. |
| critical ↔ info | 0.05 | Red vs blue survives every common CVD type on hue alone — no lightness gap needed. |

This is why the severity fills are not all tuned to the same contrast: forcing equal Lc drove them to identical lightness, which is exactly what breaks greyscale legibility. Contrast and separation are different requirements.

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
