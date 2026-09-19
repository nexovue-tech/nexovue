/**
 * F21: alerts, in the console — the default a new organization starts with, a channel
 * connected and tested, a rule made, and a real incident opened by a real check and
 * shown where a person looks (AC5, AC21, AC23, AC25, FR3, FR22, FR24).
 *
 * The incident test waits for the real prober and the real scheduler: the check runs,
 * the state change is recorded, the incident and its notification are written in the
 * same transaction, and the Alerts screen reads them back.
 */

import { randomUUID } from "node:crypto";

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

/**
 * An address on the machine running the stack that answers 404, reached by name so
 * the console accepts it, and allowed only because the development prober may reach
 * the Docker host. A check pointed at it is down by its own default assertion.
 */
const FAILING_TARGET = `http://host.docker.internal:18000/api/v1/nexovue-e2e-missing-${randomUUID().slice(0, 8)}`;

/**
 * Nothing listens here, so a send test through it fails with the transport's own reason
 * — the whole path runs, and nothing is posted to anybody else's server.
 */
const DEAD_WEBHOOK = "https://host.docker.internal:9/nexovue-e2e";

/** First run is up to ~90 s out (see synthetics.spec.ts); then a retry confirms it. */
const INCIDENT_TIMEOUT_MS = 240_000;

test("a new organization starts with a rule that emails its owners, and can add a channel and a rule of its own", async ({
  page,
}) => {
  await signUpAndLand(page);

  await page.goto("/alerts");
  await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();
  await expect(page.getByText("Nothing is open.")).toBeVisible();
  const rules = page.getByRole("region", { name: "Rules" });
  await expect(rules.getByText("Any check down")).toBeVisible();
  await expect(rules.getByText("Default")).toBeVisible();
  await expect(rules.getByText("To Organization owners")).toBeVisible();

  // A channel, connected and tested from its own screen.
  await page.goto("/settings/channels");
  await page.getByRole("button", { name: "Connect a channel" }).click();
  await page.getByLabel("Kind").selectOption("webhook");
  await page.getByLabel("Name").fill("E2E hook");
  await page.getByLabel("URL").fill(DEAD_WEBHOOK);
  await page.getByRole("button", { name: "Connect Webhook" }).click();
  // The signing secret, shown once.
  await expect(page.getByText(/^whsec_/)).toBeVisible();
  await expect(page.getByText(/shown now and never again/)).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("link", { name: /E2E hook/ }).click();
  await page.getByRole("button", { name: "Send test" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Not delivered:" })).toBeVisible({
    timeout: 20_000,
  });

  // A rule sending to it.
  await page.goto("/alerts/rules/new");
  await page.getByLabel("E2E hook").check();
  await page.getByLabel("Name").fill("Every check, to the hook");
  await page.getByRole("button", { name: "Create rule" }).click();
  await expect(page).toHaveURL(/\/alerts\/rules\/[0-9a-f-]{36}$/);

  await page.goto("/alerts");
  await expect(
    page.getByRole("region", { name: "Rules" }).getByText("Every check, to the hook"),
  ).toBeVisible();
});

test("a check that goes down opens an incident the Alerts screen and the check's page both show", async ({
  page,
}) => {
  test.setTimeout(INCIDENT_TIMEOUT_MS + 60_000);
  await signUpAndLand(page);

  await page.goto("/synthetics/new");
  await page.getByRole("link", { name: /^API One request/ }).click();
  await page.getByRole("textbox", { name: "URL" }).fill(FAILING_TARGET);
  await page.getByRole("button", { name: "Create check" }).click();
  await expect(page).toHaveURL(/\/synthetics\/[0-9a-f-]{36}$/);
  const checkId = page.url().split("/").pop() ?? "";

  try {
    const project = await onlyProjectId(page);
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`/api/v1/alerts/incidents?project_id=${project}`);
          return ((await response.json()) as { open: unknown[] }).open.length;
        },
        {
          message:
            "the check never opened an incident — check that scheduler-beat, scheduler-light and scheduler-deliveries are running",
          timeout: INCIDENT_TIMEOUT_MS,
          intervals: [10_000],
        },
      )
      .toBe(1);

    await page.goto("/alerts");
    const open = page.getByRole("region", { name: "Open now" });
    await expect(open.getByText("critical")).toBeVisible();
    await expect(open.getByText("is down")).toBeVisible();
    await expect(open.getByText(/Told: Organization owners/)).toBeVisible();

    await page.goto(`/synthetics/${checkId}`);
    const strip = page.getByRole("region", { name: "Alerts" });
    await expect(strip.getByText(/Open incident — critical/)).toBeVisible();
    await expect(strip.getByRole("link", { name: "Alert me about this" })).toBeVisible();
  } finally {
    await page.request.delete(`/api/v1/checks/${checkId}`);
  }
});

async function signUpAndLand(page: Page): Promise<void> {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
}

async function onlyProjectId(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/projects");
  expect(response.status(), await response.text()).toBe(200);
  const { projects } = (await response.json()) as { projects: { id: string }[] };
  return projects[0].id;
}
