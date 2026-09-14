import { describe, expect, it } from "vitest";
import { UNKNOWN } from "@/shared/format/number";

import {
  emitterSaidSomethingElse,
  SEVERITY_DEBUG,
  SEVERITY_ERROR,
  SEVERITY_FATAL,
  SEVERITY_FILTERS,
  SEVERITY_INFO,
  SEVERITY_WARN,
  severityFilterById,
  severityLabel,
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

describe("the level a row shows", () => {
  it("reads one spelling for a level however the emitter wrote it", () => {
    /* One project's logs carried "INFO", "info" and "Information" for the same
       number — three spellings of one level in a list people scan by level. */
    for (const number of [9, 10, 11, 12]) {
      expect(severityLabel(number)).toBe("INFO");
    }
    expect(severityLabel(13)).toBe("WARN");
    expect(severityLabel(17)).toBe("ERROR");
    expect(severityLabel(21)).toBe("FATAL");
    expect(severityLabel(5)).toBe("DEBUG");
    expect(severityLabel(1)).toBe("TRACE");
  });

  it("names the level when the emitter sent a number and no words", () => {
    /* 117,127 lines carried number 9 with no text, so the cell was blank about a
       level the record knew perfectly well. */
    expect(severityLabel(9)).toBe("INFO");
  });

  it("is an em dash when nothing was specified, not a level below TRACE", () => {
    expect(severityLabel(0)).toBe(UNKNOWN);
  });

  it("never returns something wider than the column it is drawn in", () => {
    /* The cell is five characters wide. "Information" was eleven and had no
       truncation, so it ran into the service beside it. */
    for (let number = 0; number <= 24; number += 1) {
      expect(severityLabel(number).length).toBeLessThanOrEqual(5);
    }
  });
});

describe("what the emitter actually wrote", () => {
  it("is worth showing when it differs from the level", () => {
    expect(emitterSaidSomethingElse({ severity: "Information", severity_number: 9 })).toBe(true);
    expect(emitterSaidSomethingElse({ severity: "Notice", severity_number: 9 })).toBe(true);
  });

  it("is not worth showing when it only differs in case", () => {
    expect(emitterSaidSomethingElse({ severity: "info", severity_number: 9 })).toBe(false);
    expect(emitterSaidSomethingElse({ severity: "INFO", severity_number: 9 })).toBe(false);
  });

  it("is not worth showing when the emitter wrote nothing", () => {
    expect(emitterSaidSomethingElse({ severity: "", severity_number: 9 })).toBe(false);
    expect(emitterSaidSomethingElse({ severity: "   ", severity_number: 0 })).toBe(false);
  });
});
