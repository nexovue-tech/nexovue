/**
 * Rates and ratios, written the way they are read. Latency goes through the shared
 * duration formatter, so a millisecond reads the same here as in a waterfall.
 *
 * These four functions also exist in `features/services/health-format.ts`, and the
 * copy is deliberate rather than accidental: biome forbids one feature importing
 * another, so the only way to share them is to move that module into `shared/format/`
 * and delete it from the services feature — a change in a directory this task does not
 * own. Until that happens the two must be kept identical.
 */

/** Fixed locale: the digit grouping must not change with the reader's machine. */
const NUMBER_LOCALE = "en-US";

const RATE_DECIMAL_THRESHOLD = 10;
const SMALLEST_SHOWN_RATE = 0.1;
const PERCENT = 100;
const SMALLEST_SHOWN_PERCENT = 0.1;

/**
 * Requests per minute. Below a tenth it rounds to "0.0", which reads as "none" when it
 * means "a few an hour" — so that band gets a floor marker instead.
 */
export function formatRatePerMinute(perMinute: number): string {
  if (!Number.isFinite(perMinute) || perMinute <= 0) return "0";
  if (perMinute < SMALLEST_SHOWN_RATE) return "<0.1";
  if (perMinute < RATE_DECIMAL_THRESHOLD) return perMinute.toFixed(1);
  return Math.round(perMinute).toLocaleString(NUMBER_LOCALE);
}

export function formatCount(count: number): string {
  if (!Number.isFinite(count)) return "0";
  return Math.round(count).toLocaleString(NUMBER_LOCALE);
}

/** A fraction on the wire, a percentage on the screen. */
export function formatErrorRate(fraction: number): string {
  if (!Number.isFinite(fraction) || fraction <= 0) return "0%";

  const percent = fraction * PERCENT;
  if (percent < SMALLEST_SHOWN_PERCENT) return "<0.1%";
  return `${percent.toFixed(1)}%`;
}

/**
 * The errors cell. Health is the absence of colour, and of a number: a service that
 * failed nothing gets a rule, not a zero to read past.
 */
export function formatErrors(count: number, rate: number): string {
  if (count <= 0) return "—";
  return `${formatCount(count)} · ${formatErrorRate(rate)}`;
}
