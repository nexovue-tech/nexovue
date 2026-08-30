/**
 * The errors screen, as one journey.
 *
 * A service throws the same exception three times; the screen has to collapse the three
 * into one row carrying the count and the span of time they cover, and opening that row
 * has to show the stack the throw actually carried.
 *
 * **The exception arrives as a log record, not as a span event.** That is not a
 * shortcut — it is the only path that works. `spans.exception_type` and
 * `spans.exception_message` exist as columns and nothing in the worker writes them
 * (P1-30, corrected in the plan on 2026-08-30), so the span half of the grouping query
 * has never returned a row. A spec built on a span event would fail for a reason that
 * has nothing to do with this screen.
 */

import { expect, type Locator, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { exportLogs, logBatch, type ReportedLog, SEVERITY_ERROR } from "./support/otlp";

const SERVICE = "e2e-checkout";
const EXCEPTION_TYPE = "E2eCheckoutError";

/**
 * No digits, no hex run, no address: the grouping key is the message with each of
 * those collapsed to a token, so a message that survives templating unchanged is the
 * message the screen must show back.
 */
const MESSAGE = "Card issuer refused the charge";

const STACKTRACE = [
  "Traceback (most recent call last):",
  '  File "checkout/charge.py", line 41, in charge',
  "    raise E2eCheckoutError(reason)",
  `${EXCEPTION_TYPE}: ${MESSAGE}`,
].join("\n");

/** Three throws, minutes apart, so first-seen and last-seen cannot be the same value. */
const THROWN_AGO_MS = [180_000, 120_000, 60_000] as const;

/** The pipeline is Kafka and a ClickHouse insert; both are seconds, neither is instant. */
const GROUPED_TIMEOUT_MS = 60_000;

test("three throws of one exception become one group with its stack", async ({ page, request }) => {
  test.setTimeout(GROUPED_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);

  // The empty state is asserted here rather than in a test of its own: a second test
  // would mean a second organisation, and the suite's signups share one rate limit.
  await page.goto("/errors");
  await expect(page.getByRole("heading", { name: "Nothing threw in this window" })).toBeVisible();
  await expect(page.getByText("Errors could not be loaded")).toHaveCount(0);

  const thrown = logBatch(SERVICE, THROWN_AGO_MS.map(exception));
  await exportLogs(request, { endpoint, apiKey, batch: thrown.batch });

  // The caption is the grouping claim in one sentence: one exception, three crashes.
  await expect(
    page.getByText(/^1 distinct exception · 3 occurrences in the last hour/),
  ).toBeVisible({ timeout: GROUPED_TIMEOUT_MS });

  const row = page.getByRole("button").filter({ hasText: EXCEPTION_TYPE });
  // A fresh organisation sees its own exception and no one else's.
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(MESSAGE);
  await expect(row).toContainText(SERVICE);

  await row.click();

  const panel = page.getByRole("complementary", { name: "Error detail" });
  await expect(entry(panel, "Occurrences")).toHaveText("3");
  await expect(entry(panel, "Services")).toHaveText(SERVICE);
  await expect(entry(panel, "Grouped by")).toHaveText("exception type and message");

  // The two ends of the group, and the proof they are ends: the oldest throw and the
  // newest, not the same instant twice.
  await expect(entry(panel, "First seen")).toHaveText(await asShown(page, thrown.writtenAt[0]));
  await expect(entry(panel, "Last seen")).toHaveText(await asShown(page, thrown.writtenAt[2]));

  const stack = panel.locator("section").filter({ hasText: "Stack trace" }).locator("pre");
  await expect(stack).toContainText("raise E2eCheckoutError(reason)");
  await expect(stack).toContainText('File "checkout/charge.py", line 41, in charge');
});

/** One throw, as an SDK that records exceptions on log records emits it. */
function exception(agoMs: number): ReportedLog {
  return {
    body: `${EXCEPTION_TYPE}: ${MESSAGE}`,
    severityNumber: SEVERITY_ERROR,
    severityText: "ERROR",
    agoMs,
    attributes: {
      "exception.type": EXCEPTION_TYPE,
      "exception.message": MESSAGE,
      "exception.stacktrace": STACKTRACE,
    },
  };
}

/** The value beside one of the panel's terms. */
function entry(panel: Locator, term: string): Locator {
  return panel.locator(`dt:text-is("${term}") + dd`);
}

/**
 * An instant as this browser writes it.
 *
 * The screen renders timestamps in the reader's locale and zone, so the expected text
 * has to be built in the same environment. What is being asserted is *which* instant is
 * shown, not how it is spelled.
 */
async function asShown(page: Page, iso: string): Promise<string> {
  return page.evaluate(
    (value) =>
      new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(
        new Date(value),
      ),
    iso,
  );
}
