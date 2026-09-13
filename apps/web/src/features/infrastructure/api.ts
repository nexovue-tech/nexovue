import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/**
 * Hosts and containers, read from the metrics a customer's collector sends.
 *
 * **Every measurement is nullable, and null means "not reported" — never zero.** A
 * collector runs only the scrapers it was configured with, so a host sending CPU and
 * nothing else is ordinary, and a zero in the memory column would read as "idle" when
 * the truth is "not measured". `signals` names what the node actually sent, which is how
 * the screen tells a missing scraper from a missing reading.
 *
 * Kubernetes pod and node views are Phase 2. The metrics are ingested and queryable;
 * nothing here reads them.
 */

/**
 * A `reporter` is not a machine. It is the process whose resource named neither a host
 * nor a container, named so its readings stay attached to something instead of merging
 * with every other unnamed resource in the project.
 */
export type NodeKind = "host" | "container" | "reporter";

export interface NodeSummary {
  kind: NodeKind;
  /** Empty when the resource carried no `host.name`. */
  host: string;
  container: string;

  /** `service.name` and `service.instance.id`. Filled only when `kind` is "reporter". */
  reporter: string;
  reporter_instance: string;

  /** Fraction of the node's CPUs in use, 0 to 1. Hosts only. */
  cpu_utilization: number | null;
  /** Cores consumed. Containers only — a different quantity from the fraction above. */
  cpu_cores_used: number | null;

  memory_used_bytes: number | null;
  memory_limit_bytes: number | null;
  memory_utilization: number | null;

  filesystem_used_bytes: number | null;
  filesystem_limit_bytes: number | null;
  filesystem_utilization: number | null;

  /** Rates: the underlying metrics are counters that only rise, so a level says nothing. */
  disk_bytes_per_second: number | null;
  network_bytes_per_second: number | null;

  first_seen: string;
  last_seen: string;

  /**
   * How often this node reported, measured from its own points. It is what gives
   * `last_seen` a meaning: a minute of silence is nothing from a host scraped every ten
   * minutes and three missed reports from one scraped every ten seconds. Null when the
   * window held too few points to show a cadence.
   */
  reporting_interval_seconds: number | null;

  /**
   * True when this node went quiet before the window ended. It never means "down":
   * telemetry cannot tell a dead machine from one that stopped sending.
   */
  stale: boolean;

  /** The metric names this node sent inside the window, in name order. */
  signals: string[];
}

export interface NodeListResponse {
  nodes: NodeSummary[];
  /** True when the read hit its series ceiling: a prefix of the fleet, not the fleet. */
  truncated: boolean;
}

export interface SignalSummary {
  metric_name: string;
  unit: string;
  points: number;
  series: number;
  nodes: number;
  last_seen: string;
}

interface SignalListResponse {
  signals: SignalSummary[];
}

export const nodesQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["infrastructure", "hosts", projectId, timeWindow.from, timeWindow.to] as const;

export const signalsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["infrastructure", "signals", projectId, timeWindow.from, timeWindow.to] as const;

export function useNodes(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: nodesQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<NodeListResponse>("infrastructure/hosts", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

/**
 * Which infrastructure metrics arrived at all.
 *
 * Read alongside the node list rather than instead of it: the node list says what the
 * fleet looks like, this says which scrapers are switched on. A fleet with CPU and no
 * memory is a collector configuration, not a set of hosts that lost their memory.
 */
export function useSignals(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: signalsQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<SignalListResponse>("infrastructure/signals", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
    select: (response) => response.signals,
  });
}

/** One bucket of one metric on one node. */
export interface NodeTrendPoint {
  bucket: string;
  value: number;
}

interface NodeTrend {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
  metric_name: string;
  points: NodeTrendPoint[];
}

interface NodeTrendResponse {
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  trends: NodeTrend[];
  truncated: boolean;
}

/** The key both sides build from the same four fields, rather than a shared id. */
export const nodeSeriesKey = (node: {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
}) => `${node.host}/${node.container}/${node.reporter}/${node.reporter_instance}`;

export const nodeTrendQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["node-trend", projectId, timeWindow.from, timeWindow.to] as const;

/**
 * Every node's metrics, in one request rather than one per row.
 *
 * Keyed `node/metric`, because a node has several: CPU, memory and filesystem are three
 * series on one row, and the row picks the one its column draws.
 */
export function useNodeTrend(
  projectId: string | undefined,
  timeWindow: TimeWindow,
): ReadonlyMap<string, readonly NodeTrendPoint[]> {
  const { data } = useQuery({
    queryKey: nodeTrendQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<NodeTrendResponse>("infrastructure/trend", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });

  return useMemo(
    () =>
      new Map(
        (data?.trends ?? []).map((trend) => [
          `${nodeSeriesKey(trend)}|${trend.metric_name}`,
          trend.points,
        ]),
      ),
    [data],
  );
}
