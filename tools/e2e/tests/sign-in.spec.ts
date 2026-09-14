/**
 * The returning user: the path everyone takes on every day that is not their first.
 *
 * This one reads the seeded organisation rather than creating an account, because that
 * is the point of it. It only reads, so runs cannot contend over it — and it asserts
 * nothing about *which* service is emitting, because that is a property of whoever has
 * the dev stack pointed at the seed, not of the product.
 */

import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";

import { signIn } from "./support/accounts";
import { SEEDED_USER, TRACE_LIST_COLUMNS } from "./support/stack";

const NOTHING_ARRIVING =
  "the seeded organisation has no traces in the last hour — start something that exports: `just otel-demo up` for a real shop's traffic, or `just load` for a measured minute of it";

test("a returning user signs in and reaches the traces already arriving", async ({ page }) => {
  await signIn(page, SEEDED_USER.email, SEEDED_USER.password);

  await expect(page).toHaveURL(/\/traces$/);
  await expect(page.getByRole("heading", { name: "Traces", level: 1 })).toBeVisible();

  // Scoped to the column header row rather than matched across the page: "Took" is
  // also the duration filter's label above the list ("Took · over 100 ms"), and both
  // readings are correct, so an unscoped exact match resolves to two elements.
  await expect(traceListColumns(page), NOTHING_ARRIVING).toBeVisible();
  for (const column of TRACE_LIST_COLUMNS) {
    await expect(traceListColumns(page)).toContainText(column);
  }
  await expect(page.getByRole("heading", { name: "No traces in this window" })).toHaveCount(0);
});

test("a wrong password is answered on the form, not by navigating away", async ({ page }) => {
  await signIn(page, SEEDED_USER.email, `not-${SEEDED_USER.password}`);

  await expect(page.getByRole("alert")).toHaveText(
    "That email and password do not match an account.",
  );
  await expect(page).toHaveURL(/\/login$/);
});

/**
 * The trace list's column header row.
 *
 * Located by its own full text because the rows beneath it carry bare values with no
 * labels of their own — this row is what names their columns, so it is the one element
 * whose text is exactly the column names. `Spans` is hidden below the `sm` breakpoint
 * and is therefore optional.
 */
function traceListColumns(page: Page) {
  return page.locator("div").filter({ hasText: /^Request(Spans)?TookWhen$/ });
}
