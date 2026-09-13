/**
 * How a reading changed against the previous equivalent window.
 *
 * **Most readings carry no delta, and that is the design.** A row annotated
 * every time a number moved by a percent is a row of noise, and the reader stops
 * seeing the one that moved by forty. This is the same principle as a healthy
 * row carrying no status mark: an annotation has to mean something, so it is
 * spent only where the change is worth an eye.
 *
 * Three separate things make a delta absent, and none of them is a zero:
 *
 * - **No prior window.** A service that first reported an hour ago has nothing
 *   to be compared against. "0%" would claim it was flat.
 * - **Too little behind it.** Two requests becoming three is a 50% rise and
 *   means nothing. A measure says how much traffic it needs before a change is
 *   evidence.
 * - **Unremarkable.** The change happened and is not worth saying.
 */

/** What kind of quantity is being compared, which decides how it is compared. */
export type Measure = "rate" | "ratio" | "duration" | "count";

export interface Delta {
  /** Which way it went. "up" is not "good": that depends on the measure. */
  direction: "up" | "down";
  /** Signed, in the measure's own terms — a fraction for a rate, points for a ratio. */
  change: number;
  /** How it reads: "+38%", "−1.2pp". */
  display: string;
}

/**
 * A ratio is compared in points, not in percent.
 *
 * An error rate going from 0.1% to 0.2% has doubled, and nobody needs to know.
 * One going from 0% to 2% is a relative change of infinity, which no percentage
 * can express. Both are answered by measuring the gap between them instead.
 */
const RATIO_POINTS = 0.005;

/**
 * A duration is compared relatively, and a fifth slower is worth an eye.
 */
const DURATION_CHANGE = 0.2;

/**
 * How much a volume has to move before it is news, which is a lot.
 *
 * Traffic rises and falls on its own all day, so a threshold tuned to latency
 * annotates almost every row and the annotation stops meaning anything.
 * Measured against the development project rather than guessed: at a fifth, 14
 * of 15 services carried a delta over six hours and 6 of 16 over a day. At a
 * half, one did and none did — and the one was a window the collector had been
 * restarted inside, which is exactly the change worth seeing.
 */
const VOLUME_CHANGE = 0.5;

/**
 * How much traffic the prior window needs before its number is evidence.
 *
 * Below this a change is arithmetic rather than a finding. It applies to the
 * sample behind the reading, not to the reading — an error rate computed from
 * four requests is unreliable however large the rate looks.
 */
const ENOUGH_SAMPLES = 20;

export function deltaOf(
  measure: Measure,
  current: number | null | undefined,
  previous: number | null | undefined,
  /** The sample each side rests on, when the reading is an average over one. */
  samples?: { current: number; previous: number },
): Delta | null {
  if (!isReading(current) || !isReading(previous)) return null;
  if (samples && (samples.current < ENOUGH_SAMPLES || samples.previous < ENOUGH_SAMPLES)) {
    return null;
  }

  if (measure === "ratio") {
    const change = current - previous;
    if (Math.abs(change) < RATIO_POINTS) return null;
    return { direction: change > 0 ? "up" : "down", change, display: asPoints(change) };
  }

  // Relative change needs something to be relative to. A rate rising from zero
  // is a real event with no percentage: it is reported as a change of direction
  // rather than as a ratio nothing can divide.
  if (previous === 0) {
    if (current === 0) return null;
    return { direction: "up", change: current, display: "new" };
  }

  const change = (current - previous) / previous;
  const threshold = measure === "duration" ? DURATION_CHANGE : VOLUME_CHANGE;
  if (Math.abs(change) < threshold) return null;
  return { direction: change > 0 ? "up" : "down", change, display: asPercent(change) };
}

function isReading(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** A true minus sign, not a hyphen: these sit in a column of numbers. */
const MINUS = "−";

function asPercent(change: number): string {
  const percent = Math.round(Math.abs(change) * 100);
  return `${change > 0 ? "+" : MINUS}${percent}%`;
}

function asPoints(change: number): string {
  const points = Math.abs(change) * 100;
  // One decimal below ten points, none above: "+0.8pp" is a real distinction,
  // "+41.3pp" is false precision on a number that size.
  const written = points < 10 ? points.toFixed(1) : Math.round(points).toString();
  return `${change > 0 ? "+" : MINUS}${written}pp`;
}
