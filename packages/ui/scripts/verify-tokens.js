#!/usr/bin/env node
/**
 * Fails when a default framework colour appears anywhere in the source.
 *
 * The design system says only our tokens exist, and named this check as what
 * keeps that true. It did not exist: a probe of `bg-slate-500 text-gray-300`
 * passed every gate, with the only complaint being about formatting.
 *
 * That matters more as screens are added than it did with two of them. The
 * recognisable AI-generated look is not one bad decision — it is a default
 * palette arriving one component at a time over months, each addition
 * defensible on its own.
 *
 * This checks names, not aesthetics. It cannot tell a good screen from a bad
 * one; it can tell that a screen was built from our vocabulary.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";

const ROOT = new URL("../../..", import.meta.url).pathname;

// Where UI source lives. The token files themselves are the definition of the
// palette and are checked separately, by verify-contrast.
const SEARCHED = ["apps/web/src", "packages/ui/src"];
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".css", ".html"]);
const MARKUP = new Set([".ts", ".tsx", ".js", ".jsx", ".html"]);

// Every default Tailwind colour family. Ours are `n-*`, `brand-*`, `sev-*` and
// `series-*`, so any of these names is a default that arrived from a generator
// or a copied snippet.
const DEFAULT_FAMILIES = [
  "slate",
  "gray",
  "zinc",
  "neutral",
  "stone",
  "red",
  "orange",
  "amber",
  "yellow",
  "lime",
  "green",
  "emerald",
  "teal",
  "cyan",
  "sky",
  "blue",
  "indigo",
  "violet",
  "purple",
  "fuchsia",
  "pink",
  "rose",
].join("|");

// The utilities a colour can be applied through.
const UTILITIES = [
  "bg",
  "text",
  "border",
  "ring",
  "fill",
  "stroke",
  "from",
  "to",
  "via",
  "shadow",
  "outline",
  "decoration",
  "divide",
  "accent",
  "caret",
  "placeholder",
].join("|");

const DEFAULT_COLOUR = new RegExp(
  `\\b(?:${UTILITIES})-(?:${DEFAULT_FAMILIES})-(?:50|\\d{3})\\b`,
  "g",
);

// Raw colour written straight into a component, bypassing the token layer
// entirely. OKLCH is how the tokens themselves are written, so it is allowed
// only in the files that define them.
const RAW_COLOUR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g;

const TOKEN_FILES = new Set(["packages/ui/tokens.css"]);

// The other direction, and the one that fails silently.
//
// A default colour is at least a colour. **A token name that was never declared
// produces no class at all**: Tailwind generates nothing, the element renders
// unstyled, and the typechecker and the linter are both content. It has happened
// twice — the metrics screen was written against `caution-*` when the palette
// says `warning-*`, and the projects list carried `hover:bg-surface-hover`, which
// is why that row had no hover and read as something a customer could not click.
const SEMANTIC_COLOUR = new RegExp(
  // A utility, an optional side (`border-l-`, `divide-y-`), then the name.
  `(?:^|[\\s"'\`])(?:[a-z-]+:)*(${UTILITIES})-(?:(?:[trblxy]|top|right|bottom|left|inline|block|start|end)-)?([a-z][a-z0-9]*(?:-[a-z0-9]+)*)`,
  "g",
);

// Names in these utilities' namespaces that are not tokens at all: Tailwind's own
// keywords, which it generates statically. Everything else must resolve in the
// namespace the utility reads, because tokens.css resets all five to `initial` —
// so `shadow-lg` and `text-slate-500` are equally dead.
//
// Sizes are deliberately absent. `text-sm` is declared under `--text-*` and is
// checked against it; listing `sm` here would have excused `shadow-sm`, which
// nothing declares.
const NOT_A_COLOUR = new Set(
  `left right center justify start end wrap nowrap balance pretty ellipsis clip none
   solid dashed dotted double hidden current transparent inherit black white auto
   offset collapse separate inset opacity width size wide wider widest tight tighter
   caps mono serif sans`.split(/\s+/),
);

/** Every name declared under one `--namespace-*` prefix in tokens.css. */
function declaredTokens(namespace) {
  const css = readFileSync(join(ROOT, "packages/ui/tokens.css"), "utf8");
  const declarations = css.matchAll(new RegExp(`--${namespace}-([a-z0-9-]+)\\s*:`, "g"));
  return new Set([...declarations].map((match) => match[1]));
}

// Two utilities take more than a colour: `shadow-overlay` names a shadow and
// `text-sm` a size, and both are declared in their own namespace.
const TOKENS = declaredTokens("color");
const SHADOWS = declaredTokens("shadow");
const SIZES = declaredTokens("text");
if (TOKENS.size === 0) {
  process.stderr.write("tokens.css declared no --color-* names; this check is checking nothing\n");
  process.exit(1);
}

function* sourceFiles(directory) {
  for (const entry of readdirSync(directory)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) yield* sourceFiles(path);
    else if (EXTENSIONS.has(extname(path))) yield path;
  }
}

const findings = [];
for (const searched of SEARCHED) {
  const directory = join(ROOT, searched);
  let files;
  try {
    files = [...sourceFiles(directory)];
  } catch {
    continue; // a package that does not exist yet is not a failure
  }

  for (const file of files) {
    const shown = relative(ROOT, file);
    const contents = readFileSync(file, "utf8");

    contents.split("\n").forEach((line, index) => {
      // A comment naming a colour is documentation, not a style.
      const code = line.replace(/\/\/.*$|\/\*.*?\*\//g, "");

      for (const match of code.matchAll(DEFAULT_COLOUR)) {
        findings.push({
          file: shown,
          line: index + 1,
          found: match[0],
          why: "a default framework colour; use a token (n-*, brand-*, sev-*, series-*)",
        });
      }
      if (TOKEN_FILES.has(shown)) return;

      // Only where Tailwind class names live. A `.css` file writes real CSS
      // properties — `border-right-color` reads exactly like a utility and a
      // name, and nothing here uses `@apply`, so there is no class to check.
      for (const [, utility, name] of MARKUP.has(extname(file))
        ? code.matchAll(SEMANTIC_COLOUR)
        : []) {
        // A bare number is a Tailwind scale step (`border-2`, `text-3`), never ours.
        if (NOT_A_COLOUR.has(name) || /^\d/.test(name) || TOKENS.has(name)) continue;
        if (utility === "shadow" && SHADOWS.has(name)) continue;
        if (utility === "text" && SIZES.has(name)) continue;
        // `border-b-2` and bare `border-b` can also be read as the names `b-2` and
        // `b`, because a side letter is itself name-shaped. No token is one letter
        // wide, so that reading is wrong.
        if (name.length === 1) continue;
        if (/^[a-z]-/.test(name)) continue;
        // A keyword carrying a scale step: `ring-offset-1`, `bg-opacity-50`.
        if (NOT_A_COLOUR.has(name.replace(/-\d+$/, ""))) continue;
        findings.push({
          file: shown,
          line: index + 1,
          found: name,
          why: "names no token in tokens.css, so the class is never generated and the element renders unstyled",
        });
      }

      for (const match of code.matchAll(RAW_COLOUR)) {
        findings.push({
          file: shown,
          line: index + 1,
          found: match[0],
          why: "a colour written into a component; it belongs in tokens.css",
        });
      }
    });
  }
}

if (findings.length > 0) {
  process.stderr.write(`\n${findings.length} colour(s) outside the token system:\n\n`);
  for (const { file, line, found, why } of findings) {
    process.stderr.write(`  ${file}:${line}  ${found}  — ${why}\n`);
  }
  process.stderr.write(
    "\nThe design system says only our tokens exist. A default palette arrives\n" +
      "one component at a time, which is why this is a gate and not a review note.\n\n",
  );
  process.exit(1);
}

process.stdout.write("tokens: no default or raw colours outside tokens.css\n");
