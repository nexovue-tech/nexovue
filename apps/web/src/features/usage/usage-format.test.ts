import { describe, expect, it } from "vitest";

import type { BillingPeriod, CardinalityWarning, PlanPrice, ServiceNameFamily } from "./api";
import {
  allowanceResetDay,
  CARDINALITY_REMEDY,
  describeAllowance,
  describeAllowanceSpent,
  describeCeiling,
  describeCostBasis,
  describeCounting,
  describeFamily,
  describeNoPrice,
  describeOverage,
  describePeriodProgress,
  describeRateStart,
  describeScope,
  describeServiceChange,
  formatDay,
  formatMoney,
  formatPeriod,
  formatPeriodCost,
  formatRate,
  isAllowanceSpent,
  ownPeriodNote,
} from "./usage-format";

const PRICE: PlanPrice = {
  effective_from: "2026-01-01",
  currency: "USD",
  price_per_service_minor_units: 1_000,
  max_billed_services: null,
  included_bytes_per_service: null,
};

/** August, finished, two services billed at $10 each. */
const PERIOD: BillingPeriod = {
  first_day: "2026-08-01",
  last_day: "2026-08-31",
  settled: true,
  services: 2,
  billed_services: 2,
  bytes_ingested: 500_000,
  service_cost_minor_units: 2_000,
  currency: "USD",
  included_bytes: null,
  allowance_enforced: false,
};

describe("formatDay", () => {
  it("reads the day the meter counted, in UTC", () => {
    expect(formatDay("2026-08-10")).toBe("Aug 10, 2026");
  });

  it("shows an unparseable day as it arrived rather than as Invalid Date", () => {
    expect(formatDay("not-a-day")).toBe("not-a-day");
  });
});

describe("formatMoney", () => {
  it("divides by the currency's own minor unit, not by a hardcoded hundred", () => {
    expect(formatMoney(1_000, "USD")).toBe("$10.00");
    // The yen's minor unit is the yen. Dividing by a hundred would quote ¥1,000
    // as ¥10 — a bill at a hundredth of its size.
    expect(formatMoney(1_000, "JPY")).toBe("¥1,000");
    // Three minor digits, and the same stored number is a different amount again.
    // Matched loosely because Intl separates the code with a non-breaking space.
    expect(formatMoney(1_000, "BHD")).toMatch(/^BHD\s1\.000$/);
  });

  it("shows the raw minor units when the code is not a currency at all", () => {
    // A broken row. Guessing an exponent for it would state an amount we cannot know.
    expect(formatMoney(1_000, "zz")).toBe("1,000 zz");
  });
});

describe("the rate", () => {
  it("is per service, which is the unit the bill is counted in", () => {
    expect(formatRate(PRICE)).toBe("$10.00 per service");
  });

  it("says when it started applying, so a repricing is visible", () => {
    expect(describeRateStart(PRICE)).toBe("In force since Jan 1, 2026.");
  });

  it("names a ceiling when the row has one", () => {
    expect(describeCeiling({ ...PRICE, max_billed_services: 120 })).toBe(
      "At most 120 services are billed.",
    );
  });

  it("says nothing about a ceiling when the row has none", () => {
    // Uncapped is what the null means; "no ceiling" would be a promise this row
    // cannot make on the plan's behalf.
    expect(describeCeiling(PRICE)).toBeUndefined();
  });
});

describe("describeNoPrice", () => {
  it("names the plan and refuses to let absence read as free", () => {
    const sentence = describeNoPrice("Pro");

    expect(sentence).toContain("Pro");
    expect(sentence).toContain("free");
    expect(sentence).not.toContain("0");
  });
});

describe("describeCounting", () => {
  it("says the range is finished only when it is", () => {
    expect(describeCounting(true, 0)).toBe("Every day in this range has finished being counted.");
  });

  it("counts the unfinished days it can see", () => {
    expect(describeCounting(false, 3)).toBe(
      "3 days here have not finished being counted, so these numbers can still grow.",
    );
    expect(describeCounting(false, 1)).toBe(
      "1 day here has not finished being counted, so these numbers can still grow.",
    );
  });

  it("still warns when the unfinished days are ones with no row yet", () => {
    // A day the meter has not reached is absent from the list, so nothing there
    // can carry the caveat. The range flag is the only thing that knows.
    expect(describeCounting(false, 0)).toBe(
      "Part of this range has not finished being counted, so these numbers can still grow.",
    );
  });
});

describe("the sentences", () => {
  it("says the bill is the organization's, not the selected project's", () => {
    expect(describeScope("30 days", true)).toBe(
      "Every project in this organization over the last 30 days — the bill is the organization's, not one project's.",
    );
  });

  it("speaks of no bill on a customer's own install", () => {
    expect(describeScope("30 days", false)).toBe(
      "Every project in this organization over the last 30 days.",
    );
    expect(ownPeriodNote(false)).not.toContain("bill");
    expect(ownPeriodNote(true)).toContain("a bill is measured in whole days");
  });
});

describe("the month's cost", () => {
  it("names the period as the calendar month it is", () => {
    expect(formatPeriod(PERIOD)).toBe("August 2026");
  });

  it("is the API's amount, in the currency's own minor unit", () => {
    expect(formatPeriodCost(PERIOD)).toBe("$20.00");
    // The yen's minor unit is the yen — the same trap the rate avoids.
    expect(formatPeriodCost({ ...PERIOD, currency: "JPY" })).toBe("¥2,000");
  });

  it("is nothing at all when no price is recorded, and never a zero", () => {
    expect(
      formatPeriodCost({ ...PERIOD, service_cost_minor_units: null, currency: null }),
    ).toBeUndefined();
  });

  it("says which quantity over which period the figure came from", () => {
    expect(describeCostBasis(PERIOD, "Pro")).toBe(
      "2 services in August 2026, at the rate in force.",
    );
  });

  it("says the ceiling is on the bill, not a discount on the count", () => {
    // "The 200 counted are held to the plan's ceiling" read as a discount. The
    // customer is running 200 services and being billed for 120, and the
    // sentence has to say that in their direction, not ours.
    expect(describeCostBasis({ ...PERIOD, services: 200, billed_services: 120 }, "Free")).toBe(
      "120 services in August 2026, at the rate in force. You are running 200 services; Free bills 120.",
    );
  });

  it("marks a month still being metered rather than letting it read as final", () => {
    expect(describePeriodProgress({ ...PERIOD, settled: false })).toBe(
      "August 2026 is still being metered, so this will grow.",
    );
    expect(describePeriodProgress(PERIOD)).toBe("August 2026 has finished being metered.");
  });
});

describe("the volume allowance", () => {
  it("says nothing when no allowance is recorded", () => {
    // A zero here would report every byte ingested as overage.
    expect(describeAllowance(PERIOD)).toBeUndefined();
  });

  it("shows what is used against what is included", () => {
    expect(describeAllowance({ ...PERIOD, included_bytes: 1_000_000 })).toBe(
      "500 kB of 1.0 MB included",
    );
  });

  it("says plainly when the volume is over an allowance nothing enforces", () => {
    expect(
      describeAllowance({ ...PERIOD, bytes_ingested: 2_000_000, included_bytes: 1_000_000 }),
    ).toBe("2.0 MB of 1.0 MB included — over the allowance.");
  });

  it("warns before an enforced allowance is spent, not only after", () => {
    // A refusal a customer could not see coming is the failure this exists to
    // prevent: the receiver's refusal reaches only their exporter's logs.
    expect(
      describeAllowance({ ...PERIOD, included_bytes: 1_000_000, allowance_enforced: true }),
    ).toBe(
      "500 kB of 1.0 MB included. Ingestion stops once this is spent and resumes Sep 1, 2026.",
    );
  });

  it("says an enforced allowance is spent, and when it comes back", () => {
    expect(
      describeAllowance({
        ...PERIOD,
        bytes_ingested: 2_000_000,
        included_bytes: 1_000_000,
        allowance_enforced: true,
      }),
    ).toBe("2.0 MB of 1.0 MB included — spent. Further ingestion is refused until Sep 1, 2026.");
  });

  it("resets on the day after the month it covered, not the day after today", () => {
    // A screen looking back at August in September must not say the allowance
    // returns tomorrow.
    expect(allowanceResetDay(PERIOD)).toBe("2026-09-01");
    expect(allowanceResetDay({ ...PERIOD, first_day: "2026-12-01", last_day: "2026-12-31" })).toBe(
      "2027-01-01",
    );
  });

  it("is only spent where the plan enforces the allowance", () => {
    const over = { ...PERIOD, bytes_ingested: 2_000_000, included_bytes: 1_000_000 };
    expect(isAllowanceSpent(over)).toBe(false);
    expect(isAllowanceSpent({ ...over, allowance_enforced: true })).toBe(true);
  });

  it("names the loss rather than softening it when the door is refusing", () => {
    expect(
      describeAllowanceSpent({
        ...PERIOD,
        bytes_ingested: 2_000_000,
        included_bytes: 1_000_000,
        allowance_enforced: true,
      }),
    ).toBe(
      "The volume included in August 2026 is spent, so exports are being refused until Sep 1, 2026. Telemetry sent in the meantime is not stored and cannot be recovered.",
    );
  });
});

describe("what the cost figure leaves out", () => {
  it("says the overage is undecided where nothing is refused", () => {
    expect(describeOverage(PERIOD)).toContain("is not decided");
  });

  it("says there is nothing to bill where volume is refused instead", () => {
    expect(describeOverage({ ...PERIOD, allowance_enforced: true })).toContain(
      "refused rather than billed",
    );
  });
});

describe("the cardinality warning", () => {
  const WARNING: CardinalityWarning = {
    project_id: "11111111-1111-4111-8111-111111111111",
    services_now: 12,
    services_before: 3,
    window_days: 7,
    families: [],
  };

  const FAMILY: ServiceNameFamily = {
    stem: "checkout",
    kind: "kubernetes-pod",
    members: 9,
    examples: ["checkout-7d9f8b6c4d-x2k9p"],
  };

  it("shows the change with both numbers in it", () => {
    expect(describeServiceChange(WARNING)).toBe("12 services, up from 3 in the 7 days before.");
  });

  it("does not say a bare zero when there was no previous week", () => {
    expect(describeServiceChange({ ...WARNING, services_before: 0 })).toBe(
      "12 services, none in the 7 days before.",
    );
  });

  it("names the shape it found and what it costs", () => {
    expect(describeFamily(FAMILY, true)).toContain("checkout");
    expect(describeFamily(FAMILY, true)).toContain("a Kubernetes pod suffix");
    expect(describeFamily(FAMILY, true)).toContain("billed as its own service");
    expect(describeFamily(FAMILY, false)).toContain("counted as its own service");
  });

  it("falls back to a neutral word for a shape it does not recognise", () => {
    expect(describeFamily({ ...FAMILY, kind: "something-new" }, true)).toContain("an identifier");
  });

  it("tells the customer where the identifier belongs instead", () => {
    expect(CARDINALITY_REMEDY).toContain("service.instance.id");
  });
});
