// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { SloSuggestion } from "./api";
import {
  json,
  mountAt,
  press,
  readable,
  settle,
  stubApi,
  target,
  unmountAll,
  waitFor,
} from "./slo-harness.test-util";

/**
 * The create form for all four kinds (F19 plan T2.5): what to watch, the kind, the
 * generated name, the suggestion and its arithmetic, the boundary preview, definitions,
 * too-fine refusals, the sampling question, pre-ticked health checks and the suggestion's
 * states. AC5, AC79, AC80's form half, AC101, AC106, AC114, and AC81 for the form.
 */

afterEach(unmountAll);

const BOUNDARIES = Array.from({ length: 63 }, (_, index) =>
  Math.exp((Math.log(60_000) * (index + 1)) / 64),
);
const STEPS = ["90", "95", "97", "98", "99", "99.5", "99.9", "99.95"];
const NO_SILENCE_RULE = json({ rules: [], open_incidents: [] });
const SUGGESTION_PATH = "GET /slo-readings/suggestion";

function suggestion(overrides: Partial<SloSuggestion> = {}): SloSuggestion {
  return {
    outcome: "from_worst_week",
    objective: "99.9",
    weeks: [],
    worst: "99.93",
    typical: "99.95",
    best: "99.97",
    days_of_data: 28,
    boundary_index: null,
    boundary_ms: null,
    week_boundaries_ms: [],
    highest_boundary_ms: BOUNDARIES[62] ?? 0,
    boundaries_ms: BOUNDARIES,
    steps: STEPS,
    thin_allowed: 10,
    endpoints: [],
    offer_check: false,
    check: null,
    ...overrides,
  };
}

const BASE = {
  "GET /projects": json({ projects: [] }),
  "GET /alerts/coverage/services/checkout": NO_SILENCE_RULE,
};

function input(container: Element, name: string): HTMLInputElement {
  const found = container.querySelector<HTMLInputElement>(`input[name=${name}]`);
  if (!found) throw new Error(`no input ${name}`);
  return found;
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
async function typeInto(container: Element, name: string, value: string) {
  const field = input(container, name);
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  await act(async () => {
    setValue?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    // Leaving the field asks for its history (one read, not one a keystroke).
    field.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  });
  await settle();
}

async function choose(container: Element, label: string) {
  const radio = [...container.querySelectorAll<HTMLInputElement>("input[type=radio]")].find(
    (each) => readable(each.closest("label") ?? each) === label,
  );
  if (!radio) throw new Error(`no choice "${label}"`);
  await act(async () => radio.click());
  await settle();
}

async function selectCheck(container: Element, checkId: string) {
  const select = container.querySelector<HTMLSelectElement>("select[name=check_id]");
  if (!select) throw new Error("no check select");
  await act(async () => {
    select.value = checkId;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

async function openForm(handlers: Record<string, () => Response>) {
  const calls = stubApi({ ...BASE, ...handlers }, "member");
  const { container } = await mountAt("/slos/new");
  await waitFor(() => container.querySelector("form"), "the form");
  return { calls, container };
}

describe("the form, for every kind", () => {
  it("refuses 99.999 and 89.9 beside the field and in the summary, sending nothing (AC5)", async () => {
    const { calls, container } = await openForm({ [SUGGESTION_PATH]: json(suggestion()) });
    await typeInto(container, "service_name", "checkout");
    for (const objective of ["99.999", "89.9"]) {
      await typeInto(container, "objective_percent", objective);
      await press(container, "Save");
      expect(input(container, "objective_percent").getAttribute("aria-invalid")).toBe("true");
      expect(container.querySelector("[role=alert]")).not.toBeNull();
    }
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("shows the suggestion's arithmetic, warns of a bad week, and proposes the name (FR7, AC75)", async () => {
    const { container } = await openForm({
      [SUGGESTION_PATH]: json(
        suggestion({
          outcome: "from_typical_week",
          objective: "99.95",
          worst: "98.70",
          typical: "99.95",
        }),
      ),
    });
    await typeInto(container, "service_name", "checkout");
    await waitFor(() => readable(container).includes("We suggest 99.95%"), "the suggestion");
    expect(readable(container)).toContain("Your worst week would have missed this.");
    expect(input(container, "objective_percent").value).toBe("99.95");
    expect(input(container, "name").value).toBe("checkout availability");
  });

  it("offers a labelled starting guess under 7 days (FR8)", async () => {
    const { container } = await openForm({
      [SUGGESTION_PATH]: json(suggestion({ outcome: "starter", objective: "99.5" })),
    });
    await typeInto(container, "service_name", "checkout");
    await waitFor(() => readable(container).includes("a starting guess, not from your data"), "it");
  });

  it("says when the history could not be read, offers a retry, and still saves (AC106)", async () => {
    const { calls, container } = await openForm({
      [SUGGESTION_PATH]: json({ detail: "ClickHouse is down" }, 503),
      "POST /slos": json(target(), 201),
      "GET /slos/slo-1": json(target()),
    });
    await typeInto(container, "service_name", "checkout");
    await waitFor(
      () => readable(container).includes("Could not read this service's history."),
      "the failure",
    );
    expect(readable(container)).toContain("Try again");
    await typeInto(container, "name", "checkout availability");
    await press(container, "Save");
    await waitFor(() => calls.some((call) => call.method === "POST"), "the save");
  });

  it("pre-ticks health checks, shows them, and leaves them out on save (FR34, AC80)", async () => {
    const { calls, container } = await openForm({
      [SUGGESTION_PATH]: json(
        suggestion({
          endpoints: [
            { name: "POST /checkout", health_check: false },
            { name: "GET /health", health_check: true },
          ],
        }),
      ),
      "POST /slos": json(target(), 201),
      "GET /slos/slo-1": json(target()),
    });
    await typeInto(container, "service_name", "checkout");
    await waitFor(() => readable(container).includes("look like health checks"), "the endpoints");
    const boxes = [...container.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
    const ticked = boxes.filter((box) => box.checked).map((box) => box.value);
    expect(ticked).toEqual(["GET /health"]);
    await press(container, "Save");
    const post = await waitFor(() => calls.find((call) => call.method === "POST"), "the save");
    expect(post.body).toMatchObject({
      kind: "service_availability",
      left_out_endpoints: ["GET /health"],
    });
    expect(post.body).not.toHaveProperty("check_id");
  });

  it("says a target does not alert when the service stops, with prefilled links (AC79)", async () => {
    const { container } = await openForm({ [SUGGESTION_PATH]: json(suggestion()) });
    await typeInto(container, "service_name", "checkout");
    await waitFor(
      () =>
        readable(container).includes("This target does not alert if the service stops completely."),
      "the line",
    );
    const hrefs = [...container.querySelectorAll("a")].map((link) => link.getAttribute("href"));
    expect(hrefs).toContain("/alerts/rules/new?service=checkout");
    expect(hrefs).toContain("/synthetics/new");
    expect(readable(container)).toContain("Do you sample before sending to nexovue?");
  });

  it("links 'Not sure' to how to check, and says what Yes means (FR5, AC114)", async () => {
    const { container } = await openForm({ [SUGGESTION_PATH]: json(suggestion()) });
    await choose(container, "Not sure");
    const link = container.querySelector(
      'a[href="/docs/collector#do-not-sample-in-the-collector"]',
    );
    expect(link).not.toBeNull();
    await choose(container, "Yes");
    expect(readable(container)).toContain("Alerts start off until this changes");
  });
});

describe("a latency target", () => {
  it("previews 487.2 ms for 500, says a fast failure counts, and names it (FR3, FR4, AC101)", async () => {
    const { calls, container } = await openForm({
      [SUGGESTION_PATH]: json(suggestion({ outcome: "too_few", objective: null })),
      "POST /slos": json(target(), 201),
      "GET /slos/slo-1": json(target()),
    });
    await typeInto(container, "service_name", "checkout");
    await choose(container, "Latency");
    await typeInto(container, "asked_ms", "500");
    await waitFor(
      () => readable(container).includes("This counts requests at or under 487.2 ms."),
      "the preview",
    );
    expect(readable(container)).toContain(
      "A fast failure counts here. Pair this with an availability target.",
    );
    expect(readable(container)).toContain("The highest threshold nexovue counts is 50.5 s.");
    expect(input(container, "name").value).toBe("checkout latency under 487.2 ms");
    await press(container, "Save");
    const post = await waitFor(() => calls.find((call) => call.method === "POST"), "the save");
    expect(post.body).toMatchObject({ kind: "service_latency", asked_ms: 500 });
    expect(post.body).not.toHaveProperty("left_out_endpoints");
  });

  it("refuses a threshold above the highest boundary beside its field (AC101)", async () => {
    const { calls, container } = await openForm({ [SUGGESTION_PATH]: json(suggestion()) });
    await typeInto(container, "service_name", "checkout");
    await choose(container, "Latency");
    await typeInto(container, "asked_ms", "60000");
    await typeInto(container, "name", "slow");
    await press(container, "Save");
    expect(input(container, "asked_ms").getAttribute("aria-invalid")).toBe("true");
    expect(readable(container)).toContain("the highest nexovue counts");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("shows the latency suggestion's boundary holding 95% (AC101)", async () => {
    const { container } = await openForm({
      [SUGGESTION_PATH]: json(
        suggestion({ objective: "95", boundary_index: 35, boundary_ms: BOUNDARIES[35] ?? 0 }),
      ),
    });
    await typeInto(container, "service_name", "checkout");
    await choose(container, "Latency");
    await waitFor(
      () => readable(container).includes("We suggest 95% at or under 487.2 ms."),
      "the latency suggestion",
    );
    expect(input(container, "asked_ms").value).toBe("487.2");
  });
});

describe("a check target", () => {
  const CHECKS = json({
    checks: [
      { id: "check-5m", name: "Checkout API", interval_seconds: 300 },
      { id: "check-6h", name: "Nightly export", interval_seconds: 21_600 },
    ],
    available_locations: ["local"],
  });
  const window28 = (expected: number, fastest: number) => ({
    window_days: 28,
    expected_runs: expected,
    fastest_expected_runs: fastest,
  });

  it("says what its interval allows and what would change it (FR8, AC101)", async () => {
    const { calls, container } = await openForm({
      "GET /checks": CHECKS,
      [SUGGESTION_PATH]: json(
        suggestion({
          objective: "99.9",
          check: {
            interval_seconds: 300,
            windows: [window28(8_064, 40_320)],
            alerts_can_judge: true,
          },
        }),
      ),
      "POST /slos": json(target(), 201),
      "GET /slos/slo-1": json(target()),
    });
    await choose(container, "A check");
    await waitFor(() => container.querySelector("select[name=check_id]"), "the check picker");
    await selectCheck(container, "check-5m");
    await waitFor(
      () =>
        readable(container).includes(
          "At every 5 minutes, 99.9% allows 8 failed runs over 28 days. Run it every minute or choose 99.5%.",
        ),
      "the interval line",
    );
    expect(readable(container)).not.toContain("Do you sample");
    expect(readable(container)).not.toContain("Add a check-based target");
    expect(input(container, "name").value).toBe("Checkout API availability");
    await press(container, "Save");
    const post = await waitFor(() => calls.find((call) => call.method === "POST"), "the save");
    expect(post.body).toMatchObject({ kind: "check_availability", check_id: "check-5m" });
    expect(post.body).not.toHaveProperty("upstream_sampling");
    expect(calls.find((call) => call.path === "/slo-readings/suggestion")).toBeDefined();
  });

  it("says alerts cannot judge a check every 6 hours (FR16, AC79)", async () => {
    const { container } = await openForm({
      "GET /checks": CHECKS,
      [SUGGESTION_PATH]: json(
        suggestion({
          check: {
            interval_seconds: 21_600,
            windows: [window28(112, 40_320)],
            alerts_can_judge: false,
          },
        }),
      ),
    });
    await choose(container, "A check");
    await waitFor(() => container.querySelector("select[name=check_id]"), "the check picker");
    await selectCheck(container, "check-6h");
    await waitFor(
      () =>
        readable(container).includes(
          "Alerts cannot judge a check this infrequent. It needs at least 10 runs a day.",
        ),
      "the line",
    );
  });
});

describe("too little traffic", () => {
  it("offers a check-based target where the project has a check (FR8)", async () => {
    const { container } = await openForm({
      [SUGGESTION_PATH]: json(
        suggestion({ outcome: "too_few", objective: null, offer_check: true }),
      ),
    });
    await typeInto(container, "service_name", "checkout");
    await waitFor(() => readable(container).includes("Too few events"), "the line");
    expect(readable(container)).toContain("Add a check-based target");
  });
});
