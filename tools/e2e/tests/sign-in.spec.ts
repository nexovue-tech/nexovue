/**
 * The returning user: the path everyone takes on every day that is not their first.
 *
 * This one reads the seeded organisation rather than creating an account, because that
 * is the point of it. It only reads, so runs cannot contend over it — and it asserts
 * nothing about *which* service is emitting, because that is a property of whoever has
 * the dev stack pointed at the seed, not of the product.
 */

import { expect, type Page, test } from "@playwright/test";

import { SEEDED_USER, TRACE_LIST_COLUMNS } from "./support/stack";

const NOTHING_ARRIVING =
  "the seeded organisation has no traces in the last hour — start something that exports, e.g. `just dev-tools`";

test("a returning user signs in and reaches the traces already arriving", async ({ page }) => {
  await signIn(page, SEEDED_USER.email, SEEDED_USER.password);

  await expect(page).toHaveURL(/\/traces$/);
  await expect(page.getByRole("heading", { name: "Traces", level: 1 })).toBeVisible();

  for (const column of TRACE_LIST_COLUMNS) {
    await expect(page.getByText(column, { exact: true }), NOTHING_ARRIVING).toBeVisible();
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

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}
