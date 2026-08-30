/**
 * The API's edge list, turned into the graph React Flow draws.
 *
 * Everything a node or an edge renders is decided here and carried in `data`, so the
 * components stay presentation only and the interesting decisions — what counts as
 * unhealthy, which number a node shows, how thick an arrow gets — are testable
 * without a DOM.
 */

import type { Edge, Node } from "@xyflow/react";
import { Position } from "@xyflow/react";

import { EDGE_QUEUE, isPlaceholderService, type ServiceEdge, type ServiceMapResponse } from "./api";
import { layoutServiceGraph, NODE_HEIGHT, NODE_WIDTH } from "./graph-layout";

export const SERVICE_NODE_TYPE = "service";
export const DEPENDENCY_EDGE_TYPE = "dependency";

/**
 * Health, in the design system's three states. Healthy carries no colour at all —
 * a wall of green is a tool shouting that nothing is happening.
 *
 * The thresholds are a product decision made here for the first time, and the
 * important half is the floor, not the ceiling: **any failed call is coloured.** The
 * demo data's worst real dependency fails 158 calls in 20,543 — 0.77%, under every
 * conventional 1% alerting threshold — and that edge is the entire reason someone
 * opens this screen. A map that renders it as healthy has failed at its one job.
 */
export type Health = "critical" | "warning" | "healthy";

/** One call in twenty. Above this the dependency is broken, not degraded. */
const CRITICAL_ERROR_RATE = 0.05;

export function healthOf(errorCount: number, errorRate: number): Health {
  if (errorCount <= 0) return "healthy";
  return errorRate >= CRITICAL_ERROR_RATE ? "critical" : "warning";
}

/** Six categorical tokens, cycled. Identity only — never health. */
export const SERIES_COUNT = 6;

/* ------------------------------------------------------------------ node and edge */

export interface ServiceNodeData extends Record<string, unknown> {
  name: string;
  /** Traffic through the service: what it serves if anything calls it, else what it calls. */
  callsPerMinute: number;
  flow: "serves" | "calls";
  /** Failed calls *into* this service — the callee is where the failure showed up. */
  errorCount: number;
  health: Health;
  /** False for `(uninstrumented)` and `(unknown)`: named by a caller, silent to us. */
  instrumented: boolean;
  /** 0-based index into the categorical palette. Placeholders get none. */
  seriesIndex?: number;
}

export interface DependencyEdgeData extends Record<string, unknown> {
  callsPerMinute: number;
  errorCount: number;
  errorRate: number;
  p95Ms: number;
  health: Health;
  queued: boolean;
  selfCall: boolean;
  /** 0 (quietest) to 1 (busiest edge on this map), on a log scale. */
  weight: number;
}

export type ServiceNode = Node<ServiceNodeData>;
export type DependencyEdge = Edge<DependencyEdgeData>;

export interface ServiceGraph {
  nodes: ServiceNode[];
  edges: DependencyEdge[];
  /** How many dependencies dropped at least one call. The headline number. */
  failingEdgeCount: number;
}

const EMPTY_GRAPH: ServiceGraph = { nodes: [], edges: [], failingEdgeCount: 0 };

export function buildServiceGraph(map: ServiceMapResponse | undefined): ServiceGraph {
  if (!map || map.edges.length === 0) return EMPTY_GRAPH;

  const layout = layoutServiceGraph(
    map.services,
    map.edges.map((edge) => ({ from: edge.caller, to: edge.callee })),
  );
  const traffic = trafficByService(map.edges);
  const busiest = Math.max(...map.edges.map((edge) => edge.calls_per_minute));
  const palette = seriesIndexes([...layout.placements.keys()]);

  const nodes = [...layout.placements.entries()].map(([name, placement]) =>
    toNode(name, placement, traffic.get(name), palette.get(name)),
  );

  return {
    nodes,
    edges: map.edges.map((edge) => toEdge(edge, busiest)),
    failingEdgeCount: map.edges.filter((edge) => edge.error_count > 0).length,
  };
}

/* ----------------------------------------------------------------------- internals */

interface ServiceTraffic {
  inboundPerMinute: number;
  outboundPerMinute: number;
  inboundCalls: number;
  inboundErrors: number;
}

function trafficByService(edges: readonly ServiceEdge[]): Map<string, ServiceTraffic> {
  const traffic = new Map<string, ServiceTraffic>();
  const of = (name: string) => {
    const existing = traffic.get(name);
    if (existing) return existing;
    const fresh = { inboundPerMinute: 0, outboundPerMinute: 0, inboundCalls: 0, inboundErrors: 0 };
    traffic.set(name, fresh);
    return fresh;
  };

  for (const edge of edges) {
    of(edge.caller).outboundPerMinute += edge.calls_per_minute;

    const callee = of(edge.callee);
    callee.inboundPerMinute += edge.calls_per_minute;
    callee.inboundCalls += edge.call_count;
    callee.inboundErrors += edge.error_count;
  }
  return traffic;
}

/**
 * Which categorical colour each service gets. Assigned over the sorted names so a
 * service keeps its colour while the window advances, and skipped for the
 * placeholders — `(uninstrumented)` is a gap in the picture, not a service.
 */
function seriesIndexes(names: readonly string[]): Map<string, number> {
  const indexes = new Map<string, number>();
  let next = 0;
  for (const name of [...names].sort()) {
    if (isPlaceholderService(name)) continue;
    indexes.set(name, next % SERIES_COUNT);
    next += 1;
  }
  return indexes;
}

function toNode(
  name: string,
  placement: { x: number; y: number },
  traffic: ServiceTraffic | undefined,
  seriesIndex: number | undefined,
): ServiceNode {
  const inbound = traffic?.inboundPerMinute ?? 0;
  const serves = inbound > 0;
  const errorCount = traffic?.inboundErrors ?? 0;
  const errorRate = divideOrZero(errorCount, traffic?.inboundCalls ?? 0);

  return {
    id: name,
    type: SERVICE_NODE_TYPE,
    position: { x: placement.x, y: placement.y },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    // Given before measurement so the first paint routes edges correctly rather than
    // drawing every arrow from the top-left corner and then correcting it.
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: false,
    connectable: false,
    data: {
      name,
      callsPerMinute: serves ? inbound : (traffic?.outboundPerMinute ?? 0),
      flow: serves ? "serves" : "calls",
      errorCount,
      health: healthOf(errorCount, errorRate),
      instrumented: !isPlaceholderService(name),
      seriesIndex,
    },
  };
}

function toEdge(edge: ServiceEdge, busiestPerMinute: number): DependencyEdge {
  return {
    // Kind is in the id because a service can both call and enqueue to the same peer,
    // and two edges sharing an id silently become one.
    id: `${edge.caller}→${edge.callee}:${edge.edge_kind}`,
    source: edge.caller,
    target: edge.callee,
    type: DEPENDENCY_EDGE_TYPE,
    selectable: false,
    data: {
      callsPerMinute: edge.calls_per_minute,
      errorCount: edge.error_count,
      errorRate: edge.error_rate,
      p95Ms: edge.p95_ms,
      health: healthOf(edge.error_count, edge.error_rate),
      queued: edge.edge_kind === EDGE_QUEUE,
      selfCall: edge.caller === edge.callee,
      weight: edgeWeight(edge.calls_per_minute, busiestPerMinute),
    },
  };
}

/**
 * How prominent an edge is, 0 to 1.
 *
 * Logarithmic, because the busiest edge in the demo data carries 287,896 calls and the
 * next carries 20,543. Linearly, everything but the loudest edge would be a hairline —
 * which is the opposite of what the map is for.
 */
export function edgeWeight(callsPerMinute: number, busiestPerMinute: number): number {
  if (!(busiestPerMinute > 0) || !(callsPerMinute > 0)) return 0;
  const scaled = Math.log10(1 + callsPerMinute) / Math.log10(1 + busiestPerMinute);
  return Math.min(1, Math.max(0, scaled));
}

function divideOrZero(numerator: number, denominator: number): number {
  return denominator > 0 ? numerator / denominator : 0;
}
