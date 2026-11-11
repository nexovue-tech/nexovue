// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { DeployMarker } from "../deploy-markers";
import { clusterMarkers } from "../deploy-markers";
import { ChartReleases, ReleaseStrip } from "./ChartReleases";
import { renderWithRelease, unmountAll } from "./release-test-render.test-util";

afterEach(unmountAll);

const marker = (at: number, overrides: Partial<DeployMarker> = {}): DeployMarker => ({
  at,
  version: `v${at}`,
  service: "checkout",
  source: "reported",
  state: "finished",
  releaseId: `release-${at}`,
  key: `mark-${at}`,
  ...overrides,
});

const MARKERS = [
  marker(100),
  marker(103),
  marker(105),
  marker(200, { state: "failed" }),
  marker(300, { source: "seen", state: null }),
  marker(400, { state: "rolled_back" }),
];

async function strip(): Promise<HTMLElement> {
  const clusters = clusterMarkers(MARKERS, (seconds) => seconds);
  return renderWithRelease(() =>
    createElement(ReleaseStrip, { clusters, plotLeft: 52, zone: "UTC" }),
  );
}

describe("the strip above a chart (AC39, FR37)", () => {
  it("puts a word on failed, rolled-back and seen marks, and the count on a close run", async () => {
    const container = await strip();
    const tags = [...container.querySelectorAll("button")].map((tag) => tag.textContent);

    expect(tags).toEqual(["3 releases", "failed", "seen", "rolled back"]);
  });

  it("places each tag where the chart places its dash", async () => {
    const container = await strip();
    const [first] = container.querySelectorAll<HTMLButtonElement>("button");

    expect(first?.style.left).toBe("152px");
  });

  it("shows the facts on hover or focus, and a click pins them with a link and a close", async () => {
    const container = await strip();
    const failed = container.querySelectorAll("button")[1] as HTMLButtonElement;

    await act(async () => failed.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
    const tooltip = document.body.querySelector('[role="tooltip"]');
    expect(tooltip?.textContent).toContain("v200 of checkout");
    expect(tooltip?.querySelector("a")).toBeNull();

    await act(async () => failed.click());
    const pinned = document.body.querySelector('[role="dialog"]');
    expect(pinned?.querySelector('a[href="/releases/release-200"]')).not.toBeNull();
    expect(failed.getAttribute("aria-expanded")).toBe("true");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });
});

describe("the group's line and list", () => {
  const read = { markers: MARKERS, isError: false, onRetry: () => {}, truncated: false, cap: 500 };

  it("lists every mark as text, newest first, open where asked", async () => {
    const container = await renderWithRelease(() =>
      createElement(ChartReleases, { read, zone: "UTC", open: true }),
    );
    const details = container.querySelector("details");

    expect(details?.open).toBe(true);
    expect(details?.querySelector("summary")?.textContent).toBe("Releases in this window (6)");
    const links = [...(details?.querySelectorAll("a") ?? [])].map((a) => a.getAttribute("href"));
    expect(links[0]).toBe("/releases/release-400");
    expect(links).toHaveLength(6);
  });

  it("is closed by default", async () => {
    const container = await renderWithRelease(() => createElement(ChartReleases, { read }));
    expect(container.querySelector("details")?.open).toBe(false);
  });
});
