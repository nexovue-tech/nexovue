/**
 * P1-61: the service map, drawn from real edges.
 *
 * Asserting that two service names appear would pass on a broken edge query — the node
 * list is derived from the edges, but a map with no arrows on it is the failure this
 * screen exists to avoid. So the assertion is the edge: it must be there, and it must
 * carry the call rate, the error rate and the latency of the calls that were actually
 * exported.
 *
 * The numbers are exact rather than approximate, and the load is chosen to make them so:
 * 60 calls over the screen's default one-hour window is exactly one call a minute, 6 of
 * them failing is exactly 10%, and every call is the same length so the edge's p95 is
 * that length and not an estimate of it.
 */

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { exportTrace, serviceDependency } from "./support/otlp";

/** The dependency under test: a storefront calling a payments service. */
const STOREFRONT = { service: "e2e-map-storefront", name: "POST /orders" };
const PAYMENTS = { service: "e2e-map-payments", name: "POST /charge" };

const LOAD = { calls: 60, failures: 6 };

/** What the API must compute from that load, as the screen spells it. */
const EXPECTED_RATE = "1/min";
/* One decimal place: `formatPercent` shows it so that a share too small to
   round to a whole number is not printed as zero. */
const EXPECTED_FAILURES = "6 failed · 10.0%";

/**
 * An edge is bucketed by the minute and cannot be written until that minute can no
 * longer change: its end, plus the pairing window, plus the late grace. That is around
 * eighty seconds of pipeline, not a slow test — the screen re-reads its window every ten
 * seconds on its own, so this waits on the map rather than on a clock.
 */
const EDGE_TIMEOUT_MS = 180_000;

test("a call between two services becomes an edge carrying its own numbers", async ({
  page,
  request,
}) => {
  test.setTimeout(EDGE_TIMEOUT_MS + 90_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);

  // The map is empty before the export, so the edge that appears is the one sent here
  // and not something already in the project.
  await page.goto("/service-map");
  await expectEmptyMap(page);

  const traffic = serviceDependency(STOREFRONT, PAYMENTS, LOAD);
  await exportTrace(request, { endpoint, apiKey, batch: traffic.batch });

  // The edge's latency is the duration of the calls just sent, which the map prints in
  // milliseconds at this magnitude.
  const expectedP95 = `${traffic.callDurationMs}ms`;

  // Both ends of the call are on the map, each carrying the traffic it saw. `out` and
  // `in` are the direction: a map that drew the arrow the other way would swap them.
  const caller = page.locator(".react-flow__node", { hasText: STOREFRONT.service });
  const callee = page.locator(".react-flow__node", { hasText: PAYMENTS.service });
  await expect(caller).toBeVisible({ timeout: EDGE_TIMEOUT_MS });
  await expect(caller).toContainText(`out ${EXPECTED_RATE}`);
  await expect(callee).toContainText(`in ${EXPECTED_RATE}`);
  await expect(callee).toContainText("6 failed");

  // The edge, and the three numbers it was drawn from. The label is what someone reads
  // off the map without hovering anything, which is why it is asserted and not a title.
  const edgeLabel = page.locator(".react-flow__edgelabel-renderer > div");
  await expect(edgeLabel).toHaveCount(1);
  await expect(edgeLabel).toContainText(EXPECTED_RATE);
  await expect(edgeLabel).toContainText(`p95 ${expectedP95}`);
  await expect(edgeLabel.getByText(EXPECTED_FAILURES, { exact: true })).toBeVisible();

  // The hover says the same in a sentence, and is the only place the kind of dependency
  // is in words: these calls were direct, not messages through a queue.
  await expect(edgeLabel).toHaveAttribute(
    "title",
    `${EXPECTED_RATE} · p95 ${expectedP95} · 6 failed (10.0%) · direct call`,
  );

  // And the sentence above the map counts what is drawn on it.
  await expect(
    page.getByText("2 services and 1 dependency in the last hour — 1 dependency dropping calls."),
  ).toBeVisible();
});

test("a project with no dependencies explains itself instead of failing", async ({ page }) => {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();

  await page.goto("/service-map");
  await expectEmptyMap(page);
});

/** No edges is a sentence on the screen, not a blank pane and not an error. */
async function expectEmptyMap(page: Page): Promise<void> {
  await expect(
    page.getByRole("heading", { name: "No cross-service calls in this window" }),
  ).toBeVisible();
  await expect(page.getByText("The service map could not be loaded")).toHaveCount(0);
}
