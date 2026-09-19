/**
 * Continuous profiling (F17), as one journey: a new organization's empty Profiles
 * screen, a CPU profile sent through the profiles door with its own key, the flame
 * graph that profile draws, a search over it, and the samples on the usage screen.
 *
 * Fixture-driven rather than profiler-driven, so it runs anywhere the stack does:
 * the eBPF profiler needs Linux privileges a CI runner does not grant. The real
 * profiler's payloads are the converter's testdata instead.
 */

import { expect, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { readExportSettings } from "./support/onboarding";
import { exportProfiles, heavyProfileBatch, profileBatch } from "./support/otlp";

const SERVICE = "e2e-checkout";

/** 60 % of the whole under `sha256.block`, 40 % under `json.Marshal`. */
const STACKS = [
  {
    frames: ["crypto/sha256.block", "main.(*Cart).Checkout", "main.main"],
    nanoseconds: 60_000_000,
  },
  {
    frames: ["encoding/json.Marshal", "main.(*Cart).Checkout", "main.main"],
    nanoseconds: 40_000_000,
  },
];

/** Kafka, the worker's batch and a ClickHouse insert: seconds, never instant. */
const STORED_TIMEOUT_MS = 60_000;

test("a profile sent with a new account's key becomes a flame graph, searchable and metered", async ({
  page,
  request,
}) => {
  test.setTimeout(STORED_TIMEOUT_MS * 2 + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);

  await page.goto("/profiles");
  await expect(page.getByRole("heading", { name: "No profiles yet" })).toBeVisible();
  await expect(page.getByText("Preview", { exact: true })).toBeVisible();

  await exportProfiles(request, { endpoint, apiKey, batch: profileBatch(SERVICE, STACKS) });

  // The screen refetches on its own; no reload is what a customer would do either.
  // The answer leads, in words (principles rule 1); the figures sit under it.
  const heading = page.getByRole("heading", {
    name: "crypto/sha256.block accounts for 60 % of e2e-checkout's CPU time — more than any other function.",
  });
  await expect(heading).toBeVisible({ timeout: STORED_TIMEOUT_MS });
  await expect(page.getByText("CPU time · 100 ms across 2 samples")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "crypto/sha256.block, 60 % of the total" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "encoding/json.Marshal, 40 % of the total" }),
  ).toBeVisible();

  await page.getByLabel("Search functions").fill("sha256");
  await expect(page.getByText("1 frame matches “sha256” — 60 % of the total.")).toBeVisible();

  // Zooming into the caller keeps it in the trail and widens its callees.
  await page.getByRole("button", { name: "main.(*Cart).Checkout, 100 % of the total" }).click();
  await expect(page.getByRole("navigation", { name: "Zoom" })).toContainText(
    "main.(*Cart).Checkout",
  );

  // Two samples accepted is two samples metered (AC13) — shown, and not billed.
  await expect(async () => {
    await page.goto("/settings/usage");
    await expect(page.getByText("2 · not billed in preview")).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: STORED_TIMEOUT_MS });
});

/*
 * Found by the founder: with a large profile, scrolling to the end of the functions
 * table kept scrolling the whole page and dragged the sidebar and top bar away. A
 * screen-reader caption inside a scroller that was not positioned was placed against
 * the page and stretched it past the window.
 */
test("scrolling to the end of a large profile moves the screen, never the app frame", async ({
  page,
  request,
}) => {
  test.setTimeout(STORED_TIMEOUT_MS + 60_000);

  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
  const { endpoint, apiKey } = await readExportSettings(page);
  await exportProfiles(request, {
    endpoint,
    apiKey,
    batch: heavyProfileBatch("orders-api", 3000, 45),
  });

  await page.goto("/profiles?service=orders-api");
  await expect(page.getByText("Show all 400 functions")).toBeVisible({
    timeout: STORED_TIMEOUT_MS,
  });

  await page.mouse.move(800, 500);
  for (let turn = 0; turn < 12; turn++) await page.mouse.wheel(0, 800);

  const page_ = await page.evaluate(() => ({
    tall: document.documentElement.scrollHeight,
    window: window.innerHeight,
    scrolled: window.scrollY,
  }));
  expect(page_.tall).toBe(page_.window);
  expect(page_.scrolled).toBe(0);
  await expect(page.getByRole("link", { name: "Profiles" })).toBeInViewport();
});
