/**
 * Counts and percentages, and the one rule every screen in this application turns on:
 * **a figure we do not have is a rule, never a zero.**
 *
 * "0 errors" and "we have no reading for errors" are opposite claims. A screen that
 * prints the first when it means the second tells a customer their system is healthy at
 * the moment we have stopped being able to see it, which is the failure an observability
 * product exists to prevent. So a reading that is absent, not a number, or negative —
 * none of which a measured quantity can be — renders as an em dash, and only a genuine
 * zero renders as "0".
 */

/** Nothing measured. A rule, never a zero. */
export const UNKNOWN = "—";

/** Fixed locale: the digit grouping must not change with the reader's machine. */
const NUMBER_LOCALE = "en-US";

const PERCENT = 100;
const SMALLEST_SHOWN_PERCENT = 0.1;

/**
 * Whether a value is a measurement at all.
 *
 * Negative is included: a count, a byte total, a rate and a share are all quantities
 * that cannot go below zero, so a negative one is arithmetic that broke — a counter that
 * reset mid-window, a subtraction against a missing reading — and not a figure to print.
 */
export function isReading(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value) && value >= 0;
}

export function formatCount(count: number | null | undefined): string {
  if (!isReading(count)) return UNKNOWN;
  return Math.round(count).toLocaleString(NUMBER_LOCALE);
}

/**
 * A fraction on the wire, a percentage on the screen.
 *
 * Deliberately not clamped to 100: a figure above it means the roll-up is wrong, and
 * hiding that is how a wrong number survives.
 */
export function formatPercent(fraction: number | null | undefined): string {
  if (!isReading(fraction)) return UNKNOWN;

  const percent = fraction * PERCENT;
  // A share too small to show is not an absent one, and "0.0%" would say it was.
  if (percent > 0 && percent < SMALLEST_SHOWN_PERCENT) return `<${SMALLEST_SHOWN_PERCENT}%`;
  return `${percent.toFixed(1)}%`;
}

/** Trailing zeros after the point are noise in a column; digits before it are not. */
export function trimTrailingZeros(fixed: string): string {
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}
