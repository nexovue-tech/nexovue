// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, PROJECT_ID, settle, unmountAll } from "@/shared/router-harness.test-util";

import type { ReadinessCounts, SourceConsentOverview } from "./FixConsentPanel";
import { FixConsentPanel, scopeQuery } from "./FixConsentPanel";

/**
 * F5's consent and readiness panels (plan T3.5, T3.6; spec FR6, FR7, FR10b, AC44, D20).
 * The owner's access question has nothing preselected, so Agree stays unavailable until it
 * is answered; other roles read the state only; readiness says what it counts, and an
 * empty project says there is nothing to estimate.
 */

const API_PREFIX = "/api/v1";

const OVERVIEW: SourceConsentOverview = {
  available: true,
  provider_host: "provider.example",
  model: "model-a",
  region: null,
  requesters: 4,
  repositories: [
    {
      repository_id: "r-1",
      repository: "acme/shop",
      is_private: true,
      is_reachable: true,
      consents: [
        {
          id: "c-1",
          folder_scope: "services/shop",
          provider_host: "provider.example",
          model: "model-a",
          require_github_access: false,
          consented_at: "2026-11-20T10:00:00Z",
          standing: "ended",
        },
      ],
    },
  ],
  sentences: [
    "Nexovue cannot see or control what this provider keeps or trains on; check your contract.",
  ],
  never_sent: [".env*"],
};

const READINESS: ReadinessCounts = {
  findings: 5,
  ready: 2,
  ready_without_stack: 2,
  cause_is_elsewhere: 1,
  project_finding: 0,
  closed_as_not_a_problem: 0,
  services_without_location: ["search"],
  sentence:
    "Counts only the evidence Nexovue holds; whether files match is known only after a read.",
};

function stub(role: TestRole, readiness: ReadinessCounts = READINESS) {
  const calls: { method: string; path: string; search: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, search: url.search });
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      if (path === "/auth/me") return reply(identityFor(role));
      if (path === "/projects") {
        return reply({
          projects: [{ id: PROJECT_ID, slug: "p", name: "production", first_reported_on: null }],
        });
      }
      if (path === "/connections/github/repositories/source-consent") return reply(OVERVIEW);
      if (path === "/fix-readiness") return reply(readiness);
      return reply({});
    }),
  );
  return calls;
}

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
});

const panel = () => createElement(FixConsentPanel);

describe("source consent (FR6, FR8)", () => {
  it("shows an ended consent and who can ask, and the owner must answer before agreeing", async () => {
    stub("owner");
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("Ended: the provider, key or model changed.");
    expect(shown.textContent).toContain("4 people can ask for a suggested fix today.");
    const agree = [...shown.querySelectorAll("button")].find((b) => b.textContent === "Agree");
    act(() => agree?.click());
    const confirm = [...shown.querySelectorAll("button")].find(
      (b) => b.textContent === "Agree to send this code",
    );
    expect(confirm?.hasAttribute("disabled")).toBe(true);
    expect(shown.querySelectorAll("input[type='radio']:checked")).toHaveLength(0);
    expect(shown.textContent).toContain("Nexovue cannot see or control what this provider keeps");
  });

  it("a member reads the state without the controls", async () => {
    stub("member");
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("acme/shop");
    expect([...shown.querySelectorAll("button")].some((b) => b.textContent === "Agree")).toBe(
      false,
    );
  });

  it("names a folder, slashes and all, or the whole repository", () => {
    expect(scopeQuery("services/shop/")).toBe("folder=services%2Fshop");
    expect(scopeQuery("  ")).toBe("scope=whole-repository");
  });
});

describe("readiness (FR10b, AC44)", () => {
  it("counts each result and says what it counts", async () => {
    stub("owner");
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("Ready from the error's stack: 2");
    expect(shown.textContent).toContain("Services with no code location: search");
    expect(shown.textContent).toContain("Counts only the evidence Nexovue holds");
  });

  it("with no findings, says there is nothing to estimate", async () => {
    stub("owner", {
      ...READINESS,
      findings: 0,
      sentence: "No findings in the last 30 days, so there is nothing to estimate yet.",
    });
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("nothing to estimate yet");
    expect(shown.textContent).not.toContain("Ready from the error's stack");
  });
});
