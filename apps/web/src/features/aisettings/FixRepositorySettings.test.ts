// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, settle, unmountAll } from "@/shared/router-harness.test-util";

import type { RepositoryDraftSettingsView } from "./FixRepositorySettings";
import { FixRepositorySettings } from "./FixRepositorySettings";

/**
 * An owner's per-repository draft choices (F5 plan T7.7; spec BR14, D45, AC60, AC61). Each is
 * off until an owner sets it; an owner's change is one `PUT`; other roles read the state
 * only; while write access is off in code, the API's sentence shows above the choices.
 */

const API_PREFIX = "/api/v1";
const WRITES_OFF =
  "Opening a draft pull request is switched off in this version of Nexovue, until its safety evaluation is done. Download the patch instead.";

const VIEW: RepositoryDraftSettingsView = {
  available: true,
  writes_off: WRITES_OFF,
  repositories: [
    {
      repository_id: "r-1",
      repository: "acme/shop",
      is_private: true,
      drafts_allowed: false,
      ci_on_open: false,
      draft_refused_recently: false,
      set_at: null,
    },
  ],
};

function stub(role: TestRole, view: RepositoryDraftSettingsView = VIEW) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      if (path === "/auth/me") return reply(identityFor(role));
      if (path === "/connections/github/repositories/fix-settings") return reply(view);
      if (method === "PUT") return reply({ ...view.repositories[0], drafts_allowed: true });
      return reply({});
    }),
  );
  return calls;
}

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
});

const panel = () => createElement(FixRepositorySettings);

describe("draft pull request settings (AC60, AC61)", () => {
  it("says writes are off, and an owner's allowance is one PUT, off until set", async () => {
    const calls = stub("owner");
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain(WRITES_OFF);
    expect(shown.textContent).toContain("This is Nexovue's limit, not GitHub's");
    const boxes = [...shown.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
    expect(boxes.map((box) => box.checked)).toEqual([false, false]);
    act(() => boxes[0]?.click());
    for (let round = 0; round < 4; round += 1) await settle();

    const put = calls.find((call) => call.method === "PUT");
    expect(put?.path).toBe("/connections/github/repositories/r-1/fix-settings");
    expect(put?.body).toEqual({ drafts_allowed: true, ci_on_open: false });
  });

  it("CI on open warns that it runs the model's code with the repository's secrets", async () => {
    stub("owner", {
      ...VIEW,
      repositories: [{ ...VIEW.repositories[0], drafts_allowed: true, ci_on_open: true }],
    });
    const shown = await mount(panel);
    for (let round = 0; round < 4; round += 1) await settle();

    expect(shown.textContent).toContain(
      "CI then runs the model's code, possibly with this repository's secrets, before anyone reviews it.",
    );
  });

  it("an admin and a member read the state without the controls", async () => {
    for (const role of ["admin", "member"] as const) {
      stub(role);
      const shown = await mount(panel);
      for (let round = 0; round < 4; round += 1) await settle();

      expect(shown.querySelectorAll("input[type=checkbox]")).toHaveLength(0);
      expect(shown.textContent).toContain("Allow draft pull requests: off");
      unmountAll();
      vi.unstubAllGlobals();
    }
  });
});
