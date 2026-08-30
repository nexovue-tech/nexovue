// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { ProjectDayUsage, UsageResponse } from "./api";
import { UsageView } from "./UsageScreen";
import { usageRange } from "./usage-range";

const PRODUCTION = "11111111-1111-4111-8111-111111111111";
const STAGING = "22222222-2222-4222-8222-222222222222";

const NAMES = new Map([
  [PRODUCTION, "production"],
  [STAGING, "staging"],
]);

const RANGE = usageRange(30, Date.parse("2026-08-30T12:00:00Z"));

function day(dayString: string, projectId: string, overrides: Partial<ProjectDayUsage> = {}) {
  return {
    project_id: projectId,
    day: dayString,
    spans: 1_000,
    logs: 2_000,
    metric_points: 3_000,
    bytes_ingested: 42_100_000_000,
    services: 3,
    settled: true,
    ...overrides,
  } satisfies ProjectDayUsage;
}

/**
 * Two projects running three services each, every day of the range — and four
 * distinct services between them, because they share one name.
 */
const USAGE: UsageResponse = {
  plan: { slug: "pro", name: "Pro", raw_retention_days: 30, derived_retention_days: 395 },
  price: null,
  from_day: RANGE.from,
  to_day: RANGE.to,
  settled: true,
  totals: {
    spans: 2_000,
    logs: 4_000,
    metric_points: 6_000,
    bytes_ingested: 84_200_000_000,
    services: 4,
  },
  days: [day("2026-08-29", PRODUCTION), day("2026-08-29", STAGING)],
};

const PRICED: UsageResponse["price"] = {
  effective_from: "2026-01-01",
  currency: "USD",
  price_per_service_cents: 1_000,
  max_billed_services: 120,
};

const VIEWPORT_HEIGHT = 800;
const VIEWPORT_WIDTH = 1200;

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

  // jsdom does no layout, so the scroll container measures zero tall and the
  // virtualizer correctly renders nothing into a viewport that does not exist.
  for (const [property, size] of [
    ["offsetHeight", VIEWPORT_HEIGHT],
    ["offsetWidth", VIEWPORT_WIDTH],
  ] as const) {
    Object.defineProperty(HTMLElement.prototype, property, { configurable: true, value: size });
  }
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

interface Overrides {
  usage?: UsageResponse;
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
  projectNames?: ReadonlyMap<string, string>;
}

function render(overrides: Overrides = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const root = createRoot(container);
  mounted.push(root);

  act(() =>
    root.render(
      createElement(UsageView, {
        range: RANGE,
        onRangeChange: () => {},
        usage: USAGE,
        isPending: false,
        isError: false,
        onRetry: () => {},
        projectNames: NAMES,
        ...overrides,
      }),
    ),
  );

  return container;
}

const readable = (container: HTMLElement) =>
  (container.textContent ?? "").replace(/\s+/g, " ").trim();

/** The value under one of the summary's terms, found by the term rather than by position. */
function summaryValue(container: HTMLElement, term: string): string {
  const label = [...container.querySelectorAll("dt")].find((node) => node.textContent === term);
  return label?.nextElementSibling?.textContent ?? "";
}

describe("UsageView", () => {
  it("waits with a skeleton, never a spinner", () => {
    const container = render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading usage"]')).not.toBeNull();
    expect(container.innerHTML).toContain("animate-pulse");
    expect(readable(container)).not.toContain("production");
  });

  it("shows a failed load inline, with the correlation id and a retry", () => {
    const error = new ApiError("boom", 503, {
      traceId: "abc",
      correlationId: "corr-42",
      offline: false,
      aborted: false,
    });
    const rendered = readable(render({ isError: true, error }));

    expect(rendered).toContain("Usage could not be loaded");
    expect(rendered).toContain("corr-42");
    expect(rendered).toContain("Try again");
  });

  it("says what an empty range means rather than showing a blank area", () => {
    const rendered = readable(render({ usage: { ...USAGE, days: [] } }));

    expect(rendered).toContain("Nothing metered in this range");
    expect(rendered).toContain("the meter may not have counted this far yet");
    // The plan and the rate are still worth showing when nothing was ingested.
    expect(rendered).toContain("Pro");
  });

  it("is read for the whole organization, and says so", () => {
    expect(readable(render())).toContain("the bill is the organization's, not one project's");
  });

  /* --------------------------------------------------------------------- money */

  it("shows no cost when no price is stored, and never a zero", () => {
    const rendered = readable(render());

    expect(rendered).toContain("Not priced");
    expect(rendered).toContain("No price is recorded for the Pro plan");
    expect(rendered).not.toContain("$");
    expect(rendered).not.toContain("0.00");
  });

  it("shows the rate beside the quantity and never multiplies them", () => {
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED } }));

    expect(rendered).toContain("$10.00 per service");
    expect(rendered).toContain("In force since Jan 1, 2026.");
    expect(rendered).toContain("At most 120 services are billed.");
    expect(rendered).toContain("Nothing here is an invoice.");
    // $10.00 × 4 services. A projected cost is not a number the API returns, and
    // inventing one here is the failure this test exists to catch.
    expect(rendered).not.toContain("$40");
  });

  it("reports the range total the API sent, never the sum of the days", () => {
    const container = render();

    // Two projects, three services each, one name shared: four, not six.
    const billable = summaryValue(container, "Billable services");
    expect(billable).toMatch(/^4(?!\d)/);
    expect(billable).toContain("a service reporting every day is one service");
  });

  /* ---------------------------------------------------------- what is unfinished */

  it("marks a day the meter has not finished, rather than showing a low number", () => {
    const container = render({
      usage: {
        ...USAGE,
        settled: false,
        days: [day("2026-08-30", PRODUCTION, { settled: false }), day("2026-08-29", STAGING)],
      },
    });
    const rendered = readable(container);

    expect(rendered).toContain("still counting");
    expect(rendered).toContain("1 day here has not finished being counted");
    expect(container.innerHTML).toContain("text-warning-text");
  });

  it("warns that the totals can grow even when every day it lists is finished", () => {
    // The days the meter has not reached have no row here to carry the caveat.
    const rendered = readable(render({ usage: { ...USAGE, settled: false } }));

    expect(rendered).toContain("Part of this range has not finished being counted");
  });

  it("says so plainly when the range is finished", () => {
    const container = render();

    expect(readable(container)).toContain("Every day in this range has finished being counted.");
    expect(readable(container)).not.toContain("still counting");
  });

  /* ---------------------------------------------------------------- the controls */

  it("offers the day ranges as pressed controls", () => {
    const container = render();
    const pressed = [...container.querySelectorAll("[aria-pressed]")].map((button) => [
      button.textContent,
      button.getAttribute("aria-pressed"),
    ]);

    expect(pressed).toEqual([
      ["7 days", "false"],
      ["30 days", "true"],
      ["90 days", "false"],
    ]);
  });
});
