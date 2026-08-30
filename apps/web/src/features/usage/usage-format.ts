/**
 * The numbers and the sentences on the usage screen.
 *
 * A customer reads a number here as a bill, so three rules are enforced in this file
 * rather than left to the markup: a missing price is words and never a zero, a range
 * still being counted says so, and nothing multiplies a rate by a quantity.
 */

import type { PlanPrice } from "./api";

/** Fixed locale: digit grouping and the currency symbol must not follow the machine. */
const NUMBER_LOCALE = "en-US";

const BYTE_UNITS = ["B", "kB", "MB", "GB", "TB", "PB"] as const;
// Decimal, not 1024: this is the number a customer compares against a per-GB price
// somewhere else, and everyone quoting per-GB quotes decimal.
const BYTES_PER_UNIT = 1000;

export function formatCount(count: number): string {
  if (!Number.isFinite(count)) return "0";
  return Math.round(count).toLocaleString(NUMBER_LOCALE);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";

  let value = bytes;
  let unit = 0;
  while (value >= BYTES_PER_UNIT && unit < BYTE_UNITS.length - 1) {
    value /= BYTES_PER_UNIT;
    unit += 1;
  }
  return `${unit === 0 ? Math.round(value) : value.toFixed(1)} ${BYTE_UNITS[unit]}`;
}

const dayFormatter = new Intl.DateTimeFormat(NUMBER_LOCALE, {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** `2026-08-10` as it is read. UTC, because that is the day the meter counted. */
export function formatDay(day: string): string {
  const at = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(at.getTime()) ? day : dayFormatter.format(at);
}

/* ------------------------------------------------------------------------ money */

const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat | undefined {
  const cached = moneyFormatters.get(currency);
  if (cached) return cached;
  try {
    const formatter = new Intl.NumberFormat(NUMBER_LOCALE, { style: "currency", currency });
    moneyFormatters.set(currency, formatter);
    return formatter;
  } catch {
    return undefined; // not a currency code at all; see formatMoney
  }
}

/**
 * A price stored in minor units, in its own currency.
 *
 * The divisor is the currency's own, not a hardcoded hundred: the yen's minor unit is
 * the yen, so ¥1000 is stored as 1000 and dividing by a hundred would quote a bill at a
 * hundredth of its size. `Intl` carries the ISO 4217 exponent, so it is asked rather
 * than guessed. A code `Intl` will not take is a broken row, and the minor units are
 * shown raw beside it rather than converted by a guessed exponent.
 */
export function formatMoney(minorUnits: number, currency: string): string {
  const formatter = moneyFormatter(currency);
  if (!formatter) return `${formatCount(minorUnits)} ${currency}`;

  const exponent = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  return formatter.format(minorUnits / 10 ** exponent);
}

export function formatRate(price: PlanPrice): string {
  return `${formatMoney(price.price_per_service_cents, price.currency)} per service`;
}

export function describeRateStart(price: PlanPrice): string {
  return `In force since ${formatDay(price.effective_from)}.`;
}

/** Only when there is one. Saying "no ceiling" would be a promise this row cannot make. */
export function describeCeiling(price: PlanPrice): string | undefined {
  if (price.max_billed_services === null) return undefined;
  return `At most ${formatCount(price.max_billed_services)} ${plural(price.max_billed_services, "service")} are billed.`;
}

export const NO_PRICE_LABEL = "Not priced";

export function describeNoPrice(planName: string): string {
  return `No price is recorded for the ${planName} plan, so none is shown. A zero here would read as free, which is not what a missing price means.`;
}

/** The rate and the quantity sit side by side. Multiplying them is a decision nobody has taken. */
export const NO_COST_NOTE =
  "This screen shows the rate and the services it applies to, and does not multiply them. Nothing here is an invoice.";

/* ------------------------------------------------------------------ the sentences */

export function describeScope(rangeLabel: string): string {
  return `Every project in this organization over the last ${rangeLabel} — the bill is the organization's, not one project's.`;
}

/** The union, said in words, because the daily column beside it invites adding up. */
export const UNION_CAPTION =
  "Counted once each across the whole range: a service reporting every day is one service, not one a day.";

export const DAY_TABLE_CAPTION =
  "One project on one day. The services column counts that day alone — the total above counts each service once across the range.";

export const STILL_COUNTING = "still counting";

/**
 * Whether the numbers above are final.
 *
 * A range can be unfinished with every day it lists finished, because a day the meter
 * has not reached has no row here to carry the caveat itself.
 */
export function describeCounting(settled: boolean, unsettledDays: number): string {
  if (settled) return "Every day in this range has finished being counted.";
  if (unsettledDays > 0) {
    return `${formatCount(unsettledDays)} ${plural(unsettledDays, "day")} here ${unsettledDays === 1 ? "has" : "have"} not finished being counted, so these numbers can still grow.`;
  }
  return "Part of this range has not finished being counted, so these numbers can still grow.";
}

function plural(count: number, word: string): string {
  return count === 1 ? word : `${word}s`;
}
