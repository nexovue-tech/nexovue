import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/** The error-grouping half of the M6 contract. Field names are the API's, unchanged. */

/** How the group's identity was decided, as the API spells it. */
export const GROUPED_BY_SDK = "sdk_fingerprint";
export const GROUPED_BY_TEMPLATE = "type_and_message_template";

/** Which signal the instance arrived on, as the API spells it. */
export const SOURCE_LOG = "log";

export interface ErrorGroupSummary {
  group_id: string;
  exception_type: string;
  /** Ids, numbers, hosts and addresses collapsed to tokens. This is what the group is. */
  message_template: string;
  /** What one instance actually said, tokens and all. */
  latest_message: string;
  grouped_by: string;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  services: string[];
  /** Empty when the newest instance carried no trace context, never null. */
  sample_trace_id: string;
  sample_span_id: string;
  sample_source: string;
}

export interface ErrorOccurrence {
  source: string;
  service_name: string;
  occurred_at: string;
  /** Empty string when the record carried no trace context, never null. */
  trace_id: string;
  span_id: string;
  exception_message: string;
  /** Empty when the SDK sent none — which is most SDKs on a log-shaped exception. */
  stacktrace: string;
  /** The build this came from, which is what a source map is matched on. Empty
   * when the sender stamped no version, and a stack with no release cannot be
   * symbolicated at all. */
  release: string;
}

/** One bucket of a group's history. */
export interface GroupTrendPoint {
  bucket: string;
  /** Counted as the group's own `occurrences` is, so the line and the number agree. */
  occurrences: number;
}

interface GroupTrend {
  group_id: string;
  points: GroupTrendPoint[];
}

interface GroupTrendResponse {
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  groups: GroupTrend[];
}

interface ErrorGroupListResponse {
  groups: ErrorGroupSummary[];
}

export interface ErrorGroupDetail {
  group: ErrorGroupSummary;
  /** Newest first, so the first entry is the one a reader opens. */
  recent: ErrorOccurrence[];
}

export const errorGroupListQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["error-groups", projectId, timeWindow.from, timeWindow.to] as const;

export const errorGroupQueryKey = (projectId: string, groupId: string, timeWindow: TimeWindow) =>
  ["error-group", projectId, groupId, timeWindow.from, timeWindow.to] as const;

/**
 * Every exception in the window, most frequent first.
 *
 * The API decides that order — occurrences, then recency — and the ranking is the
 * product, so nothing here re-sorts it.
 */
export function useErrorGroups(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: errorGroupListQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ErrorGroupListResponse>("error-groups", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

/**
 * One group and the instances behind it.
 *
 * A group id is derived from the rows inside a window, so the same window has to be
 * asked for again — the id from a six-hour read does not exist in a fifteen-minute one.
 */
export function useErrorGroup(
  projectId: string | undefined,
  groupId: string | undefined,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: errorGroupQueryKey(projectId ?? "", groupId ?? "", timeWindow),
    enabled: projectId !== undefined && groupId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ErrorGroupDetail>(`error-groups/${encodeURIComponent(groupId ?? "")}`, {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

export const errorGroupTrendQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["error-group-trend", projectId, timeWindow.from, timeWindow.to] as const;

/**
 * Every group's history, in one request rather than one per row.
 *
 * Separate from the list on purpose: the rows must appear as soon as the list lands and
 * fill in their lines when this arrives. A group missing from the map has no trend to
 * draw and renders the rule rather than disappearing.
 */
export function useErrorGroupTrend(
  projectId: string | undefined,
  timeWindow: TimeWindow,
): ReadonlyMap<string, readonly GroupTrendPoint[]> {
  const { data } = useQuery({
    queryKey: errorGroupTrendQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<GroupTrendResponse>("error-groups/trend", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });

  return useMemo(
    () => new Map((data?.groups ?? []).map((trend) => [trend.group_id, trend.points])),
    [data],
  );
}
