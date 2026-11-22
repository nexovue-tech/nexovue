/**
 * F4 T2.13, AC4: with chat broken, every other screen works.
 *
 * "Broken": every `/api/v1/assistant/*` request answers 500 and the AI preview 503, as a
 * failing chat service would. Each parameter-free route in the console opens, with the panel
 * open beside it, and fails on an error screen or an empty page. No CI workflow runs it; F4
 * Step 8 ran it on a native stack (status.md). The walk is paced to one person's reads
 * (`support/paced-reads.ts`).
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { CONSOLE_SCREENS } from "./support/console-screens";
import { paceReads } from "./support/paced-reads";

// A screen's own reads, the panel opening, and three checks.
const SECONDS_A_SCREEN = 10;

test("every screen works with chat broken", async ({ page }) => {
  test.setTimeout(CONSOLE_SCREENS.length * SECONDS_A_SCREEN * 1000);
  await page.route("**/api/v1/assistant/**", (route) =>
    route.fulfill({ status: 500, contentType: "application/problem+json", body: "{}" }),
  );
  const readsAllowed = paceReads(page);
  await signUp(page, newAccount());
  for (const path of CONSOLE_SCREENS) {
    await readsAllowed();
    await page.goto(path);
    // The button waits for the session, so it is awaited, never sampled at once.
    const ask = page.getByRole("button", { name: "Ask" });
    await expect(ask, path).toBeVisible();
    await ask.click();
    const main = page.locator("main");
    await expect(main, path).not.toBeEmpty();
    // The route boundary's own headings (`shared/components/ErrorScreen.tsx`).
    await expect(main.getByText("This screen stopped working"), path).toHaveCount(0);
    await expect(main.getByText("The server could not answer"), path).toHaveCount(0);
  }
});
