import { useQuery } from "@tanstack/react-query";

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
