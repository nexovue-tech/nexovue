/**
 * F20 T3.3: the builder's panel picker, in a real browser (AC74, AC134).
 *
 * A service named with a right-to-left override reaches the services list through the
 * real ingest path, and the picker shows it as a visible escape while the raw name stays
 * the option's value, so the panel it makes reads that exact service. Chromium only:
 * the repository's Playwright has one browser, Desktop Chrome, so phone Safari is the
 * founder's hand check, recorded in `docs/features/dashboards/status.md`.
 */

import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const RTL_OVERRIDE = "‮";
const ARRIVAL_TIMEOUT_MS = 90_000;
/** Slow traffic, so tail sampling keeps all of it and the rollup counts it. */
const SLOW = { count: 3, durationMs: 2400 };

test("the picker shows a hostile service name escaped, and the panel reads the raw name", async ({
  page,
  request,
}) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 60_000);
  const hostile = `pay${RTL_OVERRIDE}ment-${randomUUID().slice(0, 6)}`;
  const escaped = hostile.replace(RTL_OVERRIDE, "[U+202E]");

  await signUp(page, newAccount());
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportTrace(request, {
    endpoint,
    apiKey,
    batch: endpointTraffic({
      service: hostile,
      endpoint: "GET /pay",
      succeeded: SLOW,
      failed: { count: 0, durationMs: 0 },
    }),
  });

  await page.goto("/dashboards/new");
  await page.getByLabel("Title").fill("Picker check");
  await page.getByRole("button", { name: "Add a panel" }).click();
  const sheet = page.getByRole("dialog", { name: "Add a panel" });
  await expect(sheet).toBeVisible();

  // The list fills once the service has arrived; reopen until it has.
  const service = sheet.getByLabel("Service");
  await expect(async () => {
    await page.reload();
    await page.getByRole("button", { name: "Add a panel" }).click();
    await expect(service.locator("option", { hasText: escaped })).toHaveCount(1);
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });

  // Shown escaped, held raw.
  await expect(service.locator("option", { hasText: escaped })).toHaveAttribute("value", hostile);
  await expect(sheet).not.toContainText(RTL_OVERRIDE);
  await service.selectOption(hostile);
  await expect(sheet.getByRole("alert")).toContainText(`do not show on screen: ${escaped}`);

  await sheet.getByRole("button", { name: "Apply" }).click();
  await expect(sheet.getByText("Applied")).toBeVisible({ timeout: 30_000 });
  await sheet.getByRole("button", { name: "Add to dashboard" }).click();

  await expect(page.getByRole("heading", { name: `${escaped} · Rate`, level: 2 })).toBeVisible();
});
