import { describe, expect, it } from "vitest";

import { OUTCOMES } from "./check-model";
import {
  isOutcome,
  OUTCOME_WORDS,
  outcomeUrgency,
  runSentence,
  STATE_WORDS,
  stateUrgency,
} from "./outcomes";
import { failedEvidence } from "./render-harness.test-util";

describe("how loud a state is", () => {
  it("fills only down, warns on degraded and misconfigured, and leaves the rest quiet", () => {
    expect(stateUrgency("down")).toBe("critical");
    expect(stateUrgency("degraded")).toBe("warning");
    expect(stateUrgency("misconfigured")).toBe("warning");
    for (const quiet of ["up", "paused", "maintenance", null] as const) {
      expect(stateUrgency(quiet)).toBe("none");
    }
  });

  it("names maintenance as a phrase a sentence can hold", () => {
    expect(STATE_WORDS.maintenance).toBe("in maintenance");
  });
});

describe("how loud an outcome is", () => {
  it("treats our own failure as a warning, never as the customer's outage", () => {
    expect(outcomeUrgency("down")).toBe("critical");
    expect(outcomeUrgency("prober_error")).toBe("warning");
    expect(outcomeUrgency("misconfigured")).toBe("warning");
    expect(outcomeUrgency("degraded")).toBe("warning");
    expect(outcomeUrgency("up")).toBe("none");
  });

  it("recognises exactly the outcomes the evidence can carry", () => {
    expect(Object.keys(OUTCOME_WORDS).sort()).toEqual([...OUTCOMES].sort());
    expect(isOutcome("prober_error")).toBe(true);
    expect(isOutcome("browser")).toBe(false);
    expect(isOutcome("toString")).toBe(false);
  });
});

describe("the sentence a run is read as", () => {
  it("says a passing run passed, and nothing more", () => {
    expect(runSentence(failedEvidence({ outcome: "up", attempt: 1, error: null }))).toBe(
      "The run passed from mumbai.",
    );
  });

  it("names the attempt and the reason a run failed", () => {
    expect(runSentence(failedEvidence())).toBe(
      "The run failed from mumbai on attempt 2: 1 of 2 assertions failed",
    );
  });

  it("tells a misconfigured run apart from a failed one", () => {
    expect(
      runSentence(
        failedEvidence({
          outcome: "misconfigured",
          attempt: 1,
          error: "The secret API_TOKEN does not exist.",
        }),
      ),
    ).toBe("The run could not run as written from mumbai: The secret API_TOKEN does not exist.");
  });

  it("leaves out a location it does not have", () => {
    expect(runSentence(failedEvidence({ location: "", outcome: "degraded", attempt: 1 }))).toBe(
      "The run passed slowly: 1 of 2 assertions failed",
    );
  });
});
