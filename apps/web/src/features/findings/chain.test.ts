import { describe, expect, it } from "vitest";

import { type ChainInput, chainSteps, feedChips } from "./chain";

/**
 * The strip under a finding's title: the five steps from a finding to a pull request, where
 * this one stands, and which step is next (UX review 2026-11-28, approved drawing C).
 */

const OPEN: ChainInput = {
  findingState: "open",
  severity: "warning",
  caseState: null,
  assigneeName: null,
  issue: null,
  fixState: null,
  fixOffered: true,
  pullRequestState: null,
};

const stateOf = (input: ChainInput) => chainSteps(input).map((step) => step.status);

describe("the chain from a finding to a pull request", () => {
  it("has five steps, in order", () => {
    expect(chainSteps(OPEN).map((step) => step.name)).toEqual([
      "Finding",
      "Case",
      "GitHub issue",
      "Suggested fix",
      "Pull request",
    ]);
  });

  it("makes the case the next step of a fresh finding", () => {
    expect(stateOf(OPEN)).toEqual(["done", "now", "later", "later", "later"]);
    expect(chainSteps(OPEN)[1]).toMatchObject({ state: "Not started", detail: "Take it to start" });
  });

  it("names who has the case, or that nobody does", () => {
    const taken = chainSteps({ ...OPEN, caseState: "investigating", assigneeName: "Raj" });
    expect(taken[1]).toMatchObject({ state: "Being investigated", detail: "Raj is on it" });
    const nobody = chainSteps({ ...OPEN, caseState: "investigating" });
    expect(nobody[1].detail).toBe("Nobody assigned");
  });

  it("moves to the issue once the case is taken", () => {
    expect(stateOf({ ...OPEN, caseState: "investigating" })).toEqual([
      "done",
      "done",
      "now",
      "later",
      "later",
    ]);
  });

  it("skips an issue nobody created when a fix came first", () => {
    const steps = chainSteps({ ...OPEN, caseState: "investigating", fixState: "proposed" });
    expect(steps.map((step) => step.status)).toEqual(["done", "done", "skipped", "done", "now"]);
    expect(steps[2].state).toBe("None");
    expect(steps[3].state).toBe("Ready to read");
  });

  it("reads the issue's key and state", () => {
    const steps = chainSteps({
      ...OPEN,
      caseState: "investigating",
      issue: { key: "acme/shop#1", state: "open" },
    });
    expect(steps[2]).toMatchObject({ state: "#1 open", detail: "acme/shop", status: "done" });
    expect(steps[3].status).toBe("now");
  });

  it("says a fix being written is under way, not ready", () => {
    const steps = chainSteps({ ...OPEN, caseState: "investigating", fixState: "proposing" });
    expect(steps[3]).toMatchObject({ state: "Being written", status: "now" });
  });

  it("tells a closed pull request from an open one", () => {
    const opened = { ...OPEN, caseState: "investigating" as const, fixState: "opened" as const };
    expect(chainSteps({ ...opened, pullRequestState: "open" })[4]).toMatchObject({
      state: "Draft open",
      status: "now",
    });
    expect(chainSteps({ ...opened, pullRequestState: "closed" })[4]).toMatchObject({
      state: "Closed without merging",
      status: "done",
    });
    expect(chainSteps({ ...opened, pullRequestState: "merged" })[4].state).toBe("Merged");
  });

  it("offers no next step once the case is resolved", () => {
    const steps = chainSteps({ ...OPEN, caseState: "resolved", findingState: "closed" });
    expect(steps.some((step) => step.status === "now")).toBe(false);
    expect(steps[1].state).toBe("Resolved");
  });

  it("says a fix is not offered where none can be", () => {
    const steps = chainSteps({ ...OPEN, caseState: "investigating", fixOffered: false });
    expect(steps[3].state).toBe("Not offered");
  });
});

describe("a feed row's chain, in small (drawing C)", () => {
  it("says nothing for a finding nobody took", () => {
    expect(feedChips({ caseState: null, chain: null })).toEqual([]);
  });

  it("names the case, the issue, the fix and the pull request in order", () => {
    expect(
      feedChips({
        caseState: "investigating",
        chain: {
          issue_key: "acme/shop#1",
          issue_state: "open",
          fix_state: "opened",
          pull_request_state: "open",
        },
      }),
    ).toEqual([
      { label: "Case", tone: "plain" },
      { label: "Issue #1", tone: "plain" },
      { label: "Draft PR open", tone: "next" },
    ]);
  });

  it("marks a fix waiting to be read as the next step", () => {
    expect(
      feedChips({
        caseState: "investigating",
        chain: {
          issue_key: null,
          issue_state: null,
          fix_state: "proposed",
          pull_request_state: null,
        },
      }),
    ).toEqual([
      { label: "Case", tone: "plain" },
      { label: "Fix ready", tone: "next" },
    ]);
  });

  it("says how a resolved case and a closed pull request ended", () => {
    expect(
      feedChips({
        caseState: "resolved",
        chain: {
          issue_key: "acme/shop#1",
          issue_state: "closed",
          fix_state: "opened",
          pull_request_state: "merged",
        },
      }),
    ).toEqual([
      { label: "Resolved", tone: "plain" },
      { label: "Issue #1 closed", tone: "plain" },
      { label: "PR merged", tone: "plain" },
    ]);
  });
});
