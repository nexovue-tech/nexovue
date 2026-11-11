/**
 * F4 T2.13, AC4: with chat broken, every other screen works.
 *
 * "Broken": every `/api/v1/assistant/*` request answers 500 and the AI preview 503, as a
 * failing chat service would. Each parameter-free route in the console opens, with the panel
 * open beside it, and fails on an error screen or an empty page. **Written for the hardening
 * pass, not run** (F4 status.md): Playwright needs the stack.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

const ROUTES: string[] = JSON.parse(
  readFileSync(join(__dirname, "../../../apps/web/src/app/routes/console-routes.json"), "utf8"),
);
const SCREENS = ROUTES.filter(
  (path) => !path.includes("$") && !/^\/(admin|sign|forgot|reset|verify|accept)/.test(path),
);

test("every screen works with chat broken", async ({ page }) => {
  await page.route("**/api/v1/assistant/**", (route) =>
    route.fulfill({ status: 500, contentType: "application/problem+json", body: "{}" }),
  );
  await signUp(page, newAccount());
  for (const path of SCREENS) {
    await page.goto(path);
    const ask = page.getByRole("button", { name: "Ask" });
    if (await ask.isVisible()) await ask.click();
    const main = page.locator("main");
    await expect(main, path).not.toBeEmpty();
    // The route boundary's own headings (`shared/components/ErrorScreen.tsx`).
    await expect(main.getByText("This screen stopped working"), path).toHaveCount(0);
    await expect(main.getByText("The server could not answer"), path).toHaveCount(0);
  }
});
