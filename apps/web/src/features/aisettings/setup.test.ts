import { describe, expect, it } from "vitest";

import { type SetupInput, setupSteps } from "./setup";

/**
 * The AI page's Setup tab (UX review 2026-11-28, approved drawing D): six steps from a
 * provider to draft pull requests, each done or not, in a sentence, with where to set it.
 */

const NOTHING: SetupInput = {
  provider: null,
  issuesApp: null,
  codeApp: null,
  locations: null,
  consentedFolders: 0,
  consentedRepositories: [],
  draftRepositories: 0,
  ciOn: false,
};

const READY: SetupInput = {
  provider: { host: "api.anthropic.com", model: "claude-sonnet-5-5", tested: true, on: true },
  issuesApp: { state: "working", slug: "nexovue-acme", repositories: 1 },
  codeApp: { state: "working", repositories: 1 },
  locations: { set: 3, total: 15 },
  consentedFolders: 3,
  consentedRepositories: ["Raj-232/opentelemetry-demo"],
  draftRepositories: 1,
  ciOn: true,
};

describe("the AI setup checklist", () => {
  it("lists six steps, in the order they are done", () => {
    expect(setupSteps(NOTHING).map((step) => step.title)).toEqual([
      "AI provider",
      "GitHub for issues",
      "GitHub for code",
      "Where each service's code lives",
      "Owner agreed to send code",
      "Draft pull requests",
    ]);
  });

  it("marks nothing done on a new install, and says what each needs", () => {
    const steps = setupSteps(NOTHING);
    expect(steps.every((step) => !step.done)).toBe(true);
    expect(steps[0].detail).toBe("No provider yet");
    expect(steps[1].detail).toBe("Not connected");
  });

  it("names the provider, its model, and whether it was tested", () => {
    expect(setupSteps(READY)[0]).toMatchObject({
      done: true,
      detail: "api.anthropic.com, claude-sonnet-5-5 · tested",
    });
    const untested = setupSteps({
      ...READY,
      provider: { host: "h", model: "m", tested: false, on: false },
    });
    expect(untested[0]).toMatchObject({ done: false, detail: "h, m · not tested yet" });
  });

  it("counts code locations, and leaves the step open while services lack one", () => {
    expect(setupSteps(READY)[3]).toMatchObject({
      done: false,
      detail: "3 of 15 services set · 12 cannot get a fix from their own code",
    });
    expect(setupSteps({ ...READY, locations: { set: 15, total: 15 } })[3].done).toBe(true);
  });

  it("names where consent and drafts stand", () => {
    const steps = setupSteps(READY);
    expect(steps[4]).toMatchObject({
      done: true,
      detail: "3 folders of Raj-232/opentelemetry-demo",
    });
    expect(steps[5]).toMatchObject({ done: true, detail: "Allowed in 1 repository · Run CI on" });
  });

  it("points the first step not done at its tab", () => {
    const steps = setupSteps(READY);
    expect(steps.filter((step) => step.next).map((step) => step.title)).toEqual([
      "Where each service's code lives",
    ]);
    expect(steps[3].where).toBe("github");
  });
});
