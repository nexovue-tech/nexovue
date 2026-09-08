import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatMoney } from "@/shared/format/money";
import { formatCount } from "@/shared/format/number";

import type { BillingOverview, BillingPeriodUsage, SubscriptionSummary } from "./api";

/**
 * The sentences the billing screen says, kept out of the markup so they can be
 * asserted.
 *
 * Every one of them is a claim about money or about telemetry stopping, which
 * is why none of them is assembled inline in a component. The four rules held
 * here: an absent figure is words and never a zero, a ceiling that is enforced
 * says so and one that is not stays silent, a cancelled arrangement is named
 * rather than hidden, and nothing is described as spent that the meter has not
 * finished counting.
 */

/** How close the month is to the allowance, or null when nothing sizes one. */
export function allowanceFraction(period: BillingPeriodUsage): number | null {
  if (period.included_bytes === null || period.included_bytes <= 0) return null;
  return period.bytes_ingested / period.included_bytes;
}

/**
 * What happens at the ceiling, in the customer's own terms.
 *
 * ADR 0107 requires the screen to be able to write this before it happens as
 * well as after: telemetry that stops is indistinguishable from a service that
 * went quiet, and that is the one failure an observability product must not
 * cause.
 */
export function describeAllowance(period: BillingPeriodUsage): string {
  if (period.included_bytes === null) {
    return "No ingestion limit applies to this plan.";
  }
  const limit = formatDecimalBytes(period.included_bytes);
  const used = formatDecimalBytes(period.bytes_ingested);
  if (!period.allowance_enforced) {
    return `${used} of ${limit} used this month. Going past it does not stop ingestion.`;
  }
  return `${used} of ${limit} used this month. Ingestion stops when it is reached, until ${monthTurns(period)}.`;
}

/** The day the allowance resets, which is what a refused customer needs told. */
function monthTurns(period: BillingPeriodUsage): string {
  const last = new Date(`${period.last_day}T00:00:00Z`);
  if (Number.isNaN(last.getTime())) return "the month turns";
  const next = new Date(last);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toLocaleDateString("en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The month's cost, or why there is not one.
 *
 * Null is never rendered as a zero: an unpriced tier is not a free one, and a
 * customer shown "$0.00" for a tier priced by agreement has been told something
 * false rather than something incomplete.
 */
export function describeCost(period: BillingPeriodUsage): string {
  if (period.service_cost_minor_units === null || period.currency === null) {
    return "Priced by agreement.";
  }
  const amount = formatMoney(period.service_cost_minor_units, period.currency);
  const services = `${formatCount(period.billed_services)} billed`;
  return period.settled
    ? `${amount} for ${services}.`
    : `${amount} for ${services} so far — the month is still being metered.`;
}

/** What the arrangement is, said plainly enough to act on. */
export function describeSubscription(subscription: SubscriptionSummary): string {
  const ceiling = `${formatCount(subscription.services_committed)} services committed`;
  switch (subscription.status) {
    case "active":
      return `Active on ${subscription.plan_slug}, ${ceiling}.`;
    case "pending":
      return `Waiting for your bank to confirm the mandate. Nothing has been charged, and the plan moves when they confirm.`;
    case "cancelled":
      return `Cancelled. The plan returns to the free tier at the end of the period the processor is billing.`;
    default:
      return `${subscription.status} on ${subscription.plan_slug}, ${ceiling}.`;
  }
}

/**
 * Whether the upgrade form should be drawn at all.
 *
 * Three separate reasons not to, and they are different sentences to a
 * customer: nobody can be charged because no processor is configured, they are
 * already on this arrangement, or the mandate is still with their bank.
 */
export function checkoutAvailability(
  overview: BillingOverview,
): { available: true } | { available: false; reason: string } {
  if (overview.checkout === null) {
    return { available: false, reason: "Upgrading is not available on this deployment yet." };
  }
  const status = overview.subscription?.status;
  if (status === "pending") {
    return {
      available: false,
      reason: "A mandate is already waiting for your bank. Cancel it before starting another.",
    };
  }
  if (status === "active") {
    return {
      available: false,
      reason: "This organization already has an active subscription.",
    };
  }
  return { available: true };
}

/** The label a contact field is asked for under, keyed by the API's own names. */
export const CONTACT_FIELD_LABELS: Record<string, { label: string; autoComplete: string }> = {
  name: { label: "Full name", autoComplete: "name" },
  email: { label: "Billing email", autoComplete: "email" },
  phone: { label: "Mobile number", autoComplete: "tel" },
};
