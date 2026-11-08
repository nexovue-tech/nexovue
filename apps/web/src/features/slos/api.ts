import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { API_BASE_PATH, apiClient } from "@/shared/api-client";

import { REFRESH_MS } from "./slo-words";

/**
 * `/slos`: reliability targets (F19). Postgres only, so the list answers while
 * ClickHouse is down (AC40). Shapes are the API's `slos/schemas.py`.
 */

export type SloState =
  | "disabled"
  | "not_up_to_date"
  | "source_gone"
  | "running_out"
  | "counting"
  | "not_measured"
  | "no_recent_requests"
  | "building_history"
  | "nothing_left"
  | "too_little_traffic"
  | "on_track";

export type SloVariant =
  | "plain"
  | "was_burning"
  | "nothing_left"
  | "resolving"
  | "not_judged"
  | "exactly_on_target"
  | "missed";

export type SloQualifier =
  | "running_out"
  | "not_up_to_date"
  | "no_alerts"
  | "snoozed"
  | "missed"
  | "thin_traffic"
  | "not_measured"
  | "no_recent_requests"
  | "building_history"
  | "never_failed";

export type Sampling = "no" | "yes" | "not_sure";

/** FR1's four kinds. There is no endpoint latency kind (AC4). */
export type SloKind =
  | "service_availability"
  | "service_latency"
  | "endpoint_availability"
  | "check_availability";

export interface SloFigures {
  good: number;
  total: number;
  failures: number;
  allowed: number;
  left: number;
  missed_by: number;
  /** A decimal string, rounded towards failing; null with no events. */
  figure_percent: string | null;
  /** Null when no minute of the window has passed since the first event. */
  coverage: number | null;
  lost_minutes: number;
  excluded_minutes: number;
  days_held: number;
}

export interface SloView {
  id: string;
  project_id: string;
  name: string;
  description: string;
  kind: SloKind;
  service_name: string | null;
  endpoint_name: string | null;
  check_id: string | null;
  check_name: string | null;
  check_interval_seconds: number | null;
  /** A latency target's boundary, and what was typed: "487.2 ms", not "500 ms" (FR3). */
  boundary_index: number | null;
  boundary_ms: number | null;
  asked_ms: number | null;
  left_out_endpoints: string[];
  objective_percent: string;
  window_days: number;
  upstream_sampling: Sampling | null;
  enabled: boolean;
  label: string;
  allowance_words: string;
  state: SloState;
  variant: SloVariant;
  qualifiers: SloQualifier[];
  attention: boolean;
  severity: "critical" | "warning" | null;
  hours_left: number | null;
  quiet_minutes: number | null;
  figures: SloFigures;
  evaluated_minute: string | null;
  last_event_minute: string | null;
  longest_quiet_7d_minutes: number;
  /** The project's zone, which every time on the page shows (FR13). */
  zone: string;
  created_at: string;
  updated_at: string;
}

export interface DeletedSlo {
  id: string;
  name: string;
  label: string;
  deleted_at: string;
  purged_on: string;
  days_held: number;
  deleted_by_name: string | null;
}

export interface SloList {
  targets: SloView[];
  deleted: DeletedSlo[];
  zone: string;
  has_services: boolean;
}

/** The form's fields; `bodyOf` sends only the kind's own (the API refuses another's). */
export interface SloDraft {
  kind: SloKind;
  name: string;
  description: string;
  service_name: string;
  endpoint_name: string;
  check_id: string;
  /** The typed latency threshold, in ms, as typed. */
  asked_ms: string;
  left_out_endpoints: string[];
  objective_percent: string;
  window_days: 7 | 28;
  upstream_sampling: Sampling;
}

export const REQUEST_KINDS: readonly SloKind[] = [
  "service_availability",
  "service_latency",
  "endpoint_availability",
];

/** What the API takes for the draft's kind, and nothing another kind takes. */
export function bodyOf(draft: SloDraft, { withKind }: { withKind: boolean }) {
  const body: Record<string, unknown> = {
    name: draft.name.trim(),
    description: draft.description,
    objective_percent: draft.objective_percent.trim(),
    window_days: draft.window_days,
  };
  if (withKind) body.kind = draft.kind;
  if (draft.kind === "check_availability") return { ...body, check_id: draft.check_id };
  body.service_name = draft.service_name.trim();
  body.upstream_sampling = draft.upstream_sampling;
  if (draft.kind === "endpoint_availability") body.endpoint_name = draft.endpoint_name;
  if (draft.kind === "service_latency") body.asked_ms = Number(draft.asked_ms);
  if (draft.kind === "service_availability") body.left_out_endpoints = draft.left_out_endpoints;
  return body;
}

/** `/slo-readings/suggestion`: FR7's and FR8's arithmetic, as data (the API's schemas). */
export type Suggested =
  | "from_worst_week"
  | "from_typical_week"
  | "starter"
  | "below_lowest"
  | "too_few";

export interface SloSuggestion {
  outcome: Suggested;
  objective: string | null;
  weeks: Array<{ good: number; total: number; figure: string | null }>;
  worst: string | null;
  typical: string | null;
  best: string | null;
  days_of_data: number;
  boundary_index: number | null;
  boundary_ms: number | null;
  week_boundaries_ms: Array<number | null>;
  highest_boundary_ms: number;
  boundaries_ms: number[];
  steps: string[];
  thin_allowed: number;
  endpoints: Array<{ name: string; health_check: boolean }>;
  offer_check: boolean;
  check: {
    interval_seconds: number;
    windows: Array<{ window_days: number; expected_runs: number; fastest_expected_runs: number }>;
    alerts_can_judge: boolean;
  } | null;
}

export interface SuggestionAsk {
  projectId: string;
  kind: SloKind;
  service: string;
  endpoint: string;
  checkId: string;
}

/** Ready to ask: the source the kind needs is named. */
export function suggestionReady(ask: SuggestionAsk): boolean {
  if (ask.kind === "check_availability") return ask.checkId !== "";
  if (ask.kind === "endpoint_availability") return ask.service !== "" && ask.endpoint !== "";
  return ask.service !== "";
}

/** The form's only ClickHouse read (design, *Form states*): its failure never stops a save. */
export function useSuggestion(ask: SuggestionAsk) {
  return useQuery({
    queryKey: ["slos", "suggestion", ask] as const,
    enabled: suggestionReady(ask),
    staleTime: 5 * 60_000,
    queryFn: ({ signal }) => {
      const searchParams: Record<string, string> = { project_id: ask.projectId, kind: ask.kind };
      if (ask.kind === "check_availability") searchParams.check_id = ask.checkId;
      else searchParams.service = ask.service;
      if (ask.kind === "endpoint_availability") searchParams.endpoint = ask.endpoint;
      return apiClient.get<SloSuggestion>("slo-readings/suggestion", { searchParams, signal });
    },
  });
}

export interface CheckChoice {
  id: string;
  name: string;
  interval_seconds: number;
}

/** The project's checks, for a check target (Postgres, `/checks`). */
export function useCheckChoices(projectId: string | undefined) {
  return useQuery({
    queryKey: ["slos", "checks", projectId ?? ""] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient
        .get<{ checks: CheckChoice[] }>("checks", {
          searchParams: { project_id: projectId },
          signal,
        })
        .then((body) => body.checks),
  });
}

/** Whether a silence rule watches the service, so the form can say when a target alone
 * would not alert on it stopping (FR25a, AC79). */
export function useSilenceCovered(projectId: string | undefined, service: string) {
  return useQuery({
    queryKey: ["slos", "silence-coverage", projectId ?? "", service] as const,
    enabled: projectId !== undefined && service !== "",
    queryFn: ({ signal }) =>
      apiClient
        .get<{ rules: Array<{ condition_kind: string; enabled: boolean }> }>(
          `alerts/coverage/services/${encodeURIComponent(service)}`,
          { searchParams: { project_id: projectId }, signal },
        )
        .then((body) =>
          body.rules.some((rule) => rule.condition_kind === "service_silent" && rule.enabled),
        ),
  });
}

const listKey = (projectId: string) => ["slos", "list", projectId] as const;
const targetKey = (sloId: string) => ["slos", "target", sloId] as const;

export function useSlos(projectId: string | undefined) {
  return useQuery({
    queryKey: listKey(projectId ?? ""),
    enabled: projectId !== undefined,
    refetchInterval: REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<SloList>("slos", { searchParams: { project_id: projectId }, signal }),
  });
}

export function useSlo(sloId: string) {
  return useQuery({
    queryKey: targetKey(sloId),
    refetchInterval: REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<SloView>(`slos/${encodeURIComponent(sloId)}`, { signal }),
  });
}

function useInvalidatingMutation<Variables, Result>(
  write: (variables: Variables) => Promise<Result>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: write,
    onSuccess: () => client.invalidateQueries({ queryKey: ["slos"] }),
  });
}

export function useCreateSlo(projectId: string) {
  return useInvalidatingMutation((draft: SloDraft) =>
    apiClient.post<SloView>("slos", bodyOf(draft, { withKind: true }), {
      searchParams: { project_id: projectId },
    }),
  );
}

export function useChangeSlo(sloId: string) {
  return useInvalidatingMutation((change: Record<string, unknown>) =>
    apiClient.patch<SloView>(`slos/${encodeURIComponent(sloId)}`, change),
  );
}

export function useDeleteSlo() {
  return useInvalidatingMutation((sloId: string) =>
    apiClient.delete<void>(`slos/${encodeURIComponent(sloId)}`),
  );
}

export function useRestoreSlo() {
  return useInvalidatingMutation(({ sloId, name }: { sloId: string; name?: string }) =>
    apiClient.post<SloView>(`slos/${encodeURIComponent(sloId)}/restore`, name ? { name } : {}),
  );
}

/** FR27: a month against its objective; not judged below 90% coverage. */
export type MonthResult = "met" | "missed" | "not_judged";

export interface SloSharpFall {
  day: string;
  fall_percent: number;
}

/** FR27a: a traffic finding or silence incident on the target's service that month. */
export interface SloOutage {
  kind: "traffic_finding" | "silence_incident";
  id: string;
  started_at: string;
  minutes: number;
}

/** One local month of one target, from its stored days (the API's `SloMonth`). */
export interface SloMonth {
  month: string;
  result: MonthResult;
  figure_percent: string | null;
  objective_percent: string;
  good: number;
  total: number;
  failures: number;
  coverage: number | null;
  quiet_minutes: number;
  longest_quiet_minutes: number;
  lost_minutes: number;
  excluded_minutes: number;
  missed_runs: number | null;
  maintenance_runs: number | null;
  days_held: number;
  days_in_month: number;
  may_still_change: boolean;
  loss_known: boolean;
  changed_at: string | null;
  changed_why: "definition" | "exclusion" | "exclusion_removed" | null;
  earlier_definition_through: string | null;
  sharp_falls: SloSharpFall[];
  outages: SloOutage[];
  time_not_counted: boolean;
}

export interface SloMonths {
  id: string;
  label: string;
  kind: SloKind;
  upstream_sampling: Sampling | null;
  /** FR27b's fixed label for the kind, the CSV's too. */
  report_label: string;
  audience: string;
  zone: string;
  months: SloMonth[];
}

/** One target's row in *Last month*: live, deleted, or purged (no name). */
export interface SloMonthRow extends SloMonth {
  id: string;
  name: string | null;
  label: string;
  kind: SloKind;
  upstream_sampling: Sampling | null;
  report_label: string;
  deleted_at: string | null;
}

export interface SloLastMonth {
  month: string;
  zone: string;
  audience: string;
  targets: number;
  met: number;
  missed: number;
  not_judged: number;
  met_with_time_not_counted: number;
  rows: SloMonthRow[];
}

export interface SloHistoryEntry {
  at: string;
  what: "created" | "changed" | "deleted" | "restored";
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  /** A person's name, for a person only. */
  by_name: string | null;
}

export function useSloMonths(sloId: string) {
  return useQuery({
    queryKey: ["slos", "months", sloId] as const,
    queryFn: ({ signal }) =>
      apiClient.get<SloMonths>(`slos/${encodeURIComponent(sloId)}/months`, { signal }),
  });
}

export function useSloHistory(sloId: string) {
  return useQuery({
    queryKey: ["slos", "history", sloId] as const,
    queryFn: ({ signal }) =>
      apiClient
        .get<{ entries: SloHistoryEntry[] }>(`slos/${encodeURIComponent(sloId)}/history`, {
          signal,
        })
        .then((body) => body.entries),
  });
}

export function useLastMonth(projectId: string | undefined) {
  return useQuery({
    queryKey: ["slos", "last-month", projectId ?? ""] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<SloLastMonth>("slos/months", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}

/** FR27b's months as a file. A link, never a blob this page builds, as `traceExportUrl`. */
export function monthsCsvUrl(projectId: string, months: number, sloId?: string): string {
  const url = new URL(`${API_BASE_PATH}slos/months.csv`, window.location.origin);
  url.searchParams.set("project_id", projectId);
  url.searchParams.set("months", String(months));
  if (sloId) url.searchParams.set("slo_id", sloId);
  return url.toString();
}
