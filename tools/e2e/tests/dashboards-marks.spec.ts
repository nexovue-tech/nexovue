/**
 * F20 T5.5: findings and releases drawn on a dashboard's charts, in a real browser (AC32's
 * and AC33's drawing halves, AC61's findings half).
 *
 * The data halves are proved without a canvas: `panel-marks.test.ts` (which marks each
 * kind draws), `window-strip.test.ts` (the strip), and `test_dashboards_live.py` (the
 * context answering with ClickHouse away). Here a reported deploy and a version only seen
 * draw their rules, a finding its shading, and with ClickHouse stopped the strip and notes
 * still draw. Stopping ClickHouse is the founder's step on the dev stack; this spec checks
 * what the page says when the batch answers 503.
 *
 * Written 2026-11-11 with plan Step 5 and not run: no dev stack was up.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const SERVICE = "e2e-dashboards-marks";
const ARRIVAL_TIMEOUT_MS = 90_000;

test("a deploy's rule and the strip draw, and the strip outlives the batch", async ({
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
      service: SERVICE,
      endpoint: "GET /orders",
      succeeded: { count: 30, durationMs: 90 },
      failed: { count: 3, durationMs: 300 },
    }),
  });
  const created = await page.request.post("/api/v1/dashboards", {
    data: {
      title: `Marks ${Date.now()}`,
      description: "",
      definition: {
        schema_version: 1,
        refresh_seconds: null,
        default_range: "1h",
        service_picker: false,
        links: [],
        panels: [
          {
            id: "p_rate",
            kind: "service_trend",
            title: null,
            width: 12,
            height: "large",
            settings: { service: SERVICE, signal: "rate" },
          },
          {
            id: "p_note",
            kind: "note",
            title: null,
            width: 12,
            height: "small",
            settings: { text: "Check the queue first." },
          },
        ],
      },
    },
  });
  expect(created.ok()).toBe(true);
  const { id } = (await created.json()) as { id: string };

  await expect(async () => {
    await page.goto(`/dashboards/${id}`);
    await expect(page.locator("#panel-p_rate canvas")).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });
  await expect(page.getByLabel("In this window")).toBeVisible();

  // The batch answering 503, as a cold ClickHouse makes it: the strip and note still draw.
  await page.route("**/api/v1/dashboard-panels/read**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({ type: "about:blank", title: "Unavailable", status: 503 }),
    }),
  );
  await page.reload();
  await expect(page.getByText("Could not read telemetry")).toBeVisible();
  await expect(page.getByLabel("In this window")).toBeVisible();
  await expect(page.locator("#panel-p_note")).toContainText("Check the queue first.");
});
