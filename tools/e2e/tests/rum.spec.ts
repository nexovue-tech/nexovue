/**
 * The browser door, through a real browser.
 *
 * This is M5's exit criterion and nothing else in the suite covers it. The SDK's 108
 * unit tests run in jsdom, which has no `navigator.sendBeacon`, no CORS preflight and
 * no `text/plain` distinction — so the three things that decide whether a script tag
 * works on a customer's site are exactly the three jsdom cannot check.
 *
 * The spec mints a **site key**, not a secret key. A site key is public by design: it
 * is checked against the `Origin` header, which a browser sets and page script cannot
 * forge, so the key is allowed to sit in the page.
 *
 * The page carrying the SDK is served from the web app's own origin. That is not a
 * convenience — the origin is what the receiver checks, so the bundle has to run
 * somewhere the site key actually names.
 *
 * Requires the compose stack up (`just dev`) **and the SDK built** — the bundle is read
 * off disk, so `pnpm --filter @nexovue/browser-sdk build` has to have run. The spec says
 * so by name rather than failing on a missing file.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { RECEIVER_ORIGIN, WEB_ORIGIN } from "./support/stack";

/** The minified bundle a customer would put in a script tag. */
const BUNDLE_PATH = fileURLToPath(
  new URL("../../../packages/browser-sdk/dist/nexovue-rum.min.js", import.meta.url),
);

const SITE_KEY_NAME = "e2e-browser-door";

/** Thrown from the instrumented page, so the Errors screen has something of ours. */
const PAGE_ERROR = "E2eBrowserDoorError: the checkout button exploded";

/** Kafka plus a ClickHouse insert plus the rollup. Seconds, not instants. */
const ARRIVAL_TIMEOUT_MS = 90_000;

function readBundle(): string {
  try {
    return readFileSync(BUNDLE_PATH, "utf8");
  } catch {
    throw new Error(
      `The browser SDK bundle is missing at ${BUNDLE_PATH}. ` +
        "Run `pnpm --filter @nexovue/browser-sdk build` before this spec.",
    );
  }
}

/** Mints a site key through the form a customer uses, and returns the public key. */
async function mintSiteKey(page: Page, allowedOrigin: string): Promise<string> {
  await page.goto("/settings/api-keys");

  // By field name, not by label: the secret-key form on this screen uses the same
  // label, and the two are deliberately separated by every signal except that one.
  await page.locator('input[name="site-key-name"]').fill(SITE_KEY_NAME);
  await page.locator('textarea[name="site-key-origins"]').fill(allowedOrigin);
  await page.getByRole("button", { name: "Create browser key" }).click();

  // The key is shown in full on its row — there is nothing to withhold about a
  // value that is meant to sit in a public page.
  const row = page.locator("li", { hasText: SITE_KEY_NAME });
  await expect(row).toBeVisible();
  const key = (await row.locator("code").first().innerText()).trim();
  expect(key, "the minted site key was empty").not.toEqual("");
  return key;
}

/**
 * Loads a page on an origin the site key allows, runs the real bundle on it, and
 * makes it produce both signals.
 *
 * `addScriptTag({ content })` evaluates in the page's own realm, so the requests the
 * SDK makes carry that page's Origin — which is the whole point of doing this here
 * rather than in a fixture.
 */
async function runSdkOnAPage(page: Page, siteKey: string): Promise<void> {
  // A real document on the allowed origin. The app's own sign-in page is served
  // from it and is the lightest thing there.
  await page.goto("/");

  await page.addScriptTag({ content: readBundle() });

  await page.evaluate(
    ([key, endpoint, message]) => {
      const sdk = (globalThis as Record<string, unknown>).nexovue as {
        init: (options: Record<string, unknown>) => { active: boolean; flush: () => void };
      };
      const running = sdk.init({ siteKey: key, endpoint, serviceName: "e2e-browser-door" });
      (globalThis as Record<string, unknown>).__nexovueRunning = running;
      // Thrown asynchronously so it reaches window.onerror as a page error would,
      // rather than failing this evaluate call.
      setTimeout(() => {
        throw new Error(message);
      }, 0);
    },
    [siteKey, RECEIVER_ORIGIN, PAGE_ERROR] as const,
  );

  // FCP and TTFB are reported on load; the SDK batches by time, so the flush is what
  // puts them on the wire inside this spec's patience.
  await page.waitForTimeout(1_000);
  await page.evaluate(() => {
    const running = (globalThis as Record<string, unknown>).__nexovueRunning as {
      flush: () => void;
    };
    running.flush();
  });

  // Hiding the page is what finalises CLS and LCP and triggers the unload beacon.
  // This is the sendBeacon path, which is the one jsdom can never exercise.
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(500);
}

test("a script tag on a real page reaches both the RUM and the errors screen", async ({ page }) => {
  test.setTimeout(ARRIVAL_TIMEOUT_MS + 90_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();

  // Both signals in one test, and both empty states asserted in the same organisation.
  // A second test would mean a second organisation, and those are paced one at a time
  // against the auth allowance (support/auth-quota.ts).
  await page.goto("/rum");
  await expect(page.getByRole("heading", { name: "No browser has reported yet" })).toBeVisible();
  await expect(page.getByText("Web vitals could not be loaded")).toHaveCount(0);

  const siteKey = await mintSiteKey(page, WEB_ORIGIN);
  await runSdkOnAPage(page, siteKey);

  // The screen's own words are the proof: they are written from the rollup, so they can
  // only appear if the reading crossed the receiver's CORS check, reached ClickHouse,
  // was folded into a quantile state and merged back out again.
  await expect(async () => {
    await page.goto("/rum");
    await expect(page.getByRole("heading", { name: "No browser has reported yet" })).toHaveCount(0);
    await expect(page.getByText(/readings/).first()).toBeVisible();
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });

  // The browser door's other signal, on the same batch. This failing while the vitals
  // passed would point at error capture specifically rather than at the door.
  await expect(async () => {
    await page.goto("/errors");
    await expect(page.getByText("E2eBrowserDoorError")).toBeVisible();
  }).toPass({ timeout: ARRIVAL_TIMEOUT_MS });
});
