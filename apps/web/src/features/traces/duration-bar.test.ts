import { describe, expect, it } from "vitest";

import type { TraceSummary } from "./api";
import { barFraction, slowestDuration } from "./TraceListScreen";

/**
 * One ceiling for the whole list, never per row. A bar that autoscaled to its
 * own value is full width on every row, and a list where everything is full
 * width says nothing — the same rule `Sparkline` follows, for the same reason.
 */

const trace = (duration_ms: number): TraceSummary => ({
  trace_id: "89862496c280c749485ae30629e9737f",
  root_service: "storefront",
  root_name: "GET /checkout",
  start_time: "2026-09-13T10:00:00.000Z",
  duration_ms,
  span_count: 3,
  error_count: 0,
  status_code: "OK",
  span_services: [],
  span_endpoints: [],
});

describe("the scale every bar is drawn against", () => {
  it("is the slowest trace on the page", () => {
    expect(slowestDuration([trace(40), trace(900), trace(120)])).toBe(900);
  });

  it("is zero on an empty page rather than a crash", () => {
    expect(slowestDuration([])).toBe(0);
  });
});

describe("the width of one bar", () => {
  it("makes two different durations two different widths", () => {
    // The assertion the whole column exists for.
    expect(barFraction(100, 1000)).toBeLessThan(barFraction(800, 1000));
  });

  it("fills the cell at the ceiling and never past it", () => {
    expect(barFraction(1000, 1000)).toBe(1);
    // A page whose ceiling moved between renders must not draw past its box.
    expect(barFraction(4000, 1000)).toBe(1);
  });

  it("draws nothing when there is no scale to draw against", () => {
    // A zero-width bar on a single-trace page reads as "instant" rather than
    // as "nothing to compare this to yet".
    expect(barFraction(120, 0)).toBe(0);
  });

  it("draws nothing for a duration that is not one", () => {
    expect(barFraction(0, 1000)).toBe(0);
    expect(barFraction(-5, 1000)).toBe(0);
    expect(barFraction(Number.NaN, 1000)).toBe(0);
  });
});
