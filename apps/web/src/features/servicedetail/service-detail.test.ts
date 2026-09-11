import { describe, expect, it } from "vitest";

import type { ServiceEdge } from "@/shared/service-peers";
import { collapseAddressPeers } from "@/shared/service-peers";

import type { EndpointHealth, ErrorGroupSummary, ServiceHealth } from "./api";
import {
  dependencyKey,
  describeEndpoints,
  describeService,
  describeServiceErrors,
  describeSilence,
  detailState,
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
    callee_kind: "",
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
  hasAggregate: true,
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
      detailState({ ...LOADED, hasTrend: false, hasEndpoints: false, hasAggregate: false }),
    ).toBe("silent");
  });

  it("is ready on any one source having something to show", () => {
    expect(detailState({ ...LOADED, hasEndpoints: false, hasAggregate: false })).toBe("ready");
    expect(detailState({ ...LOADED, hasTrend: false, hasAggregate: false })).toBe("ready");
    // An aggregate but nothing else still beats a blank screen.
    expect(detailState({ ...LOADED, hasTrend: false, hasEndpoints: false })).toBe("ready");
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

  it("lists two address-named peers of one technology as one dependency", () => {
    // The map collapses these into one node; a panel that listed them separately
    // would make the same pair of peers one dependency on one screen and two on
    // another.
    const addressed = (callee: string) => ({
      ...edge("checkout", callee),
      callee_kind: "grpc",
    });
    const { edges, groups } = collapseAddressPeers([
      addressed("(172.19.0.11)"),
      addressed("(172.19.0.19)"),
    ]);

    const { callees } = neighboursOf(edges, "checkout");

    expect(callees.map((dependency) => dependency.peer)).toEqual(["(grpc hosts)"]);
    expect(callees[0].edge.call_count).toBe(20);
    expect(groups.get("(grpc hosts)")?.addresses).toEqual(["172.19.0.11", "172.19.0.19"]);
  });

  it("leaves a named peer as its own row", () => {
    const { edges, groups } = collapseAddressPeers([edge("checkout", "(valkey-cart)")]);

    const { callees } = neighboursOf(edges, "checkout");

    expect(callees.map((dependency) => dependency.peer)).toEqual(["(valkey-cart)"]);
    expect(groups.size).toBe(0);
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
    expect(describeService(HEALTHY, "hour")).toBe(
      "48 requests a minute in the last hour, none of them failing.",
    );
  });

  it("puts the failure share in the sentence when there is one", () => {
    expect(describeService(STOREFRONT, "hour")).toContain("5.5% of them failing.");
  });

  it("blames silence rather than a page it fell off, now that it asks by name", () => {
    const sentence = describeService(undefined, "24 hours");

    expect(sentence).toBe("No totals for the last 24 hours — nothing by this name reported in it.");
    expect(sentence).not.toContain("busiest");
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

function errorGroup(overrides: Partial<ErrorGroupSummary> = {}): ErrorGroupSummary {
  return {
    group_id: "1111aaaa1111aaaa",
    exception_type: "ValueError",
    message_template: "User <uuid> not found",
    latest_message: "User 9c8d7e6f-aaaa-bbbb-cccc-ddddeeeeffff not found",
    occurrences: 7,
    first_seen: "2026-08-31T09:00:00Z",
    last_seen: "2026-08-31T09:30:00Z",
    ...overrides,
  };
}

describe("describeServiceErrors", () => {
  it("counts groups and throws separately, because they need different work", () => {
    const caption = describeServiceErrors([
      errorGroup({ occurrences: 9000 }),
      errorGroup({ group_id: "2222", occurrences: 3 }),
    ]);

    expect(caption).toContain("2 distinct errors");
    expect(caption).toContain("thrown 9,003 times");
  });

  it("says the count belongs to this service and not to the project", () => {
    expect(describeServiceErrors([errorGroup()])).toContain("this service only");
  });

  it("uses the singular for one error thrown once", () => {
    const caption = describeServiceErrors([errorGroup({ occurrences: 1 })]);

    expect(caption).toContain("1 error, thrown 1 time");
  });

  it("names the empty case instead of leaving a blank caption", () => {
    expect(describeServiceErrors([])).toBe(
      "No exception was recorded in this service in the window.",
    );
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
