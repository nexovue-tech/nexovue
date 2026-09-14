// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import { rumFilters } from "./filters";
import { RumView } from "./RumScreen";
import { ROUTE_TEMPLATE_TAG, readableVitals } from "./vitals";

let container: HTMLDivElement;
let root: Root;

beforeAll(() => {
  // React 18 asks to be told this is a test environment before it renders.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props: Parameters<typeof RumView>[0]): string {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root.render(createElement(RumView, props)));
  return container.textContent ?? "";
}

const BASE = {
  filterDefinitions: rumFilters({}),
  filters: {},
  onFilterChange: () => {},
  onClearFilters: () => {},
  windowLabel: "the last hour",
  isPending: false,
  isError: false,
  onRetry: () => {},
};

describe("the RUM screen", () => {
  it("tells a customer the script is what produces these numbers when none arrived", () => {
    const text = render({ ...BASE, vitals: readableVitals([]) });
    // The failure mode this prevents: a customer with server telemetry working
    // concluding the product is broken, when they simply have not added the tag.
    expect(text).toContain("No browser has reported yet");
    expect(text).toContain("browser SDK");
  });

  it("shows every vital's value with its sample size beside it", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([
        { name: "lcp", p75: 2412, readings: 1840 },
        { name: "inp", p75: 184.6, readings: 900 },
      ]),
    });
    expect(text).toContain("2.41 s");
    expect(text).toContain("185 ms");
    expect(text).toContain("1,840 readings");
  });

  it("says a thin sample is thin rather than showing the percentile as a fact", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 9000, readings: 4 }]),
    });
    expect(text).toContain("too few for a percentile");
    expect(text).toContain("Not enough data");
  });

  it("separates the scored vitals from the two that only explain them", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 50 }]),
    });
    expect(text).toContain("Core Web Vitals");
    expect(text).toContain("Diagnostics");
  });

  it("splits the numbers by page once more than one page reported", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        page: {
          dimension: "page",
          truncated: false,
          groups: [
            {
              key: "/checkout",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 9000, readings: 60 }],
            },
            {
              key: "/",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 400, readings: 40 }],
            },
          ],
        },
      },
    });
    expect(text).toContain("/checkout");
    expect(text).toContain("9.00 s");
    expect(text).toContain("400 ms");
    // Only one split has more than one group, so there is nothing to choose between.
    expect(text).toContain("Page 2");
    // The split control offers a dimension with its group count beside it, so
    // `Device 1` is what a second option would look like. Matched that way
    // rather than on the bare word, which the device *filter* also renders —
    // narrowing to one device class and splitting by it are different controls.
    expect(text).not.toMatch(/Device \d/);
  });

  it("shows a page the readings cannot carry as a dash rather than a fast number", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        page: {
          dimension: "page",
          truncated: false,
          groups: [
            {
              key: "/busy",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 90 }],
            },
            {
              key: "/rare",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 120, readings: 3 }],
            },
          ],
        },
      },
    });
    // The failure this prevents: /rare reading as the fastest page on the site
    // because one visitor with a warm cache is the whole sample.
    expect(text).toContain("/rare");
    expect(text).not.toContain("120 ms");
    expect(text).toContain("never a zero");
  });

  it("hides the split entirely when neither dimension has more than one group", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        page: {
          dimension: "page",
          truncated: false,
          groups: [
            {
              key: "/",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 100 }],
            },
          ],
        },
        device: {
          dimension: "device",
          truncated: false,
          groups: [
            {
              key: "desktop",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 100 }],
            },
          ],
        },
      },
    });
    expect(text).not.toContain("Split by");
  });

  it("marks a route template apart from a raw URL in the page breakdown", () => {
    // A reader comparing the two has to know one row is a page and the other is
    // one order's URL, or the numbers are not comparable at all.
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        page: {
          dimension: "page",
          truncated: false,
          groups: [
            {
              key: "/cart/checkout/{orderId}",
              is_route_template: true,
              vitals: [{ name: "lcp", p75: 9000, readings: 60 }],
            },
            {
              key: "/about",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 400, readings: 40 }],
            },
          ],
        },
      },
    });
    // Asserted on the rows themselves, not on the page text: the footnote below
    // the table names the tag too, so a whole-page match would pass with no tag
    // on any row at all.
    const rows = [...container.querySelectorAll("tbody th")].map((cell) => cell.textContent ?? "");
    expect(rows).toHaveLength(2);
    const [routed, raw] = rows;
    expect(routed).toContain("/cart/checkout/{orderId}");
    expect(routed).toContain(ROUTE_TEMPLATE_TAG);
    expect(raw).toContain("/about");
    expect(raw).not.toContain(ROUTE_TEMPLATE_TAG);
    // And the sentence that says what the tag means.
    expect(text).toContain("gave the SDK a template for");
  });

  it("does not tag a device class as a route template", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        device: {
          dimension: "device",
          truncated: false,
          groups: [
            {
              key: "desktop",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 50 }],
            },
            {
              key: "mobile",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 3000, readings: 50 }],
            },
          ],
        },
      },
    });
    const rows = [...container.querySelectorAll("tbody th")].map((cell) => cell.textContent ?? "");
    expect(rows).toEqual(["desktop", "mobile"]);
    expect(text).not.toContain("gave the SDK a template for");
  });

  it("says the page list was cut rather than presenting it as the whole site", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        page: {
          dimension: "page",
          truncated: true,
          groups: [
            {
              key: "/a",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 50 }],
            },
            {
              key: "/b",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 50 }],
            },
          ],
        },
      },
    });
    expect(text).toContain("not all of them");
  });

  it("names a device class the browser did not report instead of showing an empty row", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([{ name: "lcp", p75: 2000, readings: 100 }]),
      breakdowns: {
        device: {
          dimension: "device",
          truncated: false,
          groups: [
            {
              key: "desktop",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 2000, readings: 50 }],
            },
            {
              key: "",
              is_route_template: false,
              vitals: [{ name: "lcp", p75: 3000, readings: 50 }],
            },
          ],
        },
      },
    });
    expect(text).toContain("desktop");
    expect(text).toContain("not reported");
  });

  it("offers a retry rather than an empty screen when the read failed", () => {
    const text = render({
      ...BASE,
      vitals: readableVitals([]),
      isError: true,
      error: new ApiError("clickhouse unavailable", 503, {
        traceId: "0af7651916cd43dd8448eb211c80319c",
        correlationId: "0af7651916cd43dd8448eb211c80319c",
        offline: false,
        aborted: false,
      }),
    });
    expect(text).toContain("Web vitals could not be loaded");
    expect(text).not.toContain("No browser has reported yet");
  });
});
