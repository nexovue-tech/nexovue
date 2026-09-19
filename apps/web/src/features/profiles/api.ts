import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { FlameNode } from "@/shared/components/FlameGraph";
import type { LiveWindow, TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

export interface FunctionShare {
  name: string;
  file: string;
  unsymbolized: boolean;
  self_value: number;
  total: number;
}

/**
 * A service's profile over the window. `samples` of zero means the window held
 * nothing, which the screen says rather than drawing an empty graph.
 */
export interface FlameGraphResponse {
  sample_type: string;
  unit: string;
  total: number;
  samples: number;
  merged_frames: number;
  root: FlameNode;
  functions: FunctionShare[];
}

/** One service's profile type, narrowed as the reader asked. */
export interface GraphQuery {
  service: string;
  sampleType: string;
  host?: string;
  version?: string;
  environment?: string;
  traceId?: string;
  spanId?: string;
}

export interface FilterValue {
  value: string;
  samples: number;
  last_seen: string;
}

export interface ProfileFilters {
  hosts: FilterValue[];
  versions: FilterValue[];
  environments: FilterValue[];
}

export interface TimelinePoint {
  bucket: string;
  /** Null where no sample arrived: nothing was measured, which is not zero. */
  value: number | null;
  samples: number;
}

export interface ProfileTimeline {
  unit: string;
  bucket_seconds: number;
  points: TimelinePoint[];
}

export interface DiffNode {
  name: string;
  file: string;
  unsymbolized: boolean;
  merged: number;
  weight: number;
  baseline_share: number;
  comparison_share: number;
  children: DiffNode[];
}

export interface FunctionChange {
  name: string;
  file: string;
  unsymbolized: boolean;
  baseline_self: number;
  comparison_self: number;
  baseline_total: number;
  comparison_total: number;
  change: number;
}

export interface ComparisonResponse {
  sample_type: string;
  unit: string;
  baseline_samples: number;
  comparison_samples: number;
  merged_frames: number;
  root: DiffNode;
  functions: FunctionChange[];
}

/** One side of a comparison: a version, a range, or both. */
export interface ComparisonSide {
  version?: string;
  from: string;
  to: string;
}

type Window = TimeWindow | LiveWindow;

function narrowing(graph: GraphQuery) {
  return {
    service_name: graph.service,
    type: graph.sampleType,
    host: graph.host,
    version: graph.version,
    environment: graph.environment,
  };
}

export function useFlameGraph(
  projectId: string | undefined,
  timeWindow: Window,
  graph?: GraphQuery,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["profiles", "graph", projectId ?? "", windowKeyOf(timeWindow), graph] as const,
    enabled: projectId !== undefined && graph !== undefined,
    // A filter change keeps the old graph on screen until the new one arrives,
    // rather than collapsing the page to a skeleton on every keystroke.
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      apiClient.get<FlameGraphResponse>("profiles/graph", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          ...(graph ? narrowing(graph) : {}),
          trace_id: graph?.traceId,
          span_id: graph?.spanId,
        },
        signal,
      }),
  });
}

export function useProfileFilters(
  projectId: string | undefined,
  timeWindow: Window,
  selection?: { service: string; sampleType: string },
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["profiles", "filters", projectId ?? "", windowKeyOf(timeWindow), selection] as const,
    enabled: projectId !== undefined && selection !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ProfileFilters>("profiles/filters", {
        searchParams: {
          project_id: projectId,
          service_name: selection?.service,
          type: selection?.sampleType,
          from: timeWindow.from,
          to: timeWindow.to,
        },
        signal,
      }),
  });
}

export function useProfileTimeline(
  projectId: string | undefined,
  timeWindow: Window,
  graph?: GraphQuery,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["profiles", "timeline", projectId ?? "", windowKeyOf(timeWindow), graph] as const,
    enabled: projectId !== undefined && graph !== undefined,
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      apiClient.get<ProfileTimeline>("profiles/timeline", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          ...(graph ? narrowing(graph) : {}),
        },
        signal,
      }),
  });
}

/** The newest profile held — where an empty window points the reader. */
export function useLatestProfile(
  projectId: string | undefined,
  service: string | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["profiles", "latest", projectId ?? "", service ?? ""] as const,
    enabled: projectId !== undefined && enabled,
    queryFn: ({ signal }) =>
      apiClient.get<{ last_received: string | null }>("profiles/latest", {
        searchParams: { project_id: projectId, service_name: service },
        signal,
      }),
  });
}

export function useProfileComparison(
  projectId: string | undefined,
  selection:
    | { service: string; sampleType: string; host?: string; environment?: string }
    | undefined,
  baseline: ComparisonSide | undefined,
  comparison: ComparisonSide | undefined,
) {
  const ready = selection !== undefined && baseline !== undefined && comparison !== undefined;
  return useQuery({
    queryKey: ["profiles", "compare", projectId ?? "", selection, baseline, comparison] as const,
    enabled: projectId !== undefined && ready,
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      apiClient.get<ComparisonResponse>("profiles/compare", {
        searchParams: {
          project_id: projectId,
          service_name: selection?.service,
          type: selection?.sampleType,
          host: selection?.host,
          environment: selection?.environment,
          base_from: baseline?.from,
          base_to: baseline?.to,
          base_version: baseline?.version,
          from: comparison?.from,
          to: comparison?.to,
          version: comparison?.version,
        },
        signal,
      }),
  });
}
