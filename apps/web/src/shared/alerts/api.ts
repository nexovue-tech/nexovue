import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api-client";

/**
 * What alerts on one subject (F21, FR24). In shared/ because a check's page and a
 * service's page both show it, and neither may import the alerts feature.
 */

export interface CoveringRule {
  id: string;
  name: string;
  muted_until: string | null;
  enabled: boolean;
  notifying_nobody: boolean;
  channels: { id: string; name: string }[];
}

export interface CoveringIncident {
  id: string;
  severity: "critical" | "warning";
  opened_at: string;
  rule_name: string;
}

export interface Coverage {
  rules: CoveringRule[];
  open_incidents: CoveringIncident[];
}

export function useServiceCoverage(projectId: string | undefined, service: string) {
  return useQuery({
    queryKey: ["alert-coverage", "service", service, projectId ?? ""],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<Coverage>(`alerts/coverage/services/${encodeURIComponent(service)}`, {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}

export function useCheckCoverage(projectId: string, checkId: string) {
  return useQuery({
    queryKey: ["alert-coverage", "check", checkId],
    queryFn: ({ signal }) =>
      apiClient.get<Coverage>(`alerts/coverage/checks/${checkId}`, {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}
