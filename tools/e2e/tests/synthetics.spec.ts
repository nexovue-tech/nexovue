/**
 * P1-61: the synthetics screen, against a check the stack really probed.
 *
 * A customer defines a check, our prober fetches the target on a timer, and the screen
 * reports what it measured. Nothing here is seeded: the check is created through the
 * same API the screen tells people to use, and every number asserted below travelled
 * prober → Kafka → worker → ClickHouse → API → browser.
 *
 * The five phases are the point. "Your site is slow" and "your DNS is slow" are
 * different incidents belonging to different people, and a screen that shows only a
 * total cannot tell them apart — so the spec fails unless all five are on it.
 */

import { randomUUID } from "node:crypto";

import { expect, type Locator, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

/**
 * Public, stable and TLS-terminated. It has to be public: the prober refuses a private
 * or loopback address at the socket, so a check pointed at this stack would correctly
 * fail. TLS, because the certificate reading and the `tls` phase both need a handshake.
 */
const CHECK_TARGET = `https://example.com/?nexovue-e2e=${randomUUID().slice(0, 8)}`;

/** The floor the checks API enforces, and the fastest first probe available to us. */
const CHECK_INTERVAL_SECONDS = 60;

/**
 * Beat re-reads the checks table every 30s, and a fresh schedule entry then waits its
 * whole interval before firing — so the first probe is up to ~90s out, before the
 * publish and the ClickHouse write. Generous, because this must never be the flaky one.
 */
const FIRST_PROBE_TIMEOUT_MS = 210_000;

/** Every phase of an HTTPS request, in the order the request performs them. */
const PHASES = ["dns", "tcp", "tls", "ttfb", "transfer"] as const;

/**
 * The whole path is prober → Kafka → worker → ClickHouse, and a silent screen is what
 * every break in it looks like. Naming them saves the next person the hunt.
 */
const NOTHING_MEASURED =
  "the check was created but nothing was measured against it — check that the scheduler is running (`just dev`) and that the worker is consuming";

/** Five shares rounded to whole percent can only drift half a point each. */
const SHARE_TOLERANCE_PERCENT = 3;
const WHOLE_PERCENT = 100;

test("a check defined for a new organisation is probed, listed and broken into phases", async ({
  page,
}) => {
  test.setTimeout(FIRST_PROBE_TIMEOUT_MS + 120_000);

  await signUpAndLand(page);
  const projectId = await onlyProjectId(page);
  const checkId = await createCheck(page, projectId, CHECK_TARGET);

  try {
    await page.goto("/synthetics");
    await expect(page.getByRole("heading", { name: "Synthetics", level: 1 })).toBeVisible();

    // The check exists and cannot have run yet — it is younger than its own interval.
    // The screen has to say that, and not "you have defined no checks".
    await expect(page.getByRole("heading", { name: "No probes in this window" })).toBeVisible();

    // The window advances on its own, so the list refetches without a reload.
    const row = page.getByRole("button").filter({ hasText: CHECK_TARGET });
    await expect(row, NOTHING_MEASURED).toBeVisible({ timeout: FIRST_PROBE_TIMEOUT_MS });
    // A fresh organisation sees its own check and nobody else's.
    await expect(row).toHaveCount(1);

    // Answered, and answered by the target: 200 is example.com's, not ours to invent.
    await expect(row).toContainText("up 200");
    await expect(row).toContainText("100.0%");
    // Read off the same handshake the phases were timed on.
    await expect(row).toContainText(/\d+ days/);

    const breakdown = page.locator("section").filter({ hasText: "Where the time goes" });
    await expect(breakdown.getByRole("heading", { name: "Where the time goes" })).toBeVisible();
    // The screen names the phase that dominated before anyone reads an axis.
    await expect(
      breakdown.getByText(/^(dns|tcp|tls|ttfb|transfer) is \d+% of the mean /),
    ).toBeVisible();

    for (const phase of PHASES) {
      await expect(breakdown.getByText(phase, { exact: true })).toBeVisible();
    }

    // The phases are the whole request, so their shares account for all of it. A
    // breakdown that lost a phase — or double-counted one — fails here.
    const shares = await legendSharePercents(breakdown);
    expect(shares).toHaveLength(PHASES.length);
    expect(sum(shares)).toBeGreaterThanOrEqual(WHOLE_PERCENT - SHARE_TOLERANCE_PERCENT);
    expect(sum(shares)).toBeLessThanOrEqual(WHOLE_PERCENT + SHARE_TOLERANCE_PERCENT);

    // The individual probes under the averages: at least the one we waited for.
    await expect(
      breakdown.getByText(/\d+ probes · oldest left · height is duration/),
    ).toBeVisible();
  } finally {
    // The stack is long-lived and shared. A check left behind would fetch example.com
    // once a minute forever. Soft, so a failed cleanup is reported without hiding why
    // the test failed in the first place.
    const removed = await page.request.delete(`/api/v1/checks/${checkId}`);
    expect.soft(removed.status(), `check ${checkId} was left probing the shared stack`).toBe(204);
  }
});

test("an organisation with no checks explains itself instead of failing", async ({ page }) => {
  await signUpAndLand(page);

  await page.goto("/synthetics");

  await expect(page.getByRole("heading", { name: "No checks are defined yet" })).toBeVisible();
  await expect(page.getByText("Checks could not be loaded")).toHaveCount(0);
  // "Nothing defined" and "defined but nothing measured" are different screens.
  await expect(page.getByRole("heading", { name: "No probes in this window" })).toHaveCount(0);
});

/**
 * A new organisation, signed in. `signUp` only submits the form — the session exists
 * once onboarding is on screen, and calling the API before then is a 401.
 */
async function signUpAndLand(page: Page): Promise<void> {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
}

/** The project signup created. One organisation, one project — anything else is news. */
async function onlyProjectId(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/projects");
  expect(response.status(), await response.text()).toBe(200);

  const { projects } = (await response.json()) as { projects: { id: string }[] };
  expect(projects).toHaveLength(1);
  return projects[0].id;
}

/** Defines the check the way the empty state tells a customer to: a POST to /checks. */
async function createCheck(page: Page, projectId: string, target: string): Promise<string> {
  const response = await page.request.post("/api/v1/checks", {
    data: {
      project_id: projectId,
      type: "http",
      target,
      interval_seconds: CHECK_INTERVAL_SECONDS,
    },
  });
  expect(response.status(), await response.text()).toBe(201);

  const check = (await response.json()) as { id: string; target: string };
  expect(check.target).toBe(target);
  return check.id;
}

/** The percentage each phase claims of the request, as the legend prints it. */
async function legendSharePercents(breakdown: Locator): Promise<number[]> {
  const rows = await breakdown.getByRole("listitem").allInnerTexts();
  return rows.map((row) => {
    const share = /(\d+)%\s*$/.exec(row.trim());
    if (share === null) throw new Error(`a legend row carried no share: "${row}"`);
    return Number(share[1]);
  });
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
