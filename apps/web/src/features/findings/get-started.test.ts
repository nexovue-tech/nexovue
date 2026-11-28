import { describe, expect, it } from "vitest";

import { type GetStartedInput, getStartedSteps, stepLink } from "./get-started";

/**
 * Findings' Get started (UX review 2026-11-28, approved sketch H): a new organization's first
 * steps, each read from what is already set, so nothing is asked twice.
 */

const NEW: GetStartedInput = {
  servicesReporting: 1,
  alertsReach: true,
  emailVerified: false,
  deploysReported: false,
  githubConnected: false,
  aiOn: false,
  can: { deploys: true, github: true, ai: true },
};

const titles = (input: GetStartedInput) => getStartedSteps(input).steps.map((step) => step.title);

describe("Get started", () => {
  it("lists six steps for an owner, in order, and counts what is done", () => {
    const { steps, done } = getStartedSteps(NEW);
    expect(steps.map((step) => step.title)).toEqual([
      "Send telemetry",
      "Alerts reach you",
      "Verify your email",
      "Report deploys",
      "Connect GitHub",
      "Set up AI (optional)",
    ]);
    expect(done).toBe(2);
    expect(steps.filter((step) => step.next).map((step) => step.title)).toEqual([
      "Verify your email",
    ]);
  });

  it("shows a member only the steps a member can do", () => {
    expect(titles({ ...NEW, can: { deploys: false, github: false, ai: false } })).toEqual([
      "Send telemetry",
      "Alerts reach you",
      "Verify your email",
    ]);
  });

  it("is finished, and shows nothing, once every step it shows is done", () => {
    const all = { ...NEW, emailVerified: true, deploysReported: true, githubConnected: true };
    expect(getStartedSteps({ ...all, aiOn: false }).finished).toBe(false);
    expect(getStartedSteps({ ...all, aiOn: true }).finished).toBe(true);
  });

  it("does not count a step it could not read as done", () => {
    const { steps } = getStartedSteps({ ...NEW, githubConnected: undefined });
    expect(steps.find((step) => step.title === "Connect GitHub")?.done).toBe(false);
  });

  it("waits on telemetry before anything else", () => {
    const { steps } = getStartedSteps({ ...NEW, servicesReporting: 0 });
    expect(steps[0]).toMatchObject({ done: false, next: true });
  });
});

describe("where an open step leads (sketch H)", () => {
  it("sends a project with no telemetry to the setup steps, not to an empty Services list", () => {
    expect(stepLink({ place: "services", done: false })).toEqual({
      to: "/onboarding",
      words: "Show me how",
    });
    expect(stepLink({ place: "services", done: true })).toEqual({
      to: "/services",
      words: "Services",
    });
  });
});
