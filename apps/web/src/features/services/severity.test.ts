import { describe, expect, it } from "vitest";

import {
  CRITICAL_ERROR_RATE,
  MINIMUM_REQUESTS_FOR_A_RATE,
  serviceSeverity,
  WARNING_ERROR_RATE,
} from "./severity";

const busy = (errorRate: number, errors = 1) => ({
  error_count: errors,
  error_rate: errorRate,
  request_count: 10_000,
});

describe("when a service stops being healthy", () => {
  it("is healthy with nothing failing", () => {
    expect(serviceSeverity({ error_count: 0, error_rate: 0, request_count: 10_000 })).toBe(
      "healthy",
    );
  });

  it("is healthy when the failures are too few to matter", () => {
    // The defect this replaced: one error in a hundred thousand drew the same
    // red dot as forty percent failing, so on a list of sixteen with twelve
    // marked the mark had stopped carrying information.
    expect(serviceSeverity(busy(0.00001))).toBe("healthy");
  });

  it("warns at the warning threshold and not before", () => {
    expect(serviceSeverity(busy(WARNING_ERROR_RATE - 0.0001))).toBe("healthy");
    expect(serviceSeverity(busy(WARNING_ERROR_RATE))).toBe("warning");
  });

  it("is critical at the critical threshold and not before", () => {
    expect(serviceSeverity(busy(CRITICAL_ERROR_RATE - 0.0001))).toBe("warning");
    expect(serviceSeverity(busy(CRITICAL_ERROR_RATE))).toBe("critical");
  });
});

describe("a rate needs requests behind it", () => {
  it("never calls a nearly-silent service critical", () => {
    // One error in the only two requests a quiet service received is a 50%
    // error rate and not an incident. Marking it critical would put the
    // loudest signal on the least evidence.
    const quiet = { error_count: 1, error_rate: 0.5, request_count: 2 };

    expect(serviceSeverity(quiet)).toBe("warning");
  });

  it("still says something failed, rather than nothing at all", () => {
    const quiet = { error_count: 1, error_rate: 1, request_count: 1 };

    expect(serviceSeverity(quiet)).not.toBe("healthy");
  });

  it("uses the rate once there is enough traffic for one", () => {
    const enough = {
      error_count: 1,
      error_rate: CRITICAL_ERROR_RATE,
      request_count: MINIMUM_REQUESTS_FOR_A_RATE,
    };

    expect(serviceSeverity(enough)).toBe("critical");
  });
});

describe("the thresholds themselves", () => {
  it("keeps warning below critical, which the rendering assumes", () => {
    expect(WARNING_ERROR_RATE).toBeLessThan(CRITICAL_ERROR_RATE);
  });
});
