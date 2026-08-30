import { describe, expect, it } from "vitest";

import { type DependencyEdge, layoutServiceGraph, NODE_WIDTH } from "./graph-layout";

const edge = (from: string, to: string): DependencyEdge => ({ from, to });

/** Columns are what the layout decides; pixels are just the column times a pitch. */
function layerOf(names: string[], edges: DependencyEdge[]): Record<string, number> {
  const { placements } = layoutServiceGraph(names, edges);
  return Object.fromEntries([...placements].map(([name, place]) => [name, place.layer]));
}

const placed = (names: string[], edges: DependencyEdge[]) =>
  [...layoutServiceGraph(names, edges).placements.keys()].sort();

describe("layoutServiceGraph", () => {
  it("puts a callee one column right of its caller, so arrows read left to right", () => {
    expect(layerOf(["a", "b", "c"], [edge("a", "b"), edge("b", "c")])).toEqual({
      a: 0,
      b: 1,
      c: 2,
    });
  });

  it("uses the longest path, so no arrow ever points backwards", () => {
    // a→b directly, and a→c→b the long way. b must sit right of c, not beside it.
    expect(layerOf(["a", "b", "c"], [edge("a", "b"), edge("a", "c"), edge("c", "b")])).toEqual({
      a: 0,
      b: 2,
      c: 1,
    });
  });

  it("places a service that calls itself, and lets the self-call decide nothing", () => {
    // `loadgen → loadgen` is real data, and a node cannot be to the right of itself.
    const layers = layerOf(["loadgen"], [edge("loadgen", "loadgen")]);
    expect(layers).toEqual({ loadgen: 0 });
  });

  it("keeps a self-call from disturbing the column its service belongs in", () => {
    const layers = layerOf(
      ["gateway", "loadgen"],
      [edge("loadgen", "loadgen"), edge("loadgen", "gateway")],
    );
    expect(layers).toEqual({ loadgen: 0, gateway: 1 });
  });

  it("places a sink — a service that calls nothing back", () => {
    const layers = layerOf(
      ["storefront", "checkout", "payments"],
      [edge("storefront", "checkout"), edge("checkout", "payments")],
    );
    expect(layers.payments).toBe(2);
  });

  it("places the (uninstrumented) callee like any other node", () => {
    const layers = layerOf(
      ["demo-payments", "(uninstrumented)"],
      [edge("demo-payments", "(uninstrumented)")],
    );
    expect(layers).toEqual({ "demo-payments": 0, "(uninstrumented)": 1 });
  });

  it("terminates on a cycle and still places every node in it", () => {
    // A longest-path layering over a cycle never settles; the back edge is withheld.
    expect(placed(["a", "b", "c"], [edge("a", "b"), edge("b", "c"), edge("c", "a")])).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("terminates on two services calling each other", () => {
    const layers = layerOf(["a", "b"], [edge("a", "b"), edge("b", "a")]);
    expect(Object.keys(layers).sort()).toEqual(["a", "b"]);
    expect(layers.a).not.toBe(layers.b);
  });

  it("places a service the API listed but no edge names", () => {
    expect(placed(["orphan", "a", "b"], [edge("a", "b")])).toEqual(["a", "b", "orphan"]);
  });

  it("places a service an edge names but the node list omits — an arrow needs both ends", () => {
    expect(placed(["a"], [edge("a", "ghost")])).toEqual(["a", "ghost"]);
  });

  it("returns an empty layout for an empty graph rather than a degenerate one", () => {
    expect(layoutServiceGraph([], [])).toEqual({ placements: new Map(), width: 0, height: 0 });
  });

  it("gives the same layout whatever order the edges arrive in", () => {
    const names = ["a", "b", "c", "d", "e"];
    const forward = [
      edge("a", "b"),
      edge("a", "c"),
      edge("b", "d"),
      edge("c", "d"),
      edge("d", "e"),
    ];
    const shuffled = [forward[3], forward[0], forward[4], forward[2], forward[1]];

    expect(layoutServiceGraph(names, shuffled)).toEqual(layoutServiceGraph(names, forward));
  });

  it("gives the same layout for a repeated edge as for a single one", () => {
    expect(layoutServiceGraph(["a", "b"], [edge("a", "b"), edge("a", "b")])).toEqual(
      layoutServiceGraph(["a", "b"], [edge("a", "b")]),
    );
  });

  it("separates nodes in the same column and lines up columns beside each other", () => {
    const { placements } = layoutServiceGraph(["a", "b", "c"], [edge("a", "b"), edge("a", "c")]);
    const [b, c] = [placements.get("b"), placements.get("c")];

    expect(b?.x).toBe(c?.x);
    expect(b?.y).not.toBe(c?.y);
    expect((placements.get("a")?.x ?? 0) + NODE_WIDTH).toBeLessThan(b?.x ?? 0);
  });

  it("reports a bounding box that contains every node it placed", () => {
    const { placements, width, height } = layoutServiceGraph(
      ["a", "b", "c", "d"],
      [edge("a", "b"), edge("a", "c"), edge("b", "d")],
    );

    for (const place of placements.values()) {
      expect(place.x).toBeGreaterThanOrEqual(0);
      expect(place.x).toBeLessThanOrEqual(width);
      expect(place.y).toBeGreaterThanOrEqual(0);
      expect(place.y).toBeLessThanOrEqual(height);
    }
  });

  it("stays fast and finite on a graph far larger than a customer has", () => {
    // 50 services in a chain with skip edges — the size the map is designed against.
    const names = Array.from({ length: 50 }, (_, index) => `service-${index}`);
    const edges = names.flatMap((name, index) =>
      index + 1 < names.length
        ? [
            edge(name, names[index + 1]),
            ...(index + 7 < names.length ? [edge(name, names[index + 7])] : []),
          ]
        : [],
    );

    const { placements } = layoutServiceGraph(names, edges);
    expect(placements.size).toBe(50);
  });
});
