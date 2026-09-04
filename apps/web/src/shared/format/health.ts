/**
 * The errors cell, which the service list and one service's detail both draw.
 *
 * Health is the absence of colour, and of a number: a service that failed nothing gets a
 * rule, not a zero to read past. A count we never received gets the same rule, so the
 * cell is never a claim we cannot support.
 */

import { formatCount, formatPercent, isReading, UNKNOWN } from "./number";

export function formatErrors(
  count: number | null | undefined,
  rate: number | null | undefined,
): string {
  if (!isReading(count) || count === 0) return UNKNOWN;
  return `${formatCount(count)} · ${formatPercent(rate)}`;
}
