/**
 * F4 T2.10b, AC48: with chat's panel open, screens lay out by the width they are given.
 *
 * Every parameter-free route in the console at the main widths the panel leaves, at 1024,
 * 1280 and 1440 px windows: no horizontal scroll outside a table, diagram or code block,
 * and the panel pushes the screen rather than covering it. No CI workflow runs it; F4 Step 8
 * ran it on a native stack with one worker (status.md). The walk is paced to one person's
 * reads (`support/paced-reads.ts`), so a width takes about three minutes.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { CONSOLE_SCREENS } from "./support/console-screens";
import { CHAT_NEEDS_SELF_HOSTED, isHosted } from "./support/deployment";
import { paceReads } from "./support/paced-reads";

const WIDTHS = [1024, 1280, 1440];
// A screen's own reads, the panel opening, and three measurements.
const SECONDS_A_SCREEN = 10;
// Places whose own scroll is the point: wide tables, diagrams and code.
const SCROLLS_ON_PURPOSE = "table, svg, pre, code, [data-scrolls]";

for (const width of WIDTHS) {
  test(`at ${width}px with the panel open, no screen scrolls sideways outside a table, diagram or code`, async ({
    page,
  }) => {
    test.skip(await isHosted(page), CHAT_NEEDS_SELF_HOSTED);
    test.setTimeout(CONSOLE_SCREENS.length * SECONDS_A_SCREEN * 1000);
    await page.setViewportSize({ width, height: 900 });
    const readsAllowed = paceReads(page);
    await signUp(page, newAccount());
    for (const path of CONSOLE_SCREENS) {
      await readsAllowed();
      await page.goto(path);
      // The button waits for the session, so it is awaited, never sampled at once.
      const ask = page.getByRole("button", { name: /^(Ask|Answering…|Answer ready)$/ });
      await expect(ask, path).toBeVisible();
      await ask.click();
      const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
      await expect(panel).toBeVisible();

      const main = page.locator("main");
      const pushed = await main.evaluate((element) => element.getBoundingClientRect().right);
      const panelLeft = await panel.evaluate((element) => element.getBoundingClientRect().left);
      // Soft, so one run lists every screen that fails rather than the first.
      expect
        .soft(pushed, `${path} at ${width}px: the panel covers the screen`)
        .toBeLessThanOrEqual(panelLeft + 1);

      const overflowing = await main.evaluate((element, allowed) => {
        return (
          [...element.querySelectorAll<HTMLElement>("*")]
            .filter((node) => node.scrollWidth > node.clientWidth + 1)
            // A scroll is what a person can move: clipped text (`truncate`, `sr-only`) is not one.
            .filter((node) => ["auto", "scroll"].includes(getComputedStyle(node).overflowX))
            // A table, diagram or code block scrolls on purpose, and so does a box only they
            // stick out of.
            .filter((node) => !node.closest(allowed))
            .filter((node) => {
              const edge = node.getBoundingClientRect().right + 1;
              return [...node.querySelectorAll("*")].some(
                (inner) => inner.getBoundingClientRect().right > edge && !inner.closest(allowed),
              );
            })
            .map((node) => `${node.tagName.toLowerCase()}.${node.className}`.slice(0, 120))
        );
      }, SCROLLS_ON_PURPOSE);
      expect.soft(overflowing, `${path} at ${width}px`).toEqual([]);
      expect
        .soft(
          await main.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
          `${path} at ${width}px: main scrolls sideways`,
        )
        .toBe(true);
    }
  });
}
