/**
 * The numbers and the sentences on the usage screen.
 *
 * A customer reads a number here as a bill, so four rules are enforced in this file
 * rather than left to the markup: a missing price is words and never a zero, a month
 * still being metered says so beside its cost, money is divided by its own currency's
 * minor unit, and the cost shown names what it does not include.
 */

import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatCount } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";

import type { BillingPeriod, CardinalityWarning, PlanPrice, ServiceNameFamily } from "./api";

/** Fixed locale: digit grouping and the currency symbol must not follow the machine. */
const NUMBER_LOCALE = "en-US";

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
  return `${formatMoney(price.price_per_service_minor_units, price.currency)} per service`;
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

/* -------------------------------------------------------- the month, and its cost */

const monthFormatter = new Intl.DateTimeFormat(NUMBER_LOCALE, {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

/** The billing period named as a month, because that is what it is. */
export function formatPeriod(period: BillingPeriod): string {
  const at = new Date(`${period.first_day}T00:00:00Z`);
  return Number.isNaN(at.getTime()) ? period.first_day : monthFormatter.format(at);
}

export const NO_COST_LABEL = "No cost shown";

/** Short, because the rate beside it carries the full explanation. */
export const NO_COST_REASON = "There is no recorded rate to apply, and a zero would read as free.";

/**
 * The cost of the month's services, or nothing at all.
 *
 * Undefined rather than a zero when no price is recorded, and the caller writes
 * words instead: an unpriced tier is not a free one.
 */
export function formatPeriodCost(period: BillingPeriod): string | undefined {
  if (period.service_cost_minor_units === null || period.currency === null) return undefined;
  return formatMoney(period.service_cost_minor_units, period.currency);
}

/**
 * What the figure is, said in full: which quantity, over which period.
 *
 * A ceiling that bit is named as a ceiling on the *bill*, in the customer's own
 * direction: they are running eighteen services and being billed for three. The
 * earlier wording — "the 18 counted are held to the plan's ceiling" — read as a
 * discount, which is what a service cap is not: the eighteenth service is monitored
 * and unbilled, and saying so plainly is the honest half of the sentence.
 */
export function describeCostBasis(period: BillingPeriod, planName: string): string {
  const services = `${formatCount(period.billed_services)} ${plural(period.billed_services, "service")}`;
  const basis = `${services} in ${formatPeriod(period)}, at the rate in force.`;
  if (period.billed_services >= period.services) return basis;
  return `${basis} You are running ${formatCount(period.services)} services; ${planName} bills ${formatCount(period.billed_services)}.`;
}

/**
 * The one thing the figure leaves out, which is not the same sentence on every tier.
 *
 * Where the allowance is enforced there is no overage to bill, because there is no
 * overage: it is refused (ADR 0093). Where it is not, what volume above the allowance
 * costs is still undecided, and saying so is the difference between a partial number
 * and a misleading one.
 */
export const COST_EXCLUDES_OVERAGE =
  "This covers the services only. What volume above the included allowance costs is not decided, so nothing for it is included here.";

export const NO_OVERAGE_TO_BILL =
  "This covers the services only. Volume above the included allowance is refused rather than billed, so there is nothing to add for it.";

export function describeOverage(period: BillingPeriod): string {
  return period.allowance_enforced ? NO_OVERAGE_TO_BILL : COST_EXCLUDES_OVERAGE;
}

/** A month still being metered is labelled rather than read as a small bill. */
export function describePeriodProgress(period: BillingPeriod): string {
  if (period.settled) return `${formatPeriod(period)} has finished being metered.`;
  return `${formatPeriod(period)} is still being metered, so this will grow.`;
}

/**
 * The day a spent allowance comes back: the day after the month it covered.
 *
 * The month's own last day, not the clock's — a screen looking back at August in
 * September must not claim the allowance returns tomorrow.
 */
export function allowanceResetDay(period: BillingPeriod): string {
  const at = new Date(`${period.last_day}T00:00:00Z`);
  if (Number.isNaN(at.getTime())) return period.last_day;
  at.setUTCDate(at.getUTCDate() + 1);
  return at.toISOString().slice(0, 10);
}

/**
 * What is left of the month's volume, and what happens when it runs out.
 *
 * Where the allowance is enforced the sentence says so before it is spent, not only
 * after: a refusal a customer could not see coming is the failure this screen exists
 * to prevent, and the receiver's refusal is invisible to everyone but whoever reads
 * their exporter's logs.
 */
export function describeAllowance(period: BillingPeriod): string | undefined {
  if (period.included_bytes === null) return undefined;

  const used = `${formatDecimalBytes(period.bytes_ingested)} of ${formatDecimalBytes(period.included_bytes)} included`;
  const spent = period.bytes_ingested > period.included_bytes;

  if (!period.allowance_enforced) {
    return spent ? `${used} — over the allowance.` : used;
  }
  const resumes = formatDay(allowanceResetDay(period));
  if (spent) {
    return `${used} — spent. Further ingestion is refused until ${resumes}.`;
  }
  return `${used}. Ingestion stops once this is spent and resumes ${resumes}.`;
}

/** Whether the door is refusing this organization's exports right now. */
export function isAllowanceSpent(period: BillingPeriod): boolean {
  return (
    period.allowance_enforced &&
    period.included_bytes !== null &&
    period.bytes_ingested > period.included_bytes
  );
}

/**
 * Said again, in the open, when it is actually happening.
 *
 * The receiver's refusal reaches only whoever reads their exporter's logs, so a
 * customer whose telemetry has stopped needs to be able to find out here. It names
 * the loss rather than softening it: the data sent while refused is gone.
 */
export function describeAllowanceSpent(period: BillingPeriod): string {
  return `The volume included in ${formatPeriod(period)} is spent, so exports are being refused until ${formatDay(allowanceResetDay(period))}. Telemetry sent in the meantime is not stored and cannot be recovered.`;
}

/* -------------------------------------------------------------- service names */

const FAMILY_KINDS: Record<string, string> = {
  uuid: "a uuid",
  hex: "a hex identifier",
  "kubernetes-pod": "a Kubernetes pod suffix",
  ordinal: "an ordinal",
};

/** The change itself. Both numbers, because one of them is the surprise. */
export function describeServiceChange(warning: CardinalityWarning): string {
  const now = `${formatCount(warning.services_now)} ${plural(warning.services_now, "service")}`;
  if (warning.services_before === 0) {
    return `${now}, none in the ${warning.window_days} days before.`;
  }
  return `${now}, up from ${formatCount(warning.services_before)} in the ${warning.window_days} days before.`;
}

/**
 * What the names look like, and what it costs to leave them that way.
 *
 * Named rather than implied: the customer can only act on this if they are told
 * which names, and what to move the identifier into.
 */
export function describeFamily(family: ServiceNameFamily): string {
  const shape = FAMILY_KINDS[family.kind] ?? "an identifier";
  return `${formatCount(family.members)} names differ from “${family.stem}” only by ${shape}, and each is billed as its own service.`;
}

export const CARDINALITY_REMEDY =
  "service.name must be the same for every instance of a horizontally scaled service; what tells instances apart belongs in service.instance.id.";

export const NOTHING_IS_CAPPED =
  "Nothing is dropped on account of this. Telemetry we discarded to hold a bill down is telemetry missing from your next incident.";

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
