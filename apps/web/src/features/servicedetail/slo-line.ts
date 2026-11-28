import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * The service page's one line about reliability targets (F19 design, *Elsewhere*):
 * "2 targets. 1 running out." Read from the project's target list, which answers from
 * Postgres alone, so the line stays while ClickHouse is down. AC13's scan reads this file.
 */

/** One target, as much of it as this line counts. */
export interface ServiceTarget {
  service_name: string | null;
  state: string;
}

const RUNNING_OUT = "running_out";
// The list's own refresh (`features/slos`, NFR7): the job judges once a minute.
const REFRESH_MS = 60_000;

export function useServiceTargets(projectId: string) {
  return useQuery({
    queryKey: ["service-detail", "slos", projectId] as const,
    refetchInterval: REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<{ targets: ServiceTarget[] }>("slos", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}

/** This service's targets and how many are running out; null when it has none. */
export function serviceTargetsLine(
  targets: readonly ServiceTarget[],
  serviceName: string,
): string | null {
  const mine = targets.filter((target) => target.service_name === serviceName);
  if (mine.length === 0) return null;
  const counted = mine.length === 1 ? "1 target." : `${mine.length} targets.`;
  const running = mine.filter((target) => target.state === RUNNING_OUT).length;
  return running === 0 ? counted : `${counted} ${running} running out.`;
}
