import { describe, expect, it } from "vitest";

import type { PlanPrice } from "./api";
import {
  describeCeiling,
  describeCounting,
  describeNoPrice,
  describeRateStart,
  describeScope,
  formatBytes,
  formatCount,
  formatDay,
  formatMoney,
  formatRate,
} from "./usage-format";

const PRICE: PlanPrice = {
  effective_from: "2026-01-01",
  currency: "USD",
  price_per_service_cents: 1_000,
  max_billed_services: null,
};

describe("formatCount", () => {
  it("groups thousands in a fixed locale", () => {
    expect(formatCount(1_234_567)).toBe("1,234,567");
  });

  it("survives a non-finite count rather than printing NaN", () => {
    expect(formatCount(Number.NaN)).toBe("0");
  });
});

describe("formatBytes", () => {
  it("steps up in decimal units, because that is what per-GB prices are quoted in", () => {
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1_000)).toBe("1.0 kB");
    expect(formatBytes(42_100_000_000)).toBe("42.1 GB");
  });

  it("does not round bytes into a fraction of a byte", () => {
    expect(formatBytes(1)).toBe("1 B");
  });

  it("is a plain zero when nothing was ingested", () => {
    expect(formatBytes(0)).toBe("0 B");
  });
});

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
    expect(describeScope("30 days")).toBe(
      "Every project in this organization over the last 30 days — the bill is the organization's, not one project's.",
    );
  });
});
