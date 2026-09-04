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
 * The month is deliberately not the range: four services in the browsed thirty days,
 * seven over August. A cost computed from the number on the screen would be wrong,
 * which is what the money tests below check for.
 */
const PERIOD: UsageResponse["period"] = {
  first_day: "2026-08-01",
  last_day: "2026-08-31",
  settled: true,
  services: 7,
  billed_services: 7,
  bytes_ingested: 90_000_000_000,
  service_cost_minor_units: null,
  currency: null,
  included_bytes: null,
  allowance_enforced: false,
};

/**
 * Two projects running three services each, every day of the range — and four
 * distinct services between them, because they share one name.
 */
const USAGE: UsageResponse = {
  plan: { slug: "pro", name: "Pro", raw_retention_days: 30, derived_retention_days: 395 },
  price: null,
  period: PERIOD,
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
  cardinality: [],
};

const PRICED: UsageResponse["price"] = {
  effective_from: "2026-01-01",
  currency: "USD",
  price_per_service_minor_units: 1_000,
  max_billed_services: 120,
  included_bytes_per_service: null,
};

/** The same month, priced: seven services at $10 is $70. */
const COSTED: UsageResponse["period"] = {
  ...PERIOD,
  service_cost_minor_units: 7_000,
  currency: "USD",
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
    expect(rendered).toContain("No cost shown");
    expect(rendered).toContain("No price is recorded for the Pro plan");
    expect(rendered).not.toContain("$");
    expect(rendered).not.toContain("0.00");
  });

  it("costs the month, not the range of days on the screen", () => {
    // Seven services in August at $10; the table beside it shows four for the
    // browsed thirty days. Multiplying the visible number gives $40, and that
    // is the mistake this test exists to catch.
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED, period: COSTED } }));

    expect(rendered).toContain("$70.00");
    expect(rendered).toContain("7 services in August 2026, at the rate in force.");
    expect(rendered).not.toContain("$40");
  });

  it("says what the cost leaves out", () => {
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED, period: COSTED } }));

    expect(rendered).toContain("covers the services only");
    expect(rendered).toContain("volume above the included allowance");
  });

  it("shows the rate beside the quantity it is charged on", () => {
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED } }));

    expect(rendered).toContain("$10.00 per service");
    expect(rendered).toContain("In force since Jan 1, 2026.");
    expect(rendered).toContain("At most 120 services are billed.");
  });

  it("marks a month still being metered rather than showing a quiet small number", () => {
    const metering = { ...COSTED, settled: false };
    const container = render({ usage: { ...USAGE, price: PRICED, period: metering } });
    const rendered = readable(container);

    expect(rendered).toContain("$70.00");
    expect(rendered).toContain("still metering");
    expect(rendered).toContain("August 2026 is still being metered, so this will grow.");
    expect(container.innerHTML).toContain("text-warning-text");
  });

  it("shows the volume allowance only when one is recorded", () => {
    expect(readable(render())).not.toContain("included");

    const withAllowance = readable(
      render({
        usage: {
          ...USAGE,
          price: PRICED,
          period: { ...COSTED, included_bytes: 100_000_000_000 },
        },
      }),
    );
    expect(withAllowance).toContain("90.0 GB of 100.0 GB included");
  });

  it("says the allowance is spent and when it resets, where the door is refusing", () => {
    // The receiver's RESOURCE_EXHAUSTED reaches only whoever reads their
    // exporter's logs. Without this the customer's telemetry stops and nothing
    // they can see says why.
    const spent = {
      ...COSTED,
      included_bytes: 10_000_000_000,
      allowance_enforced: true,
    };
    const container = render({ usage: { ...USAGE, price: PRICED, period: spent } });
    const rendered = readable(container);

    expect(rendered).toContain("90.0 GB of 10.0 GB included — spent");
    expect(rendered).toContain("exports are being refused until Sep 1, 2026");
    expect(rendered).toContain("cannot be recovered");
    expect(container.innerHTML).toContain("text-warning-text");
  });

  it("says nothing about a refusal on a plan that refuses nothing", () => {
    const over = { ...COSTED, included_bytes: 10_000_000_000 };
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED, period: over } }));

    expect(rendered).toContain("over the allowance");
    expect(rendered).not.toContain("being refused");
  });

  it("says there is no overage to bill where volume is refused instead", () => {
    const enforced = { ...COSTED, included_bytes: 10_000_000_000, allowance_enforced: true };
    const rendered = readable(render({ usage: { ...USAGE, price: PRICED, period: enforced } }));

    expect(rendered).toContain("refused rather than billed");
    expect(rendered).not.toContain("is not decided");
  });

  /* -------------------------------------------------------------- cardinality */

  it("shows a project's service count change, and the names behind it", () => {
    const rendered = readable(
      render({
        usage: {
          ...USAGE,
          cardinality: [
            {
              project_id: PRODUCTION,
              services_now: 12,
              services_before: 3,
              window_days: 7,
              families: [
                {
                  stem: "checkout",
                  kind: "kubernetes-pod",
                  members: 9,
                  examples: ["checkout-7d9f8b6c4d-x2k9p"],
                },
              ],
            },
          ],
        },
      }),
    );

    expect(rendered).toContain("production");
    expect(rendered).toContain("12 services, up from 3 in the 7 days before.");
    expect(rendered).toContain("a Kubernetes pod suffix");
    expect(rendered).toContain("checkout-7d9f8b6c4d-x2k9p");
    expect(rendered).toContain("service.instance.id");
  });

  it("shows the change without an accusation when no cause was found", () => {
    const rendered = readable(
      render({
        usage: {
          ...USAGE,
          cardinality: [
            {
              project_id: PRODUCTION,
              services_now: 12,
              services_before: 3,
              window_days: 7,
              families: [],
            },
          ],
        },
      }),
    );

    expect(rendered).toContain("12 services, up from 3 in the 7 days before.");
    expect(rendered).not.toContain("service.instance.id");
    expect(rendered).not.toContain("billed as its own service");
  });

  it("says nothing at all when no project's names look wrong", () => {
    const rendered = readable(render());

    expect(rendered).not.toContain("up from");
    expect(rendered).not.toContain("service.instance.id");
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
