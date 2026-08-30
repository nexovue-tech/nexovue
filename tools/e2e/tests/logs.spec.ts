/**
 * The logs screen, as two journeys — the stored half and the live half.
 *
 * The stored half is the search box and the severity dial: a project's lines are
 * exported, then narrowed twice, and each narrowing has to remove something that was
 * visible a moment earlier.
 *
 * The live half is a WebSocket, and it is the part worth proving. A line arriving on a
 * screen nobody reloaded could equally have come from the ten-second window tick
 * refetching the stored search, so that endpoint is answered as empty for the whole of
 * the tail test: everything below is real — receiver, worker, ClickHouse, socket — but a
 * line on screen can then only have come down the socket.
 */

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import {
  exportLogs,
  logBatch,
  type ReportedLog,
  SEVERITY_ERROR,
  SEVERITY_INFO,
} from "./support/otlp";

const SERVICE = "e2e-orders";

const NOTICE = line("shipment label printed for order", SEVERITY_INFO, "INFO");
const FAILURE = line("shipment label printer refused the job", SEVERITY_ERROR, "ERROR");
/** Neither matched by the search below nor by the severity floor — the control group. */
const UNRELATED = line("warmed the pricing cache", SEVERITY_INFO, "INFO");

const SEARCHED_TEXT = "shipment label";

/** Nothing stored ever says this, so anything on screen carrying it arrived live. */
const TAIL_ONLY_TEXT = "live tail proof";

/** The pipeline is Kafka and a ClickHouse insert; both are seconds, neither is instant. */
const STORED_TIMEOUT_MS = 60_000;

/** The feed polls once a second and primes ten seconds back; a frame is not instant. */
const TAIL_TIMEOUT_MS = 30_000;

test("searching narrows the stored lines, and severity narrows them again", async ({
  page,
  request,
}) => {
  test.setTimeout(STORED_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();

  const { endpoint, apiKey } = await readExportSettings(page);

  // The empty state is asserted here rather than in a test of its own: a second test
  // would mean a second organisation, and organisations are paced one at a time against
  // the auth allowance (support/auth-quota.ts). Same org, same moment, so it is honest.
  await page.goto("/logs");
  await expect(page.getByRole("heading", { name: "No log lines match" })).toBeVisible();
  await expect(page.getByText("Logs could not be loaded")).toHaveCount(0);

  await exportLogs(request, {
    endpoint,
    apiKey,
    batch: logBatch(SERVICE, [NOTICE, FAILURE, UNRELATED]).batch,
  });

  const written = page.getByText(UNRELATED.body, { exact: true });
  await expect(written).toBeVisible({ timeout: STORED_TIMEOUT_MS });
  await expect(page.getByText(NOTICE.body, { exact: true })).toBeVisible();
  await expect(page.getByText(FAILURE.body, { exact: true })).toBeVisible();

  await page.getByLabel("Search message text").fill(SEARCHED_TEXT);

  // The unmatched line goes; the two that contain the phrase stay.
  await expect(written).toHaveCount(0);
  await expect(page.getByText(NOTICE.body, { exact: true })).toBeVisible();
  await expect(page.getByText(FAILURE.body, { exact: true })).toBeVisible();

  await page.getByLabel("Severity").selectOption({ label: "Error+" });

  await expect(page.getByText(NOTICE.body, { exact: true })).toHaveCount(0);
  await expect(page.getByText(FAILURE.body, { exact: true })).toBeVisible();
  // The caption counts what is on screen, so it fails if the filter merely hid a row.
  await expect(page.getByText(/^1 line from the last hour, newest first/)).toBeVisible();
});

test("a line exported while the screen is open arrives over the tail", async ({
  page,
  request,
}) => {
  test.setTimeout(TAIL_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);

  await answerStoredSearchWithNothing(page);
  const tailFrames = collectTailFrames(page);

  await page.goto("/logs");
  await page.getByLabel("Search message text").fill(TAIL_ONLY_TEXT);

  await expect(page.getByRole("heading", { name: "No log lines match" })).toBeVisible();
  // "Pause" is offered only while the socket is open and sending.
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();

  // A window property dies with the document, so it surviving means nothing reloaded.
  await markThisDocument(page);

  const arriving = line(`${TAIL_ONLY_TEXT} for order accepted`, SEVERITY_INFO, "INFO");
  await exportLogs(request, { endpoint, apiKey, batch: logBatch(SERVICE, [arriving]).batch });

  await expect(page.getByText(arriving.body, { exact: true })).toBeVisible({
    timeout: TAIL_TIMEOUT_MS,
  });
  expect(await documentMark(page), "the page reloaded, so this proves nothing").toBe(MARK);
  expect(tailFrames.join("\n"), "the line was on screen but no frame carried it").toContain(
    arriving.body,
  );
});

function line(body: string, severityNumber: number, severityText: string): ReportedLog {
  return { body, severityNumber, severityText };
}

/**
 * Holds the stored half of the screen at empty.
 *
 * Not a stub of the thing under test: the tail is a different endpoint on a different
 * transport, and this is what stops a stored-search refetch being mistaken for it.
 */
async function answerStoredSearchWithNothing(page: Page): Promise<void> {
  await page.route("**/api/v1/logs/search**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ logs: [], next_cursor: null }),
    }),
  );
}

const MARK = "e2e-tail-first-document";

interface MarkedWindow extends Window {
  __nexovueE2eMark?: string;
}

async function markThisDocument(page: Page): Promise<void> {
  await page.evaluate((mark) => {
    (window as MarkedWindow).__nexovueE2eMark = mark;
  }, MARK);
}

async function documentMark(page: Page): Promise<string | undefined> {
  return page.evaluate(() => (window as MarkedWindow).__nexovueE2eMark);
}

/** Every text frame the tail socket delivers, in arrival order. */
function collectTailFrames(page: Page): string[] {
  const frames: string[] = [];
  page.on("websocket", (socket) => {
    if (!socket.url().includes("/logs/tail")) return;
    socket.on("framereceived", (frame) => frames.push(frame.payload.toString()));
  });
  return frames;
}
