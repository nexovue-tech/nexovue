import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

/**
 * The feed, and the reads its healthy state is made of.
 *
 * The feed is `GET /findings`: one row per detected problem, written by the detection
 * engine (ADR-022). The checks and the RED summary beneath it stay as they were, because
 * an empty feed is still not proof of health (`headline.ts`).
 */

export type FindingSeverity = "critical" | "warning";
/** Acknowledged is still open in every way that matters: somebody saw it, nobody fixed it. */
export type FindingState = "open" | "acknowledged" | "closed";
/** How a closed finding closed. Null while it is open, and the card names it once closed. */
export type ClosedReason =
  | "recovered"
  | "false_alarm"
  | "new_normal"
  | "adopted"
  | "service_gone"
  | "could_not_be_judged";
export type FindingScope = "service" | "project";
export type FindingSignalKind = "slower" | "failing" | "traffic";

/** One signal behind a finding, with the normal it was judged against. */
export interface FindingSignal {
  signal: FindingSignalKind;
  /** Null when there is no reading, never 0: a missing number and a real zero differ. */
  normal: number | null;
  current: number | null;
  worst: number | null;
  unit: "ms" | "%" | "rpm";
  comparison: string;
  endpoint: string | null;
}

export interface Finding {
  id: string;
  project_id: string;
  /** A project finding has no service: it spans several. */
  scope: FindingScope;
  service_name: string | null;
  /**
   * **The title is the product.** A finding is a sentence: subject, what changed, how
   * much, since when. F1 AC1.3 forbids raw metric names and percentile jargon here, and
   * that constraint belongs to the engine — a screen cannot rescue a title that arrives
   * as `http_server_duration_p99{service="checkout-api"} > 3.2`.
   */
  title: string;
  severity: FindingSeverity;
  state: FindingState;
  closed_reason: ClosedReason | null;
  muted: boolean;
  started_at: string;
  closed_at: string | null;
  acknowledged_at: string | null;
  signals: FindingSignal[];
  /** A rollup behind the entity, drawn with the same sparkline as everywhere else. */
  trend: number[];
}

/** One judged minute of one signal, in the signal's unit. */
export interface SignalPoint {
  at: string;
  /** Null when the minute had nothing to measure; drawn as a gap, never as zero. */
  value: number | null;
  normal: number | null;
  /**
   * Where the sensitivity's minimum change lies: above the normal for slower and
   * failing more, below it for losing traffic. Between it and the normal is the band.
   */
  edge: number | null;
}

/** One signal's readings against its normal band, oldest first (AC38). */
export interface SignalSeries {
  signal: FindingSignalKind;
  unit: FindingSignal["unit"];
  points: SignalPoint[];
}

/** A threshold or *went silent* incident on the same service (BR2). */
export interface LinkedIncident {
  id: string;
  rule_name: string;
  condition_kind: string;
  opened_at: string;
  resolved_at: string | null;
}

/** What *This is the new normal* would make one signal's normal (FR21). */
export interface ProposedShift {
  signal: FindingSignalKind;
  unit: FindingSignal["unit"];
  ratio: number;
  normal_before: number | null;
  normal_after: number | null;
}

/** Whether the level is known well enough to become the normal yet. */
export interface NewNormalPreview {
  available: boolean;
  readings: number;
  needed: number;
  signals: ProposedShift[];
}

/** One finding as its page reads it (FR31). */
export interface FindingDetail extends Finding {
  /** Empty once the readings have expired, and always for a project finding (AC40). */
  series: SignalSeries[];
  incidents: LinkedIncident[];
  /** A project finding's services; empty for a service's. */
  services: string[];
  /** A signed-in person's alone: an API token never reads these. */
  acknowledged_by?: string | null;
  acknowledged_by_name?: string | null;
  feedback_reason?: string | null;
  /** Null when closed, or a project's finding, which has no normal of its own. */
  new_normal?: NewNormalPreview | null;
}

/** What an action did: the finding as it now is, and who hears it close. */
export interface FindingChanged {
  finding: FindingDetail;
  /** The channels its *resolved* message goes to; empty when nothing is sent. */
  resolved_to: string[];
}

export interface FindingsResponse {
  findings: Finding[];
  /** How many findings the feed is leaving out because somebody muted them. */
  muted_count: number;
}

/**
 * The feed: every finding, open and closed, muted or not as asked.
 *
 * It asks for `state=all` and sends no time window, because the feed's question is
 * "what needs me, and what stopped being wrong", not "what happened between two
 * instants": an open finding from before the window still needs somebody. The window is
 * taken only for its refresh, so the feed stays as live as the summary beneath it.
 *
 * A failed read is the query's error, never an empty feed: an empty feed would read as
 * "nothing is wrong", and the error state exists so that it never does (AC18).
 */
export function useFindings(projectId: string | undefined, muted: boolean, refresh: TimeWindow) {
  return useQuery({
    ...liveOptions(refresh),
    queryKey: ["findings", "feed", projectId ?? "", muted] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<FindingsResponse>("findings", {
        searchParams: { project_id: projectId, state: "all", muted },
        signal,
      }),
  });
}

export interface ProjectTotals {
  service_count: number;
  error_count: number;
  requests_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  p95_ms: number;
}

interface ServiceOverviewResponse {
  totals: ProjectTotals;
}

interface CheckSummary {
  /** Null when the check has not run in the window — never false, which means down. */
  up: boolean | null;
}

/** What the screen knows about this project's checks. */
export interface CheckCounts {
  total: number;
  passing: number;
  down: number;
}

interface CheckListResponse {
  checks: CheckSummary[];
}

/**
 * The RED summary the healthy state carries.
 *
 * Declared here rather than imported from `features/overview`, because a feature never
 * imports another feature. What the two share is the endpoint and the token layer, which
 * is the right amount of sharing — not a type that would couple two screens' rendering
 * to one another.
 */
export function useProjectSummary(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["findings", "summary", projectId ?? "", windowKeyOf(timeWindow)] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceOverviewResponse>("services/overview", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
    select: (response) => response.totals,
  });
}

/**
 * How many checks are passing, read only once the summary has landed.
 *
 * Gated for the reason the Overview strip is: the telemetry tier allows four reads in
 * flight and refuses the fifth rather than queueing it, so a screen that fires
 * everything at once fails part of itself every time it is opened.
 */
export function useCheckCounts(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  ready: boolean,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["findings", "checks", projectId ?? "", windowKeyOf(timeWindow)] as const,
    enabled: projectId !== undefined && ready,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("synthetics/checks", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
    select: (response) => ({
      total: response.checks.length,
      passing: response.checks.filter((check) => check.up === true).length,
      // Counted apart from "not passing": a check that has not run in this
      // window is a gap in what we can see, and the headline may only call a
      // check down when a probe said so (`headline.ts`).
      down: response.checks.filter((check) => check.up === false).length,
    }),
  });
}

/** What detection is doing for a project, decided by the API, never inferred here. */
export type DetectionState =
  | "learning"
  | "judging"
  | "data_delayed"
  | "not_running"
  | "no_services";

export interface DetectionStatus {
  state: DetectionState;
  services_total: number;
  services_learning: number;
  services_judged: number;
  services_delayed: number;
  services_muted: number;
  /** A day on the project's calendar (`2026-11-03`), or null when nothing is learning. */
  earliest_judging_date: string | null;
  not_running_since: string | null;
  delayed_since: string | null;
  open_findings: number;
}

/**
 * Whether detection is judging this project, for the line above the checks
 * (`headline.ts`).
 *
 * Not gated behind the summary like the checks read: `/projects` is outside the
 * telemetry tier, so it takes none of that tier's in-flight slots and can go at once.
 * A failed read stays the query's error, and the screen shows its error state for it:
 * a missing status must never leave a healthy line standing.
 */
export function useDetectionStatus(projectId: string | undefined, refresh: TimeWindow) {
  return useQuery({
    ...liveOptions(refresh),
    queryKey: ["findings", "detection", projectId ?? ""] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DetectionStatus>(
        `projects/${encodeURIComponent(projectId ?? "")}/detection/status`,
        { signal },
      ),
  });
}

export const findingQueryKey = (findingId: string) => ["findings", "one", findingId] as const;

/** One finding and its page. */
export function useFinding(findingId: string) {
  return useQuery({
    queryKey: findingQueryKey(findingId),
    queryFn: ({ signal }) =>
      apiClient.get<FindingDetail>(`findings/${encodeURIComponent(findingId)}`, { signal }),
  });
}

export type FindingAction = "acknowledge" | "false-alarm" | "new-normal";

export interface FindingActionInput {
  action: FindingAction;
  /** A false alarm's reason, the person's to give or not. */
  reason?: string;
}

/**
 * Acknowledge, false alarm or new normal (FR19 to FR21). Each answers with the finding
 * as it now is, which replaces the page's copy; the feed and the detection status read
 * again, since the finding may have closed.
 */
export function useFindingAction(findingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["findings", "action", findingId],
    mutationFn: ({ action, reason }: FindingActionInput) =>
      apiClient.post<FindingChanged>(
        `findings/${encodeURIComponent(findingId)}/${action}`,
        action === "false-alarm" ? { reason: reason?.trim() ? reason.trim() : null } : undefined,
      ),
    onSuccess: (changed) => {
      queryClient.setQueryData(findingQueryKey(findingId), changed.finding);
      void queryClient.invalidateQueries({ queryKey: ["findings", "feed"] });
      void queryClient.invalidateQueries({ queryKey: ["findings", "detection"] });
    },
  });
}
