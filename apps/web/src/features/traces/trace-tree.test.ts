import { describe, expect, it } from "vitest";

import type { Span } from "./api";
import { buildSpanTree, collapsibleSpanIds, flattenTree } from "./trace-tree";

/** Only the fields the tree and the timeline actually read. */
function span(id: string, parent: string, startMs: number, durationMs = 1): Span {
  return {
    span_id: id,
    parent_span_id: parent,
    service_name: "svc",
    span_name: id,
    span_kind: "INTERNAL",
    start_time: new Date(startMs).toISOString(),
    duration_ms: durationMs,
    status_code: "UNSET",
    status_message: "",
    attributes: {},
    is_model_call: false,
  };
}

const ids = (spans: readonly Span[]) =>
  flattenTree(buildSpanTree(spans)).map((r) => r.span.span_id);

describe("buildSpanTree", () => {
  it("nests children under the span that called them, depth-first", () => {
    const rows = flattenTree(
      buildSpanTree([span("root", "", 0), span("child", "root", 1), span("grand", "child", 2)]),
    );
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["root", 0],
      ["child", 1],
      ["grand", 2],
    ]);
  });

  it("orders siblings by start time, breaking ties on id so the order is stable", () => {
    const rows = flattenTree(
      buildSpanTree([
        span("root", "", 0),
        span("b", "root", 5),
        span("a", "root", 5),
        span("early", "root", 1),
      ]),
    );
    expect(rows.map((r) => r.span.span_id)).toEqual(["root", "early", "a", "b"]);
  });

  it("promotes a span whose parent never arrived, and marks it orphaned", () => {
    const rows = flattenTree(buildSpanTree([span("root", "", 0), span("lost", "missing", 1)]));
    expect(rows.map((r) => r.span.span_id).sort()).toEqual(["lost", "root"]);
    expect(rows.find((r) => r.span.span_id === "lost")?.orphaned).toBe(true);
    expect(rows.find((r) => r.span.span_id === "root")?.orphaned).toBe(false);
  });

  it("keeps only the first of a duplicated span id — a re-exported batch must not double rows", () => {
    expect(ids([span("root", "", 0), span("dup", "root", 1), span("dup", "root", 9)])).toEqual([
      "root",
      "dup",
    ]);
  });

  it("surfaces a cycle instead of dropping it or hanging", () => {
    // a→b→a is reachable from no root at all; both must still appear, exactly once.
    const rows = flattenTree(buildSpanTree([span("a", "b", 0), span("b", "a", 1)]));
    expect(rows.map((r) => r.span.span_id).sort()).toEqual(["a", "b"]);
    expect(rows.every((r) => r.orphaned)).toBe(true);
  });

  it("treats a span parented to itself as a root rather than a cycle", () => {
    expect(ids([span("self", "self", 0)])).toEqual(["self"]);
  });

  it("flags a child that starts before its parent — two clocks disagreeing, not a bug to hide", () => {
    const rows = flattenTree(buildSpanTree([span("root", "", 100), span("skewed", "root", 40)]));
    expect(rows.find((r) => r.span.span_id === "skewed")?.precedesParent).toBe(true);
    expect(rows.find((r) => r.span.span_id === "root")?.precedesParent).toBe(false);
  });

  it("returns nothing for an empty trace", () => {
    expect(ids([])).toEqual([]);
  });
});

describe("flattenTree", () => {
  it("skips a collapsed subtree whole, keeping the collapsed span itself", () => {
    const tree = buildSpanTree([
      span("root", "", 0),
      span("child", "root", 1),
      span("grand", "child", 2),
      span("other", "root", 3),
    ]);
    expect(flattenTree(tree, new Set(["child"])).map((r) => r.span.span_id)).toEqual([
      "root",
      "child",
      "other",
    ]);
  });

  it("gives every row a distinct key even when span ids repeat across the tree", () => {
    const rows = flattenTree(buildSpanTree([span("root", "", 0), span("child", "root", 1)]));
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });

  it("counts children so a row knows whether it can be collapsed", () => {
    const rows = flattenTree(buildSpanTree([span("root", "", 0), span("child", "root", 1)]));
    expect(rows.find((r) => r.span.span_id === "root")?.childCount).toBe(1);
    expect(collapsibleSpanIds(rows)).toEqual(new Set(["root"]));
  });
});
