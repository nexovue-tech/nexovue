/**
 * Byte totals in the two unit systems, which are not interchangeable.
 *
 * A bill is compared against a per-GB price, and everyone quoting one quotes decimal;
 * memory is quoted in GiB by every host tool there is. Sharing one convention would put
 * two meanings of "G" on the same screen, so the caller picks the one its column means.
 */

import { isReading, UNKNOWN } from "./number";

const DECIMAL_UNITS = ["B", "kB", "MB", "GB", "TB", "PB"] as const;
const DECIMAL_BASE = 1000;

const BINARY_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
const BINARY_BASE = 1024;

/** Once three digits are already there, a decimal adds width without adding meaning. */
const DIGITS_ARE_ENOUGH = 100;

/** The number a customer compares against a per-GB price quoted somewhere else. */
export function formatDecimalBytes(bytes: number | null | undefined): string {
  return scale(bytes, DECIMAL_BASE, DECIMAL_UNITS);
}

/** The number every host tool would have shown for the same memory. */
export function formatBinaryBytes(bytes: number | null | undefined): string {
  return scale(bytes, BINARY_BASE, BINARY_UNITS);
}

function scale(bytes: number | null | undefined, base: number, units: readonly string[]): string {
  if (!isReading(bytes)) return UNKNOWN;

  let value = bytes;
  let unit = 0;
  while (value >= base && unit < units.length - 1) {
    value /= base;
    unit += 1;
  }

  // Whole bytes are never a fraction of a byte, so the smallest unit keeps no decimal.
  const digits = unit === 0 || value >= DIGITS_ARE_ENOUGH ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}
