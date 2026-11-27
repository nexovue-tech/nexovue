/**
 * F20 T10.1: a panel is dragged to a new place in the builder, and the same order is
 * saved (AC144, FR22). Written, not run (hardening pass).
 *
 * What the jsdom tests cannot see, and this does, in a real browser at desktop width:
 * - the other panels hold still while one is dragged (the library's sliding preview
 *   overlapped panels in 21 of 40 mixed-width drags, `status.md`, *Drag trial*);
 * - the panel under the pointer is marked while it is over it;
 * - a panel dragged past the grid's edge never scrolls the page sideways (8 of 40);
 * - a drop says where the panel went in the page's one live region, the saved order is
 *   the dropped one, and the toolbar's buttons still move panels afterwards.
 */

import { expect, type Locator, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

const DRAG_STEPS = 25;
const SIDEWAYS_OVERSHOOT_PX = 400;
// @dnd-kit/core PointerSensor.detach: document click listeners removed after 50 ms; margin added.
const DROP_CLICK_GUARD_MS = 150;

const panels = (page: Page) => page.locator("section[data-panel-id]");

async function order(page: Page): Promise<string[]> {
  return panels(page).evaluateAll((sections) =>
    sections.map((section) => section.getAttribute("data-panel-id") ?? ""),
  );
}

async function positions(page: Page): Promise<{ id: string; x: number; y: number }[]> {
  return panels(page).evaluateAll((sections) =>
    sections.map((section) => {
      const box = section.getBoundingClientRect();
      return { id: section.getAttribute("data-panel-id") ?? "", x: box.x, y: box.y };
    }),
  );
}

async function centre(target: Locator): Promise<{ x: number; y: number }> {
  const box = await target.boundingBox();
  if (!box) throw new Error("a panel has no box");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

const pageScrollsSideways = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

test("a panel is dragged by its handle, the others hold still, and the same order saves", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1300, height: 1000 });
  await signUp(page, newAccount());
  await page.goto("/dashboards/new?template=start_from_overview");
  await expect(page.getByRole("heading", { name: "New dashboard", level: 1 })).toBeVisible();
  await page.getByLabel("Title").fill(`Dragged ${Date.now()}`);

  const before = await order(page);
  expect(before.length).toBeGreaterThanOrEqual(3);
  const [first, second, third] = before;
  const status = page.locator("[data-live-region]");

  // Down on the first panel's handle, then to the third panel in steps.
  const handle = page.locator(`section[data-panel-id="${first}"] [data-drag-handle]`);
  const from = await centre(handle);
  const to = await centre(page.locator(`section[data-panel-id="${third}"]`));
  const held = await positions(page);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let step = 1; step <= DRAG_STEPS; step += 1) {
    await page.mouse.move(
      from.x + ((to.x - from.x) * step) / DRAG_STEPS,
      from.y + ((to.y - from.y) * step) / DRAG_STEPS,
    );
  }

  // Mid-drag: the third panel is marked, and every other panel is where it was.
  await expect(page.locator(`section[data-panel-id="${third}"][data-drop-target]`)).toBeVisible();
  const during = await positions(page);
  for (const panel of during.filter((each) => each.id !== first)) {
    const was = held.find((each) => each.id === panel.id);
    expect(panel.x).toBeCloseTo(was?.x ?? Number.NaN, 0);
    expect(panel.y).toBeCloseTo(was?.y ?? Number.NaN, 0);
  }

  // Past the grid's right edge: the page does not scroll sideways.
  await page.mouse.move(1300 + SIDEWAYS_OVERSHOOT_PX, to.y);
  expect(await pageScrollsSideways(page)).toBe(false);
  await page.mouse.move(to.x, to.y, { steps: DRAG_STEPS });
  await page.mouse.up();

  const dropped = [second, third, first, ...before.slice(3)];
  expect(await order(page)).toEqual(dropped);
  await expect(status).toContainText("moved to position 3 of");
  await expect(page.locator("[data-drop-target]")).toHaveCount(0);

  // The buttons are still the keyboard's way, over the same order. @dnd-kit's pointer sensor
  // swallows every click for 50 ms after a drop, so the drop's own release is no click; no
  // person clicks that fast, but a test does.
  await page.waitForTimeout(DROP_CLICK_GUARD_MS);
  await page
    .getByRole("button", { name: /^Move .* earlier$/ })
    .nth(2)
    .focus();
  await page.keyboard.press("Enter");
  expect((await order(page)).slice(0, 3)).toEqual([second, first, third]);

  await page.getByRole("button", { name: "Save" }).click();
  await expect(page).toHaveURL(/\/dashboards\/[0-9a-f-]{36}$/);
  await page.reload();
  await expect(panels(page).first()).toBeVisible();
  const saved = await page
    .locator("section[data-panel-id]")
    .evaluateAll((sections) =>
      sections.map((section) => section.getAttribute("data-panel-id") ?? ""),
    );
  expect(saved.slice(0, 3)).toEqual([second, first, third]);
});

test("Escape cancels a drag and the order stays", async ({ page }) => {
  await page.setViewportSize({ width: 1300, height: 1000 });
  await signUp(page, newAccount());
  await page.goto("/dashboards/new?template=start_from_overview");
  await expect(page.getByRole("heading", { name: "New dashboard", level: 1 })).toBeVisible();

  const before = await order(page);
  const handle = page.locator(`section[data-panel-id="${before[0]}"] [data-drag-handle]`);
  const from = await centre(handle);
  const to = await centre(page.locator(`section[data-panel-id="${before[2]}"]`));
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: DRAG_STEPS });
  await page.keyboard.press("Escape");
  await page.mouse.up();

  expect(await order(page)).toEqual(before);
  await expect(page.locator("[data-live-region]")).toHaveText("Move cancelled");
});

test("a handle is not a tab stop, so the toolbars stay the page's only way by keyboard", async ({
  page,
}) => {
  await signUp(page, newAccount());
  await page.goto("/dashboards/new?template=start_from_overview");
  await expect(page.getByRole("heading", { name: "New dashboard", level: 1 })).toBeVisible();

  await expect(page.locator("[data-drag-handle]").first()).toHaveAttribute("aria-hidden", "true");
  await expect(page.locator("[data-drag-handle][tabindex]")).toHaveCount(0);
  await expect(page.locator('[role="toolbar"]').first()).toBeVisible();
});
