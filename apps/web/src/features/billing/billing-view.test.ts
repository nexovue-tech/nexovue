import { describe, expect, it } from "vitest";

import type { BillingOverview, BillingPeriodUsage, PlanOption, SubscriptionSummary } from "./api";
import {
  allowanceFraction,
  checkoutAvailability,
  describeAllowance,
  describeCost,
  describeSubscription,
  initialCommitment,
  monthlyTotalMinorUnits,
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
   * ADR 0107: telemetry that stops is indistinguishable from a service that went
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

describe("the month's cost", () => {
  /**
   * An unpriced tier is not a free one. "$0.00" is a different statement from
   * "priced by agreement", and it is the one that is false.
   */
  it("says priced by agreement rather than zero", () => {
    const said = describeCost(period({ service_cost_minor_units: null, currency: null }));

    expect(said).toBe("Priced by agreement.");
    expect(said).not.toContain("0");
  });

  it("divides by the currency's own minor unit", () => {
    expect(describeCost(period())).toContain("$30.00");
  });

  /** A figure that is still being counted must not read as a final bill. */
  it("says a month still being metered is not finished", () => {
    expect(describeCost(period({ settled: false }))).toContain("still being metered");
  });
});

describe("the arrangement", () => {
  it("names the ceiling the customer committed to", () => {
    expect(describeSubscription(subscription())).toContain("5 services committed");
  });

  /** Nothing has been charged yet, and saying otherwise would alarm somebody. */
  it("says a pending mandate has charged nothing", () => {
    expect(describeSubscription(subscription({ status: "pending" }))).toContain(
      "Nothing has been charged",
    );
  });

  /**
   * Shown rather than hidden: a customer whose mandate ended and who is shown
   * nothing at all learns less than one shown the row that ended.
   */
  it("says a cancelled arrangement returns to the free tier", () => {
    expect(describeSubscription(subscription({ status: "cancelled" }))).toContain("free tier");
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
    raw_retention_days: 30,
    derived_retention_days: 395,
  };

  /**
   * ADR 0111: the bill *is* the commitment, not "up to" it. A customer who
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
