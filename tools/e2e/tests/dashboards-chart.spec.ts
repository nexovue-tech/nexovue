/**
 * F20 T4.10: thirty charts with one cursor, in a real browser (AC31's drawing half).
 *
 * jsdom has no canvas, so the data half lives in `TimePanel.test.ts` and
 * `trend-chart-options.test.ts`. This drives the drawing: moving the cursor over one
 * chart shows a value at that moment on all; one drag makes exactly one window change,
 * one address change and one batch read. Whether thirty is smooth is read from a Chrome
 * performance trace on the founder's machine, recorded in `status.md` with the machine;
 * a tap on a phone is the founder's hand check (plan, *What these tests cannot prove*).
 *
 * Address change: the dashboard's address carries the window from T6.2. Until then the
 * address must not change on a drag, and this spec says so; T6.2 turns that line round.
 */

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const SERVICE = "e2e-dashboards-chart";
const ARRIVAL_TIMEOUT_MS = 90_000;
const CHARTS = 30;
const TRAFFIC = { count: 40, durationMs: 120 };

/** Thirty charts of one service: one scan, thirty canvases. */
function thirtyCharts(): Record<string, unknown> {
  const signals = ["rate", "errors", "latency"];
  return {
    title: `Thirty charts ${Date.now()}`,
    description: "",
    definition: {
      schema_version: 1,
      refresh_seconds: null,
      default_range: "1h",
      service_picker: false,
      links: [],
      panels: Array.from({ length: CHARTS }, (_, index) => ({
        id: `p_${index + 1}`,
        kind: "service_trend",
        title: null,
        width: 4,
        height: "medium",
        settings: { service: SERVICE, signal: signals[index % signals.length] },
      })),
    },
  };
}

async function readoutOf(page: Page, panelId: string): Promise<string> {
  return (await page.locator(`#panel-${panelId} p.font-mono`).first().textContent()) ?? "";
}

test("one cursor across thirty charts, and one drag reads once", async ({ page, request }) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 120_000);
  await signUp(page, newAccount());
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportTrace(request, {
    endpoint,
    apiKey,
    batch: endpointTraffic({
      service: SERVICE,
      endpoint: "GET /orders",
      succeeded: TRAFFIC,
      failed: { count: 2, durationMs: 300 },
    }),
  });

  const created = await page.request.post("/api/v1/dashboards", { data: thirtyCharts() });
  expect(created.ok()).toBe(true);
  const { id } = (await created.json()) as { id: string };

  const reads: string[] = [];
  page.on("request", (sent) => {
    if (sent.url().includes("/api/v1/dashboard-panels/read")) reads.push(sent.url());
  });

  await expect(async () => {
    await page.goto(`/dashboards/${id}`);
    await expect(page.locator("#panel-p_1 canvas")).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });

  // The cursor over the first chart shows the same moment on a chart further down.
  const first = page.locator("#panel-p_1 .u-over");
  const box = await first.boundingBox();
  if (!box) throw new Error("the first chart has no plot");
  const resting = await readoutOf(page, "p_4");
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2);
  await expect.poll(() => readoutOf(page, "p_4")).not.toBe(resting);
  const [timeOnFirst] = (await readoutOf(page, "p_1")).split(" · ");
  const [timeOnFourth] = (await readoutOf(page, "p_4")).split(" · ");
  expect(timeOnFourth).toBe(timeOnFirst);

  // One drag: one window change and one batch read, however many charts are linked.
  const addressBefore = page.url();
  const readsBefore = reads.length;
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect.poll(() => reads.length).toBe(readsBefore + 1);
  await page.waitForTimeout(1_000);
  expect(reads.length).toBe(readsBefore + 1);
  const window = new URL(reads.at(-1) ?? "").searchParams;
  expect(window.get("from")).toMatch(/:00(\.000)?Z$/);
  expect(window.get("to")).toMatch(/:00(\.000)?Z$/);
  // Until T6.2 puts the window in the address, a drag must not change it.
  expect(page.url()).toBe(addressBefore);
});
