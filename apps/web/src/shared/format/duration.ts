/**
 * Durations read as a quantity, not a number. A waterfall is scanned, not parsed, so
 * the unit changes with the magnitude and the digit count stays roughly constant.
 */

const MICROSECONDS_PER_MS = 1000;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const MS_PER_MINUTE = MS_PER_SECOND * SECONDS_PER_MINUTE;

export function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "0ms";

  if (milliseconds < 1) return `${Math.round(milliseconds * MICROSECONDS_PER_MS)}µs`;
  if (milliseconds < MS_PER_SECOND) return `${trim(milliseconds.toFixed(1))}ms`;
  if (milliseconds < MS_PER_MINUTE) return `${trim((milliseconds / MS_PER_SECOND).toFixed(2))}s`;

  const minutes = Math.floor(milliseconds / MS_PER_MINUTE);
  const seconds = Math.round((milliseconds % MS_PER_MINUTE) / MS_PER_SECOND);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** Trailing zeros after the point are noise in a column; digits before it are not. */
function trim(fixed: string): string {
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}
