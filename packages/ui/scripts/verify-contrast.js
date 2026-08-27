// Re-measures the shipped palette against both contrast standards and against the
// colourblind lightness separation the design doc relies on.
//
// It reads tokens.css rather than a copy of the values, so the thing measured is the
// thing that ships. The expected numbers are the ones recorded in
// docs/design/design-system.md, which were produced by a hand-written APCA
// implementation; here they are checked against the official apca-w3 package, so a
// disagreement between the two shows up as DRIFT rather than as silence.
//
// Exit code: non-zero when a target is missed. Drift alone does not fail — it is
// reported, because the correct response is a decision, not an edit.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { APCAcontrast, sRGBtoY } from "apca-w3";
import { clampRgb, converter, parse, wcagContrast } from "culori";

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const toRgb = converter("rgb");

/* ------------------------------------------------------------------ token load */

/** Pulls `--name: value;` declarations out of one top-level rule of tokens.css. */
function readTokenBlock(css, ruleHeader) {
  const start = css.indexOf(ruleHeader);
  if (start === -1) throw new Error(`tokens.css has no rule "${ruleHeader}"`);
  const open = css.indexOf("{", start);
  const body = css.slice(open + 1, matchingBrace(css, open));

  const tokens = {};
  for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[name] = value.trim();
  }
  return tokens;
}

function matchingBrace(css, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  throw new Error("unbalanced braces in tokens.css");
}

/** Resolves `var(--x)` indirection, then OKLCH (or hex) to gamut-clamped sRGB floats. */
function resolveFloat(tokens, name) {
  let value = tokens[name];
  if (value === undefined) throw new Error(`unknown token ${name}`);

  const indirect = value.match(/^var\((--[\w-]+)\)$/);
  if (indirect) value = tokens[indirect[1]];

  const colour = parse(value);
  if (!colour) throw new Error(`token ${name} is not a colour: ${value}`);
  return clampRgb(toRgb(colour));
}

/** 8-bit sRGB, which is what the display receives and what both formulas are defined on. */
const quantise = ({ r, g, b }) => [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];

function lightnessOf(tokens, name) {
  const indirect = tokens[name].match(/^var\((--[\w-]+)\)$/);
  const value = indirect ? tokens[indirect[1]] : tokens[name];
  return parse(value).l;
}

const APCA_DRIFT_TOLERANCE = 1; // the doc rounds Lc to whole numbers
const WCAG_DRIFT_TOLERANCE = 0.01;

/* ------------------------------------------------------------------ the checks */

const BODY_TEXT = { lc: 75, wcag: 4.5 };
const LABEL = { lc: 60, wcag: 4.5 };
const NON_TEXT = { lc: 15, wcag: 3.0 };
const CHART_MARK = { lc: 60, wcag: 3.0 };

/** One row of the verification table in docs/design/design-system.md. */
const pair = (label, fg, bg, target, recorded) => ({ label, fg, bg, target, recorded });

const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"];

const CHECKS = {
  dark: [
    pair("primary text (n-12) on page", "--n-12", "--n-1", BODY_TEXT, [99, 17.53]),
    pair("secondary text (n-11) on page", "--n-11", "--n-1", LABEL, [63, 9.68]),
    pair("brand link (brand-11) on page", "--brand-11", "--n-1", LABEL, [73, 11.64]),
    pair("black text on brand button", "--pure-black", "--brand-9", BODY_TEXT, [78, 13.18]),
    pair(
      "critical label on badge fill",
      "--sev-critical-text",
      "--sev-critical-subtle",
      LABEL,
      [60, 8.56],
    ),
    pair("critical label on page", "--sev-critical-text", "--n-1", LABEL, [61, 9.29]),
    pair("critical dot vs page (UI)", "--sev-critical-solid", "--n-1", NON_TEXT, [35, 4.9]),
    pair(
      "warning label on badge fill",
      "--sev-warning-text",
      "--sev-warning-subtle",
      LABEL,
      [62, 8.78],
    ),
    pair("warning label on page", "--sev-warning-text", "--n-1", LABEL, [63, 9.6]),
    pair("warning dot vs page (UI)", "--sev-warning-solid", "--n-1", NON_TEXT, [66, 10.23]),
    pair("info label on badge fill", "--sev-info-text", "--sev-info-subtle", LABEL, [60, 8.46]),
    pair("info label on page", "--sev-info-text", "--n-1", LABEL, [61, 9.32]),
    pair("info dot vs page (UI)", "--sev-info-solid", "--n-1", NON_TEXT, [51, 7.49]),
    pair("worst chart series vs page (UI)", SERIES, "--n-1", CHART_MARK, [64, 9.92]),
  ],
  light: [
    pair("primary text (n-12) on page", "--n-12", "--n-1", BODY_TEXT, [98, 12.96]),
    pair("secondary text (n-11) on page", "--n-11", "--n-1", LABEL, [71, 4.52]),
    pair("brand link (brand-11) on page", "--brand-11", "--n-1", LABEL, [76, 5.46]),
    pair("black text on brand button", "--pure-black", "--brand-9", BODY_TEXT, [78, 13.18]),
    pair(
      "critical label on badge fill",
      "--sev-critical-text",
      "--sev-critical-subtle",
      LABEL,
      [67, 4.69],
    ),
    pair("critical label on page", "--sev-critical-text", "--n-1", LABEL, [74, 5.15]),
    pair("critical dot vs page (UI)", "--sev-critical-solid", "--n-1", NON_TEXT, [73, 5.08]),
    pair(
      "warning label on badge fill",
      "--sev-warning-text",
      "--sev-warning-subtle",
      LABEL,
      [67, 4.55],
    ),
    pair("warning label on page", "--sev-warning-text", "--n-1", LABEL, [74, 4.98]),
    pair("warning dot vs page (UI)", "--sev-warning-solid", "--n-1", NON_TEXT, [58, 3.16]),
    pair("info label on badge fill", "--sev-info-text", "--sev-info-subtle", LABEL, [67, 4.51]),
    pair("info label on page", "--sev-info-text", "--n-1", LABEL, [73, 4.91]),
    pair("info dot vs page (UI)", "--sev-info-solid", "--n-1", NON_TEXT, [80, 6.36]),
    pair("worst chart series vs page (UI)", SERIES, "--n-1", CHART_MARK, [63, 3.68]),
  ],
};

/** Staggered lightness so severity survives protanopia, deuteranopia and greyscale. */
const SEPARATIONS = [
  { label: "critical <-> warning", a: "--sev-critical-solid", b: "--sev-warning-solid", min: 0.1 },
  { label: "critical <-> info", a: "--sev-critical-solid", b: "--sev-info-solid", min: 0.08 },
];

/* ------------------------------------------------------------------ measurement */

// APCA is defined on 8-bit sRGB, and so is the WCAG 2.x luminance formula — both take
// channel/255. `ratio` is therefore measured on the quantised colour a browser actually
// paints. `ratioUnquantised` is the same pair before rounding: it is what reproduces the
// numbers recorded in the design doc, and the gap between the two is the whole story on
// any pair sitting within a hundredth of 4.5:1.
function measure(tokens, fg, bg) {
  const fgFloat = resolveFloat(tokens, fg);
  const bgFloat = resolveFloat(tokens, bg);
  return {
    lc: Math.abs(APCAcontrast(sRGBtoY(quantise(fgFloat)), sRGBtoY(quantise(bgFloat)))),
    ratio: wcagContrast(rgbString(quantise(fgFloat)), rgbString(quantise(bgFloat))),
    ratioUnquantised: wcagContrast(fgFloat, bgFloat),
  };
}

const rgbString = ([r, g, b]) => `rgb(${r} ${g} ${b})`;

/** For a set of candidates the doc measures the worst one, so we do too. */
function measureWorst(tokens, foregrounds, bg) {
  return [foregrounds]
    .flat()
    .map((fg) => measure(tokens, fg, bg))
    .reduce((worst, current) => (current.lc < worst.lc ? current : worst));
}

const css = readFileSync(join(packageRoot, "tokens.css"), "utf8");
const palettes = {
  dark: readTokenBlock(css, ":root {"),
  light: readTokenBlock(css, '[data-theme="light"] {'),
};

// Black is not a palette token — it is the fixed label colour on a brand fill.
for (const palette of Object.values(palettes)) palette["--pure-black"] = "#000000";

let failures = 0;
let drifts = 0;

for (const [theme, checks] of Object.entries(CHECKS)) {
  process.stdout.write(`\n${theme}\n`);
  for (const { label, fg, bg, target, recorded } of checks) {
    const { lc, ratio, ratioUnquantised } = measureWorst(palettes[theme], fg, bg);
    const passes = lc >= target.lc && ratio >= target.wcag;
    if (!passes) failures += 1;

    const [recordedLc, recordedRatio] = recorded;
    const drifted =
      Math.abs(lc - recordedLc) > APCA_DRIFT_TOLERANCE ||
      Math.abs(ratioUnquantised - recordedRatio) > WCAG_DRIFT_TOLERANCE;
    if (drifted) drifts += 1;

    const mark = passes ? "ok  " : "FAIL";
    const note = drifted ? `  DRIFT: doc records Lc ${recordedLc} / ${recordedRatio}:1` : "";
    process.stdout.write(
      `  ${mark} ${label.padEnd(32)} Lc ${lc.toFixed(1).padStart(5)}` +
        `  ${ratio.toFixed(3).padStart(6)}:1 (${ratioUnquantised.toFixed(3)} unquantised)` +
        `  needs Lc ${target.lc} / ${target.wcag}:1${note}\n`,
    );
  }

  for (const { label, a, b, min } of SEPARATIONS) {
    const delta = Math.abs(lightnessOf(palettes[theme], a) - lightnessOf(palettes[theme], b));
    const passes = delta >= min - 1e-9;
    if (!passes) failures += 1;
    process.stdout.write(
      `  ${passes ? "ok  " : "FAIL"} ${label.padEnd(32)} ΔL ${delta.toFixed(3)}` +
        `                (needs ${min})\n`,
    );
  }
}

process.stdout.write(
  `\n${failures === 0 ? "all targets met" : `${failures} target(s) missed`}` +
    `; ${drifts} value(s) drifted from docs/design/design-system.md\n`,
);

if (failures > 0) {
  process.stdout.write(
    "\nA missed target is a palette decision, not a build break. Do not edit a token to\n" +
      "make this pass — the values are the design doc's, and changing one silently puts\n" +
      "the code and the doc out of step. Raise it, decide it, then change both.\n",
  );
}

process.exitCode = failures === 0 ? 0 : 1;
