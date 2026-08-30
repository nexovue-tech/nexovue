import { describe, expect, it } from "vitest";

import type { EndpointHealth, ServiceEdge, ServiceHealth } from "./api";
import {
  dependencyKey,
  describeEndpoints,
  describeService,
  describeSilence,
  detailState,
  findRankedService,
  neighboursOf,
} from "./service-detail";

const STOREFRONT: ServiceHealth = {
  service_name: "demo-storefront",
  request_count: 2867,
  error_count: 158,
  requests_per_minute: 47.8,
  error_rate: 158 / 2867,
  p50_ms: 12.4,
  p95_ms: 62.9,
  p99_ms: 88.1,
};

const HEALTHY: ServiceHealth = {
  ...STOREFRONT,
  service_name: "demo-catalog",
  error_count: 0,
  error_rate: 0,
};

function endpoint(overrides: Partial<EndpointHealth>): EndpointHealth {
  return {
    service_name: "demo-storefront",
    span_kind: "SERVER",
    endpoint: "POST /checkout",
    request_count: 10,
    error_count: 0,
    requests_per_minute: 1,
    error_rate: 0,
    p50_ms: 1,
    p95_ms: 2,
    p99_ms: 3,
    ...overrides,
  };
}

function edge(caller: string, callee: string, kind = "call"): ServiceEdge {
  return {
    caller,
    callee,
    edge_kind: kind,
    call_count: 10,
    error_count: 0,
    calls_per_minute: 1,
    error_rate: 0,
    p50_ms: 1,
    p95_ms: 2,
    p99_ms: 3,
  };
}

const LOADED = {
  isPending: false,
  isError: false,
  hasTrend: true,
  hasEndpoints: true,
  hasRankedRow: true,
};

describe("detailState", () => {
  it("waits before it despairs — a failure mid-load would be retracted", () => {
    expect(detailState({ ...LOADED, isPending: true, isError: true })).toBe("pending");
  });

  it("reports a failed read once every read has settled", () => {
    expect(detailState({ ...LOADED, isError: true })).toBe("failed");
  });

  it("calls an unknown service silent, not missing", () => {
    expect(
      detailState({ ...LOADED, hasTrend: false, hasEndpoints: false, hasRankedRow: false }),
    ).toBe("silent");
  });

  it("is ready on any one source having something to show", () => {
    expect(detailState({ ...LOADED, hasEndpoints: false, hasRankedRow: false })).toBe("ready");
    expect(detailState({ ...LOADED, hasTrend: false, hasRankedRow: false })).toBe("ready");
    // Ranked but silent in the scoped reads still beats a blank screen.
    expect(detailState({ ...LOADED, hasTrend: false, hasEndpoints: false })).toBe("ready");
  });
});

describe("findRankedService", () => {
  it("finds the named row and nothing else", () => {
    expect(findRankedService([STOREFRONT, HEALTHY], "demo-catalog")).toBe(HEALTHY);
  });

  it("returns nothing when the service fell outside the ranked page", () => {
    expect(findRankedService([STOREFRONT], "demo-catalog")).toBeUndefined();
  });
});

describe("neighboursOf", () => {
  it("splits the graph into what calls this service and what it calls", () => {
    const edges = [
      edge("demo-frontend", "demo-storefront"),
      edge("demo-storefront", "demo-payments"),
      edge("demo-catalog", "demo-payments"),
    ];

    const { callers, callees } = neighboursOf(edges, "demo-storefront");

    expect(callers.map((dependency) => dependency.peer)).toEqual(["demo-frontend"]);
    expect(callees.map((dependency) => dependency.peer)).toEqual(["demo-payments"]);
  });

  it("puts a self-edge on both sides, because it genuinely is both ends", () => {
    const { callers, callees } = neighboursOf(
      [edge("demo-worker", "demo-worker", "queue")],
      "demo-worker",
    );

    expect(callers).toHaveLength(1);
    expect(callees).toHaveLength(1);
  });

  it("keeps a call and a queue between the same pair apart", () => {
    const edges = [
      edge("demo-frontend", "demo-storefront"),
      edge("demo-frontend", "demo-storefront", "queue"),
    ];
    const { callers } = neighboursOf(edges, "demo-storefront");

    const keys = callers.map((dependency) => dependencyKey("in", dependency));
    expect(new Set(keys).size).toBe(2);
  });
});

describe("describeService", () => {
  it("leads with the rate and says plainly when nothing failed", () => {
    expect(describeService(HEALTHY, "hour", 500)).toBe(
      "48 requests a minute in the last hour, none of them failing.",
    );
  });

  it("puts the failure share in the sentence when there is one", () => {
    expect(describeService(STOREFRONT, "hour", 500)).toContain("5.5% of them failing.");
  });

  it("says why the totals are missing rather than showing an empty strip", () => {
    expect(describeService(undefined, "24 hours", 500)).toContain("Outside the busiest 500");
  });
});

describe("describeEndpoints", () => {
  it("counts the failing ones, because that is what the ranking is for", () => {
    const endpoints = [endpoint({ error_count: 3 }), endpoint({ endpoint: "GET /cart" })];
    expect(describeEndpoints(endpoints)).toBe("2 endpoints, worst first. 1 failing.");
  });

  it("says none rather than zero when everything is healthy", () => {
    expect(describeEndpoints([endpoint({})])).toBe("1 endpoint, worst first. None failing.");
  });

  it("names the empty case instead of leaving a blank caption", () => {
    expect(describeEndpoints([])).toBe("No endpoint reported in this window.");
  });
});

describe("describeSilence", () => {
  it("refuses to claim the service does not exist", () => {
    const sentence = describeSilence("demo-storefront", "hour");

    expect(sentence).toContain("demo-storefront");
    expect(sentence).toContain("not the same as it not existing");
    expect(sentence).toContain("Widen the time range");
  });
});
