/**
 * What the screen decides before it draws anything: which state it is in, which row
 * describes this service, and which edges touch it.
 *
 * All of it is a function of the four responses and nothing else, so the states a
 * reader can land in are testable without a browser.
 */

import { formatCount, formatPercent } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import type { ServiceEdge } from "@/shared/service-peers";

import type { EndpointHealth, ErrorGroupSummary, ServiceHealth } from "./api";

/**
 * `silent` is not `missing`. The rollups only know which services reported in *this*
 * window, so no rows means "nothing arrived here", which is a different fact from
 * "no such service" — and the API returns an empty list rather than a 404 for exactly
 * that reason. The screen must say the same thing.
 */
export type DetailState = "pending" | "failed" | "silent" | "ready";

export interface DetailSources {
  /** Any of the three reads the screen cannot draw without. */
  isPending: boolean;
  isError: boolean;
  hasTrend: boolean;
  hasEndpoints: boolean;
  hasAggregate: boolean;
}

/**
 * Pending outranks failed: while one read is still in flight the screen has not
 * finished trying, and a failure shown mid-load is retracted a moment later.
 */
export function detailState(sources: DetailSources): DetailState {
  if (sources.isPending) return "pending";
  if (sources.isError) return "failed";
  if (!sources.hasTrend && !sources.hasEndpoints && !sources.hasAggregate) return "silent";
  return "ready";
}

/** One neighbour of this service, and the edge that made it one. */
export interface Dependency {
  peer: string;
  edge: ServiceEdge;
}

export interface Neighbours {
  /** Services that call this one. */
  callers: Dependency[];
  /** Services this one calls. */
  callees: Dependency[];
}

/**
 * The two slices of the dependency graph this service appears in.
 *
 * A service that calls itself is a real edge — a queue a service both publishes to and
 * consumes from produces one — and it belongs in both lists, because it is genuinely
 * both ends of that hop.
 */
export function neighboursOf(edges: readonly ServiceEdge[], serviceName: string): Neighbours {
  const callers: Dependency[] = [];
  const callees: Dependency[] = [];

  for (const edge of edges) {
    if (edge.callee === serviceName) callers.push({ peer: edge.caller, edge });
    if (edge.caller === serviceName) callees.push({ peer: edge.callee, edge });
  }

  return { callers, callees };
}

/** Stable identity for a dependency row: the same pair can appear as call and as queue. */
export function dependencyKey(direction: "in" | "out", dependency: Dependency): string {
  return `${direction}:${dependency.peer}:${dependency.edge.edge_kind}`;
}

/**
 * The sentence under the service name — the numbers first, in words, before the reader
 * meets a single column heading.
 */
export function describeService(row: ServiceHealth | undefined, windowLabel: string): string {
  // The aggregate is now asked for by name, so the only reason it is absent is that
  // this service reported nothing — never that it ranked too low to be on a page.
  if (!row) {
    return `No totals for the last ${windowLabel} — nothing by this name reported in it.`;
  }

  const traffic = `${formatRatePerMinute(row.requests_per_minute)} requests a minute in the last ${windowLabel}`;
  if (row.error_count === 0) return `${traffic}, none of them failing.`;
  return `${traffic}, ${formatPercent(row.error_rate)} of them failing.`;
}

/** The caption over the endpoint table. */
export function describeEndpoints(endpoints: readonly EndpointHealth[]): string {
  if (endpoints.length === 0) return "No endpoint reported in this window.";

  const failing = endpoints.filter((endpoint) => endpoint.error_count > 0).length;
  const counted = `${endpoints.length} ${endpoints.length === 1 ? "endpoint" : "endpoints"}, worst first`;
  return failing === 0 ? `${counted}. None failing.` : `${counted}. ${failing} failing.`;
}

/**
 * The caption over the error list.
 *
 * It counts occurrences as well as groups because those are two different facts: one
 * group thrown nine thousand times and nine thousand groups thrown once are both "a
 * lot of errors" and need entirely different work.
 */
export function describeServiceErrors(groups: readonly ErrorGroupSummary[]): string {
  if (groups.length === 0) return "No exception was recorded in this service in the window.";

  const occurrences = groups.reduce((total, group) => total + group.occurrences, 0);
  const distinct = `${groups.length} ${groups.length === 1 ? "error" : "distinct errors"}`;
  const thrown = `${formatCount(occurrences)} ${occurrences === 1 ? "time" : "times"}`;
  return `${distinct}, thrown ${thrown}, most frequent first. Counted for this service only.`;
}

/** Why a screen with a name on it is showing nothing, and what to do about it. */
export function describeSilence(serviceName: string, windowLabel: string): string {
  return `Nothing named “${serviceName}” has reported in the last ${windowLabel}. That is not the same as it not existing — this view is built from what arrived in the selected period, so a service that has stopped exporting, or was renamed, disappears from it rather than turning red. Widen the time range, or check that the service is still sending.`;
}
