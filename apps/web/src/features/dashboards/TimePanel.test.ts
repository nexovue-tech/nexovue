// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { roundedOut } from "@/shared/drag-window";

import type { StoredPanel } from "./api";
import type { OkResult } from "./kept-readings";
import { LARGE_CHART_HEIGHT, plotRows, syncKeyOf, timeChart } from "./time-lines";

/**
 * A time panel's chart as data (F20, T4.4 to T4.6, T4.9): the options it hands
 * `TrendChart`, the line it leaves the filling bucket off, and the window a drag asks for.
 * AC31's data half: jsdom has no canvas, so the cursor across charts and one drag making
 * one window change are T4.10's, in a browser.
 */

const MINUTE = 60;

function panel(kind: string, settings: Record<string, unknown>, height = "medium"): StoredPanel {
  return { id: "p_1", kind, title: null, width: 4, height: height as "medium", settings };
}

function reading(
  series: OkResult["series"],
  incompleteFrom: string | null = null,
  bucketSeconds = MINUTE,
): OkResult {
  const times = series.flatMap((line) => line.points.map(([at]) => at)).sort();
  return {
    state: "ok",
    kind: "service_trend",
    read_at: "2026-11-09T10:03:30.000Z",
    bucket_seconds: bucketSeconds,
    data_starts_at: times[0] ?? null,
    data_ends_at: times.at(-1) ?? null,
    incomplete_from: incompleteFrom,
    series,
  };
}

const T0 = "2026-11-09T10:00:00.000Z";
const T1 = "2026-11-09T10:01:00.000Z";
const T2 = "2026-11-09T10:02:00.000Z";
const T3 = "2026-11-09T10:03:00.000Z";

describe("a time panel's chart", () => {
  it("leaves the bucket still filling off the line, and keeps it as 'so far' (FR66, AC114)", () => {
    const chart = timeChart(
      panel("service_trend", { service: "checkout", signal: "rate" }),
      reading(
        [
          {
            label: "requests_per_minute",
            points: [
              [T0, 60],
              [T1, 60],
              [T2, 60],
              [T3, 21],
            ],
          },
        ],
        T3,
      ),
    );

    expect(chart.values).toEqual([[60, 60, 60]]);
    expect(chart.x).toEqual([0, 1, 2].map((minute) => Date.parse(T0) / 1000 + minute * MINUTE));
    expect(chart.soFar).toEqual({ at: Date.parse(T3) / 1000, values: [21] });
    expect(plotRows(chart)).toEqual([chart.x, [60, 60, 60]]);
  });

  it("draws every bucket of a fixed window, which has none still filling", () => {
    const chart = timeChart(
      panel("project_trend", { signal: "errors" }),
      reading([
        {
          label: "error_rate",
          points: [
            [T0, 0.01],
            [T1, 0.02],
          ],
        },
      ]),
    );
    expect(chart.values).toEqual([[1, 2]]);
    expect(chart.soFar).toBeUndefined();
    expect(chart.tone).toBe("critical");
    expect(chart.format(2)).toBe("2.0%");
  });

  it("draws a top-N chart's services in distinct colours, each line named by and linked to its service", () => {
    const chart = timeChart(
      panel("top_services_trend", { signal: "latency", order: "slowest", count: 2 }),
      reading([
        { label: "p95_ms", service: "checkout", points: [[T0, 120]] },
        { label: "p95_ms", service: "pay‮ment", points: [[T0, 80]] },
      ]),
    );
    expect(chart.distinctColours).toBe(true);
    expect(chart.lines).toEqual([
      { name: "checkout", service: "checkout", estimate: true },
      { name: "pay[U+202E]ment", service: "pay‮ment", estimate: true },
    ]);
    expect(chart.yRange).toBe("zero");
  });

  it("reads a node's gauge off zero, in its own unit", () => {
    const chart = timeChart(
      panel(
        "node_trend",
        {
          node: { host: "web-01", container: "", reporter: "", reporter_instance: "" },
          metric: "system.cpu.utilization",
        },
        "large",
      ),
      reading([{ label: "system.cpu.utilization", points: [[T0, 0.42]] }]),
    );
    expect(chart.yRange).toBe("fit");
    expect(chart.distinctColours).toBe(false);
    expect(chart.height).toBe(LARGE_CHART_HEIGHT);
    expect(chart.format(0.42)).toBe("42.0%");
  });

  it("keeps a medium chart at today's height", () => {
    const chart = timeChart(
      panel("project_trend", { signal: "rate" }),
      reading([{ label: "requests_per_minute", points: [[T0, 1]] }]),
    );
    expect(chart.height).toBe(CHART_HEIGHT);
    expect(chart.distinctColours).toBe(false);
    expect(chart.yRange).toBe("zero");
  });

  it("leaves a gap a gap, never a zero", () => {
    const chart = timeChart(
      panel("top_services_trend", { signal: "rate", order: "worst", count: 2 }),
      reading([
        {
          label: "requests_per_minute",
          service: "a",
          points: [
            [T0, 5],
            [T1, 6],
          ],
        },
        { label: "requests_per_minute", service: "b", points: [[T1, 7]] },
      ]),
    );
    expect(chart.values).toEqual([
      [5, 6],
      [null, 7],
    ]);
  });
});

describe("one dashboard, one cursor", () => {
  it("gives every chart on a dashboard the same sync key, and another dashboard another", () => {
    expect(syncKeyOf("d1")).toBe(syncKeyOf("d1"));
    expect(syncKeyOf("d1")).not.toBe(syncKeyOf("d2"));
  });
});

describe("a dragged window, rounded out (FR53)", () => {
  const at = (iso: string) => Date.parse(iso) / 1000;

  it("rounds a span under an hour out to whole minutes", () => {
    expect(roundedOut(at("2026-11-09T10:10:17Z"), at("2026-11-09T10:19:44Z"))).toEqual({
      from: "2026-11-09T10:10:00.000Z",
      to: "2026-11-09T10:20:00.000Z",
    });
  });

  it("rounds a span of an hour or more out to whole five minutes", () => {
    expect(roundedOut(at("2026-11-09T10:03:00Z"), at("2026-11-09T11:11:00Z"))).toEqual({
      from: "2026-11-09T10:00:00.000Z",
      to: "2026-11-09T11:15:00.000Z",
    });
  });

  it("gives two colleagues dragging nearly the same spike one window, so they share reads", () => {
    const mine = roundedOut(at("2026-11-09T10:12:05Z"), at("2026-11-09T10:17:50Z"));
    const theirs = roundedOut(at("2026-11-09T10:12:40Z"), at("2026-11-09T10:17:10Z"));
    expect(mine).toEqual(theirs);
  });

  it("refuses a drag that is not a span", () => {
    expect(roundedOut(10, 10)).toBeUndefined();
    expect(roundedOut(20, 10)).toBeUndefined();
    expect(roundedOut(Number.NaN, 10)).toBeUndefined();
  });

  it("keeps a whole step even for a drag inside one minute", () => {
    expect(roundedOut(at("2026-11-09T10:10:00Z"), at("2026-11-09T10:10:00.5Z"))).toEqual({
      from: "2026-11-09T10:10:00.000Z",
      to: "2026-11-09T10:11:00.000Z",
    });
  });
});
