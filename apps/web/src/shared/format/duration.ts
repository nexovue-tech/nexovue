/**
 * Durations read as a quantity, not a number. A waterfall is scanned, not parsed, so
 * the unit changes with the magnitude and the digit count stays roughly constant.
 *
 * A duration we do not have is a rule, not "0ms": an instant span and an unmeasured one
 * are different facts (see `number.ts`).
 */

import { isReading, trimTrailingZeros, UNKNOWN } from "./number";

const MICROSECONDS_PER_MS = 1000;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const MS_PER_MINUTE = MS_PER_SECOND * SECONDS_PER_MINUTE;

/**
 * A duration written in the unit its own axis uses.
 *
 * `formatDuration` picks a unit per value, which is right for a reading standing
 * on its own and wrong for a row of ticks: a waterfall spanning 492µs read
 * "0ms · 123µs · 246µs · 369µs", changing unit between the first tick and the
 * second (`docs/quality/console-review-2026-09-20.md`). Zero is the only value
 * with no magnitude to choose from, so it borrows the scale's.
 */
export function formatDurationOn(milliseconds: number, scaleMs: number): string {
  if (milliseconds !== 0) return formatDuration(milliseconds);
  if (!isReading(scaleMs) || scaleMs >= MS_PER_MINUTE) return "0s";
  if (scaleMs >= MS_PER_SECOND) return "0s";
  if (scaleMs >= 1) return "0ms";
  return "0µs";
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (!isReading(milliseconds)) return UNKNOWN;
  if (milliseconds === 0) return "0ms";

  if (milliseconds < 1) return `${Math.round(milliseconds * MICROSECONDS_PER_MS)}µs`;
  if (milliseconds < MS_PER_SECOND) return `${trimTrailingZeros(milliseconds.toFixed(1))}ms`;
  if (milliseconds < MS_PER_MINUTE) {
    return `${trimTrailingZeros((milliseconds / MS_PER_SECOND).toFixed(2))}s`;
  }

  const minutes = Math.floor(milliseconds / MS_PER_MINUTE);
  const seconds = Math.round((milliseconds % MS_PER_MINUTE) / MS_PER_SECOND);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}
