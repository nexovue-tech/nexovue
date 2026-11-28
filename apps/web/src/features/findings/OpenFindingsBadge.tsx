import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { useSelection } from "@/shared/selection";

import { type OpenFindingCount, openFindingsBadge } from "./open-findings";

const REFRESH_MS = 15_000;

const SEVERITY_BADGE = {
  critical: "bg-critical-subtle text-critical-text",
  warning: "bg-warning-subtle text-warning-text",
} as const;

/** How many findings wait for someone, beside Findings on every screen (sketch G). */
export function OpenFindingsBadge() {
  const { projectId } = useSelection();
  const count = useQuery({
    queryKey: ["findings", "open-count", projectId ?? ""] as const,
    enabled: projectId !== undefined,
    refetchInterval: REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<OpenFindingCount>("findings/open-count", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
  const badge = count.data ? openFindingsBadge(count.data) : null;
  if (!badge) return null;
  return (
    <>
      <span
        aria-hidden="true"
        className={`rounded-full px-1.5 font-medium text-2xs leading-4 ${SEVERITY_BADGE[badge.severity]}`}
      >
        {badge.text}
      </span>
      <span className="sr-only">({badge.label})</span>
    </>
  );
}
