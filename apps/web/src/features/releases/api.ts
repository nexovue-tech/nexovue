import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

/**
 * `/releases` (F28): the record of each version a project's services ran, and how a
 * period compares with the version it replaced. Shapes are the API's
 * `releases/schemas.py`. Names are telemetry text: shown as text, never as markup.
 */

export type Basis = "reported" | "seen" | "both";
export type Activity = "not_seen" | "serving" | "replaced" | "idle";
export type Reading = "higher" | "lower" | "about_same" | "not_clearly_different";
export type Method = "side_by_side" | "version_before_after" | "time_before_after";
export type BlockKind = "rollout" | "after_rollout" | "around_deploy";
export type Reason =
  | "no_previous_version"
  | "already_running"
  | "too_little_traffic"
  | "too_recent"
  | "numbers_expired"
  | "no_version_no_deploy"
  | "new_version_not_seen"
  | "no_request_traffic";

export interface ReleaseRow {
  id: string;
  period_id: string;
  service_name: string;
  version: string;
  started_at: string;
  ended_at: string | null;
  start_basis: "seen" | "reported";
  basis: Basis;
  activity: Activity;
  already_running: boolean;
  is_return: boolean;
  last_seen_at: string | null;
  last_state: string | null;
}

export interface ReleaseList {
  releases: ReleaseRow[];
  total: number;
  services: number;
  next_cursor: string | null;
  has_any: boolean;
  latest: { id: string; service_name: string; version: string; started_at: string } | null;
  window_from: string;
  window_to: string;
}

export interface Period {
  id: string;
  started_at: string;
  ended_at: string | null;
  start_basis: "seen" | "reported";
  previous_version: string | null;
  already_running: boolean;
  is_return: boolean;
}

export interface Release {
  id: string;
  project_id: string;
  service_name: string;
  version: string;
  basis: Basis;
  activity: Activity;
  replaced_by: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  first_reported_at: string | null;
  last_reported_at: string | null;
  report_count: number;
  last_state: string | null;
  /** Newest first. */
  periods: Period[];
}

export interface Side {
  version: string;
  requests: number;
  errors: number;
  p95_ms: number | null;
}

export interface CellReading {
  reading: Reading | null;
  note: "too_little_traffic" | "warm_up" | null;
}

export interface Block {
  kind: BlockKind;
  method: Method;
  from: string;
  to: string;
  minutes_compared: number;
  this_version: Side;
  previous_version: Side;
  failure_share: CellReading;
  p95: CellReading;
}

export interface Comparison {
  period_id: string;
  version: string;
  previous_version: string | null;
  reason: Reason | null;
  lead: BlockKind | null;
  as_of: string | null;
  compared_to: string;
  window_hours: number;
  blocks: Block[];
  traffic: { this_version: Side; previous_version: Side } | null;
  other_versions: string[];
  numbers_kept_days: number;
}

export interface ReleaseFilters {
  service?: string;
  version?: string;
}

/** FR22's windows, in hours; the first is the default. */
export const WINDOW_HOURS = [1, 6, 24] as const;
export type WindowHours = (typeof WINDOW_HOURS)[number];
/** While a comparison is too recent or "as of", it reads again on its own (design). */
export const COMPARISON_REFRESH_MS = 60_000;

export function useReleases(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  filters: ReleaseFilters,
  cursor: string | null,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: [
      "releases",
      projectId,
      windowKeyOf(timeWindow),
      filters.service ?? "",
      filters.version ?? "",
      cursor ?? "",
    ],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ReleaseList>("releases", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          service: filters.service || undefined,
          version: filters.version || undefined,
          cursor: cursor ?? undefined,
        },
        signal,
      }),
  });
}

export function useRelease(releaseId: string) {
  return useQuery({
    queryKey: ["release", releaseId],
    queryFn: ({ signal }) =>
      apiClient.get<Release>(`releases/${encodeURIComponent(releaseId)}`, { signal }),
  });
}

export function useComparison(
  releaseId: string,
  periodId: string | undefined,
  windowHours: WindowHours,
) {
  return useQuery({
    queryKey: ["release-comparison", releaseId, periodId ?? "", windowHours],
    queryFn: ({ signal }) =>
      apiClient.get<Comparison>(`releases/${encodeURIComponent(releaseId)}/comparison`, {
        searchParams: { period: periodId, window: windowHours },
        signal,
      }),
    refetchInterval: (query) =>
      query.state.data?.reason === "too_recent" || query.state.data?.as_of
        ? COMPARISON_REFRESH_MS
        : false,
  });
}
