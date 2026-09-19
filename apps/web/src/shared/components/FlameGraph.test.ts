// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { FlameNode } from "./FlameGraph";
import { FlameGraph } from "./FlameGraph";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

function node(
  name: string,
  total: number,
  children: FlameNode[] = [],
  extra: Partial<FlameNode> = {},
): FlameNode {
  return {
    name,
    file: "",
    unsymbolized: false,
    total,
    self_value: 0,
    merged: 0,
    children,
    ...extra,
  };
}

type Extra = Partial<Parameters<typeof FlameGraph>[0]>;

function render(root: FlameNode, mergedFrames = 0, extra: Extra = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const reactRoot = createRoot(container);
  mounted.push(reactRoot);
  const draw = (graph: FlameNode) =>
    createElement(FlameGraph, {
      root: graph,
      mergedFrames,
      formatValue: (value: number) => `${value} ms`,
      ...extra,
    });
  act(() => reactRoot.render(draw(root)));
  rerender = (graph) => act(() => reactRoot.render(draw(graph)));
  return container;
}

let rerender: (graph: FlameNode) => void = () => {};

function box(container: HTMLElement, label: string): HTMLElement {
  const found = [...container.querySelectorAll<HTMLElement>("figure > div button")].find(
    (element) => element.getAttribute("aria-label")?.startsWith(label),
  );
  if (!found) throw new Error(`no box labelled ${label}`);
  return found;
}

describe("the flame graph", () => {
  const graph = node("all", 100, [
    node("http.serve", 90, [node("sha256.block", 60), node("json.Marshal", 30)]),
    node("gc.mark", 10),
  ]);

  it("draws each box as wide as its share, and says so above the graph", () => {
    const container = render(graph);

    expect(box(container, "sha256.block").style.width).toContain("60%");
    expect(box(container, "gc.mark").getAttribute("aria-label")).toBe("gc.mark, 10 % of the total");
    expect(container.querySelector("figcaption")?.textContent).toContain("not time");
  });

  it("places a callee beside its sibling, under its caller", () => {
    const container = render(graph);
    const first = box(container, "sha256.block");
    const second = box(container, "json.Marshal");

    expect(first.style.left).toBe("0%");
    expect(second.style.left).toBe("60%");
    expect(first.style.top).toBe(second.style.top);
  });

  it("gives a frame's file and line in its tooltip, and the file alone when lines differ (FR12)", () => {
    const container = render(
      node("all", 10, [
        node("handle", 6, [], { file: "cart.go", line: 112 }),
        node("serve", 4, [], { file: "http.go", line: null }),
      ]),
    );

    expect(box(container, "handle").title).toContain("cart.go:112");
    expect(box(container, "serve").title).toContain("http.go");
    expect(box(container, "serve").title).not.toContain("http.go:");
  });

  it("marks an unsymbolized frame by its binary, never blank (AC11)", () => {
    const container = render(node("all", 10, [node("libc.so.6", 10, [], { unsymbolized: true })]));
    const frame = box(container, "libc.so.6 (unsymbolized)");

    expect(frame.dataset.unsymbolized).toBe("true");
    expect(frame.title).toContain("no symbols");
  });

  it("names a merged box by how many frames it stands for, and says frames were merged", () => {
    const container = render(node("all", 10, [node("frames merged", 10, [], { merged: 37 })]), 37);

    expect(box(container, "37 frames merged").dataset.merged).toBe("true");
    expect(container.querySelector("figcaption")?.textContent).toContain(
      "37 small frames are merged",
    );
  });

  it("highlights every frame a search matches and gives their combined share (AC8)", () => {
    const recursive = node("all", 100, [
      node("walk", 80, [node("walk", 50, [node("leaf", 50)]), node("other", 30)]),
      node("idle", 20),
    ]);
    const container = render(recursive, 0, { search: "WALK" });

    const matched = container.querySelectorAll("[data-matched]");
    expect(matched).toHaveLength(2);
    // A recursive function's time is spent once: 80 %, not 130 %.
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      "2 frames match “WALK” — 80 % of the total.",
    );
    expect(box(container, "idle").className).toContain("opacity-40");
  });

  it("says when a search matches nothing", () => {
    const container = render(graph, 0, { search: "nope" });
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      "No frame matches “nope”.",
    );
  });

  it("highlights the function picked in the table, and only that function", () => {
    const container = render(graph, 0, { selected: { name: "json.Marshal", file: "" } });
    expect(box(container, "json.Marshal").dataset.matched).toBe("true");
    expect(box(container, "sha256.block").dataset.matched).toBeUndefined();
  });

  it("zooms into a clicked frame, keeps its callers above, and zooms back out", () => {
    const container = render(graph);

    act(() => box(container, "http.serve").click());
    expect(box(container, "sha256.block").style.width).toContain("66.6667%");
    expect(box(container, "all").style.width).toContain("100%");
    const trail = container.querySelector('nav[aria-label="Zoom"]');
    expect(trail?.textContent).toContain("All");
    expect(trail?.textContent).toContain("http.serve");

    const all = [...(trail?.querySelectorAll("button") ?? [])].find((b) => b.textContent === "All");
    act(() => all?.click());
    expect(container.querySelector('nav[aria-label="Zoom"]')).toBeNull();
    expect(box(container, "sha256.block").style.width).toContain("60%");
  });

  it("keeps the zoom when the same graph is fetched again", () => {
    const container = render(graph);
    act(() => box(container, "http.serve").click());

    rerender(
      node("all", 200, [
        node("http.serve", 100, [node("sha256.block", 50), node("json.Marshal", 50)]),
        node("gc.mark", 100),
      ]),
    );

    expect(box(container, "sha256.block").style.width).toContain("50%");
    expect(container.querySelector('nav[aria-label="Zoom"]')?.textContent).toContain("http.serve");
  });

  it("colours a comparison by change in share and explains the colours (AC9)", () => {
    const compared = node(
      "all",
      100,
      [
        node("grew", 30, [], { baseline_share: 0.2, comparison_share: 0.4 }),
        node("shrank", 60, [], { baseline_share: 0.8, comparison_share: 0.5 }),
        node("new", 10, [], { baseline_share: 0, comparison_share: 0.1 }),
      ],
      { baseline_share: 1, comparison_share: 1 },
    );
    const container = render(compared, 0, { difference: true });

    expect(box(container, "grew,").dataset.change).toBe("more");
    expect(box(container, "shrank").dataset.change).toBe("less");
    expect(box(container, "new").dataset.change).toBe("new");
    expect(box(container, "grew,").getAttribute("aria-label")).toBe(
      "grew, a larger share: 20 % before, 40 % after",
    );
    // Not colour alone: on screen the change is a glyph as well (principles rule 6).
    expect(box(container, "grew,").title.startsWith("▲ grew")).toBe(true);
    expect(container.querySelector('[aria-label="What the colours mean"]')?.textContent).toContain(
      "a larger share",
    );
  });
});
