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

/** FR55: a measure the stored reading has higher by version, with its counts. */
export interface StoredFlag {
  measure: "failure_share" | "p95";
  this_requests: number;
  this_errors: number;
  this_p95_ms: number | null;
  previous_requests: number;
  previous_errors: number;
  previous_p95_ms: number | null;
}

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
  /** FR55, from what a job stored 30 minutes in; empty for any other reading (AC81). */
  flags: StoredFlag[];
  /** FR17, from the same stored reading. */
  versions_combined: boolean;
}

/** FR46: a service whose CI and traffic name versions differently, newest of each. */
export interface NamingDiffers {
  service_name: string;
  reported_version: string;
  seen_version: string;
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
  naming_differs: NamingDiffers[];
}

/** A finding on the service that started while a period served (FR34). */
export interface LaterFinding {
  id: string;
  title: string;
  severity: string;
  state: string;
  started_at: string;
}

/** One reported deploy, facts only: the sender's time beside our arrival time. */
export interface DeployFact {
  id: string;
  deployed_at: string;
  received_at: string;
  state: "started" | "finished" | "failed" | "rolled_back";
  commit_sha: string | null;
}

export interface Period {
  id: string;
  started_at: string;
  ended_at: string | null;
  start_basis: "seen" | "reported";
  previous_version: string | null;
  already_running: boolean;
  is_return: boolean;
  /** Oldest first. */
  later_findings: LaterFinding[];
  later_findings_truncated: boolean;
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
  /** Oldest claim first. */
  deploys: DeployFact[];
}

export interface Side {
  version: string;
  requests: number;
  errors: number;
  p95_ms: number | null;
  /** Requests over the minutes that had any, not the whole stretch (item 36b). */
  requests_per_minute: number | null;
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
  /** The previous side's stretch: the same minutes side by side, before otherwise. */
  previous_from: string;
  previous_to: string;
  minutes_compared: number;
  this_version: Side;
  previous_version: Side;
  failure_share: CellReading;
  p95: CellReading;
  /** FR25: the whole service a week earlier; before-and-after methods only. */
  last_week: Side | null;
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
  incomplete: boolean;
  minutes_differing: number;
  versions_combined: boolean;
  mixed_environments: boolean;
  /** Opened from a finding that started after the period (FR22). */
  finding: { id: string; started_at: string; reaches: boolean } | null;
}

export type ErrorKind = "new_here" | "gone_here" | "in_both" | "this_version";

export interface VersionErrorGroup {
  group_id: string;
  exception_type: string;
  message_template: string;
  kind: ErrorKind;
  this_occurrences: number;
  previous_occurrences: number;
  from_browser: boolean;
}

export interface VersionErrors {
  period_id: string;
  version: string;
  previous_version: string | null;
  from: string;
  to: string;
  window_hours: number;
  groups: VersionErrorGroup[];
  truncated: boolean;
  timed_out: boolean;
  aged_out_on: string | null;
  may_be_incomplete: boolean;
  browser_door_read: boolean;
}

export type RolloutSeriesKind = "version" | "no_version" | "combined" | "other_versions";

export interface Rollout {
  period_id: string;
  series: Array<{ kind: RolloutSeriesKind; version: string | null }>;
  minutes: Array<{ minute: string; requests: number[] }>;
  as_of: string | null;
  compared_to: string;
}

export interface SenderDeploy {
  deploy_id: string;
  deployed_at: string;
  url: string | null;
  source: string;
  metadata: Record<string, string> | null;
  metadata_withheld: boolean;
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

/**
 * The comparison. `windowHours` undefined lets the API choose: an hour, or, opened
 * from a finding, the smallest window reaching it (FR22).
 */
export function useComparison(
  releaseId: string,
  periodId: string | undefined,
  windowHours: WindowHours | undefined,
  findingId: string | undefined,
) {
  return useQuery({
    queryKey: ["release-comparison", releaseId, periodId ?? "", windowHours ?? "", findingId ?? ""],
    queryFn: ({ signal }) =>
      apiClient.get<Comparison>(`releases/${encodeURIComponent(releaseId)}/comparison`, {
        searchParams: { period: periodId, window: windowHours, finding: findingId },
        signal,
      }),
    refetchInterval: (query) =>
      query.state.data?.reason === "too_recent" || query.state.data?.as_of
        ? COMPARISON_REFRESH_MS
        : false,
  });
}

export function useVersionErrors(releaseId: string, periodId: string, windowHours: number) {
  return useQuery({
    queryKey: ["release-errors", releaseId, periodId, windowHours],
    queryFn: ({ signal }) =>
      apiClient.get<VersionErrors>(`releases/${encodeURIComponent(releaseId)}/errors`, {
        searchParams: { period: periodId, window: windowHours },
        signal,
      }),
  });
}

export function useRollout(releaseId: string, periodId: string, windowHours: number) {
  return useQuery({
    queryKey: ["release-rollout", releaseId, periodId, windowHours],
    queryFn: ({ signal }) =>
      apiClient.get<Rollout>(`releases/${encodeURIComponent(releaseId)}/rollout`, {
        searchParams: { period: periodId, window: windowHours },
        signal,
      }),
    refetchInterval: (query) => (query.state.data?.as_of ? COMPARISON_REFRESH_MS : false),
  });
}

/** What senders wrote (FR54): a person's read, never a token's. */
export function useSenderDetails(releaseId: string) {
  return useQuery({
    queryKey: ["release-sender-details", releaseId],
    queryFn: ({ signal }) =>
      apiClient.get<{ deploys: SenderDeploy[] }>(
        `releases/${encodeURIComponent(releaseId)}/sender-details`,
        { signal },
      ),
  });
}

export interface CoverageList {
  at: string;
  services: Array<{
    service_name: string;
    coverage: "deploys_and_versions" | "deploys_only" | "versions_only" | "none";
    last_reported_at: string | null;
    first_heard_at: string | null;
  }>;
}

/** Each service's coverage now (FR38): the list names those we hear nothing about. */
export function useCoverage(projectId: string | undefined) {
  return useQuery({
    queryKey: ["release-coverage", projectId],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CoverageList>("releases/coverage", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}
