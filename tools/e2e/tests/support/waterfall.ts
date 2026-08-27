/**
 * Asserting the waterfall's shape rather than its markup.
 *
 * A parent-child edge is not visible as an attribute — it is visible as behaviour:
 * collapsing an ancestor takes its descendants off the screen and undoing it brings them
 * back. That is the proof a person gets, so it is the proof the suite takes.
 */

import { expect, type Page } from "@playwright/test";

/**
 * The waterfall's identity line: the root span's service, then what it was called.
 * Both come from the trace itself, so a mislabelled root fails here.
 */
export async function expectRootSpan(
  page: Page,
  root: { service: string; name: string },
): Promise<void> {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`${root.service} ${root.name}`);
}

/**
 * Proves the named service's spans hang off the root rather than merely sharing a trace
 * id with it. Collapsing every parent leaves only root spans on screen; if this service's
 * spans go with them, they were underneath one.
 */
export async function expectDescendantService(page: Page, service: string): Promise<void> {
  const spansOfService = page.getByText(service, { exact: true });
  await expect(spansOfService.first()).toBeVisible();

  await page.getByRole("button", { name: "Collapse all" }).click();
  await expect(spansOfService).toHaveCount(0);

  await page.getByRole("button", { name: "Expand all" }).click();
  await expect(spansOfService.first()).toBeVisible();
}
