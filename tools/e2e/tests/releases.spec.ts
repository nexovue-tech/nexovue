/**
 * F28: releases, from traffic that carries `service.version` to the pages a person reads.
 *
 * A fresh organisation opens an empty list, exports two versions of one service, and the
 * minute-by-minute sync turns what the rollup saw into releases. The list, the release page
 * and the project's Deploys section are then read at desktop width and at 375px, where a
 * page that scrolls sideways is a defect (plan, *Gates*, Design QA).
 *
 * Nothing is arranged behind the product's back: credentials come off the onboarding screen
 * and every assertion is on text a person can see, except the horizontal-scroll check, which
 * reads the page's own width.
 */

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { sendSignedDeploy, sendVersionLoad } from "./support/releases";

const PHONE = { width: 375, height: 812 };
const SERVICE = "e2e-releases";
const OLD_VERSION = "build-1001";
const NEW_VERSION = "build-1002";

/** The sync runs each minute, and the rollup settles a minute behind the clock. */
const SYNC_TIMEOUT_MS = 4 * 60_000;

/** Every section has answered: the page is read as a person reads it, not mid-load. */
async function expectLoaded(page: Page): Promise<void> {
  await expect(page.getByRole("status", { name: /^(Loading|Reading)/ })).toHaveCount(0);
}

/**
 * Nothing sticks out past the right edge, unless it sits in a scroller made for that (a table
 * or code block). The page's own width is not enough: the console scrolls inside its shell.
 */
async function expectNoSidewaysScroll(page: Page, where: string): Promise<void> {
  const sticking = await page.evaluate((width) => {
    const insideScroller = (element: Element): boolean => {
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        const { overflowX } = getComputedStyle(parent);
        if (overflowX === "auto" || overflowX === "scroll") return parent !== document.body;
      }
      return false;
    };
    return [...document.querySelectorAll("body *")]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.right > width + 1 && !insideScroller(element);
      })
      .slice(0, 5)
      .map((element) => `${element.tagName.toLowerCase()}.${element.className}`.slice(0, 80));
  }, PHONE.width);
  expect(sticking, `${where} has elements past ${PHONE.width}px`).toEqual([]);
}

test("traffic with a version becomes releases, a release page, and a Deploys section", async ({
  page,
  request,
}) => {
  test.setTimeout(SYNC_TIMEOUT_MS + 120_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const credentials = await readExportSettings(page);

  await page.goto("/releases");
  await expect(page.getByRole("heading", { name: "Releases", level: 1 })).toBeVisible();
  await expect(page.getByText("No releases yet.")).toBeVisible();

  // The old version for eight minutes, then the new one for seven, with the new one failing
  // more. Seven settled minutes are enough for the comparison to read, not only to wait.
  for (const minutesAgo of [16, 15, 14, 13, 12, 11, 10, 9]) {
    await sendVersionLoad(request, credentials, {
      service: SERVICE,
      version: OLD_VERSION,
      minutesAgo,
      requests: 60,
      failures: 1,
    });
  }
  for (const minutesAgo of [8, 7, 6, 5, 4, 3, 2]) {
    await sendVersionLoad(request, credentials, {
      service: SERVICE,
      version: NEW_VERSION,
      minutesAgo,
      requests: 60,
      failures: 6,
    });
  }

  const newRow = page.getByRole("link", { name: new RegExp(`${SERVICE}\\s+${NEW_VERSION}`) });
  await expect(async () => {
    await page.reload();
    await expect(newRow).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: SYNC_TIMEOUT_MS, intervals: [10_000] });

  await expect(page.getByText("2 releases in this window on 1 service.")).toBeVisible();
  const oldRow = page.getByRole("link", { name: new RegExp(`${SERVICE}\\s+${OLD_VERSION}`) });
  await expect(oldRow).toBeVisible();
  // Seen in traffic is not a deploy: the row says where it learned of the release.
  await expect(newRow).toContainText("Seen in traffic");
  await expect(newRow).not.toContainText("Reported by your CI");

  await newRow.click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(NEW_VERSION);
  await expect(page.getByRole("region", { name: "Comparison" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Share of traffic by version" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Facts" })).toBeVisible();
  await expectLoaded(page);
  // Order, never cause (BR1): the page offers no verdict.
  await expect(page.getByText(/caused by|bad release|root cause/i)).toHaveCount(0);

  // Mint a secret in the console and report a deploy the way a CI recipe does.
  await page.goto("/settings/projects#deploys");
  await expect(page.getByRole("heading", { name: "Deploys" })).toBeVisible();
  await page.getByRole("button", { name: "Mint a secret" }).click();
  const minted = page.locator("div").filter({ hasText: "Copy this secret now." }).last();
  const secret = ((await minted.locator("code").first().textContent()) ?? "").trim();
  await page.getByRole("button", { name: "I have stored it" }).click();
  const webhookUrl = ((await page.locator("code.select-all").first().textContent()) ?? "").trim();
  const status = await sendSignedDeploy(request, {
    webhookUrl,
    secret,
    service: SERVICE,
    version: NEW_VERSION,
    deliveryId: `e2e-${Date.now()}`,
  });
  expect(status, "a signed deploy for a known version is accepted").toBeLessThan(300);
  await expect(page.getByText("Last deploy received")).toBeVisible({ timeout: 30_000 });

  // The phone: each F28 page that holds a table, a chart or a long identifier.
  await page.setViewportSize(PHONE);
  await page.goto("/releases");
  await expect(newRow).toBeVisible();
  await expectNoSidewaysScroll(page, "/releases");
  await newRow.click();
  await expect(page.getByRole("region", { name: "Comparison" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Facts" })).toBeVisible();
  await expectLoaded(page);
  await expectNoSidewaysScroll(page, "the release page");
  await page.goto("/settings/projects#deploys");
  await expect(page.getByRole("heading", { name: "Deploys" })).toBeVisible();
  await expect(page.getByText("Webhook URL")).toBeVisible();
  await expectLoaded(page);
  await expectNoSidewaysScroll(page, "the Deploys section");
});
