import { formatDuration } from "@/shared/format/duration";

import type { PhaseBreakdown, PhaseBucket } from "./api";

/**
 * Turning the phase breakdown into something a stacked area chart draws directly.
 *
 * The endpoint already guarantees the hard parts: every bucket carries every phase, a
 * phase with no reading is 0, and `phases` is request order. So the only work here is
 * the running total, and it is worth doing in one tested place — a stack drawn from
 * per-phase values instead of cumulative ones looks plausible and is wrong.
 */

/**
 * uPlot's aligned-data shape, spelled out here so this module stays free of the library.
 * One x row, then one y row per drawn series.
 */
export type StackedRows = [xSeconds: number[], ...cumulative: number[][]];

export interface PhasePlot {
  data: StackedRows;
  /**
   * Draw order — outermost band first. Every row is filled down to the baseline, so
   * drawing the largest cumulative first and painting smaller ones over it leaves each
   * phase's own band visible in its own colour. Same picture as uPlot's band-pair
   * stacking with none of its index arithmetic.
   */
  drawn: DrawnSeries[];
  /** Request order, for reading. The chart is drawn in the reverse of this. */
  legend: PhaseReading[];
  bucketCount: number;
  /** Mean total across the buckets that carry a reading. */
  meanTotalMs: number;
}

export interface DrawnSeries {
  phase: string;
  seriesToken: SeriesToken;
}

export interface PhaseReading {
  phase: string;
  seriesToken: SeriesToken;
  meanMs: number;
  /** Share of the mean total, 0–100. Zero when nothing was measured. */
  sharePercent: number;
}

export type SeriesToken = 1 | 2 | 3 | 4 | 5 | 6;

/**
 * The tokens in their own order, which is now the right order.
 *
 * This used to reorder them — `[1, 3, 5, 2, 4, 6]` — because `--series-1` (200°) and
 * `--series-2` (180°) were twenty degrees apart and adjacent bands in a stack share an
 * edge. That was a workaround in one chart for a palette that had two colours too close
 * together everywhere else as well. The palette was fixed instead: every pair is now at
 * least 30° apart in hue or 0.10 apart in lightness, enforced by
 * `packages/ui/scripts/verify-contrast.js`, so neighbours no longer need rearranging.
 */
const PALETTE_ORDER: readonly SeriesToken[] = [1, 2, 3, 4, 5, 6];

const PERCENT = 100;

const EMPTY_PLOT: PhasePlot = {
  data: [[]],
  drawn: [],
  legend: [],
  bucketCount: 0,
  meanTotalMs: 0,
};

/** uPlot's default `ms: 1e-3` means the x scale is unix seconds, not milliseconds. */
const MS_PER_SECOND = 1000;

export function seriesTokenFor(phaseIndex: number): SeriesToken {
  return PALETTE_ORDER[phaseIndex % PALETTE_ORDER.length];
}

export function buildPhasePlot(breakdown: PhaseBreakdown | undefined): PhasePlot {
  if (!breakdown || breakdown.phases.length === 0 || breakdown.buckets.length === 0) {
    return EMPTY_PLOT;
  }

  const { phases, buckets } = breakdown;
  const timestamps = buckets.map((bucket) => Date.parse(bucket.timestamp) / MS_PER_SECOND);

  // One cumulative row per phase, in request order: row k is the sum of phases 0..k.
  const cumulative = phases.map(() => new Array<number>(buckets.length).fill(0));
  for (let bucket = 0; bucket < buckets.length; bucket++) {
    let running = 0;
    for (let phase = 0; phase < phases.length; phase++) {
      running += durationOf(buckets[bucket], phases[phase]);
      cumulative[phase][bucket] = running;
    }
  }

  const drawn = phases
    .map((phase, index) => ({ phase, seriesToken: seriesTokenFor(index) }))
    .reverse();

  return {
    data: [timestamps, ...[...cumulative].reverse()],
    drawn,
    legend: readPhases(phases, buckets),
    bucketCount: buckets.length,
    meanTotalMs: mean(buckets.map((bucket) => bucket.total_ms)),
  };
}

/** Every phase's mean over the window, and how much of the request it accounts for. */
function readPhases(phases: string[], buckets: PhaseBucket[]): PhaseReading[] {
  const means = phases.map((phase) => mean(buckets.map((bucket) => durationOf(bucket, phase))));
  const total = means.reduce((sum, value) => sum + value, 0);

  return phases.map((phase, index) => ({
    phase,
    seriesToken: seriesTokenFor(index),
    meanMs: means[index],
    sharePercent: total > 0 ? (means[index] / total) * PERCENT : 0,
  }));
}

/**
 * The phase the eye should land on. Ties go to the earlier phase, which is the one
 * further up the request — fixing dns makes tcp's number move, and not the reverse.
 */
export function dominantPhase(legend: readonly PhaseReading[]): PhaseReading | undefined {
  return legend.reduce<PhaseReading | undefined>(
    (worst, reading) => (worst === undefined || reading.meanMs > worst.meanMs ? reading : worst),
    undefined,
  );
}

/**
 * A phase key the endpoint added after this screen was written still plots; it just has
 * no reading in an older bucket. Treating a missing key as 0 keeps the stack additive
 * rather than leaving a NaN that swallows the whole series.
 */
function durationOf(bucket: PhaseBucket, phase: string): number {
  const value = bucket.durations_ms[phase];
  return Number.isFinite(value) ? value : 0;
}

function mean(values: readonly number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

/** What each phase actually is, so the sentence explains rather than names. */
const PHASE_MEANING: Record<string, string> = {
  dns: "resolving the name",
  tcp: "opening the connection",
  tls: "the TLS handshake",
  ttfb: "the server thinking before it answered",
  transfer: "sending the response body",
};

const ALL_PHASES =
  "Each probe is timed in five phases — dns, tcp, tls, ttfb and transfer — which together are the whole request.";

/** How many of the window's runs the readings above were measured from. */
export interface RunCoverage {
  runs: number;
  successfulRuns: number;
}

/**
 * The sentence over the chart.
 *
 * **A failed run is timed too**, and its phases are real: DNS still resolved
 * before the connection was refused (`probes/http_check.py`). What is not real
 * is the explanation — "the server thinking before it answered" was printed
 * over a window in which no server answered at all
 * (`docs/quality/console-review-2026-09-20.md`, D4). So the sentence says what
 * the readings were measured from whenever some of the runs failed, and drops
 * the per-phase meaning entirely when none of them succeeded.
 */
export function describeBreakdown(
  legend: readonly PhaseReading[],
  meanTotalMs: number,
  coverage?: RunCoverage,
): string {
  const worst = dominantPhase(legend);
  if (!worst || meanTotalMs <= 0) return ALL_PHASES;

  const failed = coverage ? coverage.runs - coverage.successfulRuns : 0;
  const share = Math.round(worst.sharePercent);
  const total = formatDuration(meanTotalMs);

  if (coverage && coverage.runs > 0 && coverage.successfulRuns === 0) {
    return `${worst.phase} is ${share}% of the mean ${total} — measured up to the point each run failed, not to an answer.`;
  }

  const meaning = PHASE_MEANING[worst.phase];
  const explanation = meaning ? ` — ${meaning}` : "";
  const caveat =
    failed > 0
      ? ` ${failed} of ${coverage?.runs} runs failed and are counted here, and a failed run ends early.`
      : "";
  return `${worst.phase} is ${share}% of the mean ${total}${explanation}.${caveat}`;
}
