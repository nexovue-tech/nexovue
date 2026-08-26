// Copies the exact IBM Plex faces the design system uses out of the @fontsource
// packages and into ./fonts, which is what ships. Only the latin subset is taken:
// the product UI is English-only, and the other subsets triple the payload.
// Re-run after bumping the @fontsource devDependencies.

import { copyFileSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const fontsDirectory = join(packageRoot, "fonts");

/** Every face the type scale can reach. Anything not listed here cannot be used. */
const REQUIRED_FACES = [
  { family: "ibm-plex-sans", weight: 400, style: "normal" },
  { family: "ibm-plex-sans", weight: 400, style: "italic" },
  { family: "ibm-plex-sans", weight: 500, style: "normal" },
  { family: "ibm-plex-sans", weight: 600, style: "normal" },
  { family: "ibm-plex-mono", weight: 400, style: "normal" },
  { family: "ibm-plex-mono", weight: 500, style: "normal" },
  { family: "ibm-plex-serif", weight: 400, style: "normal" },
  { family: "ibm-plex-serif", weight: 600, style: "normal" },
];

const SUBSET = "latin";

function sourceFileFor({ family, weight, style }) {
  return join(
    packageRoot,
    "node_modules",
    "@fontsource",
    family,
    "files",
    `${family}-${SUBSET}-${weight}-${style}.woff2`,
  );
}

function targetFileFor({ family, weight, style }) {
  return join(fontsDirectory, `${family}-${SUBSET}-${weight}-${style}.woff2`);
}

rmSync(fontsDirectory, { recursive: true, force: true });
mkdirSync(fontsDirectory, { recursive: true });

let totalBytes = 0;
for (const face of REQUIRED_FACES) {
  const source = sourceFileFor(face);
  const target = targetFileFor(face);
  copyFileSync(source, target);
  totalBytes += statSync(target).size;
}

const copied = readdirSync(fontsDirectory).length;
process.stdout.write(`${copied} faces, ${Math.round(totalBytes / 1024)} KB total\n`);
