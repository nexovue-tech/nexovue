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
