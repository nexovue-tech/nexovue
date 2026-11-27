// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { identityFor } from "@/shared/identity.test-util";
import { mount, settle, unmountAll } from "@/shared/router-harness.test-util";
import { pollWhileSettingUp, SETUP_POLL_MS } from "./api";
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

  it("says why a code location was not saved, rather than nothing (live test E2)", async () => {
    stub(APP);
    const plain = fetch as unknown as (url: URL, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: URL, init?: RequestInit) =>
        url.pathname === `${API_PREFIX}/code-locations` && init?.method === "PUT"
          ? new Response(
              JSON.stringify({
                title: "Unprocessable",
                status: 422,
                detail: "That folder is outside the repository.",
              }),
              { status: 422, headers: { "content-type": "application/problem+json" } },
            )
          : plain(url, init),
      ),
    );
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();
    const field = (name: string) => shown.querySelector<HTMLInputElement>(`input[name="${name}"]`);
    for (const [name, value] of [
      ["service", "checkout"],
      ["repository", "acme/shop"],
    ] as const) {
      const input = field(name);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    }
    for (let round = 0; round < 4; round += 1) await settle();
    // Folder and path in production are left blank: both are optional.
    [...shown.querySelectorAll("button")]
      .find((button) => button.textContent === "Save code location")
      ?.click();
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain("The code location was not saved");
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

describe("drafts in a public repository (FR42; live test, 2026-11-28)", () => {
  it("refuses until an admin allows it after a warning, and sends the allowance", async () => {
    stub({
      ...APP,
      repositories: [
        {
          id: "r-1",
          key: "acme/site",
          is_private: false,
          is_reachable: true,
          public_allowed_at: null,
        },
      ],
    });
    const shown = await mount(() => createElement(FixAppPanel));
    for (let round = 0; round < 4; round += 1) await settle();
    expect(shown.textContent).toContain("Public: drafts refused until an admin allows them.");

    const press = (words: string) =>
      [...shown.querySelectorAll("button")].find((button) => button.textContent === words)?.click();
    press("Allow drafts");
    for (let round = 0; round < 2; round += 1) await settle();
    expect(shown.textContent).toContain("anyone can see that something is broken, and where");
    press("Allow drafts in acme/site");
    for (let round = 0; round < 4; round += 1) await settle();

    const calls = (fetch as unknown as { mock: { calls: [URL, RequestInit?][] } }).mock.calls;
    const sent = calls.find(
      ([url, init]) =>
        url.pathname === `${API_PREFIX}/connections/c-1/places/r-1` && init?.method === "PATCH",
    );
    expect(sent?.[1]?.body).toBe(JSON.stringify({ public_allowed: true }));
  });
});

describe("a setup GitHub finishes in the background (live tests C1, E2)", () => {
  it("re-reads while a setup is in progress, and stops once it settles or expires", () => {
    expect(pollWhileSettingUp([{ state: "setting_up", setup_step: "on_github" }])).toBe(
      SETUP_POLL_MS,
    );
    expect(
      pollWhileSettingUp([
        { state: "working", setup_step: null },
        { state: "setting_up", setup_step: "not_installed" },
      ]),
    ).toBe(SETUP_POLL_MS);
    expect(pollWhileSettingUp([{ state: "setting_up", setup_step: "expired" }])).toBe(false);
    expect(pollWhileSettingUp([{ state: "working", setup_step: null }])).toBe(false);
    expect(pollWhileSettingUp([])).toBe(false);
  });
});
