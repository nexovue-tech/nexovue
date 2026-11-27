// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { identityFor } from "@/shared/identity.test-util";
import { mount, settle, unmountAll } from "@/shared/router-harness.test-util";

import type { CodeLocations, FixAppView } from "./FixAppPanel";
import { FixAppPanel } from "./FixAppPanel";

/**
 * F5's Fix App and code locations panel (plan T3.3, T3.4, T3.6; spec FR5b, AC12a): a grant
 * not yet read is an em dash, never "none"; a location says whether an owner agreed; a
 * service with no location is listed; an App not created says so first.
 */

const API_PREFIX = "/api/v1";

const APP: FixAppView = {
  available: true,
  state: "working",
  setup_step: null,
  install_url: null,
  permissions: null,
  granted_at: null,
  connection_id: "c-1",
  repositories: [{ id: "r-1", key: "acme/shop", is_private: true, is_reachable: true }],
};

const LOCATIONS: CodeLocations = {
  locations: [
    {
      id: "l-1",
      service: "checkout",
      project_id: null,
      repository_id: "r-1",
      repository: "acme/shop",
      folder: "services/shop",
      path_in_production: null,
      consented: false,
      set_at: "2026-11-21T10:00:00Z",
    },
  ],
  services_without_location: ["search"],
};

function stub(app: FixAppView) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.slice(API_PREFIX.length);
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      if (path === "/auth/me") return reply(identityFor("admin"));
      if (path === "/connections/github/fix-app") return reply(app);
      if (path === "/code-locations") return reply(LOCATIONS);
      return reply({});
    }),
  );
}

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
});

describe("the Nexovue suggestions App (T3.3)", () => {
  it("shows an unread grant as an em dash, the repositories and the code locations", async () => {
    stub(APP);
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("Permissions GitHub granted: —");
    expect(shown.textContent).toContain("acme/shop · reachable");
    expect(shown.textContent).toContain("No owner has agreed for this folder yet");
    expect(shown.textContent).toContain("search · —");
  });

  it("offers to create the App when there is none, and no locations before it", async () => {
    stub({ ...APP, state: null, repositories: [] });
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("Create the App on GitHub");
    expect(shown.textContent).toContain("The Nexovue suggestions App is not set up yet.");
  });

  it("offers to create the App again when a setup expired, rather than a dead end", async () => {
    stub({ ...APP, state: "setting_up", setup_step: "expired", repositories: [] });
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("Create the App on GitHub");
  });

  it("offers to stop a setup still on GitHub, rather than a dead end, and stopping deletes it", async () => {
    stub({ ...APP, state: "setting_up", setup_step: "on_github", repositories: [] });
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();

    const stop = [...shown.querySelectorAll("button")].find(
      (button) => button.textContent === "Stop setting up",
    );
    expect(stop).toBeDefined();
    stop?.click();
    for (let round = 0; round < 4; round += 1) await settle();
    const calls = (fetch as unknown as { mock: { calls: [URL, RequestInit?][] } }).mock.calls;
    expect(
      calls.some(
        ([url, init]) =>
          url.pathname === `${API_PREFIX}/connections/c-1` && init?.method === "DELETE",
      ),
    ).toBe(true);
  });

  it("T7.7: before raising, warns what write reaches and signed commits, and names no plan requirement (F5 decision 95)", async () => {
    stub(APP);
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();

    const raise = shown.querySelector("[data-testid=fix-app-raise]");
    expect(raise?.textContent).toContain(
      "Write access reaches every repository you installed this App on, including branches and tags that may start your deploys.",
    );
    // GitHub allows draft pull requests in every repository on every plan (D62).
    expect(raise?.textContent).not.toContain("Enterprise Cloud");
    expect(raise?.textContent).not.toContain("check your plan");
    expect(raise?.textContent).toContain("GitHub's organization owner then accepts");
    expect(raise?.textContent).toContain("a draft's commit may not count as signed");
  });
});
