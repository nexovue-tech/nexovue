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
| Primitives | **Base UI** | Built by the original Radix engineers, now at MUI. npm latest is `1.0.0-rc.0` as of 2026-08-26 — still a release candidate, so **no primitives are adopted yet**; revisit when it ships stable |
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

**Badge anatomy.** A severity badge is a *subtle tinted fill* + *coloured label* + *border* — never white text on a saturated block. Saturated mid-lightness fills sit in the 4.5:1 dead zone where neither white nor black text passes WCAG, and forcing it distorts the colour badly. Each severity therefore has four tokens: `-subtle` (badge fill), `-text` (label), `-border`, and `-solid` (the dot or accent bar, a non-text element needing only 3:1).

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

Generated in OKLCH at fixed hue with chroma gamut-clamped per step, then verified against both contrast standards — not eyeballed. Hex comments are the sRGB equivalents.

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
  --n-11: oklch(0.775 0.006 157.1);   /* #B3B7B4 */
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

  --sev-critical-subtle: oklch(0.205 0.045 25);   /* #280E0C badge fill */
  --sev-critical-border: oklch(0.355 0.075 25);   /* #5D2A27 */
  --sev-critical-solid:  oklch(0.620 0.200 25);   /* #E64343 dot / accent bar */
  --sev-critical-text:   oklch(0.779 0.129 25);   /* #FF958D label */
  --sev-warning-subtle: oklch(0.205 0.044 70);   /* #241300 badge fill */
  --sev-warning-border: oklch(0.355 0.075 70);   /* #543403 */
  --sev-warning-solid:  oklch(0.800 0.162 70);   /* #FFA82A dot / accent bar */
  --sev-warning-text:   oklch(0.782 0.167 70);   /* #FBA20F label */
  --sev-info-subtle: oklch(0.205 0.045 240);   /* #011929 badge fill */
  --sev-info-border: oklch(0.355 0.075 240);   /* #0A405F */
  --sev-info-solid:  oklch(0.700 0.160 240);   /* #00A9F7 dot / accent bar */
  --sev-info-text:   oklch(0.760 0.137 240);   /* #51BCFF label */
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
  --n-11: oklch(0.563 0.006 157.1);   /* #737674 */
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
  --brand-11: oklch(0.505 0.117 157.1);   /* #0A7849 */
  --brand-12: oklch(0.310 0.058 157.1);   /* #123924 */

  --sev-critical-subtle: oklch(0.965 0.017 25);   /* #FFEFEE badge fill */
  --sev-critical-border: oklch(0.860 0.075 25);   /* #FFBFB8 */
  --sev-critical-solid:  oklch(0.560 0.200 25);   /* #D02B31 dot / accent bar */
  --sev-critical-text:   oklch(0.553 0.170 25);   /* #C23D3C label */
  --sev-warning-subtle: oklch(0.965 0.025 70);   /* #FFF1E2 badge fill */
  --sev-warning-border: oklch(0.860 0.075 70);   /* #F1C99C */
  --sev-warning-solid:  oklch(0.660 0.143 70);   /* #C98000 dot / accent bar */
  --sev-warning-text:   oklch(0.547 0.119 70);   /* #9C6200 label */
  --sev-info-subtle: oklch(0.965 0.019 240);   /* #E9F6FF badge fill */
  --sev-info-border: oklch(0.860 0.075 240);   /* #A4D8FE */
  --sev-info-solid:  oklch(0.480 0.110 240);   /* #006495 dot / accent bar */
  --sev-info-text:   oklch(0.540 0.124 240);   /* #0076AF label */
  --sev-healthy: var(--n-11);   /* no hue, by design */

  --series-1: oklch(0.600 0.102 200);   /* #009298 cyan */
  --series-2: oklch(0.600 0.109 180);   /* #009583 teal */
  --series-3: oklch(0.600 0.140 290);   /* #806FCD violet */
  --series-4: oklch(0.600 0.140 265);   /* #567CD3 indigo */
  --series-5: oklch(0.600 0.140 330);   /* #AE5EA8 magenta */
  --series-6: oklch(0.600 0.140 10);   /* #C4576D rose */
}
```

### Verification — against two standards, deliberately

Measured with **APCA** (the W3C draft algorithm) *and* **WCAG 2.1 ratios**. Both, because they disagree and each answers a different question:

- **APCA** models perception far better on dark backgrounds, where WCAG 2 systematically misjudges. It is what makes the interface actually readable.
- **WCAG 2.1 AA is what anyone can hold us to.** APCA belongs to WCAG 3, which is [still developmental and not an official recommendation](https://git.apcacontrast.com/documentation/README) — "WCAG 3 Compliant does not exist yet." Enterprise procurement asks for WCAG 2.1 AA and a VPAT.

Tuning to APCA alone produced a palette that **failed WCAG 2.1 AA in six places**, all in the light theme. Every value below clears both.

Targets: body text Lc 75 / 4.5:1 · labels and chart marks Lc 60 / 4.5:1 · non-text UI Lc 15 / 3:1.

| Pair | APCA Lc | WCAG | Targets | |
|---|---:|---:|---|:--|
| dark · primary text (n-12) on page | 99 | 17.53:1 | Lc 75 · 4.5:1 | ✓ |
| dark · secondary text (n-11) on page | 63 | 9.68:1 | Lc 60 · 4.5:1 | ✓ |
| dark · brand link (brand-11) on page | 73 | 11.64:1 | Lc 60 · 4.5:1 | ✓ |
| dark · black text on brand button | 78 | 13.18:1 | Lc 75 · 4.5:1 | ✓ |
| dark · critical label on badge fill | 60 | 8.56:1 | Lc 60 · 4.5:1 | ✓ |
| dark · critical label on page | 61 | 9.29:1 | Lc 60 · 4.5:1 | ✓ |
| dark · critical dot vs page (UI) | 35 | 4.90:1 | Lc 15 · 3.0:1 | ✓ |
| dark · warning label on badge fill | 62 | 8.78:1 | Lc 60 · 4.5:1 | ✓ |
| dark · warning label on page | 63 | 9.60:1 | Lc 60 · 4.5:1 | ✓ |
| dark · warning dot vs page (UI) | 66 | 10.23:1 | Lc 15 · 3.0:1 | ✓ |
| dark · info label on badge fill | 60 | 8.46:1 | Lc 60 · 4.5:1 | ✓ |
| dark · info label on page | 61 | 9.32:1 | Lc 60 · 4.5:1 | ✓ |
| dark · info dot vs page (UI) | 51 | 7.49:1 | Lc 15 · 3.0:1 | ✓ |
| dark · worst chart series vs page (UI) | 64 | 9.92:1 | Lc 60 · 3.0:1 | ✓ |
| light · primary text (n-12) on page | 98 | 12.96:1 | Lc 75 · 4.5:1 | ✓ |
| light · secondary text (n-11) on page | 71 | 4.52:1 | Lc 60 · 4.5:1 | ✓ |
| light · brand link (brand-11) on page | 76 | 5.46:1 | Lc 60 · 4.5:1 | ✓ |
| light · black text on brand button | 78 | 13.18:1 | Lc 75 · 4.5:1 | ✓ |
| light · critical label on badge fill | 67 | 4.69:1 | Lc 60 · 4.5:1 | ✓ |
| light · critical label on page | 74 | 5.15:1 | Lc 60 · 4.5:1 | ✓ |
| light · critical dot vs page (UI) | 73 | 5.08:1 | Lc 15 · 3.0:1 | ✓ |
| light · warning label on badge fill | 67 | 4.55:1 | Lc 60 · 4.5:1 | ✓ |
| light · warning label on page | 73 | 4.93:1 | Lc 60 · 4.5:1 | ✓ |
| light · warning dot vs page (UI) | 58 | 3.16:1 | Lc 15 · 3.0:1 | ✓ |
| light · info label on badge fill | 67 | 4.51:1 | Lc 60 · 4.5:1 | ✓ |
| light · info label on page | 73 | 4.91:1 | Lc 60 · 4.5:1 | ✓ |
| light · info dot vs page (UI) | 80 | 6.36:1 | Lc 15 · 3.0:1 | ✓ |
| light · worst chart series vs page (UI) | 63 | 3.68:1 | Lc 60 · 3.0:1 | ✓ |

> **WCAG figures are measured on 8-bit sRGB**, corrected 2026-08-26. WCAG 2.x defines relative luminance over 8-bit channel values — what a browser actually paints — while the first pass computed it on unquantised floats. One pair passed in float and failed once rounded: light warning-label-on-badge measured 4.509 unquantised but **4.488 as painted**, below AA. `--sev-warning-text` moved from L 0.549 to 0.547 and now measures 4.55. Two pairs remain thin by design — light info-on-badge at 4.53 and light secondary text at 4.54 — so any future change to those hues must be re-measured quantised, not in float.
>
> Verified with the official `apca-w3` package rather than a hand-implementation. Across all 28 pairs the APCA values agreed exactly; only the WCAG quantisation differed.

### Colourblind separation

Severity *dots* are deliberately staggered in lightness where hues are confusable:

| Pair | ΔL dark | ΔL light | Why |
|---|---|---|---|
| critical ↔ warning | **0.18** | **0.10** | Red and amber collapse together under protanopia and deuteranopia. Lightness carries the difference when hue cannot. |
| critical ↔ info | 0.08 | 0.08 | Red vs blue survives every common CVD type on hue alone. |

This is why severity colours are *not* all tuned to identical contrast. An early pass did exactly that and drove all three to the same lightness — perfectly legible, and indistinguishable in greyscale. **Contrast and separation are different requirements**, and optimising for one silently destroys the other.

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

## Global handling

Components without a policy get one invented per screen. These are the policies.

**Errors.** React 19 root handlers (`onUncaughtError` / `onCaughtError`) report centrally — note React 19 no longer re-throws, so uncaught errors reach `window.reportError`. A root boundary means **never a white page**: a recoverable screen showing the correlation id. Route-level boundaries keep one broken screen from taking the shell with it. `window.onerror` and `unhandledrejection` catch what boundaries cannot — async work and event handlers.

**API failures are mapped once**, in the query client, never per screen: `401` clears the session and redirects to login · `403` renders forbidden · `429` toasts honouring `Retry-After` · `5xx` toasts and retries · offline shows a banner. A feature that handles `401` itself is a bug.

**Where each error appears.** Toast for transient. Inline for field and form errors. Full screen only for boundary-level failure. Every user-visible error carries the correlation id, because the id is what turns "it broke" into a log line and a trace.

**Loading is skeletons, not spinners.** A skeleton says what is coming and holds the layout still; a spinner says only that something is happening. That difference is the reading-product thesis applied to waiting.

## Responsive

The sidebar is a **column above `md`, a drawer below it**. At 375px a fixed 13rem rail
takes more than half the screen and leaves body text wrapping every two or three words —
which fails the commitment that the product is usable on a phone at 2am.

The drawer closes when a link is followed, on Escape, and on the scrim. The scrim is
**dark in both themes** (`--color-scrim`): a light scrim on a light ground fades content
rather than dimming it, which reads as "disabled" instead of "something is on top".

**The 404 renders inside the shell**, not outside it. Auth screens sit outside deliberately
— there is no project or navigation to show yet — but someone who mistypes a URL is already
inside the app, and the one thing they need is the way back.

## Icons

**`lucide-react`, used sparingly.** The ban below is on Lucide *as the entire visual language* — icons carrying all the meaning, a grid of glyph-topped cards — not on Lucide existing. Tree-shaken, each icon costs 200–500 bytes.

The rule: **an icon never carries meaning alone.** Navigation is an index of words; active state is a left rule and a weight change, not a coloured glyph. Icons earn their place where a word would be worse — a dismiss control, a disclosure chevron, an external-link marker, the command-palette hint. If removing the icon makes the interface unusable, the label is missing.

Rejected: **Phosphor**, whose six weights would pair elegantly with a typography-led system — genuinely tempting, and heavier than a product with a handful of glyphs can justify. Revisit if icons ever become a real part of the visual language.

## Banned

The recognizable AI-generated UI signature: default shadcn styling · slate-grey palette · uniform rounded card grids · Lucide icons as the entire visual language · gradient hero accents · 16px padding everywhere · colour as the sole carrier of meaning.
