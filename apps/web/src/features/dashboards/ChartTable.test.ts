// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import type { StoredPanel } from "./api";
import { ChartTable, tableHeaders, tableRows } from "./ChartTable";
import type { OkResult } from "./kept-readings";
import type { Marks } from "./time-lines";
import { NO_MARKS, timeChart } from "./time-lines";

/**
 * A chart's numbers as a table (F20, T4.8): the same numbers the chart draws (AC34),
 * latency marked as an estimate (AC113), a column for the finding or release at each
 * time (AC107), and the bucket still filling as its own row, "so far" (FR66).
 */

const ZONE = "UTC";
const T0 = "2026-11-09T10:00:00.000Z";
const T1 = "2026-11-09T10:01:00.000Z";
const T2 = "2026-11-09T10:02:00.000Z";

const LATENCY: StoredPanel = {
  id: "p_lat",
  kind: "service_trend",
  title: null,
  width: 4,
  height: "medium",
  settings: { service: "checkout", signal: "latency" },
};

function reading(incompleteFrom: string | null): OkResult {
  return {
    state: "ok",
    kind: "service_trend",
    read_at: T2,
    bucket_seconds: 60,
    data_starts_at: T0,
    data_ends_at: T2,
    incomplete_from: incompleteFrom,
    series: [
      {
        label: "p50_ms",
        points: [
          [T0, 10],
          [T1, 11],
          [T2, 12],
        ],
      },
      {
        label: "p95_ms",
        points: [
          [T0, 80],
          [T1, 81],
          [T2, 82],
        ],
      },
      {
        label: "p99_ms",
        points: [
          [T0, 100],
          [T2, 102],
        ],
      },
    ],
  };
}

const MARKS: Marks = {
  spans: [
    { from: Date.parse(T1) / 1000, to: Date.parse(T1) / 1000 + 30, name: "checkout is slower" },
  ],
  markers: [],
};

describe("the table's numbers", () => {
  it("are the chart's numbers, newest first, a gap a dash and never zero (AC34)", () => {
    const rows = tableRows(timeChart(LATENCY, reading(null)), NO_MARKS, ZONE);
    expect(rows.map((row) => row.values)).toEqual([
      ["12ms", "82ms", "102ms"],
      ["11ms", "81ms", "—"],
      ["10ms", "80ms", "100ms"],
    ]);
  });

  it("mark latency as an estimate in every header that holds it (AC113)", () => {
    expect(tableHeaders(timeChart(LATENCY, reading(null)))).toEqual([
      "p50 (est.)",
      "p95 (est.)",
      "p99 (est.)",
    ]);
  });

  it("name the finding at its time, and nothing elsewhere (AC107)", () => {
    const rows = tableRows(timeChart(LATENCY, reading(null)), MARKS, ZONE);
    expect(rows.map((row) => row.marks)).toEqual(["", "Finding: checkout is slower", ""]);
  });

  it("show the bucket still filling as its own row, 'so far' (FR66)", () => {
    const [newest, ...rest] = tableRows(timeChart(LATENCY, reading(T2)), NO_MARKS, ZONE);
    expect(newest?.time).toMatch(/so far$/);
    expect(newest?.values).toEqual(["12ms", "82ms", "102ms"]);
    expect(rest.every((row) => !row.time.endsWith("so far"))).toBe(true);
    expect(rest).toHaveLength(2);
  });
});

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

describe("the table on screen", () => {
  it("is a real table with a header per line and the finding column", async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    mounted.push(root);
    await act(async () =>
      root.render(
        createElement(ChartTable, {
          chart: timeChart(LATENCY, reading(null)),
          marks: MARKS,
          zone: ZONE,
          caption: "checkout · Latency across the window.",
        }),
      ),
    );
    const headers = [...container.querySelectorAll("th")].map((cell) => cell.textContent);
    expect(headers).toEqual([
      "Time",
      "p50 (est.)",
      "p95 (est.)",
      "p99 (est.)",
      "Finding or release",
    ]);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(3);
    expect(container.querySelector("caption")?.textContent).toBe(
      "checkout · Latency across the window.",
    );
  });
});
