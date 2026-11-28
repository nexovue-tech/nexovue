/**
 * F4 T2.11: one question, end to end, in the real panel, against T2.0's fake provider.
 *
 * Needs the dev stack with `--profile fake-provider` and `scheduler-insight` running, and an
 * organization connected to it (OpenAI-compatible, http://172.30.250.10:8080/v1), AI and chat
 * on and consented, **with services reporting in the last half hour**: a step that read no
 * rows cannot be cited, so the fake's answer would be withheld. No CI workflow runs it; F4
 * Step 8 ran it on a native stack (status.md). The phone arm is AC48's at 375 px.
 *
 * F4 Step 3 adds: a step's link opened in a fresh session shows the same project and range
 * (AC22, T3.3a), and the panel by keyboard alone (AC50).
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

  // The step's own row while the question runs; the fake takes 3 s over its answer
  // (`FAKE_PROVIDER_ANSWER_SECONDS`), so the panel's 1.5 s reads see it live.
  await expect(panel.getByRole("listitem").filter({ hasText: "Reading services" })).toBeVisible({
    timeout: 60_000,
  });
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
  // The button waits for the session, so it is awaited before it is measured.
  await expect(ask).toBeVisible();
  const box = await ask.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  await ask.click();
  const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
  // `inert` is set on the shell around `main` (AppShell.tsx) and holds everything inside it;
  // the property reflects only an element's own attribute, so the subtree is asked.
  await expect
    .poll(() => page.locator("main").evaluate((element) => element.closest("[inert]") !== null))
    .toBe(true);
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

test("a step's link opened in a fresh session shows the same project and range", async ({
  page,
  browser,
}) => {
  await signIn(page, EMAIL, PASSWORD);
  await page.goto("/services");
  await page.getByRole("button", { name: "Ask" }).click();
  const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
  await panel.getByRole("button", { name: "What is failing right now?" }).click();
  await panel.getByRole("button", { name: "Send" }).click();
  await expect(panel.getByText(/Read \d+ things?/)).toBeVisible({ timeout: 60_000 });
  await panel.getByText(/Read \d+ things?/).click();
  const href = await panel
    .getByRole("link", { name: "Open what it read" })
    .first()
    .getAttribute("href");
  expect(href).toContain("project=");

  // A colleague's browser: no stored window, no chosen project.
  const fresh = await browser.newContext();
  const colleague = await fresh.newPage();
  await signIn(colleague, EMAIL, PASSWORD);
  await colleague.goto(href ?? "/");
  const project = new URL(href ?? "/", "http://x").searchParams.get("project");
  await expect(colleague.getByRole("combobox", { name: /project/i })).toHaveValue(project ?? "");
  if ((href ?? "").includes("from=")) {
    await expect(colleague.getByTestId("address-window")).toBeVisible();
  }
  await fresh.close();
});

test("by keyboard alone: open, ask, close with Escape keeping the draft, focus returns", async ({
  page,
}) => {
  await signIn(page, EMAIL, PASSWORD);
  await page.goto("/services");
  const ask = page.getByRole("button", { name: "Ask" });
  await ask.focus();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("complementary", { name: "Ask about your telemetry" });
  const box = panel.getByRole("textbox");
  await expect(box).toBeFocused();
  await page.keyboard.type("What deployed today?");
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  await expect(ask).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(box).toHaveValue("What deployed today?");
  await expect(panel.getByRole("status")).toHaveCount(1);
});
