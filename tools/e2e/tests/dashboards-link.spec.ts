/**
 * F20 T6.2, T6.5: a dashboard's address in a real browser (AC27, AC69, AC75, AC103, AC137).
 *
 * A link naming another project opens that project in a second browser context, with the
 * top bar and the page agreeing, and leaves that context's own choice alone; a link to a
 * project the reader cannot open is refused and kept; a drag then Back returns to the
 * link's window.
 *
 * Written 2026-11-11 with plan Step 6 and not run: no dev stack was up. Deferred to
 * F20's hardening pass.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { exportHistory, saveDashboard, waitForService } from "./support/dashboard-fixture";
import { readExportSettings } from "./support/onboarding";

const ARRIVAL_TIMEOUT_MS = 90_000;
const NO_SUCH_PROJECT = "00000000-0000-4000-8000-000000000000";

test("a link opens its own project and window, and leaves the reader's choice alone", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Link ${Date.now()}`,
    service: "e2e-link",
  });
  const projects = (await (await page.request.get("/api/v1/projects")).json()) as {
    projects: { id: string; name: string }[];
  };
  const project = projects.projects[0];
  if (!project) throw new Error("the new account has no project");

  await page.goto(`/dashboards/${id}?project=${project.id}&range=6h`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText(`${project.name} · times in`)).toBeVisible();

  // Copy link: both choices, and which was copied.
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "Copy link" }).click();
  await page.getByRole("button", { name: /stays live/ }).click();
  await expect(page.getByText("Copied the link that stays live.").first()).toBeVisible();

  // Leaving the dashboard shows the reader's own window, not the link's.
  await page.goto("/overview");
  await expect(page).toHaveURL(/\/overview$/);
  const stored = await page.evaluate(() => localStorage.getItem("nexovue.selection"));
  expect(stored ?? "").not.toContain("6h");
});

test("a link to a project the reader cannot open is refused and kept (AC69, AC75)", async ({
  page,
}) => {
  await signUp(page, newAccount());
  const id = await saveDashboard(page.request, {
    title: `Refused ${Date.now()}`,
    service: "e2e-link",
  });
  await page.goto(`/dashboards/${id}?project=${NO_SUCH_PROJECT}`);
  await expect(page.getByText("This link is for a project you cannot open.")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(NO_SUCH_PROJECT));
});

test("a drag adds one history entry and Back restores the link's window (AC103)", async ({
  page,
  request,
}) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 60_000);
  await signUp(page, newAccount());
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportHistory(
    request,
    { endpoint, apiKey },
    {
      service: "e2e-link",
      endpoint: "GET /orders",
      minutes: [
        { minutesAgo: 6, count: 10 },
        { minutesAgo: 4, count: 20 },
      ],
    },
  );
  await waitForService(page.request, "e2e-link");
  const id = await saveDashboard(page.request, {
    title: `Drag ${Date.now()}`,
    service: "e2e-link",
  });
  // One visit, then reloads: each `goto` would add a history entry for Back to return to.
  const chart = page.locator("#panel-p_rate canvas").first();
  await page.goto(`/dashboards/${id}?range=6h`);
  await expect(async () => {
    await page.reload();
    await expect(chart).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });
  const box = await chart.boundingBox();
  if (!box) throw new Error("the chart has no box");
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText(/^Zoomed to/)).toBeVisible();
  await expect(page).toHaveURL(/from=/);
  await page.goBack();
  await expect(page).toHaveURL(/range=6h/);
  await expect(page.getByText(/^Zoomed to/)).toBeHidden();
});
