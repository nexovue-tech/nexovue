/**
 * F20 T5.6a: a hostile telemetry value on a dashboard, in a real browser (AC35, AC74).
 *
 * A service whose name holds markup and a right-to-left override reports traffic; a
 * dashboard shows it in a chart's heading and legend, a table and a top-N chart. Each
 * place must show the value as literal characters with the override made visible as
 * `[U+202E]`, and nothing may run. jsdom proves the render half in `just check`
 * (`step-5-panels.test.ts`, `DashboardView.test.ts`); only a browser draws the canvas.
 * Phone Safari is the founder's hand check, recorded in `status.md` (AC74).
 *
 * Written 2026-11-11 with plan Step 5 and not run: no dev stack was up.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const HOSTILE = "<img src=x onerror=window.__ran=1>‮checkout";
const ESCAPED = "<img src=x onerror=window.__ran=1>[U+202E]checkout";
const ARRIVAL_TIMEOUT_MS = 90_000;

function document(): Record<string, unknown> {
  const panel = (id: string, kind: string, settings: Record<string, unknown>) => ({
    id,
    kind,
    title: null,
    width: 6,
    height: "medium",
    settings,
  });
  return {
    title: `Hostile names ${Date.now()}`,
    description: "",
    definition: {
      schema_version: 1,
      refresh_seconds: null,
      default_range: "1h",
      service_picker: false,
      links: [],
      panels: [
        panel("p_chart", "service_trend", { service: HOSTILE, signal: "rate" }),
        panel("p_top", "top_services_trend", { signal: "rate", order: "worst", count: 5 }),
        panel("p_table", "services_table", { order: "worst", rows: 5 }),
        panel("p_endpoints", "endpoints_table", { service: HOSTILE, order: "worst", rows: 5 }),
      ],
    },
  };
}

test("a hostile service name shows as literal text everywhere, and nothing runs", async ({
  page,
  request,
}) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 60_000);
  await signUp(page, newAccount());
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportTrace(request, {
    endpoint,
    apiKey,
    batch: endpointTraffic({
      service: HOSTILE,
      endpoint: "GET /orders",
      succeeded: { count: 20, durationMs: 80 },
      failed: { count: 2, durationMs: 200 },
    }),
  });
  const created = await page.request.post("/api/v1/dashboards", { data: document() });
  expect(created.ok()).toBe(true);
  const { id } = (await created.json()) as { id: string };

  await expect(async () => {
    await page.goto(`/dashboards/${id}`);
    await expect(page.locator("#panel-p_table tbody")).toContainText(ESCAPED, { timeout: 5_000 });
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });

  for (const panelId of ["p_chart", "p_table", "p_endpoints"]) {
    await expect(page.locator(`#panel-${panelId}`)).toContainText(ESCAPED);
  }
  await page.locator("#panel-p_top canvas").hover();
  await expect(page.locator("#panel-p_top")).toContainText(ESCAPED);
  await expect(page.locator("main img[src='x']")).toHaveCount(0);
  expect(await page.evaluate(() => (window as { __ran?: number }).__ran)).toBeUndefined();
});
