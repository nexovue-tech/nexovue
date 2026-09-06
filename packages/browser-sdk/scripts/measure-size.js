import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, gzipSync } from "node:zlib";

/**
 * Weighs the artifact a script tag would serve. The budget is a gate, not a
 * note: about 70% of it is fixed by @opentelemetry/sdk-trace-web plus
 * web-vitals, so what is left for our own code is small enough to lose by
 * accident.
 */
const BUDGET_GZIP_BYTES = 25 * 1024;

const bundlePath = fileURLToPath(new URL("../dist/nexovue-rum.min.js", import.meta.url));

let bundle;
try {
  bundle = readFileSync(bundlePath);
} catch {
  console.error(`no bundle at ${bundlePath} — run \`pnpm run build\` first`);
  process.exit(1);
}

const gzip = gzipSync(bundle, { level: 9 }).byteLength;
const brotli = brotliCompressSync(bundle).byteLength;

console.error(
  `nexovue-rum.min.js  raw ${bundle.byteLength}  gzip ${gzip}  brotli ${brotli}  ` +
    `(budget ${BUDGET_GZIP_BYTES} gzip)`,
);

if (gzip > BUDGET_GZIP_BYTES) {
  console.error(`over the gzip budget by ${gzip - BUDGET_GZIP_BYTES} bytes`);
  process.exit(1);
}
