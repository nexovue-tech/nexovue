// Fails when a built JavaScript chunk passes the ceiling (group 9, D3).
//
// Every route once shipped in one 1.3 MB file, so the first screen waited for all
// fifty-odd. Screens are now loaded when their route is first opened; this keeps a
// new eager import, or a dependency that grew, from quietly undoing that.
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const CEILING_BYTES = 500 * 1024;
const assets = new URL("../dist/assets/", import.meta.url).pathname;

const oversized = readdirSync(assets)
  .filter((name) => name.endsWith(".js"))
  .map((name) => ({ name, bytes: statSync(join(assets, name)).size }))
  .filter(({ bytes }) => bytes > CEILING_BYTES);

if (oversized.length > 0) {
  for (const { name, bytes } of oversized) {
    process.stderr.write(
      `${name}: ${Math.round(bytes / 1024)} kB, over ${CEILING_BYTES / 1024} kB\n`,
    );
  }
  process.stderr.write(
    "A chunk passed the ceiling. Load the screen lazily (app/router.tsx) or split it.\n",
  );
  process.exit(1);
}
process.stdout.write(`every chunk is under ${CEILING_BYTES / 1024} kB\n`);
