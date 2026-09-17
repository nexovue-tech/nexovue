/**
 * F18: synthetic checks, created, run, understood and removed in the console — nothing
 * through the API that a person could not do on screen (AC24, AC26, AC29).
 *
 * The first test waits for the real prober. Every number it asserts travelled
 * prober → Kafka → worker → ClickHouse → API → browser, and the state it reads was
 * written by the scheduler after the run was published.
 */

import { randomUUID } from "node:crypto";

import { expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

/**
 * Public, stable and TLS-terminated. It has to be public: the prober refuses a private
 * or loopback address at the socket, so a check pointed at this stack would correctly
 * fail. TLS, because the certificate reading and the `tls` phase both need a handshake.
 */
const TARGET = `https://example.com/?nexovue-e2e=${randomUUID().slice(0, 8)}`;

/**
 * Beat re-reads the checks table every 30s and staggers a new check's first run inside
 * its interval, so the first run is up to ~90s out, before the publish, the state write
 * and the ClickHouse insert. Generous, because this must never be the flaky one.
 */
const FIRST_RUN_TIMEOUT_MS = 240_000;

const PHASES = ["dns", "tcp", "tls", "ttfb", "transfer"] as const;

const NOTHING_MEASURED =
  "the check was created but no run was recorded — check that scheduler-beat and scheduler-light are running (`just dev`) and that the worker is consuming";

test("an API check created in the console runs, explains itself, and is edited, paused and deleted there", async ({
  page,
}) => {
  test.setTimeout(FIRST_RUN_TIMEOUT_MS + 180_000);
  await signUpAndLand(page);

  await page.goto("/synthetics");
  await expect(page.getByRole("heading", { name: "No checks yet" })).toBeVisible();
  await page.getByRole("link", { name: "New check" }).first().click();
  await page.getByRole("link", { name: /^API One request/ }).click();

  await page.getByRole("textbox", { name: "URL" }).fill(TARGET);
  await page.getByRole("button", { name: "Create check" }).click();

  // The new check's own page, before it has run: it says so rather than showing zeros.
  await expect(page).toHaveURL(/\/synthetics\/[0-9a-f-]{36}$/);
  const checkId = page.url().split("/").pop() ?? "";
  try {
    await expect(page.getByText("not judged yet")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Not run yet" })).toBeVisible();

    // Waited for through the checks API rather than by holding the live page open for
    // minutes: a live page reads telemetry every tick, and the suite's other specs share
    // this machine's telemetry quota.
    await page.goto("/synthetics");
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`/api/v1/checks/${checkId}`);
          return ((await response.json()) as { confirmed_state: string | null }).confirmed_state;
        },
        { message: NOTHING_MEASURED, timeout: FIRST_RUN_TIMEOUT_MS, intervals: [10_000] },
      )
      .toBe("up");
    await page.goto(`/synthetics/${checkId}`);
    await expect(page.getByText(/^up$/).first()).toBeVisible();

    // The run and what it saw: the default assertion, passed, against example.com's 200.
    const runs = page.getByRole("region", { name: "Runs" });
    await expect(runs.getByRole("button", { name: /passed/ }).first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(runs.getByText(/The run passed from local/)).toBeVisible();
    await expect(runs.getByText("✓ passed").first()).toBeVisible();

    // Where the time went: every phase of an HTTPS request is named.
    const breakdown = page.locator("section").filter({ hasText: "Where the time goes" });
    await expect(breakdown.getByRole("heading", { name: "Where the time goes" })).toBeVisible({
      timeout: 60_000,
    });
    for (const phase of PHASES) {
      await expect(breakdown.getByText(phase, { exact: true }).first()).toBeVisible();
    }

    // Edit: rename it, save, and the list shows the new name.
    await page.getByRole("tab", { name: "Settings" }).click();
    const name = page.getByRole("textbox", { name: "Name" });
    await name.fill("Example home page");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText(/^Saved\. The next run uses this/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Example home page", level: 1 })).toBeVisible();

    // Pause and resume, without losing anything.
    await page.getByRole("tab", { name: "Overview" }).click();
    // Exact: the shell's own "Pause refreshing" control is on the page too.
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByText("paused", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();

    await page.goto("/synthetics");
    const row = page.getByRole("link").filter({ hasText: "Example home page" });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("up");
    await expect(row).toContainText("API");

    // Delete states what is lost, in real numbers, before it can be confirmed (AC27).
    await row.click();
    await page.getByRole("button", { name: "Delete…" }).click();
    await expect(
      page.getByText(/removes its definition, \d+ recorded runs? of history and 0 stored files/),
    ).toBeVisible();
    await page.getByRole("button", { name: "Delete check" }).click();
    await expect(page).toHaveURL(/\/synthetics$/);
    await expect(page.getByRole("heading", { name: "No checks yet" })).toBeVisible();
  } finally {
    // The stack is shared and long-lived: a check left behind would call example.com
    // every minute for ever. A 404 means the test already deleted it.
    const removed = await page.request.delete(`/api/v1/checks/${checkId}`);
    expect
      .soft([204, 404], `check ${checkId} was left probing the shared stack`)
      .toContain(removed.status());
  }
});

test("Run now shows a failing assertion's expected and actual values and records nothing", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await signUpAndLand(page);

  await page.goto("/synthetics/new?kind=api");
  await page.getByRole("textbox", { name: "URL" }).fill(TARGET);
  await page.getByRole("tab", { name: /Assertions/ }).click();
  await page.getByRole("combobox", { name: "Assertion 1 comparison" }).selectOption("equals");
  await page.getByRole("textbox", { name: "Assertion 1 expected value" }).fill("201");
  await page.getByRole("button", { name: "Run now" }).click();

  const result = page.getByRole("region", { name: "Run now result" });
  await expect(
    result.getByText(/^The run failed from local: expected status code equals 201, got 200$/),
  ).toBeVisible({
    timeout: 90_000,
  });
  await expect(result.getByText("✗ failed")).toBeVisible();

  // Nothing was saved and nothing was recorded (AC24).
  const project = await onlyProjectId(page);
  const listed = await page.request.get(`/api/v1/checks?project_id=${project}`);
  expect(((await listed.json()) as { checks: unknown[] }).checks).toHaveLength(0);
});

test("every kind can be created, found by the Kind filter, paused, resumed and deleted in the console", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await signUpAndLand(page);
  const created: CreatedCheck[] = [];

  try {
    // API
    await page.goto("/synthetics/new?kind=api");
    await page.getByRole("textbox", { name: "URL" }).fill(TARGET);
    await page.getByRole("textbox", { name: "Name" }).fill("Every kind: API");
    await page.getByRole("button", { name: "Create check" }).click();
    created.push(await openedCheck(page, "api"));

    // TCP
    await page.goto("/synthetics/new?kind=tcp");
    await page.getByRole("textbox", { name: "Host" }).fill("example.com");
    await page.getByRole("textbox", { name: "Port" }).fill("443");
    await page.getByRole("button", { name: "Create check" }).click();
    created.push(await openedCheck(page, "tcp"));
    expect(created.at(-1)?.name).toBe("example.com:443");

    // DNS
    await page.goto("/synthetics/new?kind=dns");
    await page.getByRole("textbox", { name: "Domain name" }).fill("example.com");
    await page.getByRole("button", { name: "Create check" }).click();
    created.push(await openedCheck(page, "dns"));

    // Heartbeat: the URL is shown once, with the warning that it will not be again.
    await page.goto("/synthetics/new?kind=heartbeat");
    await page.getByRole("textbox", { name: "Name" }).fill("Nightly export");
    await page.getByRole("button", { name: "Create check" }).click();
    const heartbeat = page.getByRole("region", { name: "Heartbeat created" });
    await expect(heartbeat.getByText("Copy it now — it is not shown again.")).toBeVisible();
    const url = await heartbeat.locator("code").first().innerText();
    expect(url).toMatch(/\/api\/v1\/heartbeat\/[\w-]{20,}$/);
    // The job's call is accepted with no session at all.
    const ping = await page.request.post(new URL(url).pathname, { headers: { cookie: "" } });
    expect(ping.status()).toBe(200);
    await heartbeat.getByRole("link", { name: "Open the check" }).click();
    created.push(await openedCheck(page, "heartbeat"));

    // Multistep, with two requests
    await page.goto("/synthetics/new?kind=multistep");
    await page.getByRole("textbox", { name: "URL" }).fill("https://example.com/");
    await page
      .getByRole("list", { name: "Requests, in order" })
      .getByRole("button", { name: /^2\s/ })
      .click();
    await page.getByRole("textbox", { name: "URL" }).fill("https://example.com/?second");
    await page.getByRole("button", { name: "Create check" }).click();
    created.push(await openedCheck(page, "multistep"));

    // Each kind narrows the list to exactly its own check.
    for (const check of created) {
      await page.goto(`/synthetics?check_type=${check.kind}`);
      const rows = checkRows(page);
      await expect(rows, `the ${check.kind} filter`).toHaveCount(1);
      await expect(rows).toHaveAttribute("href", new RegExp(`/synthetics/${check.id}$`));
      for (const other of created.filter((candidate) => candidate !== check)) {
        await expect(
          page.getByText(other.name, { exact: true }),
          `the ${check.kind} filter hides ${other.kind}`,
        ).toHaveCount(0);
      }
    }

    for (const check of created) {
      await page.goto(`/synthetics/${check.id}`);
      // Exact: the shell's own "Pause refreshing" control is on the page too.
      await page.getByRole("button", { name: "Pause", exact: true }).click();
      await expect(page.getByRole("status").filter({ hasText: "Paused." })).toBeVisible();
      await page.getByRole("button", { name: "Resume", exact: true }).click();
      await expect(page.getByRole("status").filter({ hasText: "Resumed." })).toBeVisible();
      await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();

      await page.getByRole("button", { name: "Delete…" }).click();
      const remove = page.getByRole("button", { name: "Delete check" });
      await expect(remove, `deleting the ${check.kind} check`).toBeEnabled();
      await remove.click();
      await expect(page).toHaveURL(/\/synthetics$/);
      const gone = await page.request.get(`/api/v1/checks/${check.id}`);
      expect(gone.status(), `the ${check.kind} check was deleted`).toBe(404);
    }
    await expect(page.getByRole("heading", { name: "No checks yet" })).toBeVisible();
  } finally {
    // A check left behind would keep probing the shared stack; a 404 is one already deleted.
    for (const check of created) await page.request.delete(`/api/v1/checks/${check.id}`);
  }
});

test("a pasted cURL command becomes a check, with its token kept as a secret", async ({ page }) => {
  test.setTimeout(120_000);
  await signUpAndLand(page);

  await page.goto("/synthetics/import");
  await page
    .getByRole("textbox", { name: "cURL command" })
    .fill(
      `curl -X POST ${TARGET} -H 'Authorization: Bearer e2e-token-value' -H 'Content-Type: application/json' -d '{"a": 1}'`,
    );
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByText(/1 check/).first()).toBeVisible();
  await page.getByRole("button", { name: /^Create/ }).click();

  await expect(page).toHaveURL(/\/synthetics$/);
  const project = await onlyProjectId(page);
  const checks = (await (
    await page.request.get(`/api/v1/checks?project_id=${project}`)
  ).json()) as {
    checks: Array<{ id: string; config: unknown }>;
  };
  try {
    expect(checks.checks).toHaveLength(1);
    // The token is a secret, not text in the check.
    expect(JSON.stringify(checks.checks[0].config)).not.toContain("e2e-token-value");
    const secrets = (await (
      await page.request.get(`/api/v1/projects/${project}/check-secrets`)
    ).json()) as {
      secrets: Array<{ name: string }>;
    };
    expect(secrets.secrets).toHaveLength(1);
    expect(JSON.stringify(secrets)).not.toContain("e2e-token-value");
  } finally {
    for (const check of checks.checks) await page.request.delete(`/api/v1/checks/${check.id}`);
  }
});

interface CreatedCheck {
  id: string;
  kind: string;
  name: string;
}

/** The check page a create lands on, read back as what the list will show. */
async function openedCheck(page: Page, kind: string): Promise<CreatedCheck> {
  await expect(page).toHaveURL(/\/synthetics\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  const heading = page.getByRole("heading", { level: 1 });
  return { id: page.url().split("/").pop() ?? "", kind, name: await heading.innerText() };
}

/** Rows of the check list: links to a check, not to New or Import. */
function checkRows(page: Page) {
  return page.locator(
    'a[href*="/synthetics/"]:not([href*="/synthetics/new"]):not([href*="/synthetics/import"])',
  );
}

/**
 * A new organisation, signed in. `signUp` only submits the form — the session exists
 * once onboarding is on screen, and calling the API before then is a 401.
 */
async function signUpAndLand(page: Page): Promise<void> {
  await signUp(page, newAccount());
  await expect(page.getByRole("heading", { name: "Send your first span" })).toBeVisible();
}

async function onlyProjectId(page: Page): Promise<string> {
  const response = await page.request.get("/api/v1/projects");
  expect(response.status(), await response.text()).toBe(200);
  const { projects } = (await response.json()) as { projects: { id: string }[] };
  expect(projects).toHaveLength(1);
  return projects[0].id;
}
