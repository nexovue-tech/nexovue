import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

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
  kind: string;
  service_name: string | null;
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

export interface SloDraft {
  name: string;
  description: string;
  service_name: string;
  objective_percent: string;
  window_days: 7 | 28;
  upstream_sampling: Sampling;
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
    apiClient.post<SloView>("slos", draft, { searchParams: { project_id: projectId } }),
  );
}

export function useChangeSlo(sloId: string) {
  return useInvalidatingMutation((change: Partial<SloDraft> & { enabled?: boolean }) =>
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
