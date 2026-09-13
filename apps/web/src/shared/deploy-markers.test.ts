import { describe, expect, it } from "vitest";

import type { DeployEvent } from "./deploy-markers";
import { deployMarkers } from "./deploy-markers";

const WINDOW = {
  serviceName: "checkout",
  from: "2026-09-13T10:00:00Z",
  to: "2026-09-13T11:00:00Z",
};

const deploy = (overrides: Partial<DeployEvent> = {}): DeployEvent => ({
  version: "2026.9.13-a1b2c3d",
  service_name: "checkout",
  deployed_at: "2026-09-13T10:30:00Z",
  ...overrides,
});

describe("which deploys a chart draws", () => {
  it("places one inside the window at its own moment, in seconds", () => {
    // uPlot's time scale is in seconds. Passing millis puts the rule fifty
    // thousand years to the right, off the canvas and silently invisible.
    expect(deployMarkers([deploy()], WINDOW)).toEqual([
      { at: Date.parse("2026-09-13T10:30:00Z") / 1000, version: "2026.9.13-a1b2c3d" },
    ]);
  });

  it("draws nothing for a deploy outside the window rather than clamping it", () => {
    // A rule pinned to the chart's left edge says "this deploy happened at the
    // start of the window", which is a claim about a time nobody measured.
    const outside = [
      deploy({ deployed_at: "2026-09-13T09:00:00Z" }),
      deploy({ deployed_at: "2026-09-13T12:00:00Z" }),
    ];

    expect(deployMarkers(outside, WINDOW)).toEqual([]);
  });

  it("ignores a deploy of a different service", () => {
    // A project deploys many services, and a rule from an unrelated one is an
    // invitation to blame the wrong change for the step in this chart.
    expect(deployMarkers([deploy({ service_name: "payments" })], WINDOW)).toEqual([]);
  });

  it("orders them, so two deploys read left to right", () => {
    const versions = deployMarkers(
      [
        deploy({ version: "second", deployed_at: "2026-09-13T10:45:00Z" }),
        deploy({ version: "first", deployed_at: "2026-09-13T10:15:00Z" }),
      ],
      WINDOW,
    ).map((marker) => marker.version);

    expect(versions).toEqual(["first", "second"]);
  });

  it("drops a deploy whose timestamp is not one", () => {
    expect(deployMarkers([deploy({ deployed_at: "whenever" })], WINDOW)).toEqual([]);
  });

  it("draws nothing at all when the window itself is unreadable", () => {
    expect(deployMarkers([deploy()], { ...WINDOW, from: "not a time" })).toEqual([]);
  });
});

describe("a chart with no single service", () => {
  it("draws every deploy in the window, whichever service shipped it", () => {
    // The dashboard's panels are the whole project, and a deploy that moved
    // rate, errors and latency together is the reading — whoever shipped it.
    const markers = deployMarkers(
      [
        deploy({ version: "checkout-1", service_name: "checkout" }),
        deploy({ version: "payments-1", service_name: "payments" }),
      ],
      { from: WINDOW.from, to: WINDOW.to },
    );

    expect(markers.map((marker) => marker.version)).toEqual(["checkout-1", "payments-1"]);
  });

  it("still drops one outside the window", () => {
    const markers = deployMarkers([deploy({ deployed_at: "2026-09-13T09:00:00Z" })], {
      from: WINDOW.from,
      to: WINDOW.to,
    });

    expect(markers).toEqual([]);
  });
});
