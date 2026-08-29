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
