/**
 * F20 T3.4: a dashboard built with the keyboard alone, in a real browser (AC37).
 *
 * Step 3's judging path: start a dashboard, add a panel with a real service, move it,
 * widen it, save, reload. Every action is a key press; the mouse is never used. A screen
 * reader pass is separate and recorded by a person (plan T11.7).
 */

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const SERVICE = "e2e-dashboards";
const ARRIVAL_TIMEOUT_MS = 90_000;
const SLOW = { count: 3, durationMs: 2400 };

/** Tab until focus is on what `matches` names, as a keyboard user would. */
async function tabTo(page: Page, name: string, limit = 80): Promise<void> {
  for (let presses = 0; presses < limit; presses += 1) {
    const label = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      return active?.getAttribute("aria-label") ?? active?.textContent?.trim() ?? "";
    });
    if (label === name) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Tab never reached ${name}`);
}

test("a dashboard is built, rearranged and saved with the keyboard alone", async ({
  page,
  request,
}) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 90_000);
  await signUp(page, newAccount());
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportTrace(request, {
    endpoint,
    apiKey,
    batch: endpointTraffic({
      service: SERVICE,
      endpoint: "GET /orders",
      succeeded: SLOW,
      failed: { count: 0, durationMs: 0 },
    }),
  });

  await page.goto("/dashboards/new?template=start_from_overview");
  await expect(page.getByRole("heading", { name: "New dashboard", level: 1 })).toBeVisible();
  const title = page.getByLabel("Title");
  await title.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(`Keyboard ${Date.now()}`);

  // Add a panel naming the real service, from the sheet, by keyboard.
  await expect(async () => {
    await tabTo(page, "Add a panel");
    await page.keyboard.press("Enter");
    const sheet = page.getByRole("dialog", { name: "Add a panel" });
    await expect(sheet.getByLabel("Service").locator(`option[value="${SERVICE}"]`)).toHaveCount(1);
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });
  const sheet = page.getByRole("dialog", { name: "Add a panel" });
  await sheet.getByLabel("Service").focus();
  await sheet.getByLabel("Service").selectOption(SERVICE);
  await tabTo(page, "Apply");
  await page.keyboard.press("Enter");
  await expect(sheet.getByText("Applied")).toBeVisible({ timeout: 30_000 });
  await tabTo(page, "Add to dashboard");
  await page.keyboard.press("Enter");

  // Focus lands on the new panel's toolbar; one tab stop, arrows along it.
  const name = `${SERVICE} · Rate`;
  const status = page.locator("[data-live-region]");
  await expect(status).toHaveText(`${name} added at position 4 of 4`);
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", `Move ${name} earlier`);
  await page.keyboard.press("Enter");
  await expect(status).toHaveText(`${name} moved to position 3 of 4`);
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", `Move ${name} earlier`);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", `Make ${name} wider`);
  await page.keyboard.press("Enter");
  await expect(status).toHaveText(`${name} is now 6 wide`);

  await tabTo(page, "Save");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/dashboards\/[0-9a-f-]{36}$/);

  await page.reload();
  const headings = page.getByRole("heading", { level: 2 });
  await expect(headings).toHaveText(["Rate", "Errors", name, "Latency"]);
});
