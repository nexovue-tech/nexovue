import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { findingStatsKey, servicesDetectionKey } from "@/shared/detection/api";
import type { OverviewPoint } from "@/shared/overview-plots";
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
  /**
   * The job's key for the entry: the signal, or `endpoint:<name>` for an endpoint failing on
   * its own. Null for a finding stored before keys were kept.
   */
  key: string | null;
  /** Null when there is no reading, never 0: a missing number and a real zero differ. */
  normal: number | null;
  current: number | null;
  worst: number | null;
  unit: "ms" | "%" | "rpm";
  comparison: string;
  endpoint: string | null;
  /**
   * The normal band as it was when judged (FR16), stored with the finding so it outlives
   * the readings. Null for a finding stored before the band was kept.
   */
  band_low: number | null;
  band_high: number | null;
  /**
   * The evidence endpoint's value then and the same time last week (FR3): its p95 for
   * slower, its share failing for failing more. Null when no week was compared.
   */
  endpoint_current: number | null;
  endpoint_last_week: number | null;
}

// ---- the case (F22): whether people are working, apart from the signal.
export type CaseState = "investigating" | "resolved";
export type CaseResolution =
  | "resolved"
  | "false_alarm"
  | "new_normal"
  | "tracker_closed"
  | "no_activity"
  | "recovered_untouched";
/** Whether the problem is measured now for the whole case. */
export type CaseSignal = "open" | "closed";
/** Whether the assignment email will be tried (FR5); the screen says the truth. */
export type EmailOutcome = "will_try" | "not_configured" | "rate_limited" | "none";

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
  /** The service's snooze and mute as they stand now, not when the finding opened. */
  service_snoozed_until: string | null;
  service_muted_until: string | null;
  started_at: string;
  closed_at: string | null;
  acknowledged_at: string | null;
  signals: FindingSignal[];
  /** A rollup behind the entity, drawn with the same sparkline as everywhere else. */
  trend: number[];
  /** The case (F22). Null throughout means nobody ever took it. */
  case_state: CaseState | null;
  case_resolution: CaseResolution | null;
  case_opened_at: string | null;
  case_resolved_at: string | null;
  /** The case this finding joined (FR12); its page leads to the root. */
  root_finding_id: string | null;
  case_signal: CaseSignal;
  /** The worse of this finding's severity and an open joined finding's. */
  case_severity: FindingSeverity;
  open_joined_finding_id: string | null;
  open_joined_since: string | null;
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
  /** The case's people (FR20): a token never reads these. */
  assignee_id?: string | null;
  assignee_name?: string | null;
  /** When the current assignee took or was given the case (D76). */
  case_taken_at?: string | null;
  /** On a root with an open joined finding: acted since it came back? (AC100). */
  assignee_acted_since_join?: boolean | null;
  case_resolved_by_name?: string | null;
  /** FR48: whether to add the PagerDuty line after Take this and resolve. */
  project_has_pagerduty?: boolean;
  /** Whether this install can send the assignment email at all (FR5). */
  email_available?: boolean;
}

/** One timeline row (FR16). A person's fields; a token gets kinds and times. */
export type EventActorKind = "person" | "system" | "tracker";

export interface TimelineEvent {
  id: string;
  kind: string;
  at: string;
  actor_kind?: EventActorKind;
  actor_id?: string | null;
  actor_name?: string | null;
  /** Enums, ids and numbers only, by the writer's construction (AC24). */
  detail?: Record<string, unknown>;
  /** A note's words, joined live: a deleted note's text is gone (AC19). */
  note_body?: string | null;
}

export interface TimelineResponse {
  events: TimelineEvent[];
  /** Pass back as `before` for the next 100 older rows; null at the end. */
  next_before: string | null;
}

export interface CaseChanged {
  finding: FindingDetail;
  email_outcome: EmailOutcome;
}

export interface CaseNote {
  id: string;
  author_id: string | null;
  author_name: string | null;
  body: string;
  created_at: string;
}

export interface NoteWritten {
  note: CaseNote;
  finding: FindingDetail;
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
  /** True when there were more than the feed's 200, and only the first 200 came. */
  truncated: boolean;
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
  /** The same buckets Overview draws. The feed draws them too, from this one answer. */
  points: OverviewPoint[];
}

/** What the feed keeps of the answer: the totals, and the buckets its charts draw. */
export interface ProjectSummary {
  totals: ProjectTotals;
  points: OverviewPoint[];
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
 * The RED summary the healthy state carries, with the buckets behind it.
 *
 * Declared here rather than imported from `features/overview`, because a feature never
 * imports another feature; the one shared shape, `OverviewPoint`, lives in `shared/`
 * beside the chart-building it feeds. The answer already holds `points`, so drawing
 * Overview's three charts on an empty feed costs no extra scan (FR3).
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
    select: (response): ProjectSummary => ({ totals: response.totals, points: response.points }),
  });
}

/**
 * How many checks are passing, read only once the summary has landed.
 *
 * Gated for the reason the Overview strip is: the telemetry tier's in-flight cap is 8
 * per API process and a read past it waits in the limiter rather than being refused —
 * and at two API processes each holds a cap of 4, so a screen that fires everything at
 * once still leaves a read waiting out the queue (F20 plan, T1.8).
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
  | "no_services"
  | "too_many_services";

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
  /** Services past the cap, which are not judged; a project judges at most the limit. */
  services_too_many: number;
  judged_service_limit: number;
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
export const timelineQueryKey = (findingId: string) => ["findings", "timeline", findingId] as const;

/**
 * FR45: the page refreshes every 30 seconds, a guess, on a visible page only —
 * react-query pauses the interval while the window is unfocused.
 */
export const PAGE_REFRESH_MS = 30_000;

/** One finding and its page. */
export function useFinding(findingId: string) {
  return useQuery({
    queryKey: findingQueryKey(findingId),
    refetchInterval: PAGE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<FindingDetail>(`findings/${encodeURIComponent(findingId)}`, { signal }),
  });
}

/** The root's page, read when this page's finding joined a case (FR12). */
export function useRootCase(rootId: string | null) {
  return useQuery({
    queryKey: findingQueryKey(rootId ?? ""),
    enabled: rootId != null,
    refetchInterval: PAGE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<FindingDetail>(`findings/${encodeURIComponent(rootId ?? "")}`, { signal }),
  });
}

/** The case's timeline, newest first (FR17), refreshed with the page. */
export function useTimeline(findingId: string) {
  return useQuery({
    queryKey: timelineQueryKey(findingId),
    refetchInterval: PAGE_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<TimelineResponse>(`findings/${encodeURIComponent(findingId)}/timeline`, {
        signal,
      }),
  });
}

/** Older timeline rows, fetched on Show earlier; appended by the caller. */
export function fetchEarlierTimeline(findingId: string, before: string) {
  return apiClient.get<TimelineResponse>(`findings/${encodeURIComponent(findingId)}/timeline`, {
    searchParams: { before },
  });
}

/** A working member the reassign picker may offer (FR4). Shapes are the members
 * API's; declared here because a feature never imports another feature. */
export interface AssignableMember {
  id: string;
  email: string;
  name: string | null;
  role: string;
  status: string;
}

export function useAssignableMembers(enabled: boolean) {
  return useQuery({
    queryKey: ["findings", "assignable-members"] as const,
    enabled,
    queryFn: ({ signal }) => apiClient.get<{ members: AssignableMember[] }>("members", { signal }),
    // AC62: the picker never offers a viewer, a pending invitee or one removed.
    select: (response) =>
      response.members.filter((member) => member.role !== "viewer" && member.status === "active"),
  });
}

export interface CaseChangeInput {
  case_state?: CaseState;
  assignee_id?: string | null;
  expected_assignee_id?: string | null;
  expected_case_state?: CaseState | null;
}

function refreshAfterCaseChange(
  queryClient: ReturnType<typeof useQueryClient>,
  findingId: string,
  finding: FindingDetail,
) {
  // The response is the root's page; the acted-on page re-reads itself too.
  queryClient.setQueryData(findingQueryKey(finding.id), finding);
  void queryClient.invalidateQueries({ queryKey: findingQueryKey(findingId) });
  void queryClient.invalidateQueries({ queryKey: timelineQueryKey(findingId) });
  void queryClient.invalidateQueries({ queryKey: timelineQueryKey(finding.id) });
  void queryClient.invalidateQueries({ queryKey: ["findings", "feed"] });
}

/** Take, assign, resolve or reopen (FR1 to FR6): `PATCH /findings/{id}/case`. */
export function useCaseChange(findingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["findings", "case", findingId],
    mutationFn: (input: CaseChangeInput) =>
      apiClient.patch<CaseChanged>(`findings/${encodeURIComponent(findingId)}/case`, input),
    onSuccess: (changed) => refreshAfterCaseChange(queryClient, findingId, changed.finding),
  });
}

/** Write a note (FR14): it lands on the root, and may start the case. */
export function useAddNote(findingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["findings", "note", findingId],
    mutationFn: (body: string) =>
      apiClient.post<NoteWritten>(`findings/${encodeURIComponent(findingId)}/notes`, { body }),
    onSuccess: (written) => refreshAfterCaseChange(queryClient, findingId, written.finding),
  });
}

/** Delete a note (FR15): the text is gone; the timeline keeps the fact. */
export function useDeleteNote(findingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["findings", "note-delete", findingId],
    mutationFn: (noteId: string) =>
      apiClient.delete<FindingDetail>(
        `findings/${encodeURIComponent(findingId)}/notes/${encodeURIComponent(noteId)}`,
      ),
    onSuccess: (finding) => refreshAfterCaseChange(queryClient, findingId, finding),
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
 * as it now is, which replaces the page's copy. What a closed or marked finding changes
 * reads again: the feed, the detection status, the false-alarm counts, and the
 * finding markers on its project's service pages.
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
      const projectId = changed.finding.project_id;
      void queryClient.invalidateQueries({ queryKey: ["findings", "feed"] });
      void queryClient.invalidateQueries({ queryKey: ["findings", "detection"] });
      void queryClient.invalidateQueries({ queryKey: findingStatsKey(projectId) });
      // Every service's, by prefix: a project finding spans several.
      void queryClient.invalidateQueries({ queryKey: servicesDetectionKey(projectId) });
    },
  });
}
