/**
 * F20 T6.8: the dashboard at 375 x 667 (AC68, AC100, AC101, AC108): no sideways scroll,
 * the sticky line and the "could not be read" count reachable while scrolling, runbook
 * links outside the actions menu, and every touch control at least 44 px.
 *
 * Written 2026-11-11 with plan Step 6 and not run: no dev stack was up, and a phone
 * hand check is the founder's. Deferred to F20's hardening pass.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { saveDashboard } from "./support/dashboard-fixture";

const PHONE = { width: 375, height: 667 };
const TOUCH_MIN_PX = 44;

test.use({ viewport: PHONE });

test("no sideways scroll, a menu for actions, links outside it, 44 px controls", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Phone ${Date.now()}`,
    service: "e2e-phone",
  });
  await page.goto(`/dashboards/${id}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  await expect(page.getByRole("link", { name: "Runbook" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy link" })).toBeHidden();
  await page.getByRole("button", { name: "Dashboard actions" }).click();
  await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();

  for (const name of ["Dashboard actions", "Copy link", "Full screen"]) {
    const box = await page.getByRole("button", { name }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(TOUCH_MIN_PX);
  }
  const pick = await page.getByRole("button", { name: "Pick a range" }).boundingBox();
  expect(pick?.height ?? 0).toBeGreaterThanOrEqual(TOUCH_MIN_PX);
});

test("the count stays reachable while the page scrolls, and its link moves focus (AC100)", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Count ${Date.now()}`,
    service: "e2e-phone",
  });
  await page.route("**/api/v1/dashboard-panels/read**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        correlation_id: "e2e",
        window_start: new Date(Date.now() - 3_600_000).toISOString(),
        window_end: new Date().toISOString(),
        panels: { p_rate: { state: "failed", kind: "service_trend" } },
      }),
    }),
  );
  await page.goto(`/dashboards/${id}`);
  const count = page.getByRole("button", { name: /panels? could not be read/ });
  await expect(count).toBeVisible();
  await page.mouse.wheel(0, 400);
  await expect(count).toBeInViewport();
  await count.click();
  await expect(page.locator("#panel-p_rate")).toBeFocused();
});
