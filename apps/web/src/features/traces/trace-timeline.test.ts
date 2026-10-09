import { describe, expect, it } from "vitest";

import type { Span } from "./api";
import { buildTimeline, placeSpan, spanEndMs, timelineTicks, toEpochMs } from "./trace-timeline";

function span(startMs: number, durationMs: number): Span {
  return {
    span_id: "s",
    parent_span_id: "",
    service_name: "svc",
    span_name: "s",
    span_kind: "INTERNAL",
    start_time: new Date(startMs).toISOString(),
    duration_ms: durationMs,
    status_code: "UNSET",
    status_message: "",
    attributes: {},
    is_model_call: false,
  };
}

describe("toEpochMs", () => {
  it("reads an ISO timestamp", () => {
    expect(toEpochMs("2026-08-27T10:15:04.123Z")).toBe(Date.UTC(2026, 7, 27, 10, 15, 4, 123));
  });

  it("falls back to 0 rather than poisoning every later sum with NaN", () => {
    expect(toEpochMs("not a time")).toBe(0);
  });
});

describe("buildTimeline", () => {
  it("spans the extremes of what actually arrived", () => {
    const timeline = buildTimeline([span(1000, 50), span(1200, 300)]);
    expect(timeline.startMs).toBe(1000);
    expect(timeline.endMs).toBe(1500);
    expect(timeline.spanMs).toBe(500);
  });

  it("widens for a child that ends after its parent — skew stays visible, not clipped", () => {
    const timeline = buildTimeline([span(1000, 100), span(1050, 400)]);
    expect(timeline.endMs).toBe(1450);
  });

  it("widens for a child that starts before its parent", () => {
    expect(buildTimeline([span(1000, 10), span(900, 10)]).startMs).toBe(900);
  });

  it("is empty, not NaN, for a trace with no spans", () => {
    expect(buildTimeline([])).toEqual({ startMs: 0, endMs: 0, spanMs: 0 });
  });

  it("ignores a negative duration rather than running the window backwards", () => {
    expect(spanEndMs(span(1000, -50))).toBe(1000);
  });
});

describe("placeSpan", () => {
  const timeline = buildTimeline([span(1000, 1000)]);

  it("places a span proportionally within the window", () => {
    const window = buildTimeline([span(0, 1000)]);
    expect(placeSpan(span(250, 500), window)).toEqual({ offsetPercent: 25, widthPercent: 50 });
  });

  it("gives an instantaneous span a visible mark instead of nothing", () => {
    const window = buildTimeline([span(0, 1000)]);
    expect(placeSpan(span(500, 0), window).widthPercent).toBeGreaterThan(0);
  });

  it("makes a single instantaneous span the whole window — it is the entire trace", () => {
    expect(placeSpan(span(1000, 0), buildTimeline([span(1000, 0)]))).toEqual({
      offsetPercent: 0,
      widthPercent: 100,
    });
  });

  it("never lets a bar overflow the track", () => {
    const placement = placeSpan(span(1900, 5000), timeline);
    expect(placement.offsetPercent + placement.widthPercent).toBeLessThanOrEqual(100);
  });

  it("clamps a span starting before the window to the left edge", () => {
    expect(placeSpan(span(0, 100), timeline).offsetPercent).toBe(0);
  });
});

describe("timelineTicks", () => {
  it("returns count+1 gridlines spanning the full width", () => {
    const ticks = timelineTicks(buildTimeline([span(0, 800)]), 4);
    expect(ticks).toHaveLength(5);
    expect(ticks[0]).toEqual({ percent: 0, atMs: 0 });
    expect(ticks[4]).toEqual({ percent: 100, atMs: 800 });
  });
});
