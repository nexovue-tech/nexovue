/**
 * F4 T2.11: one question, end to end, in the real panel, against T2.0's fake provider.
 *
 * Needs the dev stack with `--profile fake-provider` and an organization connected to it
 * (OpenAI-compatible, http://172.30.250.10:8080/v1), AI and chat on and consented. **Written
 * for the hardening pass, not run** (F4 status.md): Playwright needs the stack, and no CI
 * workflow runs it. The phone arm is AC48's at 375 px.
 */

import { expect, test } from "@playwright/test";

import { signIn } from "./support/accounts";

const EMAIL = process.env.NEXOVUE_CHAT_E2E_EMAIL ?? "";
const PASSWORD = process.env.NEXOVUE_CHAT_E2E_PASSWORD ?? "";

test.beforeEach(() => {
  test.skip(
    !EMAIL || !PASSWORD,
    "needs NEXOVUE_CHAT_E2E_EMAIL and _PASSWORD for an organization connected to the fake provider",
  );
});

test("a question shows its steps live, then the answer whole, with its label and a link", async ({
  page,
}) => {
  await signIn(page, EMAIL, PASSWORD);
  await page.goto("/services");
  await page.getByRole("button", { name: "Ask" }).click();
  const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
  await panel.getByRole("button", { name: "What is failing right now?" }).click();
  await panel.getByRole("button", { name: "Send" }).click();

  await expect(panel.getByText("Reading services")).toBeVisible({ timeout: 60_000 });
  await expect(panel.getByText("Every service that reported is listed in step s1.")).toBeVisible({
    timeout: 60_000,
  });
  await expect(panel.getByText(/The model's wording|Counted from telemetry/)).toBeVisible();
  await panel.getByText(/Read \d+ things?/).click();
  await expect(panel.getByRole("link", { name: "Open what it read" })).toBeVisible();
});

test("on a phone, the Ask button fits, a link closes the panel and offers Back to answer", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 740 });
  await signIn(page, EMAIL, PASSWORD);
  await page.goto("/services");
  const ask = page.getByRole("button", { name: /^(Ask|Answer ready)$/ });
  const box = await ask.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await ask.click();
  const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
  await expect(page.locator("main")).toHaveJSProperty("inert", true);
  await panel.getByRole("button", { name: "What is failing right now?" }).click();
  await panel.getByRole("button", { name: "Send" }).click();
  await expect(panel.getByText("Every service that reported is listed in step s1.")).toBeVisible({
    timeout: 60_000,
  });
  await panel.getByText(/Read \d+ things?/).click();
  await panel.getByRole("link", { name: "Open what it read" }).first().click();
  await expect(panel).toBeHidden();
  await expect(page.getByRole("button", { name: "Back to answer" })).toBeVisible();
});
