/**
 * M1's exit condition, as one journey.
 *
 * Someone signs up, is handed an endpoint and a key, exports a trace with them, and
 * watches the product notice it and draw it. Nothing is set up behind the product's
 * back: the credentials come off the onboarding screen, and the trace is read back
 * through the trace list a customer would use.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { crossServiceTrace, exportTrace } from "./support/otlp";
import { expectDescendantService, expectRootSpan } from "./support/waterfall";

/** The two services the exported trace crosses between. */
const STOREFRONT = { service: "e2e-storefront", name: "POST /orders" };
const PAYMENTS = { service: "e2e-payments", name: "orders.charge" };

/**
 * The worker batches on its own schedule, and onboarding backs its polling off. Both are
 * seconds, not minutes — but this is the assertion that must never be the flaky one.
 */
const FIRST_SPAN_TIMEOUT_MS = 90_000;

test("a span exported with a new account's key becomes a waterfall in its browser", async ({
  page,
  request,
}) => {
  test.setTimeout(FIRST_SPAN_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());

  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Listening for your first span" })).toBeVisible();

  // Exactly the two variables the screen just told the customer to set.
  const { endpoint, apiKey } = await readExportSettings(page);
  const trace = crossServiceTrace(STOREFRONT, PAYMENTS);
  await exportTrace(request, { endpoint, apiKey, batch: trace.batch });

  const arrival = page.getByRole("heading", { name: "Your first span arrived." });
  await expect(arrival).toBeVisible({ timeout: FIRST_SPAN_TIMEOUT_MS });
  // It names what arrived, so "something arrived" cannot pass for "yours arrived".
  await expect(page.getByText(STOREFRONT.service, { exact: true })).toBeVisible();
  await expect(page.getByText(STOREFRONT.name, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "See everything arriving" }).click();

  const listedTrace = page.getByRole("link").filter({ hasText: STOREFRONT.name });
  // A fresh organisation sees its own trace and no one else's.
  await expect(listedTrace).toHaveCount(1);
  await expect(listedTrace).toContainText(STOREFRONT.service);

  await listedTrace.click();
  await expect(page).toHaveURL(new RegExp(`/traces/${trace.traceId}$`));

  await expectRootSpan(page, STOREFRONT);
  await expect(page.getByText(PAYMENTS.name, { exact: true })).toBeVisible();
  // The edge that matters: the callee's span is a descendant of the caller's, in a
  // different service. A single-service trace could not produce this.
  await expectDescendantService(page, PAYMENTS.service);
});

test("a project with nothing in it explains itself instead of failing", async ({ page }) => {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();

  await page.goto("/traces");

  await expect(page.getByRole("heading", { name: "No traces in this window" })).toBeVisible();
  await expect(page.getByText("Traces could not be loaded")).toHaveCount(0);
});
