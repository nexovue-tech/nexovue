// @vitest-environment jsdom

import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import type { StoredPanel } from "./api";
import { ChartLegend, readoutAt } from "./ChartLegend";
import type { OkResult } from "./kept-readings";
import type { Marks } from "./time-lines";
import { NO_MARKS, timeChart } from "./time-lines";

/**
 * The value at the cursor, as text and links (F20, T4.7): one line for a single line; for
 * several, a row each, in order of the value at the cursor, each name a real link that
 * opens its service and hides no line (AC106); what is at that moment named, finding and
 * release (AC107's readout, from data, since jsdom has no canvas).
 */

const ZONE = "UTC";
const T0 = "2026-11-09T10:00:00.000Z";
const T1 = "2026-11-09T10:01:00.000Z";
const T2 = "2026-11-09T10:02:00.000Z";
const at = (iso: string) => Date.parse(iso) / 1000;

function ok(series: OkResult["series"], incompleteFrom: string | null = null): OkResult {
  return {
    state: "ok",
    kind: "top_services_trend",
    read_at: T2,
    bucket_seconds: 60,
    data_starts_at: T0,
    data_ends_at: T2,
    incomplete_from: incompleteFrom,
    series,
  };
}

const TOP: StoredPanel = {
  id: "p_top",
  kind: "top_services_trend",
  title: null,
  width: 6,
  height: "medium",
  settings: { signal: "errors", order: "worst", count: 3 },
};

const LATENCY: StoredPanel = {
  id: "p_lat",
  kind: "service_trend",
  title: null,
  width: 4,
  height: "medium",
  settings: { service: "checkout", signal: "latency" },
};

const topChart = () =>
  timeChart(
    TOP,
    ok([
      {
        label: "error_rate",
        service: "alpha",
        points: [
          [T0, 0.01],
          [T1, 0.05],
        ],
      },
      {
        label: "error_rate",
        service: "bravo",
        points: [
          [T0, 0.03],
          [T1, 0.02],
        ],
      },
      { label: "error_rate", service: "charlie", points: [[T0, 0.02]] },
    ]),
  );

describe("the readout, from data", () => {
  it("orders several lines by the value at the cursor, a gap last", () => {
    const first = readoutAt(topChart(), 0, NO_MARKS, ZONE);
    expect(first?.rows.map((row) => row.service)).toEqual(["bravo", "charlie", "alpha"]);
    const second = readoutAt(topChart(), 1, NO_MARKS, ZONE);
    expect(second?.rows.map((row) => [row.service, row.value])).toEqual([
      ["alpha", "5.0%"],
      ["bravo", "2.0%"],
      ["charlie", "—"],
    ]);
  });

  it("formats a single line's value in its unit and marks latency as an estimate (AC31, AC113)", () => {
    const chart = timeChart(
      LATENCY,
      ok([
        { label: "p50_ms", points: [[T0, 12]] },
        { label: "p95_ms", points: [[T0, 88]] },
        { label: "p99_ms", points: [[T0, 140]] },
      ]),
    );
    const readout = readoutAt(chart, 0, NO_MARKS, ZONE);
    expect(readout?.rows.find((row) => row.name === "p95")?.value).toBe("88ms (est.)");
    expect(readout?.time).toContain("10:00");
  });

  it("names the finding and the release at the cursor, and a seen version is never a deploy (AC107)", () => {
    const marks: Marks = {
      spans: [{ from: at(T0), to: at(T1) + 30, id: "f1", name: "checkout is slower" }],
      markers: [
        {
          at: at(T1) + 10,
          version: "v2.1",
          service: "checkout",
          source: "reported",
          state: null,
          releaseId: null,
          key: "a",
        },
        {
          at: at(T1) + 20,
          version: "v2.2",
          service: "checkout",
          source: "seen",
          state: null,
          releaseId: null,
          key: "b",
        },
      ],
    };
    expect(readoutAt(topChart(), 1, marks, ZONE)?.marks).toEqual([
      "Finding: checkout is slower",
      "Deploy v2.1",
      "Version seen in telemetry v2.2",
    ]);
    expect(readoutAt(topChart(), 0, { ...marks, markers: [] }, ZONE)?.marks).toEqual([
      "Finding: checkout is slower",
    ]);
  });

  it("reads the newest bucket 'so far' while it is still filling, and with no cursor (FR66)", () => {
    const chart = timeChart(
      LATENCY,
      ok(
        [
          {
            label: "p95_ms",
            points: [
              [T0, 80],
              [T1, 90],
            ],
          },
        ],
        T1,
      ),
    );
    const resting = readoutAt(chart, null, NO_MARKS, ZONE);
    expect(resting?.soFar).toBe(true);
    expect(resting?.rows[0]?.value).toBe("90ms (est.)");
    expect(readoutAt(chart, 0, NO_MARKS, ZONE)?.soFar).toBe(false);
  });
});

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

async function mount(): Promise<HTMLElement> {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const chart = topChart();
  const rootRoute = createRootRoute({
    component: () =>
      createElement(ChartLegend, {
        readout: readoutAt(chart, 0, NO_MARKS, ZONE),
        lines: chart.lines.length,
      }),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({
        getParentRoute: () => rootRoute,
        path: "/services/$name",
        component: () => null,
      }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));
  return container;
}

describe("the legend on screen (AC106)", () => {
  it("makes each service a real link the keyboard reaches, opening that service", async () => {
    const container = await mount();
    const links = [...container.querySelectorAll("a")];
    expect(links.map((link) => link.textContent)).toEqual(["bravo", "charlie", "alpha"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/services/bravo",
      "/services/charlie",
      "/services/alpha",
    ]);
    // A real link: no tabindex taking it out of the order, no button posing as one.
    expect(links.every((link) => link.tabIndex === 0)).toBe(true);
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("keeps a row for every line, so following one hides none", async () => {
    const container = await mount();
    expect(container.querySelectorAll("li")).toHaveLength(3);
  });
});
