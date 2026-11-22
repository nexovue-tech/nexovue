/**
 * F3's browser checks, at phone and desktop width (jsdom does no layout, so widths, target
 * sizes and focus are judged here):
 *
 * - AC17 (T5.8): the explanation section with a 500-character unbroken service name, 30
 *   facts and plainer words: no sideways scroll at 390 px, 44 px targets below `md`, the
 *   facts an `ol` with hidden "Basis:" text, focus on the first newly shown fact after "Show
 *   all", and at 1920 px no capped container.
 * - AC70 (T2.x): a service finding with a long title at 390 by 664: the header line fully
 *   visible, and a working link to the right service and window visible with no scrolling.
 * - AC90, AC103 (T4.7): `/settings/ai` with a 500-character address and model name, a long
 *   body and many runs; and with the AI container not running.
 *
 * The finding's and the settings' reads are answered from fixed data through `page.route`, so
 * the layout is judged on the longest values the page must hold. The sign-in and the shell
 * are the real stack. **Not part of `just check`**: it needs the dev stack, like the others.
 */

import { expect, type Locator, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";

const FINDING_ID = "0b9f3a52-6a55-4c1e-9d0e-3c5a7c1e2f10";
const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const STARTED = new Date(Date.now() - 20 * 60_000).toISOString();
const LONG_SERVICE = "checkout-".repeat(56).slice(0, 500);
const FACT_COUNT = 30;
const FIRST_FACTS = 5;
/** AC17: action targets below `md`. */
const TARGET_PX = 44;
const PHONE = { width: 390, height: 664 } as const;
const DESKTOP = { width: 1920, height: 1080 } as const;

const fact = (number: number) => ({
  id: `e${number}`,
  number,
  kind: "error_group",
  basis: "telemetry",
  sentence: [
    { name: LONG_SERVICE },
    { text: ` threw ${"TimeoutError".repeat(12)} ${number} times in the window.` },
  ],
  at: null,
  read_at: null,
  role: null,
  absence: null,
  read: null,
  is_cause: false,
  supports_cause: false,
  target: { kind: "error_group", group_id: `g${number}`, services: [LONG_SERVICE] },
});

const FACTS = Array.from({ length: FACT_COUNT }, (_, index) => fact(index + 1));

const explanation = {
  finding_id: FINDING_ID,
  state: "read",
  version: 3,
  latest_version: 3,
  read_at: STARTED,
  reads_failed: 0,
  facts: FACTS,
  likely_cause: null,
  cause_held: true,
  previous_cause: null,
  shared_cause_count: 0,
  wording_state: "done",
  wording: {
    model: "model-a",
    from_version: 3,
    from_read_at: STARTED,
    claims: [
      {
        type: "restates_fact",
        segments: [{ name: LONG_SERVICE }, { text: " threw many timeouts, as fact 1 says." }],
        evidence_ids: ["e1", "e2"],
      },
    ],
    picks: [],
  },
  ai_on: true,
  runs_left: 2,
  runs_reset_at: null,
  next_run_at: null,
  next_read_at: null,
  rating_reasons: ["not_useful", "bad_wording"],
  my_rating: null,
};

async function stubFinding(page: Page, named: { service?: string } = {}) {
  const service = named.service ?? LONG_SERVICE;
  const title = `${service} is slower than normal`;
  const finding = {
    id: FINDING_ID,
    project_id: PROJECT_ID,
    scope: "service",
    service_name: service,
    title,
    severity: "warning",
    state: "open",
    closed_reason: null,
    muted: false,
    service_snoozed_until: null,
    service_muted_until: null,
    started_at: STARTED,
    closed_at: null,
    acknowledged_at: null,
    signals: [],
    trend: [],
    series: [],
    incidents: [],
    services: [],
    release_context: {
      before: null,
      coverage: "deploys_and_versions",
      last_reported_at: null,
      first_heard_at: null,
      nearby: [],
      called_known: true,
      affected: [],
    },
    case_state: null,
    case_resolution: null,
    case_opened_at: null,
    case_resolved_at: null,
    root_finding_id: null,
    case_signal: "open",
    case_severity: "warning",
    open_joined_finding_id: null,
    open_joined_since: null,
    links: [],
  };
  const answers: Record<string, unknown> = {
    [`/api/v1/findings/${FINDING_ID}`]: finding,
    [`/api/v1/findings/${FINDING_ID}/timeline`]: { events: [], next_before: null },
    [`/api/v1/findings/${FINDING_ID}/explanation`]: explanation,
  };
  await page.route("**/api/v1/findings/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = answers[path];
    if (body === undefined) return route.continue();
    return route.fulfill({ json: body });
  });
}

async function sidewaysScroll(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/**
 * The narrowest `max-width` on any ancestor of `target` below `limit`, as a number, or null
 * when none caps it. "Cap sentences, never containers": a prose line may be capped, the
 * block that holds a list or a table may not. Percentages and `none` are no cap.
 */
async function containerCap(target: Locator, limit: number): Promise<string | null> {
  return target.evaluate((element, below) => {
    for (let node = element.parentElement; node !== null; node = node.parentElement) {
      const cap = getComputedStyle(node).maxWidth;
      if (cap === "none" || cap.endsWith("%")) continue;
      const pixels = Number.parseFloat(cap);
      if (cap.endsWith("px") && pixels < below) {
        return `${node.tagName.toLowerCase()}.${node.className} max-width ${cap}`;
      }
    }
    return null;
  }, limit);
}

async function heightOf(target: Locator): Promise<number> {
  return (await target.boundingBox())?.height ?? 0;
}

test("AC17: the explanation section fits 390 px, with 44 px targets, an ordered list and focus", async ({
  page,
}) => {
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  await stubFinding(page);
  await page.goto(`/findings/${FINDING_ID}`);

  const section = page.locator('section[aria-labelledby="finding-explanation"]');
  await expect(section).toBeVisible();
  const facts = section.locator("#explanation-facts ol");
  await expect(facts).toBeVisible();
  expect(await sidewaysScroll(page), "the page scrolls sideways").toBeLessThanOrEqual(0);

  // Facts are an ordered list, and each says its basis to a screen reader.
  await expect(facts.locator("> li")).toHaveCount(FIRST_FACTS);
  await expect(facts.locator("li").first().locator(".sr-only", { hasText: "Basis:" })).toHaveCount(
    1,
  );

  // Look next, and every fact's link, are at least 44 px high below `md`.
  const lookNext = page.locator("#finding-look-next").locator("xpath=..").getByRole("link");
  expect(await lookNext.count()).toBeGreaterThan(0);
  for (const link of await lookNext.all()) {
    expect(await heightOf(link), "a Look next link is under 44 px").toBeGreaterThanOrEqual(
      TARGET_PX,
    );
  }
  const factLinks = facts.getByRole("link");
  expect(await factLinks.count()).toBeGreaterThan(0);
  for (const link of await factLinks.all()) {
    expect(await heightOf(link), "a fact's link is under 44 px").toBeGreaterThanOrEqual(TARGET_PX);
  }

  // "Show all": focus lands on the first fact that was not shown before.
  await section.getByRole("button", { name: `Show all ${FACT_COUNT} facts` }).click();
  await expect(facts.locator("> li")).toHaveCount(FACT_COUNT);
  await expect(facts.locator(`li[data-fact="${FIRST_FACTS + 1}"]`)).toBeFocused();
  expect(
    await sidewaysScroll(page),
    "the page scrolls sideways after Show all",
  ).toBeLessThanOrEqual(0);
});

test("AC17: at 1920 px no container around the facts is capped", async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  await signUp(page, newAccount());
  await stubFinding(page);
  await page.goto(`/findings/${FINDING_ID}`);

  const section = page.locator('section[aria-labelledby="finding-explanation"]');
  const facts = section.locator("#explanation-facts ol");
  await expect(facts).toBeVisible();
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
  expect(await containerCap(facts, DESKTOP.width), "a container around the facts is capped").toBe(
    null,
  );
});

test("AC17: a citation opens the facts and moves to its fact", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  await stubFinding(page);
  await page.goto(`/findings/${FINDING_ID}`);

  const wording = page.getByTestId("explanation-wording");
  await expect(wording).toBeVisible();
  const citation = wording.getByRole("button", { name: /^Fact 2:/ });
  await citation.click();
  await expect(page.locator('li[data-fact="2"]')).toBeFocused();
});

// AC70 does not hold as built (status.md, open decision 22): the case and the signal controls
// come first on a phone (F22 D86, NFR8), so no link carrying the finding's window is in the
// first 664 px, and the header's service link carries no window. `test.fail` keeps the check
// honest: when the decision is taken and built, this passes and Playwright says to remove the line.
test("AC70: a long title at 390 by 664 leaves the header and one working link in view", async ({
  page,
}) => {
  test.fail(true, "open decision 22: AC70 conflicts with D86's one DOM order");
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  // About 80 characters, where the design starts to wrap names (design, *Layout*).
  const service = "payments-gateway-".repeat(5).slice(0, 80);
  await stubFinding(page, { service });
  await page.goto(`/findings/${FINDING_ID}`);

  const header = page.getByTestId("explanation-header");
  await expect(header).toBeVisible();
  const box = await header.boundingBox();
  expect(box, "the header line has no box").not.toBeNull();
  expect(
    (box?.y ?? 0) + (box?.height ?? 0),
    "the header line runs past the screen",
  ).toBeLessThanOrEqual(PHONE.height);
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);

  // At least one link to the right service, and the finding's window, is wholly in view.
  const links = page.locator("#finding-look-next").locator("xpath=..").getByRole("link");
  const shown: string[] = [];
  for (const link of await links.all()) {
    const rect = await link.boundingBox();
    if (rect === null) continue;
    const inView = rect.y >= 0 && rect.y + rect.height <= PHONE.height;
    const href = (await link.getAttribute("href")) ?? "";
    if (inView && href.includes("from=") && href.includes("to=")) shown.push(href);
  }
  expect(shown.length, "no link with the finding's window is fully visible").toBeGreaterThan(0);
});

// ------------------------------------------------------------------ /settings/ai (AC90, AC103)

const LONG_URL = `https://${"provider-gateway-".repeat(24)}.example.com/v1`.slice(0, 480);
const LONG_MODEL = `model/${"x".repeat(190)}`;

const connection = {
  wire_format: "openai_chat",
  base_url: LONG_URL,
  provider_host: `https://${"provider-gateway-".repeat(24)}.example.com`,
  address_kind: "public",
  model: LONG_MODEL,
  region_label: null,
  has_key: true,
  call_seconds: 30,
  monthly_token_limit: 2_000_000,
  price_input: null,
  price_output: null,
  checked_at: STARTED,
  checked_ok: true,
  test_current: true,
  model_changed: false,
  consent: {
    by_user_id: null,
    by_name: "An owner with a long name ".repeat(6),
    by_left: false,
    at: STARTED,
    current: true,
  },
};

const settings = (overrides: Record<string, unknown> = {}) => ({
  on: true,
  full: true,
  available: true,
  unavailable_reason: null,
  container_running: true,
  beat_lock_exposed: false,
  off_reason: null,
  missing: [],
  connection,
  formats_offered: ["openai_chat"],
  consent_fingerprint: "f".repeat(64),
  schema_version: 1,
  suggested_monthly_tokens: 2_000_000,
  ...overrides,
});

const RUNS = Array.from({ length: 40 }, (_, index) => ({
  id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
  started_at: STARTED,
  finished_at: STARTED,
  seconds: 2.4,
  purpose: "explain",
  outcome: "done",
  finding_id: FINDING_ID,
  provider_host: `https://${"provider-gateway-".repeat(24)}.example.com`,
  model: LONG_MODEL,
  input_tokens: 1200,
  output_tokens: 300,
  tokens_estimated: false,
  fields_sent: Array.from({ length: 12 }, (_, field) => `facts[].field_${field}_${"n".repeat(30)}`),
  by_person: index % 2 === 0,
  host_consent: "consented",
}));

async function stubAiSettings(page: Page, body: unknown) {
  await page.route("**/api/v1/ai/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/ai/connection") return route.fulfill({ json: body });
    if (path === "/api/v1/ai/runs") return route.fulfill({ json: RUNS });
    if (path === "/api/v1/ai/usage") {
      return route.fulfill({
        json: {
          month: "2026-11-01",
          resets_on: "2026-12-01",
          used_tokens: 410_000,
          reserved_tokens: 3_000,
          monthly_token_limit: 2_000_000,
          input_tokens: 300_000,
          output_tokens: 110_000,
          estimated_cost: null,
          worst_case_cost: null,
        },
      });
    }
    if (path === "/api/v1/ai/project-opt-outs") {
      return route.fulfill({
        json: Array.from({ length: 12 }, (_, index) => ({
          project_id: `00000000-0000-4000-8000-${String(index).padStart(12, "1")}`,
          name: `A project named ${"long ".repeat(20)}${index}`,
          opted_out: index % 3 === 0,
        })),
      });
    }
    return route.continue();
  });
}

test("AC90: /settings/ai with long values fits 390 px", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  await stubAiSettings(page, settings());
  await page.goto("/settings/ai");

  await expect(page.getByRole("heading", { name: /AI/ }).first()).toBeVisible();
  await expect(page.getByText(LONG_MODEL.slice(0, 40)).first()).toBeVisible();
  expect(await sidewaysScroll(page), "the page scrolls sideways").toBeLessThanOrEqual(0);
});

test("AC90: /settings/ai at 1920 px has no capped table or block", async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  await signUp(page, newAccount());
  await stubAiSettings(page, settings());
  await page.goto("/settings/ai");

  await expect(page.getByText(LONG_MODEL.slice(0, 40)).first()).toBeVisible();
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
  const tables = page.locator("table");
  for (const table of await tables.all()) {
    expect(await containerCap(table, DESKTOP.width), "a container around a table is capped").toBe(
      null,
    );
  }
});

test("AC103: with no AI container running, /settings/ai says so and the switch is off", async ({
  page,
}) => {
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  await stubAiSettings(
    page,
    settings({ on: false, container_running: false, missing: ["container"] }),
  );
  await page.goto("/settings/ai");

  await expect(page.getByText(/container is not running/i).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Turn AI wording on" })).toBeDisabled();
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
});

test("AC85: a member reads only that AI wording is off here", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await signUp(page, newAccount());
  await stubAiSettings(page, { on: false, full: false });
  await page.goto("/settings/ai");

  await expect(
    page.getByText("AI wording is off here. An owner or admin can set it up."),
  ).toBeVisible();
  expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0);
});
