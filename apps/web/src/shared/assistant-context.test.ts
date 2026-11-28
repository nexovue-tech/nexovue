// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useOffersAssistant } from "@/shared/assistant-context";
import { identityFor, type TestRole } from "@/shared/identity.test-util";
import { mount, unmountAll } from "@/shared/router-harness.test-util";

/**
 * F4: whether chat is offered at all, the one rule the TopBar's Ask button and the finding
 * page's link share: a role that may ask, not a support view, and an install that offers AI.
 */

function Offered() {
  return createElement("output", null, String(useOffersAssistant()));
}

function stub(role: TestRole, deployment: "hosted" | "self-hosted", supportView = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.replace("/api/v1", "");
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      if (path === "/auth/me") {
        const identity = identityFor(role);
        return reply(
          supportView
            ? {
                ...identity,
                support_view: { staff: "nexovue support", until: "2026-11-12T10:00:00Z" },
              }
            : identity,
        );
      }
      if (path === "/system/info") return reply({ deployment, version: "dev" });
      if (path === "/projects") return reply({ projects: [] });
      return new Response("{}", { status: 404 });
    }),
  );
}

afterEach(() => {
  unmountAll();
  vi.unstubAllGlobals();
});

describe("whether chat is offered", () => {
  it.each([
    ["member", "self-hosted", false, "true"],
    ["viewer", "self-hosted", false, "false"],
    ["member", "hosted", false, "false"],
    ["owner", "self-hosted", true, "false"],
  ] as const)("%s on %s, support view %s: %s", async (role, deployment, supportView, offered) => {
    stub(role, deployment, supportView);
    const shown = await mount(Offered);
    expect(shown.querySelector("output")?.textContent).toBe(offered);
  });
});
