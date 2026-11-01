import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../api-client";

/**
 * Detection's per-service state and the controls over it (F6, FR18, FR22, FR25). In
 * shared/ because the finding page, a service's page and the project settings all
 * read and change it, and none of them may import another feature.
 */

/** The badge's one word, decided by the API (`detection/service.py`). */
export type ServiceDetectionState =
  | "judged"
  | "learning"
  | "muted"
  | "data_delayed"
  | "not_running"
  | "no_detection_yet"
  | "too_many_services"
  | "no_incoming_requests";

export type SignalKind = "slower" | "failing" | "traffic";

export interface SignalStatus {
  signal: SignalKind;
  state: "judged" | "learning" | "too_little_traffic" | "not_judged";
  /** A day on the project's calendar (`2026-11-03`) while it learns. */
  learning_until: string | null;
  reason: "no_baseline" | "not_recorded" | null;
}

/** A finding on the service's timeline. */
export interface FindingMarker {
  id: string;
  started_at: string;
  closed_at: string | null;
  severity: "critical" | "warning";
}

export interface ServiceDetection {
  state: ServiceDetectionState;
  signals: SignalStatus[];
  muted_until: string | null;
  snoozed_until: string | null;
  findings: FindingMarker[];
  truncated: boolean;
}

export const serviceDetectionKey = (projectId: string | undefined, service: string) =>
  ["detection", "service", projectId ?? "", service] as const;

/** One service's detection state, and its findings over the chart's window. */
export function useServiceDetection(
  projectId: string | undefined,
  service: string,
  range: { from: string; to: string },
) {
  return useQuery({
    queryKey: [...serviceDetectionKey(projectId, service), range.from, range.to] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceDetection>(`services/${encodeURIComponent(service)}/detection`, {
        searchParams: { project_id: projectId, from: range.from, to: range.to },
        signal,
      }),
  });
}

export type Sensitivity = "low" | "normal" | "high";

/** A project's sensitivity, and the IANA zone its normal and its titles are read in. */
export interface DetectionSettings {
  sensitivity: Sensitivity;
  timezone: string;
}

export const detectionSettingsKey = (projectId: string | undefined) =>
  ["detection", "settings", projectId ?? ""] as const;

export function useDetectionSettings(projectId: string | undefined) {
  return useQuery({
    queryKey: detectionSettingsKey(projectId),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DetectionSettings>(
        `projects/${encodeURIComponent(projectId ?? "")}/detection`,
        { signal },
      ),
  });
}

/** What is quietening a service now, if anything. */
export interface ServiceControls {
  muted_until: string | null;
  snoozed_until: string | null;
}

/** Snooze (every working role, at most 4 hours) or mute (admins, at most 30 days). */
export type Quieting = "snooze" | "mute";

export interface QuietInput {
  kind: Quieting;
  /** An ISO instant still to come, or null to end it now. */
  until: string | null;
}

export function useQuietService(projectId: string, service: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["detection", "quiet", projectId, service],
    mutationFn: ({ kind, until }: QuietInput) => {
      const path = `services/${encodeURIComponent(service)}/${kind}`;
      const body = { project_id: projectId, until };
      return kind === "snooze"
        ? apiClient.post<ServiceControls>(path, body)
        : apiClient.put<ServiceControls>(path, body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serviceDetectionKey(projectId, service) });
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
    },
  });
}

export function useSaveDetectionSettings(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["detection", "settings", "save", projectId],
    mutationFn: (settings: DetectionSettings) =>
      apiClient.put<DetectionSettings>(
        `projects/${encodeURIComponent(projectId)}/detection`,
        settings,
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData(detectionSettingsKey(projectId), saved);
      // Titles and the headline name the zone; the feed reads them again.
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
    },
  });
}

/** FR26: findings opened and false alarms over a window, against the budget. */
export interface FindingStats {
  days: number;
  opened: number;
  false_alarms: number;
  judged_services: number;
  /** Null when no service has been judged: there is nothing to divide by. */
  false_finding_rate_per_service_week: number | null;
}

export function useFindingStats(projectId: string) {
  return useQuery({
    queryKey: ["detection", "stats", projectId] as const,
    queryFn: ({ signal }) =>
      apiClient.get<FindingStats>("findings/stats", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}
