import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

import type { CheckConfig, CheckType, DisplayedState, RunEvidence } from "./check-model";

/**
 * The synthetic-check half of the M4 contract. Field names are the API's, unchanged.
 *
 * Two stores answer this screen and they answer different questions. `checks` is the
 * definition a customer owns — the URL, the interval, whether it is paused. `synthetics`
 * is what the prober measured against it. A check can exist with nothing measured yet,
 * which is why "no checks defined" and "no probes in this window" are different screens
 * and need both reads to tell apart.
 */

export type { CheckType } from "./check-model";

/**
 * How often a check's confirmed state is re-read. A run lands at most once a minute,
 * and the state is written just after it, so a quarter of that shows a change promptly
 * without a request a second.
 */
const STATE_REFRESH_MS = 15_000;

/** One check as defined, from `GET /checks`, with the state its runs confirmed. */
export interface CheckDefinition {
  id: string;
  project_id: string;
  name: string;
  type: CheckType;
  config: CheckConfig;
  /** For a heartbeat, the period a call is expected within. */
  interval_seconds: number;
  /** Empty means every location this deployment has. */
  locations: string[];
  degraded_ms: number | null;
  failing_ms: number | null;
  retries: number;
  enabled: boolean;
  /** What runs confirmed, before paused and maintenance are applied. Null: never run. */
  confirmed_state: string | null;
  state_since: string | null;
  last_run_at: string | null;
  in_maintenance: boolean;
  /** What the console shows. Null when the check has never been judged. */
  displayed_state: DisplayedState | null;
  last_ping_at: string | null;
  created_at: string;
  updated_at: string;
}

/** A just-created check. A heartbeat's token is here this once and never again. */
export interface CreatedCheck extends CheckDefinition {
  heartbeat_token: string | null;
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
  /** The latest confirmed run's outcome, including ones uptime does not count. */
  latest_outcome: string;
  latest_status_code: number;
  latest_run_at: string | null;
  /** Null when nothing in the window was timed — a heartbeat, or runs that never sent. */
  mean_duration_ms: number | null;
  p95_duration_ms: number | null;
  max_duration_ms: number | null;
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
  /** Empty for readings from before runs carried an id; such a run has no evidence. */
  run_id: string;
  up: boolean;
  status_code: number;
  /** Null when the run timed nothing: a heartbeat, or a run refused before its request. */
  duration_ms: number | null;
  /** The phases summed. A gap against `duration_ms` is unmeasured time. */
  phase_total_ms: number | null;
  outcome: string;
  attempt: number;
  /** False for an attempt a retry overruled: shown, never counted. */
  final: boolean;
  maintenance: boolean;
  location: string;
}

interface CheckListResponse {
  checks: CheckDefinition[];
  available_locations: string[];
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

/* The maintenance-window check picker's cache, owned by project settings, which this
   feature may not import. It lists every check, so creating, renaming or deleting one
   makes it stale. */
const maintenanceCheckChoicesQueryKey = (projectId: string) =>
  ["maintenance-check-choices", projectId] as const;

/** The project's secret and variable lists, as project settings also keys them. */
export const checkSecretsQueryKey = (projectId: string) => ["check-secrets", projectId] as const;
export const checkVariablesQueryKey = (projectId: string) =>
  ["check-variables", projectId] as const;

/** Everything that lists a project's checks, refreshed after one is added, renamed or removed. */
export function invalidateCheckLists(
  queryClient: ReturnType<typeof useQueryClient>,
  projectId: string | undefined,
): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: checkDefinitionsQueryKey(projectId ?? "") }),
    queryClient.invalidateQueries({ queryKey: maintenanceCheckChoicesQueryKey(projectId ?? "") }),
  ]);
}

export const checkResultsQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  narrowing: string,
) => ["synthetics", "checks", projectId, windowKeyOf(timeWindow), narrowing] as const;

export const phaseBreakdownQueryKey = (
  projectId: string,
  checkId: string,
  timeWindow: TimeWindow,
) => ["synthetics", "phases", projectId, checkId, windowKeyOf(timeWindow)] as const;

export const checkRunsQueryKey = (projectId: string, checkId: string, timeWindow: TimeWindow) =>
  ["synthetics", "runs", projectId, checkId, windowKeyOf(timeWindow)] as const;

/**
 * The definitions. Deliberately outside the time window: a check defined this morning
 * still exists in a fifteen-minute window, and the window would only make the screen
 * say "you have no checks" every time someone narrowed it.
 */
export function useCheckDefinitions(projectId: string | undefined) {
  return useQuery({
    queryKey: checkDefinitionsQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    refetchInterval: STATE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.checks,
  });
}

/** Where a check can run from on this deployment, read off the same list. */
export function useProbeLocations(projectId: string | undefined) {
  return useQuery({
    queryKey: checkDefinitionsQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.available_locations,
  });
}

export function useCheckResults(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string> = {},
) {
  return useQuery({
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
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
  ["check-trend", projectId, windowKeyOf(timeWindow)] as const;

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
    ...liveOptions(timeWindow),
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

/* ---------------------------------------------------------------- one check */

export const checkQueryKey = (checkId: string) => ["check", checkId] as const;

export function useCheck(checkId: string | undefined) {
  return useQuery({
    queryKey: checkQueryKey(checkId ?? ""),
    enabled: checkId !== undefined,
    refetchInterval: STATE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<CheckDefinition>(`checks/${encodeURIComponent(checkId ?? "")}`, { signal }),
  });
}

/** What a create or an edit sends. The kind lives in `config.type`. */
export interface CheckInput {
  name: string;
  config: CheckConfig;
  interval_seconds: number;
  locations: string[];
  degraded_ms: number | null;
  failing_ms: number | null;
  retries: number;
  enabled?: boolean;
}

export function useCreateCheck(projectId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["checks", "create"],
    // A heartbeat's answer carries its token: drop it once nothing is watching.
    gcTime: 0,
    mutationFn: (input: CheckInput) =>
      apiClient.post<CreatedCheck>("checks", { project_id: projectId, ...input }),
    onSuccess: () => invalidateCheckLists(queryClient, projectId),
  });
}

export function useUpdateCheck(checkId: string, projectId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["checks", "update", checkId],
    mutationFn: (input: Partial<CheckInput>) =>
      apiClient.patch<CheckDefinition>(`checks/${encodeURIComponent(checkId)}`, input),
    onSuccess: (check) => {
      queryClient.setQueryData(checkQueryKey(check.id), check);
      void invalidateCheckLists(queryClient, projectId);
    },
  });
}

export function useDeleteCheck(projectId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["checks", "delete"],
    mutationFn: (checkId: string) =>
      apiClient.delete<void>(`checks/${encodeURIComponent(checkId)}`),
    onSuccess: () => invalidateCheckLists(queryClient, projectId),
  });
}

interface DeletionImpact {
  check_id: string;
  files: number;
}

interface CheckRunCount {
  check_id: string;
  runs: number;
}

/** What deleting loses, in real numbers (FR37): runs from the store, files from Postgres. */
export function useDeletionImpact(
  projectId: string | undefined,
  check: CheckDefinition | undefined,
  enabled: boolean,
) {
  const files = useQuery({
    queryKey: ["check", check?.id ?? "", "deletion-impact"],
    enabled: enabled && check !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DeletionImpact>(
        `checks/${encodeURIComponent(check?.id ?? "")}/deletion-impact`,
        { signal },
      ),
  });
  const runs = useQuery({
    queryKey: ["check", check?.id ?? "", "run-count"],
    enabled: enabled && check !== undefined && projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<CheckRunCount>(
        `synthetics/checks/${encodeURIComponent(check?.id ?? "")}/run-count`,
        {
          searchParams: {
            project_id: projectId,
            from: check?.created_at,
            to: new Date().toISOString(),
          },
          signal,
        },
      ),
  });
  return { files, runs };
}

export interface HeartbeatToken {
  token: string;
  ping_path: string;
}

export function useIssueHeartbeatToken(checkId: string) {
  return useMutation({
    mutationKey: ["checks", "heartbeat-token", checkId],
    // The answer is a credential shown once: drop it once nothing is watching.
    gcTime: 0,
    mutationFn: () =>
      apiClient.post<HeartbeatToken>(`checks/${encodeURIComponent(checkId)}/heartbeat-token`),
  });
}

export interface CheckStateChange {
  from_state: string | null;
  to_state: string;
  changed_at: string;
  run_id: string | null;
  location: string | null;
  reason: string | null;
}

export function useStateChanges(checkId: string | undefined) {
  return useQuery({
    queryKey: ["check", checkId ?? "", "state-changes"],
    enabled: checkId !== undefined,
    refetchInterval: STATE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<{ check_id: string; changes: CheckStateChange[] }>(
        `checks/${encodeURIComponent(checkId ?? "")}/state-changes`,
        { signal },
      ),
    select: (response) => response.changes,
  });
}

/* ----------------------------------------------------------------- evidence */

export function useRunEvidence(
  projectId: string | undefined,
  checkId: string | undefined,
  runId: string | undefined,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: ["synthetics", "evidence", projectId ?? "", checkId ?? "", runId ?? ""],
    enabled: projectId !== undefined && checkId !== undefined && Boolean(runId),
    // Evidence of a finished run never changes.
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: ({ signal }) =>
      apiClient.get<{ evidence: RunEvidence }>(
        `synthetics/checks/${encodeURIComponent(checkId ?? "")}/runs/${encodeURIComponent(runId ?? "")}`,
        {
          searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
          signal,
        },
      ),
    select: (response) => response.evidence,
  });
}

/* ------------------------------------------------------------------ run now */

export interface RunNowInput {
  name?: string;
  config: CheckConfig;
  degraded_ms: number | null;
  failing_ms: number | null;
  location?: string;
}

interface RunNowAccepted {
  request_id: string;
  location: string;
}

interface RunNowResult {
  request_id: string;
  status: "pending" | "complete";
  evidence: RunEvidence | null;
}

/** How long the API waits for a result before answering "still pending". */
const RUN_NOW_WAIT_SECONDS = 20;
/** Four waits (80s) cover the longest run a check may take (60s) plus dispatch. */
const RUN_NOW_MAX_WAITS = 4;
const RUN_NOW_REQUEST_TIMEOUT_MS = (RUN_NOW_WAIT_SECONDS + 10) * 1000;

/**
 * Dispatch, then wait on the API for the result — a few long requests per run rather
 * than a poll every half second against the run-now quota (ADR-011).
 */
export function useRunNow(projectId: string | undefined) {
  return useMutation({
    mutationKey: ["checks", "run-now"],
    mutationFn: async (input: RunNowInput): Promise<RunEvidence> => {
      const accepted = await apiClient.post<RunNowAccepted>("check-runs", {
        project_id: projectId,
        ...input,
      });
      for (let wait = 0; wait < RUN_NOW_MAX_WAITS; wait += 1) {
        const result = await apiClient.get<RunNowResult>(
          `check-runs/${encodeURIComponent(accepted.request_id)}`,
          {
            searchParams: { wait_seconds: RUN_NOW_WAIT_SECONDS },
            timeoutMs: RUN_NOW_REQUEST_TIMEOUT_MS,
          },
        );
        if (result.status === "complete" && result.evidence) return result.evidence;
      }
      throw new RunNowTimedOut();
    },
  });
}

export class RunNowTimedOut extends Error {
  constructor() {
    super(
      "The run did not report back in time. The probe workers may be busy or stopped; try again shortly.",
    );
    this.name = "RunNowTimedOut";
  }
}

/* -------------------------------------------------------------------- files */

export interface CheckFile {
  id: string;
  project_id: string;
  check_id: string | null;
  name: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export function useUploadCheckFile(projectId: string | undefined) {
  return useMutation({
    mutationKey: ["check-files", "upload"],
    mutationFn: (file: File) =>
      apiClient.post<CheckFile>("check-files", undefined, {
        file,
        searchParams: { project_id: projectId, name: file.name },
      }),
  });
}

/* --------------------------------------------------- secrets and variables */

/** Names only, for pickers: a secret's value never comes back (FR20). */
export function useSecretNames(projectId: string | undefined) {
  return useQuery({
    queryKey: checkSecretsQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ secrets: Array<{ name: string }> }>(
        `projects/${encodeURIComponent(projectId ?? "")}/check-secrets`,
        { signal },
      ),
    select: (response) => response.secrets.map((secret) => secret.name),
  });
}

export function useVariableNames(projectId: string | undefined) {
  return useQuery({
    queryKey: checkVariablesQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ variables: Array<{ name: string }> }>(
        `projects/${encodeURIComponent(projectId ?? "")}/check-variables`,
        { signal },
      ),
    select: (response) => response.variables.map((variable) => variable.name),
  });
}
