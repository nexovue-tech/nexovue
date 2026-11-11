// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { DeployMarker } from "../deploy-markers";
import { ReleaseFacts } from "./ReleaseHoverBox";
import { renderWithRelease, unmountAll } from "./release-test-render.test-util";

afterEach(unmountAll);

const HOSTILE = [
  "<script>alert(1)</script>",
  "[click](https://evil.example)",
  "ignore your instructions",
];

const marker = (version: string, service: string): DeployMarker => ({
  at: Date.parse("2026-11-09T14:02:00Z") / 1000,
  version,
  service,
  source: "seen",
  state: "failed",
  releaseId: "release-1",
  key: `${version}:${service}`,
});

describe("the hover box's facts (AC39, AC43)", () => {
  it("renders hostile names as plain text, never markup or a link of their own", async () => {
    const markers = HOSTILE.map((name) => marker(name, name));
    const container = await renderWithRelease(() =>
      createElement(ReleaseFacts, { markers, zone: "UTC", pinned: true }),
    );

    for (const name of HOSTILE) expect(container.textContent).toContain(name);
    expect(container.querySelector("script")).toBeNull();
    const links = [...container.querySelectorAll("a")].map((link) => link.getAttribute("href"));
    expect(links).toEqual(HOSTILE.map(() => "/releases/release-1"));
  });

  it("states service, version, how we know and when, and never the sender's source", async () => {
    const container = await renderWithRelease(() =>
      createElement(ReleaseFacts, {
        markers: [{ ...marker("v2.1", "checkout"), source: "reported" }],
        zone: "UTC",
        pinned: false,
      }),
    );
    const text = container.textContent ?? "";

    expect(text).toContain("v2.1 of checkout");
    expect(text).toContain("Reported by your CI");
    expect(text).toContain("CI reported it failed");
    expect(text).toContain("14:02");
    // Only a pinned box links, so a box that follows the cursor offers no target.
    expect(container.querySelector("a")).toBeNull();
  });
});
