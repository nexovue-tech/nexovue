/**
 * The top bar at every width a customer uses: every control inside the screen,
 * live and with a pinned window.
 *
 * Found by the founder on a phone: the bar did not wrap, so the refresh controls, the
 * range picker and "Back to live" sat past the right edge where nobody could reach
 * them — at 390 px the bar was 274 px too wide, and 68 px on a tablet.
 */

import { expect, type Locator, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

const WIDTHS = [360, 390, 768, 1024, 1440];

test("every control in the top bar is on screen, at every width, live or pinned", async ({
  page,
}) => {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/services");
    const bar = page.locator("header").first();

    // Live, then pinned the way a customer pins one — through the bar's own controls,
    // which is also the proof that they can be reached at this width.
    await expect(bar.getByRole("button", { name: "Pick a range" })).toBeVisible();
    expect(await offScreen(bar), `${width}px, live`).toEqual([]);

    await bar.getByRole("button", { name: "Pick a range" }).click();
    await page.getByRole("button", { name: "Use this range" }).click();
    await expect(bar.getByRole("button", { name: "Back to live" })).toBeVisible();
    expect(await offScreen(bar), `${width}px, pinned`).toEqual([]);

    await bar.getByRole("button", { name: "Back to live" }).click();
  }
});

/** The text of every visible control or label in the bar that lies past its edges. */
function offScreen(bar: Locator): Promise<string[]> {
  return bar.evaluate((header) => {
    const edge = header.getBoundingClientRect();
    return [...header.querySelectorAll("button, select, span")]
      .filter((element) => element.children.length === 0 && (element.textContent ?? "").trim())
      .filter((element) => (element as HTMLElement).offsetParent !== null)
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return (
          box.right > edge.right + 1 || box.left < edge.left - 1 || box.bottom > edge.bottom + 1
        );
      })
      .map((element) => (element.textContent ?? "").trim());
  });
}
