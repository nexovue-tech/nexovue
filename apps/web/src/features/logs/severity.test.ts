import { describe, expect, it } from "vitest";

import {
  SEVERITY_DEBUG,
  SEVERITY_ERROR,
  SEVERITY_FATAL,
  SEVERITY_FILTERS,
  SEVERITY_INFO,
  SEVERITY_WARN,
  severityFilterById,
  severityStyle,
  severityTone,
} from "./severity";

describe("severityTone", () => {
  it("leaves everything up to INFO without colour", () => {
    // Health is the absence of colour, and on a log screen INFO is the health.
    for (const number of [1, 4, SEVERITY_DEBUG, 8, SEVERITY_INFO, 12]) {
      expect(severityTone(number)).toBe("quiet");
    }
  });

  it("colours the whole WARN band amber", () => {
    for (const number of [SEVERITY_WARN, 14, 15, 16]) {
      expect(severityTone(number)).toBe("warning");
    }
  });

  it("colours ERROR and FATAL red", () => {
    for (const number of [SEVERITY_ERROR, 20, SEVERITY_FATAL, 24]) {
      expect(severityTone(number)).toBe("critical");
    }
  });

  it("treats a severity number nobody defined as quiet rather than alarming", () => {
    expect(severityTone(0)).toBe("quiet");
    expect(severityTone(-3)).toBe("quiet");
  });
});

describe("severityStyle", () => {
  it("gives a quiet line no rule to draw", () => {
    expect(severityStyle(SEVERITY_INFO).rule).toBe("border-l-transparent");
  });

  it("carries a failure by a rule as well as by a colour", () => {
    const style = severityStyle(SEVERITY_ERROR);
    expect(style.rule).toBe("border-l-critical-solid");
    expect(style.label).toBe("text-critical-text");
  });

  it("keeps warning and critical apart in both carriers", () => {
    const warning = severityStyle(SEVERITY_WARN);
    const critical = severityStyle(SEVERITY_ERROR);
    expect(warning.rule).not.toBe(critical.rule);
    expect(warning.label).not.toBe(critical.label);
  });
});

describe("severityFilterById", () => {
  it("reads back every filter it offers", () => {
    for (const filter of SEVERITY_FILTERS) {
      expect(severityFilterById(filter.id)).toBe(filter);
    }
  });

  it("falls back to no floor at all for an id it does not know", () => {
    expect(severityFilterById("nonsense").minSeverity).toBeUndefined();
  });

  it("offers no floor for All, so a zero floor is never sent as a filter", () => {
    expect(severityFilterById("all").minSeverity).toBeUndefined();
    expect(severityFilterById("error").minSeverity).toBe(SEVERITY_ERROR);
  });
});
