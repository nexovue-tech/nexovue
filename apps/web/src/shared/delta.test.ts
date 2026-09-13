import { describe, expect, it } from "vitest";

import { deltaOf } from "./delta";

describe("an absent comparison", () => {
  it("renders no delta when the previous window holds no reading", () => {
    // A service that first reported an hour ago has nothing to be compared
    // against. "0%" would claim it was flat, which is a different statement.
    expect(deltaOf("rate", 120, undefined)).toBeNull();
    expect(deltaOf("rate", 120, null)).toBeNull();
  });

  it("renders no delta when this window holds no reading", () => {
    expect(deltaOf("duration", null, 400)).toBeNull();
  });

  it("refuses a comparison the numbers cannot support", () => {
    expect(deltaOf("rate", Number.NaN, 10)).toBeNull();
    expect(deltaOf("rate", 10, Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("an unremarkable reading", () => {
  it("carries no annotation when a rate moved the way traffic moves", () => {
    // A third is an ordinary day. Measured on the development project: at a
    // fifth, 14 of 15 services carried a delta over six hours, which is a
    // screen of annotations that has stopped saying anything.
    expect(deltaOf("rate", 110, 100)).toBeNull();
    expect(deltaOf("rate", 133, 100)).toBeNull();
  });

  it("carries no annotation when a duration barely moved", () => {
    expect(deltaOf("duration", 415, 400)).toBeNull();
  });

  it("carries no annotation when an error rate moved a fraction of a point", () => {
    // 0.1% to 0.2% has doubled and nobody needs to know.
    expect(deltaOf("ratio", 0.002, 0.001)).toBeNull();
  });

  it("says nothing about two identical readings rather than saying zero", () => {
    expect(deltaOf("rate", 100, 100)).toBeNull();
    expect(deltaOf("ratio", 0.01, 0.01)).toBeNull();
  });
});

describe("a change worth an eye", () => {
  it("reports a rate that rose by half", () => {
    expect(deltaOf("rate", 150, 100)).toEqual({
      direction: "up",
      change: 0.5,
      display: "+50%",
    });
  });

  it("reports a duration that fell, with a minus sign rather than a hyphen", () => {
    const delta = deltaOf("duration", 200, 400);

    expect(delta?.direction).toBe("down");
    // These sit in a column of numbers, where a hyphen is the wrong width.
    expect(delta?.display).toBe("−" + "50%");
  });

  it("measures an error rate in points, because a ratio has no useful ratio", () => {
    expect(deltaOf("ratio", 0.03, 0.01)).toEqual({
      direction: "up",
      change: expect.closeTo(0.02, 10),
      display: "+2.0pp",
    });
  });

  it("drops the decimal on a large swing in points, which it cannot justify", () => {
    expect(deltaOf("ratio", 0.62, 0.09)?.display).toBe("+53pp");
  });

  it("wants a bigger move from a volume than from a duration", () => {
    // Traffic swings on its own; a response time that rose by a third did not.
    expect(deltaOf("rate", 140, 100)).toBeNull();
    expect(deltaOf("count", 140, 100)).toBeNull();
    expect(deltaOf("duration", 140, 100)?.display).toBe("+40%");
    expect(deltaOf("count", 200, 100)?.display).toBe("+100%");
  });
});

describe("a reading that rose from nothing", () => {
  it("says so rather than dividing by zero", () => {
    // A service that was failing for nobody and is now failing has changed by an
    // infinite percentage, which no annotation can print.
    expect(deltaOf("rate", 12, 0)).toEqual({ direction: "up", change: 12, display: "new" });
  });

  it("stays silent when both windows are zero", () => {
    expect(deltaOf("rate", 0, 0)).toBeNull();
  });

  it("still measures a ratio in points, where zero is an ordinary value", () => {
    expect(deltaOf("ratio", 0.02, 0)?.display).toBe("+2.0pp");
  });
});

describe("a reading with too little behind it", () => {
  it("renders no delta when either window is too thin to mean anything", () => {
    // Two requests becoming three is a 50% rise and is arithmetic, not a finding.
    expect(deltaOf("duration", 300, 200, { current: 3, previous: 2 })).toBeNull();
    expect(deltaOf("duration", 300, 200, { current: 400, previous: 2 })).toBeNull();
    expect(deltaOf("duration", 300, 200, { current: 2, previous: 400 })).toBeNull();
  });

  it("reports the same change once both windows carry enough", () => {
    expect(deltaOf("duration", 300, 200, { current: 400, previous: 380 })?.display).toBe("+50%");
  });

  it("needs no sample when the caller has none to give", () => {
    // A gauge is not an average over a sample, so there is nothing to be thin.
    expect(deltaOf("rate", 150, 100)).not.toBeNull();
  });
});
