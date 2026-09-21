import { describe, expect, it } from "vitest";

import type { PhaseBreakdown, PhaseBucket } from "./api";
import { buildPhasePlot, describeBreakdown, dominantPhase, seriesTokenFor } from "./phase-series";

/** The five phases the HTTP prober reports, in the order the endpoint serves them. */
const PHASES = ["dns", "tcp", "tls", "ttfb", "transfer"];

/** Shaped like the real response: every phase in every bucket, and they sum to total. */
function bucket(timestamp: string, durations: Record<string, number>): PhaseBucket {
  const filled = Object.fromEntries(PHASES.map((phase) => [phase, durations[phase] ?? 0]));
  return {
    timestamp,
    durations_ms: filled,
    total_ms: Object.values(filled).reduce((sum, value) => sum + value, 0),
    samples: 4,
  };
}

const BREAKDOWN: PhaseBreakdown = {
  check_id: "6d3c2b1a-0000-4000-8000-000000000001",
  bucket_seconds: 30,
  phases: PHASES,
  buckets: [
    bucket("2026-08-30T10:00:00Z", { dns: 12, tcp: 24, tls: 61, ttfb: 704, transfer: 18 }),
    bucket("2026-08-30T10:00:30Z", { dns: 9, tcp: 22, tls: 58, ttfb: 812, transfer: 21 }),
    bucket("2026-08-30T10:01:00Z", { dns: 11, tcp: 26, tls: 64, ttfb: 1021, transfer: 22 }),
  ],
};

/** Floats through a running total; equality to the cent is not the claim being made. */
const TOLERANCE = 1e-9;

describe("buildPhasePlot", () => {
  const plot = buildPhasePlot(BREAKDOWN);

  it("puts unix seconds on x, because uPlot's default scale is seconds", () => {
    expect(plot.data[0]).toEqual([
      Date.UTC(2026, 7, 30, 10, 0, 0) / 1000,
      Date.UTC(2026, 7, 30, 10, 0, 30) / 1000,
      Date.UTC(2026, 7, 30, 10, 1, 0) / 1000,
    ]);
  });

  it("emits one row per phase plus the x row", () => {
    expect(plot.data).toHaveLength(PHASES.length + 1);
    expect(plot.data.every((row) => row.length === BREAKDOWN.buckets.length)).toBe(true);
  });

  it("draws outermost first, so a smaller fill paints each band over the one below", () => {
    expect(plot.drawn.map((series) => series.phase)).toEqual([...PHASES].reverse());
  });

  /** The invariant this whole screen rests on: the phases are the duration, not a sample of it. */
  it("makes the outermost series exactly the reported total in every bucket", () => {
    const outermost = plot.data[1];
    BREAKDOWN.buckets.forEach((source, index) => {
      expect(outermost[index]).toBeCloseTo(source.total_ms, 9);
    });
  });

  it("has fixture buckets whose phases sum to the reported total", () => {
    for (const source of BREAKDOWN.buckets) {
      const summed = Object.values(source.durations_ms).reduce((sum, value) => sum + value, 0);
      expect(Math.abs(summed - source.total_ms)).toBeLessThan(TOLERANCE);
    }
  });

  it("stacks monotonically — every cumulative row sits under the one outside it", () => {
    // data[1] is the outermost; each following row is one phase smaller.
    for (let row = 2; row < plot.data.length; row++) {
      for (let bucket = 0; bucket < plot.bucketCount; bucket++) {
        expect(plot.data[row][bucket]).toBeLessThanOrEqual(plot.data[row - 1][bucket]);
      }
    }
  });

  it("makes the innermost series the first phase alone", () => {
    const innermost = plot.data[plot.data.length - 1];
    expect(innermost).toEqual([12, 9, 11]);
  });

  it("reports each phase's mean and its share of the request", () => {
    const ttfb = plot.legend.find((reading) => reading.phase === "ttfb");
    expect(ttfb?.meanMs).toBeCloseTo((704 + 812 + 1021) / 3, 9);

    const shares = plot.legend.reduce((sum, reading) => sum + reading.sharePercent, 0);
    expect(shares).toBeCloseTo(100, 9);
  });

  it("means the same total whether taken per phase or per bucket", () => {
    const fromPhases = plot.legend.reduce((sum, reading) => sum + reading.meanMs, 0);
    expect(fromPhases).toBeCloseTo(plot.meanTotalMs, 9);
  });

  it("keeps the legend in request order while the chart draws in reverse", () => {
    expect(plot.legend.map((reading) => reading.phase)).toEqual(PHASES);
  });
});

describe("buildPhasePlot with nothing to draw", () => {
  it("is empty for an absent response rather than a chart of one point at zero", () => {
    expect(buildPhasePlot(undefined).bucketCount).toBe(0);
  });

  it("is empty when the window held no buckets", () => {
    expect(buildPhasePlot({ ...BREAKDOWN, buckets: [] }).drawn).toEqual([]);
  });

  it("is empty when the check reported no phases at all", () => {
    expect(buildPhasePlot({ ...BREAKDOWN, phases: [] }).legend).toEqual([]);
  });
});

describe("buildPhasePlot with an unexpected shape", () => {
  it("treats a phase key missing from a bucket as zero, not NaN", () => {
    const partial: PhaseBreakdown = {
      ...BREAKDOWN,
      phases: ["dns", "ttfb"],
      buckets: [
        { timestamp: "2026-08-30T10:00:00Z", durations_ms: { dns: 5 }, total_ms: 5, samples: 1 },
      ],
    };
    expect(buildPhasePlot(partial).data[1]).toEqual([5]);
  });

  it("plots a phase the prober added later, wrapping the palette rather than dropping it", () => {
    const extended = { ...BREAKDOWN, phases: [...PHASES, "redirect", "verify"] };
    const plot = buildPhasePlot(extended);
    expect(plot.legend).toHaveLength(7);
    expect(plot.legend[6].seriesToken).toBe(seriesTokenFor(0));
  });
});

describe("seriesTokenFor", () => {
  it("takes the palette in its own order, which the palette itself keeps distinct", () => {
    // It used to reorder them to dodge two tokens that were twenty degrees apart.
    // The palette carries that guarantee now; `verify-contrast.js` enforces it.
    expect([0, 1, 2, 3, 4, 5].map(seriesTokenFor)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("wraps, so a seventh phase reuses the first colour rather than none", () => {
    expect(seriesTokenFor(6)).toBe(seriesTokenFor(0));
  });
});

describe("dominantPhase", () => {
  it("names the phase the time actually went to", () => {
    expect(dominantPhase(buildPhasePlot(BREAKDOWN).legend)?.phase).toBe("ttfb");
  });

  it("prefers the earlier phase on a tie — it is the one upstream", () => {
    const flat = buildPhasePlot({
      ...BREAKDOWN,
      buckets: [bucket("2026-08-30T10:00:00Z", { dns: 10, tcp: 10 })],
    });
    expect(dominantPhase(flat.legend)?.phase).toBe("dns");
  });

  it("has no answer when there is nothing to read", () => {
    expect(dominantPhase([])).toBeUndefined();
  });
});

describe("the sentence over the chart says what it measured (D4)", () => {
  const legend = [
    { phase: "dns", seriesToken: 1 as const, meanMs: 3.5, sharePercent: 40 },
    { phase: "ttfb", seriesToken: 4 as const, meanMs: 5.2, sharePercent: 60 },
  ];

  it("explains the phase when the runs it measured actually succeeded", () => {
    const said = describeBreakdown(legend, 8.7, { runs: 60, successfulRuns: 60 });

    expect(said).toContain("the server thinking before it answered");
    expect(said).not.toContain("failed");
  });

  it("never narrates a server answering when no run in the window did", () => {
    // The defect: 0% uptime, every run refused at the connection, and the screen
    // still read "ttfb is 60% of the mean 8.7ms — the server thinking before it
    // answered".
    const said = describeBreakdown(legend, 8.7, { runs: 60, successfulRuns: 0 });

    expect(said).not.toContain("the server thinking");
    expect(said).toContain("up to the point each run failed");
  });

  it("owns up to the failures it folded in", () => {
    const said = describeBreakdown(legend, 8.7, { runs: 60, successfulRuns: 57 });

    expect(said).toContain("3 of 60 runs failed");
    expect(said).toContain("ends early");
  });

  it("says only what a phase is when there is nothing to measure", () => {
    expect(describeBreakdown([], 0)).toContain("five phases");
  });
});
