// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PanelResult, SloBudget, StoredPanel } from "./api";
import { PanelReading } from "./PanelReading";
import { panelTitle, stateWords } from "./panel-words";
import { mountScreen, text, unmountAll } from "./render-harness.test-util";
import { budgetState, budgetWords, STALE_AFTER_MS } from "./slo-budget-words";

/**
 * A reliability-target panel's words (T10.2d), from F19's own figures, on a pinned clock:
 * the state, what is still allowed, the burn while running out, "as of" in the project's
 * zone, and Not up to date once the job has stopped refreshing it (F19 NFR7). Then the
 * panel drawn through `PanelReading`, as the view, the builder and the side sheet draw it.
 */

const NOW = Date.parse("2026-11-21T10:04:30.000Z");
const EVALUATED = "2026-11-21T10:03:00.000Z";

const budget = (changes: Partial<SloBudget> = {}): SloBudget => ({
  id: "0195f000-0000-7000-8000-000000000003",
  project_id: "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f",
  name: "checkout availability",
  label: "checkout availability, 99.9% over 28 days",
  kind: "service_availability",
  allowance_words: "Failures still allowed",
  objective_percent: "99.90",
  window_days: 28,
  state: "on_track",
  severity: null,
  attention: false,
  figures: { allowed: 2_800, left: 2_520, failures: 280, figure_percent: "99.99" },
  burn: null,
  evaluated_minute: EVALUATED,
  created_at: "2026-09-01T00:00:00.000Z",
  zone: "UTC",
  ...changes,
});

const panel = (slo: string, title: string | null = null): StoredPanel => ({
  id: "p_slo",
  kind: "slo_budget",
  title,
  width: 4,
  height: "small",
  settings: { slo },
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  unmountAll();
});

describe("what a reliability-target panel says", () => {
  it("says the state and what is still allowed, as of when it was worked out", () => {
    expect(budgetWords(budget(), NOW)).toEqual({
      state: "on_track",
      allowance: "Failures still allowed: 2,520 of 2,800.",
      burn: null,
      asOf: "As of 10:03 UTC.",
    });
  });

  it("says how fast and how long what is left lasts while running out", () => {
    const running = budget({
      state: "running_out",
      severity: "critical",
      burn: { rate: 14.7, since: "2026-11-21T09:43:00.000Z", hours_left: 50, resolving: false },
    });

    expect(budgetWords(running, NOW).burn).toBe(
      "Using allowed failures 14 times faster than allowed. What is left lasts about 2 days.",
    );
  });

  it("says each kind's own noun, so a latency target never says failures (AC89)", () => {
    const slow = budget({
      kind: "service_latency",
      allowance_words: "Slow requests still allowed",
      state: "running_out",
      burn: { rate: 6.2, since: EVALUATED, hours_left: null, resolving: false },
    });

    expect(budgetWords(slow, NOW)).toMatchObject({
      allowance: "Slow requests still allowed: 2,520 of 2,800.",
      burn: "Using allowed slow requests 6 times faster than allowed.",
    });
  });

  it("says no rate once the burn is resolving, and never shows less than nothing left", () => {
    const resolving = budget({
      state: "running_out",
      burn: { rate: 3.1, since: EVALUATED, hours_left: 10, resolving: true },
      figures: { allowed: 2_800, left: -40, failures: 2_840, figure_percent: "99.89" },
    });

    expect(budgetWords(resolving, NOW)).toMatchObject({
      burn: null,
      allowance: "Failures still allowed: 0 of 2,800.",
    });
  });

  it("reads Not up to date once the job has not refreshed it for 15 minutes (F19 NFR7)", () => {
    const later = Date.parse(EVALUATED) + STALE_AFTER_MS + 60_000;
    vi.setSystemTime(later);

    expect(budgetState(budget({ state: "running_out" }), later)).toBe("not_up_to_date");
    expect(budgetWords(budget(), later)).toMatchObject({
      state: "not_up_to_date",
      asOf: "Not up to date since 10:03 UTC.",
    });
    expect(budgetState(budget({ state: "disabled" }), later)).toBe("disabled");
    expect(budgetState(budget(), Date.parse(EVALUATED) + STALE_AFTER_MS)).toBe("on_track");
  });

  it("says a target never worked out was never worked out", () => {
    expect(budgetWords(budget({ evaluated_minute: null, created_at: EVALUATED }), NOW).asOf).toBe(
      "Not worked out yet.",
    );
  });
});

describe("the panel's title and its not_in_project words", () => {
  const context = { canEdit: true, owner: null, projectName: "staging", overLimit: 0, now: NOW };

  it("is titled by its target's name, escaped, unless it has its own title", () => {
    expect(panelTitle(panel("check‮out"))).toEqual({ text: "check[U+202E]out" });
    expect(panelTitle(panel("checkout availability", "Checkout"))).toEqual({ text: "Checkout" });
  });

  it("claims only that the project holds no target by that name (AC22)", () => {
    const missing: PanelResult = { state: "not_in_project", kind: "slo_budget" };

    expect(stateWords(missing, panel("old name"), context)).toBe(
      "No reliability target named old name in staging.",
    );
  });
});

describe("the panel drawn", () => {
  it("draws the state, the allowance, the burn, as of, and a link to the target", async () => {
    const reading = {
      state: "ok" as const,
      kind: "slo_budget",
      read_at: new Date(NOW).toISOString(),
      bucket_seconds: 0,
      data_starts_at: null,
      data_ends_at: null,
      series: [],
      budget: budget({
        state: "running_out",
        severity: "warning",
        burn: { rate: 14.7, since: EVALUATED, hours_left: 30, resolving: false },
      }),
    };
    const { container } = await mountScreen(
      createElement(PanelReading, {
        panel: panel("checkout availability"),
        reading,
        zone: "UTC",
        description: "checkout availability",
        now: NOW,
      }),
      () => undefined,
    );

    expect([...container.querySelectorAll("span, p, a")].map(text)).toEqual([
      "Running out, can wait",
      "Failures still allowed: 2,520 of 2,800.",
      "Using allowed failures 14 times faster than allowed. What is left lasts about 30 hours.",
      "As of 10:03 UTC.",
      "Open the target",
    ]);
    // The target page's own badge and tone: a warning outlined, words always.
    expect(container.querySelector("span")?.className).toContain("border-warning-border");
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      "/slos/0195f000-0000-7000-8000-000000000003",
    );
  });
});
