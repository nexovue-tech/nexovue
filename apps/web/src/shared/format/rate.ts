/**
 * Calls per minute, in the two widths this application has room for.
 *
 * A table cell can hold every digit of a large rate; a node drawn on the service map has
 * to hold a name and a number inside one small box, so there the digit count matters
 * more than the precision — `4.8k` beats `4,798`. Both refuse to round a live service
 * down to nothing, because a few calls an hour is traffic and "0" would deny it.
 */

import { formatCount, isReading, trimTrailingZeros, UNKNOWN } from "./number";

/** Below this a rate rounds to "0.0", which reads as "none" when it means "a few". */
const SMALLEST_SHOWN_RATE = 0.1;

/** Above this the whole number carries the magnitude and the decimal is noise. */
const DECIMAL_BELOW = 10;

const THOUSAND = 1000;
const MILLION = 1_000_000;

/** The rate in a table cell: a decimal while it is small, grouped digits once it is not. */
export function formatRatePerMinute(perMinute: number | null | undefined): string {
  if (!isReading(perMinute)) return UNKNOWN;
  if (perMinute === 0) return "0";
  if (perMinute < SMALLEST_SHOWN_RATE) return `<${SMALLEST_SHOWN_RATE}`;
  if (perMinute < DECIMAL_BELOW) return perMinute.toFixed(1);
  return formatCount(perMinute);
}

/** The same rate drawn inside a map node, abbreviated to a near-constant width. */
export function formatCompactRate(perMinute: number | null | undefined): string {
  if (!isReading(perMinute)) return UNKNOWN;
  if (perMinute === 0) return "0";
  if (perMinute < SMALLEST_SHOWN_RATE) return `<${SMALLEST_SHOWN_RATE}`;
  if (perMinute < DECIMAL_BELOW) return trimTrailingZeros(perMinute.toFixed(1));
  if (perMinute < THOUSAND) return String(Math.round(perMinute));
  if (perMinute < MILLION) return `${trimTrailingZeros((perMinute / THOUSAND).toFixed(1))}k`;
  return `${trimTrailingZeros((perMinute / MILLION).toFixed(1))}M`;
}
