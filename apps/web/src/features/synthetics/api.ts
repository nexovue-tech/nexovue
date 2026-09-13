import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";

/**
 * The synthetic-check half of the M4 contract. Field names are the API's, unchanged.
 *
 * Two stores answer this screen and they answer different questions. `checks` is the
 * definition a customer owns — the URL, the interval, whether it is paused. `synthetics`
 * is what the prober measured against it. A check can exist with nothing measured yet,
 * which is why "no checks defined" and "no probes in this window" are different screens
 * and need both reads to tell apart.
 */

export type CheckType = "http" | "browser";

/** One check as defined, from `GET /checks`. The target is the only human identity. */
export interface CheckDefinition {
  id: string;
  project_id: string;
  type: CheckType;
  target: string;
  interval_seconds: number;
  enabled: boolean;
  config: Record<string, unknown>;
  created_at: string;
}

export interface TlsCertificate {
  days_remaining: number;
  issuer: string;
  /** Read off the certificate, so it is the host the handshake actually reached. */
  server_address: string;
}

export interface CheckResultSummary {
  check_id: string;
  check_type: string;
  runs: number;
  successful_runs: number;
  /** 0 to 100. Zero runs reports 0.0, so read `runs` before trusting it. */
  uptime_percent: number;
  /** Null when nothing reported a status in the window — not the same as down. */
  up: boolean | null;
  latest_status_code: number;
  latest_run_at: string | null;
  mean_duration_ms: number;
  p95_duration_ms: number;
  max_duration_ms: number;
  /** Null for a plain-HTTP target, or one never probed in this window. */
  tls: TlsCertificate | null;
}

export interface PhaseBucket {
  timestamp: string;
  /** Every phase named in the response's `phases`; a phase with no reading is 0. */
  durations_ms: Record<string, number>;
  total_ms: number;
  samples: number;
}

export interface PhaseBreakdown {
  check_id: string;
  bucket_seconds: number;
  /** Request order, and the key set of every bucket's `durations_ms`. */
  phases: string[];
  buckets: PhaseBucket[];
}

export interface CheckRun {
  timestamp: string;
  up: boolean;
  status_code: number;
  duration_ms: number;
  /** The phases summed. A gap against `duration_ms` is unmeasured time. */
  phase_total_ms: number;
}

interface CheckListResponse {
  checks: CheckDefinition[];
}

interface CheckResultListResponse {
  checks: CheckResultSummary[];
}

interface CheckRunListResponse {
  check_id: string;
  /** Newest first. */
  runs: CheckRun[];
}

/** Enough probes to read the recent shape without asking for the whole window. */
const RECENT_RUN_LIMIT = 120;

export const checkDefinitionsQueryKey = (projectId: string) => ["checks", projectId] as const;

export const checkResultsQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  narrowing: string,
) => ["synthetics", "checks", projectId, timeWindow.from, timeWindow.to, narrowing] as const;

export const phaseBreakdownQueryKey = (
  projectId: string,
  checkId: string,
  timeWindow: TimeWindow,
) => ["synthetics", "phases", projectId, checkId, timeWindow.from, timeWindow.to] as const;

export const checkRunsQueryKey = (projectId: string, checkId: string, timeWindow: TimeWindow) =>
  ["synthetics", "runs", projectId, checkId, timeWindow.from, timeWindow.to] as const;

/**
 * The definitions. Deliberately outside the time window: a check defined this morning
 * still exists in a fifteen-minute window, and the window would only make the screen
 * say "you have no checks" every time someone narrowed it.
 */
export function useCheckDefinitions(projectId: string | undefined) {
  return useQuery({
    queryKey: checkDefinitionsQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.checks,
  });
}

export function useCheckResults(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string> = {},
) {
  return useQuery({
    queryKey: checkResultsQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckResultListResponse>("synthetics/checks", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          ...narrowing,
        },
        signal,
      }),
    select: (response) => response.checks,
  });
}

export function usePhaseBreakdown(
  projectId: string | undefined,
  checkId: string | undefined,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: phaseBreakdownQueryKey(projectId ?? "", checkId ?? "", timeWindow),
    enabled: projectId !== undefined && checkId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<PhaseBreakdown>(
        `synthetics/checks/${encodeURIComponent(checkId ?? "")}/phases`,
        {
          searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
          signal,
        },
      ),
  });
}

export function useCheckRuns(
  projectId: string | undefined,
  checkId: string | undefined,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: checkRunsQueryKey(projectId ?? "", checkId ?? "", timeWindow),
    enabled: projectId !== undefined && checkId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckRunListResponse>(
        `synthetics/checks/${encodeURIComponent(checkId ?? "")}/runs`,
        {
          searchParams: {
            project_id: projectId,
            from: timeWindow.from,
            to: timeWindow.to,
            limit: RECENT_RUN_LIMIT,
          },
          signal,
        },
      ),
    select: (response) => response.runs,
  });
}

/** One bucket of a check's history. */
export interface CheckTrendPoint {
  bucket: string;
  runs: number;
  successful_runs: number;
  /** Null when nothing succeeded in the bucket: a failed probe has no response time. */
  mean_duration_ms: number | null;
}

interface CheckTrend {
  check_id: string;
  points: CheckTrendPoint[];
}

interface CheckTrendResponse {
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  checks: CheckTrend[];
}

export const checkTrendQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["check-trend", projectId, timeWindow.from, timeWindow.to] as const;

/**
 * Every check's history, in one request rather than one per row.
 *
 * Separate from the results query so the table appears as soon as the summaries land and
 * fills in its lines when this arrives.
 */
export function useCheckTrend(
  projectId: string | undefined,
  timeWindow: TimeWindow,
): ReadonlyMap<string, readonly CheckTrendPoint[]> {
  const { data } = useQuery({
    queryKey: checkTrendQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckTrendResponse>("synthetics/checks/trend", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });

  return useMemo(
    () => new Map((data?.checks ?? []).map((trend) => [trend.check_id, trend.points])),
    [data],
  );
}
