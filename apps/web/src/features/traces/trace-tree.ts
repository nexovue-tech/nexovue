import type { Span } from "./api";
import { spanStartMs } from "./trace-timeline";

/**
 * The API returns spans flat and in start-time order; the shape of a request is the
 * browser's to reconstruct.
 *
 * Three things go wrong in real traces and all three are handled here rather than
 * anywhere downstream: a parent that never arrived (sampled away, still in flight, or
 * dropped), duplicate ids from a retried export, and — rarely, but it must not hang the
 * tab — a parent chain that loops.
 */

export interface WaterfallRow {
  /** Unique per row: span ids are not guaranteed unique across a re-exported batch. */
  key: string;
  span: Span;
  depth: number;
  childCount: number;
  /** Its parent id points at a span this trace does not contain. */
  orphaned: boolean;
  /** It starts before the parent it belongs to — two clocks disagreeing. */
  precedesParent: boolean;
}

interface SpanNode {
  span: Span;
  children: SpanNode[];
  orphaned: boolean;
}

/** Start time first; the id breaks ties so spans that begin together keep an order. */
function byStartThenId(left: SpanNode, right: SpanNode): number {
  const difference = spanStartMs(left.span) - spanStartMs(right.span);
  return difference !== 0 ? difference : left.span.span_id.localeCompare(right.span.span_id);
}

export function buildSpanTree(spans: readonly Span[]): SpanNode[] {
  const nodes = new Map<string, SpanNode>();
  for (const span of spans) {
    if (!nodes.has(span.span_id)) nodes.set(span.span_id, { span, children: [], orphaned: false });
  }

  const roots: SpanNode[] = [];
  for (const node of nodes.values()) {
    const parentId = node.span.parent_span_id;
    // The contract says a root's parent is "", never null — but a missing value must
    // read as "no parent" rather than as a parent that failed to arrive.
    const isRoot = !parentId || parentId === node.span.span_id;
    const parent = isRoot ? undefined : nodes.get(parentId);

    if (isRoot) {
      roots.push(node);
    } else if (parent) {
      parent.children.push(node);
    } else {
      node.orphaned = true; // The parent is missing, so this subtree stands on its own.
      roots.push(node);
    }
  }

  for (const node of nodes.values()) node.children.sort(byStartThenId);
  roots.sort(byStartThenId);

  return attachCycleSurvivors(roots, nodes);
}

/**
 * Every span must appear exactly once. A cycle has no root, so its members are reachable
 * from nothing — they are lifted to the top level rather than silently dropped.
 */
function attachCycleSurvivors(roots: SpanNode[], nodes: Map<string, SpanNode>): SpanNode[] {
  const reachable = new Set<SpanNode>();
  const pending = [...roots];
  while (pending.length > 0) {
    const node = pending.pop() as SpanNode;
    if (reachable.has(node)) continue;
    reachable.add(node);
    pending.push(...node.children);
  }

  const stranded = [...nodes.values()].filter((node) => !reachable.has(node));
  if (stranded.length === 0) return roots;

  for (const node of stranded) node.orphaned = true;
  return [...roots, ...stranded].sort(byStartThenId);
}

/**
 * Depth-first, which is what makes indentation readable: a span is always directly
 * under the one it was called from. Collapsed subtrees are skipped whole.
 */
export function flattenTree(
  roots: readonly SpanNode[],
  collapsed: ReadonlySet<string> = new Set(),
): WaterfallRow[] {
  const rows: WaterfallRow[] = [];
  const visited = new Set<SpanNode>();

  function walk(node: SpanNode, depth: number, parent: SpanNode | undefined) {
    if (visited.has(node)) return; // A loop in the parent chain must not become a loop here.
    visited.add(node);

    rows.push({
      key: `${node.span.span_id}:${rows.length}`,
      span: node.span,
      depth,
      childCount: node.children.length,
      orphaned: node.orphaned,
      precedesParent: parent !== undefined && spanStartMs(node.span) < spanStartMs(parent.span),
    });

    if (collapsed.has(node.span.span_id)) return;
    for (const child of node.children) walk(child, depth + 1, node);
  }

  for (const root of roots) walk(root, 0, undefined);
  return rows;
}

/** Every span id that has children, so "collapse all" does not have to walk the tree twice. */
export function collapsibleSpanIds(rows: readonly WaterfallRow[]): Set<string> {
  return new Set(rows.filter((row) => row.childCount > 0).map((row) => row.span.span_id));
}
