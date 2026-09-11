import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatMoney } from "@/shared/format/money";
import { formatCount } from "@/shared/format/number";

import type { BillingOverview, BillingPeriodUsage, PlanOption, SubscriptionSummary } from "./api";

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
 * The screen must be able to write this before it happens as well as after:
 * telemetry that stops is indistinguishable from a service that went quiet, and
 * that is the one failure an observability product must not cause.
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
export function describeCost(
  period: BillingPeriodUsage,
  ratePerServiceMinorUnits: number | null,
): string {
  if (period.service_cost_minor_units === null || period.currency === null) {
    return "Priced by agreement.";
  }

  // **A rate of zero is a tier that is not billed, and it needs no quantity.**
  // The free tier caps the count it would bill at three, so this row read
  // "₹0.00 for 3 billed" beside a Services row saying 26 — two numbers that
  // disagree, about a bill that does not exist. The cap is doing real work, but
  // it is sizing the month's *volume*, and the allowance line below says so.
  if (ratePerServiceMinorUnits === 0) return "Nothing to pay on this plan.";

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
    // Not "waiting for your bank". A `pending` row is written when the customer
    // is handed to the processor, so it is equally the state of somebody who
    // walked back without authorising anything — and this row cannot tell the
    // two apart, because the only thing that ever reports an authorisation is
    // the webhook, and that moves the row to `active`. Naming a bank that was
    // never asked describes an arrangement the customer does not have.
    case "pending":
      return `A checkout was started and is not finished. Nothing has been charged — if you authorised the mandate, the plan moves when the processor confirms it.`;
    case "cancelled":
      return `Cancelled. The plan returns to the free tier at the end of the period the processor is billing.`;
    default:
      return `${subscription.status} on ${subscription.plan_slug}, ${ceiling}.`;
  }
}

/**
 * What ending this arrangement actually does, which depends on whether it ever
 * began. A `pending` row bills nothing and holds no plan, so the free tier is
 * where the customer already is — warning them about losing it names a loss
 * they cannot take, and reads as a threat over a checkout they abandoned.
 */
export function describeCancellation(subscription: SubscriptionSummary): string {
  if (subscription.status === "pending") {
    return "This discards a checkout that was never completed. Nothing has been charged, and the plan does not change.";
  }
  return "Cancelling returns this organization to the free tier when the processor's current period ends. The free tier's allowance is smaller, and ingestion stops when it is reached.";
}

/** What the button ends: an arrangement, or an attempt at one. */
export function cancellationLabel(subscription: SubscriptionSummary): string {
  return subscription.status === "pending" ? "Discard this checkout" : "Cancel subscription";
}

/** The confirming button, worded to match what is being ended. */
export function cancellationConfirmLabel(subscription: SubscriptionSummary): string {
  return subscription.status === "pending" ? "Yes, discard it" : "Yes, cancel the subscription";
}

/**
 * Whether the upgrade form should be drawn at all.
 *
 * **A `pending` row does not block one, and that is the correction.** This used
 * to refuse with "a mandate is already waiting for your bank", which trapped the
 * customer it was meant to protect: a `pending` row is a checkout that was
 * *started*, not a mandate that exists. Anyone who closed the tab, lost the
 * page, or hit an error at the processor was left with the form hidden and
 * nothing to press but Cancel — on an arrangement their bank had never
 * authorised.
 *
 * The API had it right all along: `begin_plan_change` refuses only an *active*
 * arrangement on the same plan, so the screen was stricter than the service.
 *
 * Two mandates cannot quietly both charge, because only one can reach `active` —
 * the second checkout is refused once the first activates.
 */
export function checkoutAvailability(
  overview: BillingOverview,
): { available: true } | { available: false; reason: string } {
  if (overview.checkout === null) {
    return { available: false, reason: "Upgrading is not available on this deployment yet." };
  }
  if (overview.subscription?.status === "active") {
    return {
      available: false,
      reason: "This organization already has an active subscription.",
    };
  }
  return { available: true };
}

/**
 * The line above the form when a checkout was started and never finished.
 *
 * Said rather than hidden: the customer is about to see a form they have already
 * filled in once, and not explaining why reads as the first attempt having
 * silently failed.
 */
export function unfinishedCheckoutNotice(overview: BillingOverview): string | null {
  if (overview.subscription?.status !== "pending") return null;
  return "You started a checkout that was never completed. Nothing has been charged. Starting another replaces it.";
}

/**
 * What a month on a plan comes to, at a given commitment.
 *
 * The bill *is* the commitment. Not "up to" — a customer who commits
 * to five services and runs three pays for five, and a screen that hedged the
 * number would be the one place they were told otherwise.
 */
export function monthlyTotalMinorUnits(plan: PlanOption, services: number): number {
  return plan.price_per_service_minor_units * services;
}

/** The count the picker starts on: what they already committed to, or one. */
export function initialCommitment(overview: BillingOverview): number {
  return overview.subscription?.services_committed ?? 1;
}

/** The label a contact field is asked for under, keyed by the API's own names. */
export const CONTACT_FIELD_LABELS: Record<string, { label: string; autoComplete: string }> = {
  name: { label: "Full name", autoComplete: "name" },
  email: { label: "Billing email", autoComplete: "email" },
  phone: { label: "Mobile number", autoComplete: "tel" },
};
