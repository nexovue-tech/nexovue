/**
 * P1-61: the services overview, as the one path a customer takes through it.
 *
 * A fresh organisation opens the screen before it has sent anything, exports a known
 * amount of traffic with the key onboarding just handed it, and reads its own service
 * back with the numbers that traffic implies — request rate, failures and the slow tail,
 * exactly as the columns print them. Then it opens the service, which is the only thing
 * the screen can be clicked for, and gets that endpoint's numbers underneath.
 *
 * Nothing is arranged behind the product's back: the credentials come off the onboarding
 * screen and every assertion is on text a person can see.
 */

import { expect, type Locator, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { endpointTraffic, exportTrace } from "./support/otlp";

const SERVICE = "e2e-services";
const ENDPOINT = "GET /checkout";

/**
 * The traffic, and why it is shaped like this.
 *
 * Tail sampling stores every failed trace and every trace at or over the 2s slow
 * threshold, and one healthy trace in ten. The rollup this screen reads is a
 * materialized view over stored spans, so healthy traffic reaches it at a tenth of its
 * real volume — measured: 30 healthy traces exported, 5 counted. Only traffic the
 * sampler is obliged to keep can be asserted on exactly, so every request here is either
 * slow or failed. See the P1-61 report: the sampled rollup is a product defect, and this
 * spec is written to state true numbers rather than to hide it.
 */
const SUCCEEDED = { count: 27, durationMs: 2400 };
const FAILED = { count: 3, durationMs: 9000 };

/** What those requests read as, once the screen has done its arithmetic. */
const REQUESTS = SUCCEEDED.count + FAILED.count;
const WINDOW_MINUTES = 60;
const CELLS = {
  /** 30 requests over the default one-hour window. */
  rate: String(REQUESTS / WINDOW_MINUTES),
  /** Count and share together: health is the absence of both. */
  failures: "3 · 10.0%",
  /** The 27 that returned are the middle of the distribution. */
  p50: "2.4s",
  /** The 3 that failed are its tail, so p95 and p99 are both the failure's duration. */
  tail: "9s",
};

/**
 * The export has to be batched, sampled and rolled up before it is queryable, and the
 * screen advances its own window every ten seconds. Both are seconds, not minutes — but
 * this is the assertion that must never be the flaky one.
 */
const ARRIVAL_TIMEOUT_MS = 90_000;

test("a service's traffic becomes its row on the overview, and its endpoint underneath", async ({
  page,
  request,
}) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);

  // Before anything is sent, the screen says so rather than showing an empty table or
  // a failure. This is the same organisation a moment earlier, so it is honestly empty.
  //
  // **"Never reported", not "nothing in this window".** T62 split the two: a project
  // that has never been sent anything is a setup problem, and a project that reported
  // last week and is silent now is an outage. A brand-new account is the first.
  await page.goto("/services");
  await expect(page.getByRole("heading", { name: "No services yet" })).toBeVisible();
  await expect(page.getByText("Services could not be loaded")).toHaveCount(0);

  await exportTrace(request, {
    endpoint,
    apiKey,
    batch: endpointTraffic({
      service: SERVICE,
      endpoint: ENDPOINT,
      succeeded: SUCCEEDED,
      failed: FAILED,
    }),
  });

  const serviceRow = page.getByRole("button").filter({ hasText: SERVICE });
  // A fresh organisation sees its own service and no one else's.
  await expect(serviceRow).toHaveCount(1, { timeout: ARRIVAL_TIMEOUT_MS });
  await expect(page.getByText("1 service reporting in the last hour, worst first.")).toBeVisible();

  await expectHealthCells(serviceRow);

  await serviceRow.click();
  await expect(serviceRow).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(ENDPOINT, { exact: true })).toBeVisible();
  // The kind is part of an endpoint's identity, so the row carries it.
  await expect(page.getByText("SERVER", { exact: true })).toBeVisible();
  // All of the service's traffic went to this one endpoint, so the endpoint line
  // repeats the service's own numbers. A second cell where there was one is the proof
  // the endpoint read ran and agreed — an endpoint drawn as a bare label could not.
  await expect(page.getByText(CELLS.failures, { exact: true })).toHaveCount(2);
  await expect(page.getByText(CELLS.rate, { exact: true })).toHaveCount(2);

  await serviceRow.click();
  await expect(serviceRow).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(ENDPOINT, { exact: true })).toHaveCount(0);
});

/** The four numbers the row is read for, each in its own column. */
async function expectHealthCells(row: Locator): Promise<void> {
  await expect(row.getByText(CELLS.rate, { exact: true })).toBeVisible();
  await expect(row.getByText(CELLS.failures, { exact: true })).toBeVisible();
  await expect(row.getByText(CELLS.p50, { exact: true })).toBeVisible();
  // p95 and p99 are the same duration here, and both columns must carry it.
  await expect(row.getByText(CELLS.tail, { exact: true })).toHaveCount(2);
}
