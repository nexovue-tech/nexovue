import { describe, expect, it } from "vitest";

import type { DeployMarker, ReleaseMark } from "./deploy-markers";
import {
  clusterMarkers,
  markersLine,
  markerTag,
  releaseMarkers,
  truncatedLine,
} from "./deploy-markers";

const WINDOW = {
  serviceName: "checkout",
  from: "2026-09-13T10:00:00Z",
  to: "2026-09-13T11:00:00Z",
};

const mark = (overrides: Partial<ReleaseMark> = {}): ReleaseMark => ({
  release_id: "release-1",
  period_id: null,
  deploy_id: "deploy-1",
  service_name: "checkout",
  version: "2026.9.13-a1b2c3d",
  at: "2026-09-13T10:30:00Z",
  basis: "reported",
  state: "finished",
  ...overrides,
});

const marker = (at: number, overrides: Partial<DeployMarker> = {}): DeployMarker => ({
  at,
  version: "v2",
  service: "checkout",
  source: "reported",
  state: "finished",
  releaseId: "release-1",
  key: `mark-${at}`,
  ...overrides,
});

describe("which marks a chart draws", () => {
  it("places one inside the window at its own moment, in seconds, with what it is", () => {
    // uPlot's time scale is in seconds. Passing millis puts the rule fifty
    // thousand years to the right, off the canvas and silently invisible.
    expect(releaseMarkers([mark()], WINDOW)).toEqual([
      {
        at: Date.parse("2026-09-13T10:30:00Z") / 1000,
        version: "2026.9.13-a1b2c3d",
        service: "checkout",
        source: "reported",
        state: "finished",
        releaseId: "release-1",
        key: "deploy-1",
      },
    ]);
  });

  it("draws nothing for a mark outside the window rather than clamping it", () => {
    // A rule pinned to the chart's left edge says "this happened at the start of
    // the window", which is a claim about a time nobody measured.
    const outside = [mark({ at: "2026-09-13T09:00:00Z" }), mark({ at: "2026-09-13T12:00:00Z" })];
    expect(releaseMarkers(outside, WINDOW)).toEqual([]);
  });

  it("draws nothing at all when the window itself is unreadable", () => {
    expect(releaseMarkers([mark()], { ...WINDOW, from: "not a time" })).toEqual([]);
  });

  it("ignores another service's mark on a service's chart, and keeps every one on Overview", () => {
    const marks = [mark({ service_name: "payments", deploy_id: "d2" }), mark()];
    expect(releaseMarkers(marks, WINDOW)).toHaveLength(1);
    expect(releaseMarkers(marks, { from: WINDOW.from, to: WINDOW.to })).toHaveLength(2);
  });

  it("orders them, so two marks read left to right, and drops a time that is not one", () => {
    const versions = releaseMarkers(
      [
        mark({ version: "second", at: "2026-09-13T10:45:00Z", deploy_id: "b" }),
        mark({ version: "first", at: "2026-09-13T10:15:00Z", deploy_id: "a" }),
        mark({ at: "whenever", deploy_id: "c" }),
      ],
      WINDOW,
    ).map((each) => each.version);
    expect(versions).toEqual(["first", "second"]);
  });
});

describe("the word a mark carries (FR37)", () => {
  it("names failed, rolled-back and seen-only marks, and nothing else", () => {
    expect(markerTag(marker(1, { state: "failed" }))).toBe("failed");
    expect(markerTag(marker(1, { state: "rolled_back" }))).toBe("rolled back");
    expect(markerTag(marker(1, { source: "seen", state: null }))).toBe("seen");
    expect(markerTag(marker(1))).toBeNull();
    expect(markerTag(marker(1, { state: "started" }))).toBeNull();
  });
});

describe("marks close together (AC39)", () => {
  const atPixel = (seconds: number) => seconds;

  it("collapse within 8 pixels into one tag with the count", () => {
    const clusters = clusterMarkers([marker(100), marker(104), marker(108), marker(130)], atPixel);
    expect(clusters.map((cluster) => [cluster.left, cluster.markers.length, cluster.tag])).toEqual([
      [100, 3, "3 releases"],
      [130, 1, null],
    ]);
  });

  it("keep a lone mark's own word", () => {
    const [cluster] = clusterMarkers([marker(50, { state: "failed" })], atPixel);
    expect(cluster.tag).toBe("failed");
  });

  it("leave out a mark the chart cannot place", () => {
    expect(clusterMarkers([marker(1)], () => Number.NaN)).toEqual([]);
  });
});

describe("the line under a chart group", () => {
  it("says what the marks mean and how many", () => {
    expect(markersLine(3)).toBe(
      "Dashed lines mark the 3 releases in this window. A tag names failed, rolled-back and seen-only ones.",
    );
    expect(markersLine(1)).toBe(
      "Dashed lines mark the 1 release in this window. A tag names failed, rolled-back and seen-only ones.",
    );
    expect(markersLine(0)).toBe("No release in this window.");
  });

  it("says when the cap kept only the newest (FR35)", () => {
    expect(truncatedLine(500)).toBe("Showing the newest 500 of this window.");
  });
});
