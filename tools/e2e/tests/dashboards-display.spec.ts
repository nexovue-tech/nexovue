/**
 * F20 T6.9, T6.10: full screen and the note's wording (AC76, AC119, AC142's browser half).
 *
 * Full screen hides the sidebar and top bar, and Escape and a visible "Leave full screen"
 * leave. Opening and using a dashboard makes requests only to the install's own address.
 *
 * Written 2026-11-11 with plan Step 6 and not run: no dev stack was up. Deferred to
 * F20's hardening pass.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { saveDashboard } from "./support/dashboard-fixture";

test("full screen hides the chrome, Tab reaches 'Leave full screen', Escape leaves (AC76)", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Wall ${Date.now()}`,
    service: "e2e-wall",
  });
  await page.goto(`/dashboards/${id}`);
  await page.getByRole("button", { name: "Full screen" }).click();
  await expect(page).toHaveURL(/display=1/);
  await expect(page.getByRole("navigation")).toBeHidden();

  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Leave full screen" })).toBeFocused();
  await expect(page.getByRole("button", { name: "Leave full screen" })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/display=1/);
  await expect(page.getByRole("navigation").first()).toBeVisible();
});

test("a note says who can read it and that text can be purged (AC119)", async ({ page }) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Note ${Date.now()}`,
    service: "e2e-wall",
  });
  await page.goto(`/dashboards/${id}`);
  const note = page.locator("#panel-p_note");
  await expect(note).toContainText("Everyone in this organization can read this.");
  await expect(note).toContainText("purge");
});

test("opening and using a dashboard requests only the install's own address (AC142)", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, { title: `Own ${Date.now()}`, service: "e2e-wall" });
  const origin = new URL(page.url()).origin;
  const foreign: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.origin !== origin) foreign.push(request.url());
  });
  await page.goto(`/dashboards/${id}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.goto(`/dashboards/${id}/edit`);
  await expect(page.getByText("Editing")).toBeVisible();
  expect(foreign).toEqual([]);
});
