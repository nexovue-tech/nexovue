import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardContext, PanelResult, StoredPanel } from "./api";
import { seriesListWindow } from "./api";
import { worstCaseReads } from "./draft";
import type { OkResult } from "./kept-readings";
import { ALL_IN_PROJECT, marksFor } from "./panel-marks";
import { combinedWords, mixedUnitsWords, panelTitle, stateWords, unread } from "./panel-words";
import { metricFormat, timeChart } from "./time-lines";

/**
 * A metric series panel in the console (F20, Step 8), as data: its line named by its
 * attributes through the escapes (FR61, AC74's data half), every series combined into one
 * line that says how many (AC52), two units drawing nothing and saying so (AC51), a
 * changed series and too many series in words that follow the reader's role (AC115,
 * FR65), the project's marks with their note (AC132), and a chosen series counted as two
 * reads (design, *`series_changed`*). The clock is pinned; nothing here reads it.
 */

const NOW = Date.parse("2026-11-12T10:00:00.000Z");
const HASH = "13040248669695784898";
const HOSTILE = "<img src=x onerror=alert(1)>‮";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

const metricPanel = (settings: Record<string, unknown> = {}): StoredPanel => ({
  id: "p_metric",
  kind: "metric_series",
  title: null,
  width: 6,
  height: "medium",
  settings: {
    metric: "orders_processed",
    shape: "sum",
    service: "checkout",
    series: HASH,
    aggregation: "sum",
    ...settings,
  },
});

const ok = (changes: Partial<OkResult> = {}): OkResult => ({
  state: "ok",
  kind: "metric_series",
  read_at: "2026-11-12T10:00:00.000Z",
  bucket_seconds: 60,
  data_starts_at: "2026-11-12T09:00:00.000Z",
  data_ends_at: "2026-11-12T09:02:00.000Z",
  series: [
    {
      label: "orders_processed",
      attributes: [{ key: "region", value: "eu" }],
      points: [
        ["2026-11-12T09:00:00.000Z", 5],
        ["2026-11-12T09:01:00.000Z", 8],
        ["2026-11-12T09:02:00.000Z", 10],
      ],
    },
  ],
  series_combined: 2,
  units: ["1"],
  ...changes,
});

const words = (result: PanelResult, shown: StoredPanel, canEdit: boolean) =>
  stateWords(result, shown, {
    canEdit,
    owner: "Asha",
    projectName: "staging",
    overLimit: 0,
    now: NOW,
  });

describe("a metric series line", () => {
  it("is named by the chosen series' attributes, each escaped (FR61)", () => {
    const chart = timeChart(
      metricPanel(),
      ok({
        series: [
          {
            label: "orders_processed",
            attributes: [
              { key: "region", value: HOSTILE },
              { key: "tier", value: "gold" },
            ],
            points: [["2026-11-12T09:00:00.000Z", 5]],
          },
        ],
      }),
    );
    const [line] = chart.lines;
    expect(line?.name).not.toContain("‮");
    expect(line?.name).toContain("[U+202E]");
    expect(line?.name).toMatch(/^region=<img src=x onerror=alert\(1\)>\[U\+202E\], tier=gold$/);
  });

  it("combining every series says how many it combined (AC52)", () => {
    const all = metricPanel({ series: null });
    const chart = timeChart(
      all,
      ok({
        series_combined: 1234,
        series: [{ label: "orders_processed", attributes: null, points: [] }],
      }),
    );
    expect(chart.lines[0]?.name).toBe("orders_processed · 1,234 series combined");
    expect(combinedWords(all, 1234)).toBe("Combines 1,234 series.");
    expect(combinedWords(all, 1)).toBeUndefined();
    expect(panelTitle(all)).toEqual({
      service: "checkout",
      text: "orders_processed · all series, total",
    });
  });

  it("draws a sum's rise from zero and a gauge's level as it is", () => {
    expect(timeChart(metricPanel(), ok()).yRange).toBe("zero");
    expect(timeChart(metricPanel({ shape: "gauge" }), ok()).yRange).toBe("fit");
    expect(timeChart(metricPanel(), ok()).values).toEqual([[5, 8, 10]]);
  });

  it("writes each reading in the unit its metric was sent in, a gauge below zero too", () => {
    expect(metricFormat("ms")(1500)).toBe("1.5s");
    expect(metricFormat("s")(2)).toBe("2s");
    expect(metricFormat("By")(2_000_000)).toMatch(/^2(\.0)?\s?MB$/);
    expect(metricFormat("1")(1234.5678)).toBe("1,234.57");
    expect(metricFormat("{jobs}")(-3)).toBe("-3 {jobs}");
    expect(metricFormat(HOSTILE)(1)).toContain("[U+202E]");
    expect(metricFormat("")(Number.NaN)).toBe("—");
  });
});

describe("a metric series' words", () => {
  it("says two units are not added, and names them (AC51)", () => {
    expect(mixedUnitsWords(metricPanel(), ["ms", "s"])).toBe(
      "orders_processed arrives in 2 units in this window (ms, s), so its series are not added together.",
    );
    expect(mixedUnitsWords(metricPanel(), ["ms"])).toBeUndefined();
    expect(mixedUnitsWords({ ...metricPanel(), kind: "node_trend" }, ["a", "b"])).toBeUndefined();
  });

  it("says a changed series still reports, and only an editor is told to choose (AC115, FR65)", () => {
    const changed: PanelResult = { state: "series_changed", kind: "metric_series" };
    expect(words(changed, metricPanel(), true)).toBe(
      "orders_processed still reports, under different attributes. Choose again.",
    );
    expect(words(changed, metricPanel(), false)).toBe(
      "orders_processed still reports, under different attributes. Ask Asha, or someone who can edit it, to choose again.",
    );
    expect(unread(changed)).toBe(true);
  });

  it("says too many series to combine, with the cap (AC52)", () => {
    const many: PanelResult = { state: "too_many_series", kind: "metric_series", limit: 1000 };
    expect(words(many, metricPanel({ series: null }), true)).toBe(
      "This metric has more than 1,000 series, too many to combine. Choose one series.",
    );
    expect(unread(many)).toBe(true);
  });

  it("says a metric with nothing has not reported, not that its service is quiet", () => {
    const nothing: PanelResult = {
      state: "empty",
      kind: "metric_series",
      read_at: "2026-11-12T10:00:00.000Z",
      last_seen_at: null,
    };
    expect(words(nothing, metricPanel(), true)).toBe(
      "This metric has not reported in this window.",
    );
  });
});

describe("a metric series' marks", () => {
  it("draws every finding and release in the project and says so (FR18, AC132)", () => {
    const context = {
      window_start: "2026-11-12T09:00:00.000Z",
      window_end: "2026-11-12T10:00:00.000Z",
      findings: [
        {
          id: "f1",
          scope: "service",
          service_name: "payments",
          severity: "warning",
          title: "payments is slower",
          started_at: "2026-11-12T09:10:00.000Z",
          closed_at: null,
        },
      ],
      markers: [],
      coverage: {},
    } as unknown as DashboardContext;

    const marking = marksFor(metricPanel(), context);

    expect(marking.note).toBe(ALL_IN_PROJECT);
    expect(marking.marks.spans).toHaveLength(1);
  });
});

describe("a metric series' reads", () => {
  const definition = (...panels: StoredPanel[]) => ({
    schema_version: 1 as const,
    refresh_seconds: 60 as const,
    default_range: null,
    service_picker: false,
    links: [],
    panels,
  });

  it("counts a chosen series as two, one per metric and service, and every series as one", () => {
    const eu = metricPanel();
    const us = { ...metricPanel({ series: "42" }), id: "p_us" };
    const all = { ...metricPanel({ series: null }), id: "p_all" };
    expect(worstCaseReads(definition(eu))).toBe(2);
    expect(worstCaseReads(definition(eu, us))).toBe(3);
    expect(worstCaseReads(definition(all))).toBe(1);
  });

  it("lists series over at most the last 7 days of the window (AC121)", () => {
    expect(
      seriesListWindow({ from: "2026-10-13T10:00:00.000Z", to: "2026-11-12T10:00:00.000Z" }),
    ).toEqual({ from: "2026-11-05T10:00:00.000Z", to: "2026-11-12T10:00:00.000Z" });
    const hour = { from: "2026-11-12T09:00:00.000Z", to: "2026-11-12T10:00:00.000Z" };
    expect(seriesListWindow(hour)).toBe(hour);
  });
});
