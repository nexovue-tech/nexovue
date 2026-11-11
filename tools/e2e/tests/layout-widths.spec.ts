/**
 * F4 T2.10b, AC48: with chat's panel open, screens lay out by the width they are given.
 *
 * Every parameter-free route in the console at the main widths the panel leaves, at 1024,
 * 1280 and 1440 px windows: no horizontal scroll outside a table, diagram or code block,
 * and the panel pushes the screen rather than covering it. **Written for the hardening pass,
 * not run** (F4 status.md): Playwright needs the stack.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

const ROUTES: string[] = JSON.parse(
  readFileSync(join(__dirname, "../../../apps/web/src/app/routes/console-routes.json"), "utf8"),
);
const WIDTHS = [1024, 1280, 1440];
// Places whose own scroll is the point: wide tables, diagrams and code.
const SCROLLS_ON_PURPOSE = "table, svg, pre, code, [data-scrolls]";

const SCREENS = ROUTES.filter(
  (path) => !path.includes("$") && !/^\/(admin|sign|forgot|reset|verify|accept)/.test(path),
);

test("with the panel open, no screen scrolls sideways outside a table, diagram or code", async ({
  page,
}) => {
  await signUp(page, newAccount());
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of SCREENS) {
      await page.goto(path);
      const ask = page.getByRole("button", { name: /^(Ask|Answering…|Answer ready)$/ });
      if (await ask.isVisible()) await ask.click();
      const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
      await expect(panel).toBeVisible();

      const main = page.locator("main");
      const pushed = await main.evaluate((element) => element.getBoundingClientRect().right);
      const panelLeft = await panel.evaluate((element) => element.getBoundingClientRect().left);
      expect(pushed, `${path} at ${width}px: the panel covers the screen`).toBeLessThanOrEqual(
        panelLeft + 1,
      );

      const overflowing = await main.evaluate((element, allowed) => {
        return [...element.querySelectorAll<HTMLElement>("*")]
          .filter((node) => node.scrollWidth > node.clientWidth + 1)
          .filter((node) => getComputedStyle(node).overflowX !== "visible")
          .filter((node) => !node.closest(allowed))
          .map((node) => `${node.tagName.toLowerCase()}.${node.className}`.slice(0, 120));
      }, SCROLLS_ON_PURPOSE);
      expect(overflowing, `${path} at ${width}px`).toEqual([]);
      expect(
        await main.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        `${path} at ${width}px: main scrolls sideways`,
      ).toBe(true);
    }
  }
});
