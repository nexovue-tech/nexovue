import { describe, expect, it } from "vitest";

import type {
  BillingOverview,
  BillingPeriodUsage,
  Charge,
  PlanOption,
  SubscriptionSummary,
} from "./api";
import { CONFIRMATION_POLL_MS, CONFIRMATION_WINDOW_MS, confirmationPoll } from "./api";
import {
  allowanceFraction,
  cancellationLabel,
  chargeAmount,
  chargeDay,
  chargeOutcome,
  chargeTerms,
  checkoutAvailability,
  commitmentError,
  commitmentForPlan,
  describeAllowance,
  describeCancellation,
  describeCost,
  describeSubscription,
  HISTORY_IS_NOT_AN_INVOICE,
  initialCommitment,
  monthlyTotalMinorUnits,
  PAYMENT_METHOD_IS_THE_PROVIDERS,
  unfinishedCheckoutNotice,
} from "./billing-view";

const GIB = 1024 ** 3;

function period(overrides: Partial<BillingPeriodUsage> = {}): BillingPeriodUsage {
  return {
    first_day: "2026-09-01",
    last_day: "2026-09-30",
    settled: true,
    services: 3,
    billed_services: 3,
    bytes_ingested: 2 * GIB,
    browser_bytes_ingested: 0,
    service_cost_minor_units: 3_000,
    currency: "USD",
    included_bytes: 6 * GIB,
    browser_included_bytes: null,
    allowance_enforced: true,
    ...overrides,
  };
}

function subscription(overrides: Partial<SubscriptionSummary> = {}): SubscriptionSummary {
  return {
    plan_slug: "pro",
    status: "active",
    services_committed: 5,
    activated_at: "2026-09-01T00:00:00Z",
    cancelled_at: null,
    ...overrides,
  };
}

function overviewWith(overrides: Partial<BillingOverview> = {}): BillingOverview {
  return {
    plan: {
      slug: "free",
      name: "Free",
      raw_retention_days: 7,
      derived_retention_days: 30,
    },
    price: null,
    subscription: null,
    period: period(),
    checkout: { required_contact_fields: ["email"] },
    currency: "USD",
    available_currencies: ["INR", "USD"],
    plans: [],
    ...overrides,
  };
}

describe("what happens at the ceiling", () => {
  /**
   * Telemetry that stops is indistinguishable from a service that went
   * quiet, so the screen has to be able to say this *before* it happens.
   */
  it("says ingestion stops, and names the day the allowance resets", () => {
    const said = describeAllowance(period());

    expect(said).toContain("Ingestion stops");
    // The 1st, not the 30th: a refused customer needs the day it comes back.
    expect(said).toContain("Oct 1, 2026");
  });

  it("does not threaten a stop on a plan where nothing is enforced", () => {
    const said = describeAllowance(period({ allowance_enforced: false }));

    expect(said).not.toContain("stops");
    expect(said).toContain("does not stop ingestion");
  });

  it("says there is no limit rather than showing a limit of nothing", () => {
    expect(describeAllowance(period({ included_bytes: null }))).toBe(
      "No ingestion limit applies to this plan.",
    );
  });
});

describe("the proportion spent", () => {
  it("is the month against the allowance", () => {
    expect(allowanceFraction(period())).toBeCloseTo(1 / 3);
  });

  /** Nothing to be a proportion of, and dividing would be a NaN on the screen. */
  it("is nothing when no allowance is recorded", () => {
    expect(allowanceFraction(period({ included_bytes: null }))).toBeNull();
    expect(allowanceFraction(period({ included_bytes: 0 }))).toBeNull();
  });
});

/** Any non-zero rate: this suite is about the sentence, not the arithmetic. */
const RATE = 600;

describe("the month's cost", () => {
  /**
   * An unpriced tier is not a free one. "$0.00" is a different statement from
   * "priced by agreement", and it is the one that is false.
   */
  it("says priced by agreement rather than zero", () => {
    const said = describeCost(period({ service_cost_minor_units: null, currency: null }), null);

    expect(said).toBe("Priced by agreement.");
    expect(said).not.toContain("0");
  });

  it("divides by the currency's own minor unit", () => {
    expect(describeCost(period(), RATE)).toContain("$30.00");
  });

  /** A figure that is still being counted must not read as a final bill. */
  it("says a month still being metered is not finished", () => {
    expect(describeCost(period({ settled: false }), RATE)).toContain("still being metered");
  });

  /**
   * The free tier caps the count it would bill at three. Beside a Services row
   * reading 26, this row said "for 3 billed" — two numbers disagreeing about a
   * bill that does not exist, because the rate is zero.
   */
  it("names no billed quantity on a tier that is not billed", () => {
    const said = describeCost(period({ billed_services: 3, service_cost_minor_units: 0 }), 0);

    expect(said).toBe("Nothing to pay on this plan.");
    expect(said).not.toContain("3");
    expect(said).not.toContain("billed");
  });

  /** The quantity is exactly what a paying customer needs to check their bill. */
  it("still names it when there is something to pay", () => {
    expect(describeCost(period({ billed_services: 26 }), RATE)).toContain("26 billed");
  });
});

describe("the arrangement", () => {
  it("names the ceiling the customer committed to", () => {
    expect(describeSubscription(subscription())).toContain("5 services committed");
  });

  /** Nothing has been charged yet, and saying otherwise would alarm somebody. */
  it("says a pending checkout has charged nothing", () => {
    expect(describeSubscription(subscription({ status: "pending" }))).toContain(
      "Nothing has been charged",
    );
  });

  /**
   * The row is written when the customer is handed to the processor, so it is
   * also the state of somebody who pressed back. Telling that person their bank
   * is confirming a mandate describes an arrangement they never made.
   */
  it("does not claim a bank is confirming a pending checkout", () => {
    const said = describeSubscription(subscription({ status: "pending" }));

    expect(said).not.toContain("your bank");
    expect(said).toContain("not finished");
  });

  it("offers to discard a pending checkout rather than cancel a subscription", () => {
    const pending = subscription({ status: "pending" });

    expect(cancellationLabel(pending)).toBe("Discard this checkout");
    expect(describeCancellation(pending)).toContain("never completed");
  });

  /** A live arrangement really does end at the processor's period boundary. */
  it("still warns about losing the allowance when there is something to lose", () => {
    const active = subscription({ status: "active" });

    expect(cancellationLabel(active)).toBe("Cancel subscription");
    expect(describeCancellation(active)).toContain("free tier");
  });

  /**
   * The free tier is where a pending customer already is, so warning them they
   * will be returned to it names a loss they cannot take.
   */
  it("does not threaten a pending customer with the free tier", () => {
    expect(describeCancellation(subscription({ status: "pending" }))).not.toContain("free tier");
  });

  /**
   * Shown rather than hidden: a customer whose mandate ended and who is shown
   * nothing at all learns less than one shown the row that ended.
   */
  it("says a cancelled arrangement returns to the free tier", () => {
    expect(describeSubscription(subscription({ status: "cancelled" }))).toContain("free tier");
  });
});

describe("how many services a plan can be bought for", () => {
  const plan = (min: number | null) => ({ min_committed_services: min }) as PlanOption;

  it("asks for one or more on a plan with no minimum", () => {
    expect(commitmentError(0, plan(null))).toBe("A whole number of services, one or more.");
    expect(commitmentError(1.5, plan(null))).toBe("A whole number of services, one or more.");
    expect(commitmentError(1, plan(null))).toBeUndefined();
  });

  it("says the plan's minimum, and accepts it", () => {
    expect(commitmentError(9, plan(10))).toBe("This plan starts at 10 services.");
    expect(commitmentError(10, plan(10))).toBeUndefined();
  });

  it("starts the field at the minimum when the current number is below it", () => {
    expect(commitmentForPlan(3, plan(10))).toBe(10);
    expect(commitmentForPlan(12, plan(10))).toBe(12);
    expect(commitmentForPlan(3, plan(null))).toBe(3);
  });
});

describe("what a month costs at a commitment", () => {
  const pro: PlanOption = {
    slug: "pro",
    name: "Pro",
    currency: "INR",
    price_per_service_minor_units: 39_900,
    included_bytes_per_service: 10 * 1024 ** 3,
    max_billed_services: null,
    min_committed_services: null,
    raw_retention_days: 30,
    derived_retention_days: 395,
  };

  /**
   * The bill *is* the commitment, not "up to" it. A customer who
   * commits to five and runs three pays for five, and the screen must be the
   * place that says so rather than the invoice.
   */
  it("is the rate times the count", () => {
    expect(monthlyTotalMinorUnits(pro, 5)).toBe(199_500);
  });

  it("is the rate itself for one service", () => {
    expect(monthlyTotalMinorUnits(pro, 1)).toBe(39_900);
  });
});

describe("where the commitment field starts", () => {
  /** Changing plan should not silently reset somebody's ceiling to one. */
  it("is what they already committed to", () => {
    expect(
      initialCommitment(overviewWith({ subscription: subscription({ services_committed: 8 }) })),
    ).toBe(8);
  });

  it("is one for an organization that has never subscribed", () => {
    expect(initialCommitment(overviewWith())).toBe(1);
  });
});

describe("whether an upgrade can be started", () => {
  it("can be started by an organization with no arrangement", () => {
    expect(checkoutAvailability(overviewWith())).toEqual({ available: true });
  });

  /** A form whose submit would 503 is worse than a sentence saying why. */
  it("cannot be started when no processor is configured", () => {
    const answer = checkoutAvailability(overviewWith({ checkout: null }));

    expect(answer.available).toBe(false);
  });

  /**
   * Two mandates for one plan is two monthly debits for one subscription, and
   * the API refuses it — but the screen should not draw a form that gets a 409.
   */
  it("cannot be started while one is already active", () => {
    const answer = checkoutAvailability(overviewWith({ subscription: subscription() }));

    expect(answer.available).toBe(false);
  });

  /**
   * The trap this used to set. A `pending` row is a checkout that was started,
   * not a mandate that exists — so anyone who closed the tab or hit an error at
   * the processor was left with the form hidden and nothing to press but Cancel,
   * on an arrangement their bank had never authorised.
   */
  it("can be started again when an earlier checkout was never finished", () => {
    const answer = checkoutAvailability(
      overviewWith({ subscription: subscription({ status: "pending" }) }),
    );

    expect(answer.available).toBe(true);
  });

  it("says why the form is being shown a second time", () => {
    const notice = unfinishedCheckoutNotice(
      overviewWith({ subscription: subscription({ status: "pending" }) }),
    );

    expect(notice).toContain("Nothing has been charged");
  });

  it("says nothing extra when no checkout was left unfinished", () => {
    expect(unfinishedCheckoutNotice(overviewWith())).toBeNull();
    expect(unfinishedCheckoutNotice(overviewWith({ subscription: subscription() }))).toBeNull();
  });

  /** A cancelled arrangement is over, so a customer may start again. */
  it("can be started again after a cancellation", () => {
    const answer = checkoutAvailability(
      overviewWith({ subscription: subscription({ status: "cancelled" }) }),
    );

    expect(answer.available).toBe(true);
  });
});

describe("waiting for the processor to confirm", () => {
  /**
   * The race a paying customer can lose: the processor redirects their browser
   * back while its webhook is still in flight. Measured on 2026-09-10, both
   * landed within four seconds of each other.
   */
  it("keeps asking while an arrangement is unsettled", () => {
    expect(confirmationPoll("pending", 0)).toBe(CONFIRMATION_POLL_MS);
    expect(confirmationPoll("pending", CONFIRMATION_WINDOW_MS - 1)).toBe(CONFIRMATION_POLL_MS);
  });

  it("stops the moment there is an answer", () => {
    expect(confirmationPoll("active", 0)).toBe(false);
    expect(confirmationPoll("cancelled", 0)).toBe(false);
    expect(confirmationPoll(undefined, 0)).toBe(false);
  });

  /**
   * `pending` is also where an abandoned checkout rests, and that page can sit
   * open all day. It must not ask all day.
   */
  it("gives up on a mandate that was never going to be confirmed", () => {
    expect(confirmationPoll("pending", CONFIRMATION_WINDOW_MS)).toBe(false);
  });
});

describe("what a customer whose plan is already live can do", () => {
  /**
   * **The dead end this removes.** An active subscription used to end the
   * section with one sentence and nothing to press, so a paying customer could
   * not see what else existed, let alone move to it.
   */
  it("is told the two steps, in the order they happen", () => {
    const answer = checkoutAvailability(overviewWith({ subscription: subscription() }));

    expect(answer.available).toBe(false);
    if (answer.available) return;
    expect(answer.nextStep).toContain("Cancel this subscription");
    // **Not "choose the new plan here".** The plans below cannot be pressed
    // until the subscription is cancelled, so an instruction to choose one now
    // describes a control the customer can see and cannot use — which reads as
    // a broken button rather than as a sequence. The sentence has to say what
    // the list is for while it is inert.
    expect(answer.nextStep).toContain("become choosable");
    expect(answer.nextStep).toContain("compare them first");
    expect(answer.nextStep).not.toContain("choose the new plan here");
  });

  /**
   * No processor configured is a fact about the deployment, not about the
   * customer. An instruction there sends them looking for a control nobody has.
   */
  it("is given no instruction when there is nothing it could do", () => {
    const answer = checkoutAvailability(overviewWith({ checkout: null }));

    expect(answer.available).toBe(false);
    if (answer.available) return;
    expect(answer.nextStep).toBeNull();
  });
});

describe("what was charged", () => {
  function charge(overrides: Partial<Charge> = {}): Charge {
    return {
      charged_at: "2026-10-01T03:00:00Z",
      outcome: "paid",
      plan_slug: "pro",
      services_committed: 5,
      currency: "INR",
      amount_minor_units: 199_500,
      ...overrides,
    };
  }

  it("states the amount in its own currency", () => {
    expect(chargeAmount(charge())).toContain("1,995");
  });

  /**
   * **Never a zero.** The API sends null when the rate behind an arrangement
   * can no longer be read back, and "₹0.00" for a month somebody paid for is a
   * false statement rather than an incomplete one.
   */
  it("shows an em dash for an amount nobody can state", () => {
    expect(chargeAmount(charge({ amount_minor_units: null }))).toBe("—");
    expect(chargeAmount(charge({ amount_minor_units: null }))).not.toContain("0");
  });

  /**
   * The month boundaries this screen states are UTC, so a charge rendered in
   * the reader's own zone could sit in a different month from the period it
   * paid for — on the one screen where the two have to agree.
   */
  it("dates a charge in UTC", () => {
    expect(chargeDay(charge({ charged_at: "2026-10-01T03:00:00Z" }))).toBe("Oct 1, 2026");
  });

  it("says so rather than throwing when the date is unreadable", () => {
    expect(chargeDay(charge({ charged_at: "not a date" }))).toBe("—");
  });

  /** A customer looking for a missing month must find out here that it failed. */
  it("names a charge that did not go through", () => {
    expect(chargeOutcome(charge({ outcome: "failed" }))).toBe("Not paid");
    expect(chargeOutcome(charge())).toBe("Paid");
  });

  it("says what the charge was for, so the amount can be checked", () => {
    expect(chargeTerms(charge())).toContain("pro");
    expect(chargeTerms(charge())).toContain("5");
  });

  /**
   * **The claim this product must not make.** A tax invoice carries a seller's
   * registration, a numbered series and the buyer's address; none is stored, so
   * the screen says where the real one comes from instead of printing something
   * that looks like one.
   */
  it("does not call itself a tax invoice", () => {
    expect(HISTORY_IS_NOT_AN_INVOICE).toContain("tax invoice");
    expect(HISTORY_IS_NOT_AN_INVOICE).toContain("comes from the provider");
  });

  /** No card, no last four, no expiry is stored — so none is displayed. */
  it("says the payment method is the provider's and not ours", () => {
    expect(PAYMENT_METHOD_IS_THE_PROVIDERS).toContain("never by us");
  });
});
