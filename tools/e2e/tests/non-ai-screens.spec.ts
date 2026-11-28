/**
 * F3 T5.9 (AC48, A77): with the AI container stopped, every screen that is not AI's still
 * opens. A person signs up for an organization of their own, then each path the sidebar
 * offers (`route-manifest.ts`) and each settings screen (`settings-nav.ts`) is opened, except
 * `/settings/ai`, which says the container is not running. A screen fails on an
 * `InlineFailure` (the shared failure block), on an empty `main`, or on any 5xx the page
 * received.
 *
 * Run it with `just e2e-insight-stopped`, which stops `scheduler-insight` first. It does not
 * stop anything itself, so it is also a plain smoke test of every screen with the container up.
 * The paths are read from the two manifests, so a new screen is opened without an edit here.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { paceReads } from "./support/paced-reads";

const WEB = new URL("../../../apps/web/src/", import.meta.url);

/** What the screen has no business opening here: nexovue's operator console, and AI's own. */
const EXCLUDED = new Set(["/admin", "/settings/ai"]);

/** Every `path: "/…"` the two manifests declare, in order, without repeats. */
function manifestPaths(): string[] {
  const paths = new Set<string>();
  for (const file of ["app/routes/route-manifest.ts", "shared/settings/settings-nav.ts"]) {
    const source = readFileSync(fileURLToPath(new URL(file, WEB)), "utf8");
    for (const match of source.matchAll(/\bpath:\s*"(\/[^"]*)"/g)) {
      const path = match[1];
      if (path !== undefined && !EXCLUDED.has(path)) paths.add(path);
    }
  }
  return [...paths];
}

const PATHS = manifestPaths();
/** The shared `InlineFailure` block: a red left rule, with no role of its own to find it by. */
const INLINE_FAILURE = ".border-critical-solid.border-l-2";
const SETTLE_MS = 1500;

test("the manifests list screens to open", () => {
  // A parse that found nothing would pass the loop below having opened nothing.
  expect(PATHS.length).toBeGreaterThan(20);
  expect(PATHS).toContain("/services");
  expect(PATHS).toContain("/settings/members");
});

test("every screen that is not AI's opens with no failure block, no empty page and no 5xx", async ({
  page,
}) => {
  test.setTimeout(PATHS.length * 15_000);
  const failures: string[] = [];
  let current = "";
  page.on("response", (response) => {
    if (response.status() >= 500) {
      failures.push(`${current}: ${response.status()} from ${new URL(response.url()).pathname}`);
    }
  });

  // Paced to one person's reads, as `layout-widths.spec.ts` is: unpaced, the walk outran the
  // per-person read limit and a screen read as empty under its 429 (2026-11-28).
  const readsAllowed = paceReads(page);
  await signUp(page, newAccount());
  for (const path of PATHS) {
    await readsAllowed();
    current = path;
    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();
    await page.waitForTimeout(SETTLE_MS);
    const text = ((await page.locator("main").textContent()) ?? "").trim();
    if (text.length === 0) failures.push(`${path}: the page is empty`);
    if ((await page.locator(INLINE_FAILURE).count()) > 0) {
      failures.push(`${path}: an InlineFailure is showing`);
    }
  }
  expect(failures).toEqual([]);
});
