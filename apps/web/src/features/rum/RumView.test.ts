// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import { RumView } from "./RumScreen";
import { readableVitals } from "./vitals";

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
  windowLabel: "hour",
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
