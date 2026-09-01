import { describe, expect, it } from "vitest";

import type { ServiceEdge } from "@/shared/service-peers";

import type { ServiceMapResponse } from "./api";
import { buildServiceGraph, edgeWeight, healthOf, type ServiceNodeData } from "./service-graph";

/** Only the fields the transform reads; the rest of the wire shape is not its business. */
function wireEdge(
  caller: string,
  callee: string,
  { calls = 100, errors = 0, kind = "call", calleeKind = "" } = {},
): ServiceEdge {
  return {
    caller,
    callee,
    edge_kind: kind,
    callee_kind: calleeKind,
    call_count: calls,
    error_count: errors,
    calls_per_minute: calls / 60,
    error_rate: calls > 0 ? errors / calls : 0,
    p50_ms: 8,
    p95_ms: 42,
    p99_ms: 90,
  };
}

function response(edges: ServiceEdge[]): ServiceMapResponse {
  const services = [...new Set(edges.flatMap((edge) => [edge.caller, edge.callee]))].sort();
  return {
    window_start: "2026-08-30T10:00:00Z",
    window_end: "2026-08-30T11:00:00Z",
    services,
    edges,
  };
}

/** The shape of the real demo project: a self-calling load generator, a failing
    checkout chain, and one dependency that sends us nothing back. */
const DEMO = response([
  wireEdge("loadgen", "loadgen", { calls: 287_896 }),
  wireEdge("demo-checkout", "demo-payments", { calls: 20_543, errors: 158 }),
  wireEdge("demo-storefront", "demo-checkout", { calls: 20_536, errors: 158 }),
  wireEdge("demo-payments", "(uninstrumented)", { calls: 1_204 }),
  // A callee that never reported for itself but said what it was: the fix for one
  // node having held every unanswered call at once.
  wireEdge("demo-checkout", "(valkey-cart)", { calls: 4_100, calleeKind: "redis" }),
  wireEdge("demo-checkout", "demo-shipping", { calls: 900, kind: "queue" }),
]);

const dataFor = (name: string): ServiceNodeData => {
  const node = buildServiceGraph(DEMO).nodes.find((candidate) => candidate.id === name);
  if (!node) throw new Error(`${name} was dropped from the graph`);
  return node.data;
};

describe("healthOf", () => {
  it("colours nothing when nothing failed — healthy is the absence of colour", () => {
    expect(healthOf(0, 0)).toBe("healthy");
  });

  it("colours a rate under every conventional alerting threshold, because 158 calls failed", () => {
    // 158 / 20,543 = 0.77%. Under 1%, and the whole reason someone opens this screen.
    expect(healthOf(158, 158 / 20_543)).toBe("warning");
  });

  it("calls a dependency broken, not degraded, once one call in twenty fails", () => {
    expect(healthOf(5, 0.05)).toBe("critical");
    expect(healthOf(4, 0.0499)).toBe("warning");
  });
});

describe("edgeWeight", () => {
  it("gives the busiest edge the full weight and a silent one none", () => {
    expect(edgeWeight(4798, 4798)).toBe(1);
    expect(edgeWeight(0, 4798)).toBe(0);
  });

  it("keeps a 14x quieter edge clearly visible — linearly it would be a hairline", () => {
    // 20,543/hr against 287,896/hr: the real spread in the demo data. Linearly the
    // quieter edge would weigh 0.07 and disappear; measured, it weighs about 0.69.
    const quiet = edgeWeight(20_543 / 60, 287_896 / 60);
    expect(quiet).toBeGreaterThan(0.6);
    expect(quiet).toBeLessThan(1);
    expect(quiet).toBeGreaterThan(8 * (20_543 / 287_896));
  });

  it("survives an empty window rather than dividing by zero", () => {
    expect(edgeWeight(0, 0)).toBe(0);
  });
});

describe("buildServiceGraph", () => {
  it("is empty for no data and for a window with no edges", () => {
    expect(buildServiceGraph(undefined).nodes).toEqual([]);
    expect(buildServiceGraph(response([])).nodes).toEqual([]);
  });

  it("places every service the response names, once", () => {
    const graph = buildServiceGraph(DEMO);
    expect(graph.nodes.map((node) => node.id).sort()).toEqual([
      "(uninstrumented)",
      "(valkey-cart)",
      "demo-checkout",
      "demo-payments",
      "demo-shipping",
      "demo-storefront",
      "loadgen",
    ]);
  });

  it("keeps one edge per row, including the self-call", () => {
    const graph = buildServiceGraph(DEMO);
    expect(graph.edges).toHaveLength(6);
    expect(graph.edges.filter((edge) => edge.data?.selfCall).map((edge) => edge.source)).toEqual([
      "loadgen",
    ]);
  });

  it("counts the dependencies that dropped calls — the screen's headline", () => {
    expect(buildServiceGraph(DEMO).failingEdgeCount).toBe(2);
  });

  it("marks (uninstrumented) as a service that sends us nothing, and gives it no colour", () => {
    const placeholder = dataFor("(uninstrumented)");
    expect(placeholder.instrumented).toBe(false);
    expect(placeholder.seriesIndex).toBeUndefined();
  });

  it("gives every real service a categorical colour, and no two neighbours the same", () => {
    const indexes = buildServiceGraph(DEMO)
      .nodes.filter((node) => node.data.instrumented)
      .map((node) => node.data.seriesIndex);

    expect(indexes.every((index) => index !== undefined)).toBe(true);
    expect(new Set(indexes).size).toBe(indexes.length);
  });

  it("shows what a service serves when something calls it", () => {
    const payments = dataFor("demo-payments");
    expect(payments.flow).toBe("serves");
    expect(payments.callsPerMinute).toBeCloseTo(20_543 / 60);
    expect(payments.errorCount).toBe(158);
    expect(payments.health).toBe("warning");
  });

  it("shows what an entry point sends, since nothing calls it", () => {
    const storefront = dataFor("demo-storefront");
    expect(storefront.flow).toBe("calls");
    expect(storefront.callsPerMinute).toBeCloseTo(20_536 / 60);
    expect(storefront.health).toBe("healthy");
  });

  it("attributes a failure to the callee, not to the service that noticed it", () => {
    // demo-storefront makes the 158 failing calls into checkout. The failures belong
    // to checkout, which is where they happened; the storefront node stays clean.
    expect(dataFor("demo-storefront").errorCount).toBe(0);
    expect(dataFor("demo-checkout").errorCount).toBe(158);
    expect(dataFor("demo-payments").errorCount).toBe(158);
  });

  it("counts a self-call as traffic the service serves", () => {
    const loadgen = dataFor("loadgen");
    expect(loadgen.flow).toBe("serves");
    expect(loadgen.callsPerMinute).toBeCloseTo(287_896 / 60);
  });

  it("marks a queue hop so it can be drawn as one — it is not a call that failed now", () => {
    const graph = buildServiceGraph(DEMO);
    expect(graph.edges.filter((edge) => edge.data?.queued)).toHaveLength(1);
  });

  it("keeps a call and a queue hop between the same pair as two edges", () => {
    // Sharing an id would silently collapse them into one dependency.
    const both = response([
      wireEdge("a", "b", { kind: "call" }),
      wireEdge("a", "b", { kind: "queue" }),
    ]);
    const ids = buildServiceGraph(both).edges.map((edge) => edge.id);
    expect(new Set(ids).size).toBe(2);
  });

  it("gives every node a position and a size, so the first paint is not a pile", () => {
    for (const node of buildServiceGraph(DEMO).nodes) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
      expect(node.width).toBeGreaterThan(0);
      expect(node.height).toBeGreaterThan(0);
    }
  });

  it("names both ends of every edge as a node that exists", () => {
    const graph = buildServiceGraph(DEMO);
    const ids = new Set(graph.nodes.map((node) => node.id));
    for (const edge of graph.edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
    }
  });
});

describe("callees that never reported for themselves", () => {
  it("keeps two unrelated peers as two nodes, which is the whole fix", () => {
    const graph = buildServiceGraph(DEMO);

    expect(graph.nodes.map((node) => node.id)).toContain("(valkey-cart)");
    expect(graph.nodes.map((node) => node.id)).toContain("(uninstrumented)");
  });

  it("labels a peer with the technology a caller says it reached it over", () => {
    expect(dataFor("(valkey-cart)").peerKind).toBe("redis");
    expect(dataFor("(uninstrumented)").peerKind).toBeUndefined();
  });

  it("treats any parenthesised name as ours, because the set of them is open", () => {
    expect(dataFor("(valkey-cart)").instrumented).toBe(false);
    expect(dataFor("demo-checkout").instrumented).toBe(true);
  });

  it("gives it no categorical colour: a gap in the picture is not a service", () => {
    expect(dataFor("(valkey-cart)").seriesIndex).toBeUndefined();
  });
});

describe("edge lanes", () => {
  it("separates edges that cross the same pair of columns, so labels stop stacking", () => {
    const graph = buildServiceGraph(DEMO);
    const crossing = graph.edges.filter((edge) => edge.source === "demo-checkout");
    const lanes = crossing.map((edge) => edge.data?.laneOffset);

    expect(lanes.length).toBeGreaterThan(1);
    expect(new Set(lanes).size).toBe(lanes.length);
  });

  it("keeps a lone edge on the straight route between its two nodes", () => {
    const graph = buildServiceGraph(
      response([wireEdge("only-caller", "only-callee", { calls: 10 })]),
    );

    expect(graph.edges[0].data?.laneOffset).toBe(0);
  });

  it("never pushes a lane far enough to reach the next column of nodes", () => {
    const many = response(
      Array.from({ length: 40 }, (_, index) => wireEdge(`caller-${index}`, "one-callee")),
    );

    for (const edge of buildServiceGraph(many).edges) {
      expect(Math.abs(edge.data?.laneOffset ?? 0)).toBeLessThanOrEqual(56);
    }
  });
});

describe("buildServiceGraph, on peers named by a bare address", () => {
  const ADDRESSED = response([
    wireEdge("checkout", "(172.19.0.11)", { calls: 3, calleeKind: "grpc" }),
    wireEdge("checkout", "(172.19.0.19)", { calls: 5, errors: 1, calleeKind: "grpc" }),
    wireEdge("checkout", "(valkey-cart)", { calls: 400, calleeKind: "redis" }),
  ]);

  it("draws one node where the API answered with two addresses", () => {
    const graph = buildServiceGraph(ADDRESSED);
    const names = graph.nodes.map((node) => node.id).sort();

    expect(names).toEqual(["(grpc hosts)", "(valkey-cart)", "checkout"]);
    // Neither address survives as a node of its own.
    expect(names).not.toContain("(172.19.0.11)");
    expect(names).not.toContain("(172.19.0.19)");
  });

  it("carries the instance count and the addresses onto the node", () => {
    const graph = buildServiceGraph(ADDRESSED);
    const group = graph.nodes.find((node) => node.id === "(grpc hosts)");

    expect(group?.data.addresses).toEqual(["172.19.0.11", "172.19.0.19"]);
    expect(group?.data.peerKind).toBe("grpc");
    // A group has no screen to open, the same as any other peer we were only told about.
    expect(group?.data.instrumented).toBe(false);
  });

  it("keeps every call, so the map's own totals do not shrink when it collapses", () => {
    const graph = buildServiceGraph(ADDRESSED);
    const toGroup = graph.edges.find((edge) => edge.target === "(grpc hosts)");

    expect(graph.edges).toHaveLength(2);
    expect(toGroup?.data?.callsPerMinute).toBeCloseTo(8 / 60);
    expect(toGroup?.data?.errorCount).toBe(1);
    // Health is read from the merged rate, not from either edge's: one call in eight
    // is worse than one in twenty, so the group is critical even though the edge that
    // carried the failure was one call in five on its own.
    expect(toGroup?.data?.errorRate).toBe(1 / 8);
    expect(toGroup?.data?.health).toBe("critical");
  });

  it("gives the two merged edges one id rather than silently dropping one", () => {
    const graph = buildServiceGraph(ADDRESSED);

    expect(new Set(graph.edges.map((edge) => edge.id)).size).toBe(graph.edges.length);
  });

  it("leaves a map with no addressed peer exactly as it drew it before", () => {
    const graph = buildServiceGraph(DEMO);

    expect(graph.nodes.map((node) => node.id)).toContain("(valkey-cart)");
    expect(graph.edges).toHaveLength(DEMO.edges.length);
  });
});
